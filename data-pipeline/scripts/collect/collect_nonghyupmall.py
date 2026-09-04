from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[2]
MAIN_URL = "https://www.nonghyupmall.com/BC31010R/main.nh?basketCnt=0&cdnAplYn=N"
BASE_SEARCH_URL = "https://www.nonghyupmall.com/BC1F010M/srchTotalList.nh"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "raw" / "nonghyup"
DEFAULT_MAX_PAGES = 0
DEFAULT_LIST_SIZE = 60
PLAYWRIGHT_TIMEOUT_MS = 60000
CSV_ENCODING = "utf-8-sig"
SOURCE_NAME = "NONGHYUP_MALL"
CHANNEL_NAME = "ONLINE"

PRODUCTS = {
    "egg": {"product_name": "계란", "search_keyword": "계란"},
    "milk": {"product_name": "우유", "search_keyword": "우유"},
    "sugar": {"product_name": "설탕", "search_keyword": "설탕"},
    "flour": {"product_name": "밀가루", "search_keyword": "밀가루"},
    "butter": {"product_name": "버터", "search_keyword": "버터"},
}

IRRELEVANT_PRODUCT_PATTERNS = {
    "milk": re.compile(r"우유맛|우유향|웨하스|크리스피롤|두유|요거트|요구르트", re.IGNORECASE),
    "butter": re.compile(r"버터(?:쿠키|오징어|맛|향|스콘|롤|칩|와플|비스킷|크래커|아몬드)", re.IGNORECASE),
    "egg": re.compile(r"계란(?:맛|향)|에그(?:타르트|쿠키|롤|샌드|과자)", re.IGNORECASE),
    "sugar": re.compile(r"설탕(?:맛|향)|슈가(?:롤|쿠키|캔디|사탕)", re.IGNORECASE),
    "flour": re.compile(r"밀가루(?:맛|향)", re.IGNORECASE),
}

OUTPUT_COLUMNS = [
    "collected_at",
    "source",
    "channel",
    "product_key",
    "product_name",
    "query",
    "keyword",
    "page",
    "source_url",
    "item_id",
    "uitem_id",
    "item_name",
    "brand_name",
    "display_price",
    "original_price",
    "sale_price",
    "unit_price",
    "promotion_type",
    "price_source",
    "site_no",
    "salestr_no",
    "shipping_type",
    "shipping_detail_type",
    "shipping_type_code",
    "shipping_type_detail_code",
    "deal_item_yn",
    "product_url",
    "item_url",
    "raw_cart_json",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProductRequest:
    product_key: str
    product_name: str
    search_keyword: str


def product_request(product_key: str) -> ProductRequest:
    product = PRODUCTS[product_key]
    return ProductRequest(
        product_key=product_key,
        product_name=product["product_name"],
        search_keyword=product["search_keyword"],
    )


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else ROOT / path


def build_search_url(keyword: str) -> str:
    return f"{BASE_SEARCH_URL}?{urlencode({'searchTerm_main': keyword, 'CHAN_C': '1101', 'chanC': '1101'})}"


def item_url(item_id: str) -> str:
    return f"https://www.nonghyupmall.com/BC14010R/viewDetailPage.nh?wrsC={item_id}" if item_id else ""


def normalize_price_text(value: Any) -> str:
    if value is None:
        return ""
    match = re.search(r"(\d[\d,]*)", str(value))
    return match.group(1).replace(",", "") if match else ""


def extract_brand_name(item_name: str) -> str:
    bracket = re.match(r"^\[([^\]]+)\]", item_name)
    if bracket:
        return bracket.group(1).strip()
    first_token = re.split(r"\s+", item_name.strip(), maxsplit=1)[0]
    return first_token if len(first_token) <= 20 else ""


def extract_capacity(item_name: str) -> str:
    patterns = [
        r"\d+(?:\.\d+)?\s*(?:ml|mL|ML|l|L|g|G|kg|KG)\s*(?:[xX*]\s*\d+\s*(?:개|입|팩|봉)?)?",
        r"\d+\s*(?:구|개입|개|입|팩|봉)",
    ]
    matches: list[str] = []
    for pattern in patterns:
        matches.extend(match.group(0).strip() for match in re.finditer(pattern, item_name))
    return " / ".join(dict.fromkeys(matches))


def total_count_from_text(text: str) -> int | None:
    match = re.search(r"총\s*([\d,]+)\s*개의\s*상품", text)
    return int(match.group(1).replace(",", "")) if match else None


def parse_price_block(text: str) -> tuple[str, str, str]:
    original = ""
    sale = ""
    original_match = re.search(r"정가\s*:\s*([\d,]+)\s*원", text)
    sale_match = re.search(r"(?:판매가|쿠폰할인가)\s*:\s*([\d,]+)\s*원", text)
    if original_match:
        original = normalize_price_text(original_match.group(1))
    if sale_match:
        sale = normalize_price_text(sale_match.group(1))
    display = sale or original or normalize_price_text(text)
    return display, original, sale


def filter_product_rows(product: ProductRequest, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    pattern = IRRELEVANT_PRODUCT_PATTERNS.get(product.product_key)
    if pattern is None:
        return rows
    return [row for row in rows if not pattern.search(" ".join((row["brand_name"], row["item_name"])))]


def extract_product_rows(
    page: Any,
    *,
    collected_at: str,
    product: ProductRequest,
    page_number: int,
    source_url: str,
) -> list[dict[str, str]]:
    cards = page.evaluate(
        """() => {
          const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
          return [...document.querySelectorAll('li.product-item')].map((card) => {
            const productLink = card.querySelector('[data-wrs-c]');
            const itemId = productLink?.getAttribute('data-wrs-c') || '';
            const name = clean(card.querySelector('img[alt]')?.getAttribute('alt'))
              || clean(card.querySelector('.product-info')?.innerText)
              || clean(card.innerText);
            const priceText = clean(card.querySelector('.product-price')?.innerText);
            return {
              item_id: itemId,
              item_name: name,
              price_text: priceText,
              card_text: clean(card.innerText),
              html: card.outerHTML
            };
          });
        }"""
    )
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for card in cards:
        item_id = str(card.get("item_id") or "").strip()
        item_name = str(card.get("item_name") or "").strip()
        if not item_id or not item_name:
            continue
        if item_id in seen:
            continue
        seen.add(item_id)
        display_price, original_price, sale_price = parse_price_block(str(card.get("price_text") or card.get("card_text") or ""))
        url = item_url(item_id)
        raw_json = json.dumps(card, ensure_ascii=False, separators=(",", ":"))
        rows.append(
            {
                "collected_at": collected_at,
                "source": SOURCE_NAME,
                "channel": CHANNEL_NAME,
                "product_key": product.product_key,
                "product_name": product.product_name,
                "query": product.search_keyword,
                "keyword": product.search_keyword,
                "page": str(page_number),
                "source_url": source_url,
                "item_id": item_id,
                "uitem_id": "",
                "item_name": item_name,
                "brand_name": extract_brand_name(item_name),
                "display_price": display_price,
                "original_price": original_price,
                "sale_price": sale_price or display_price,
                "unit_price": extract_capacity(item_name),
                "promotion_type": "",
                "price_source": "rendered_product_card.product_price",
                "site_no": "",
                "salestr_no": "",
                "shipping_type": "",
                "shipping_detail_type": "",
                "shipping_type_code": "",
                "shipping_type_detail_code": "",
                "deal_item_yn": "",
                "product_url": url,
                "item_url": url,
                "raw_cart_json": raw_json,
            }
        )
    return rows


def current_total_count(page: Any) -> int | None:
    body_text = page.locator("body").inner_text()
    return total_count_from_text(body_text)


def current_active_page(page: Any) -> int | None:
    value = page.locator("#page").first.get_attribute("value")
    try:
        return int(value or "")
    except ValueError:
        return None


def wait_for_search_page(page: Any) -> None:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_selector("li.product-item, .pagination", timeout=PLAYWRIGHT_TIMEOUT_MS)
    page.wait_for_timeout(1000)


def go_to_page(page: Any, page_number: int) -> bool:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    selector = f'.pagination a[href*="pageNavigate({page_number})"]'
    link = page.locator(selector).first
    before_url = page.url
    try:
        if link.count() > 0:
            link.click(timeout=10000)
        else:
            invoked = page.evaluate(
                """(pageNumber) => {
                  if (typeof window.pageNavigate !== 'function') return false;
                  window.pageNavigate(pageNumber);
                  return true;
                }""",
                page_number,
            )
            if not invoked:
                return False
        try:
            page.wait_for_url(lambda url: url != before_url and "page=" in url, timeout=30000)
        except PlaywrightTimeoutError:
            pass
        wait_for_search_page(page)
    except Exception:
        return False
    return current_active_page(page) == page_number


def collect_product_with_chrome(
    page: Any,
    *,
    product: ProductRequest,
    collected_at: str,
    max_pages: int,
    list_size: int,
) -> dict[str, Any]:
    all_rows: list[dict[str, str]] = []
    page_stats: list[dict[str, Any]] = []
    termination_reason = "max_pages"
    statuses: list[int | None] = []

    url = build_search_url(product.search_keyword)
    response = page.goto(url, wait_until="domcontentloaded", timeout=PLAYWRIGHT_TIMEOUT_MS)
    statuses.append(response.status if response else None)
    wait_for_search_page(page)
    total_count = current_total_count(page)
    estimated_total_pages = math.ceil(total_count / list_size) if total_count else None
    effective_max_pages = max_pages if max_pages > 0 else estimated_total_pages

    page_number = 1
    while effective_max_pages is None or page_number <= effective_max_pages:
        rows = filter_product_rows(
            product,
            extract_product_rows(
                page,
                collected_at=collected_at,
                product=product,
                page_number=page_number,
                source_url=page.url,
            ),
        )
        all_rows.extend(rows)
        page_stats.append(
            {
                "page": page_number,
                "status": statuses[-1] if statuses else None,
                "url": page.url,
                "rows": len(rows),
            }
        )
        logger.info(
            "Nonghyup Mall page collected: product=%s page=%s rows=%s",
            product.product_key,
            page_number,
            len(rows),
        )
        if not rows:
            termination_reason = "last_page"
            break
        if effective_max_pages is not None and page_number >= effective_max_pages:
            termination_reason = "max_pages"
            break
        next_page = page_number + 1
        if not go_to_page(page, next_page):
            termination_reason = "pagination_unavailable"
            break
        page_number = next_page

    rows = deduplicate_rows(all_rows)
    return {
        "product_key": product.product_key,
        "product_name": product.product_name,
        "keyword": product.search_keyword,
        "source": SOURCE_NAME,
        "channel": CHANNEL_NAME,
        "browser": "chrome",
        "collected_at": collected_at,
        "max_pages": max_pages,
        "list_size": list_size,
        "total_count": total_count,
        "estimated_total_pages": estimated_total_pages,
        "page_stats": page_stats,
        "visited_page_count": len(page_stats),
        "termination_reason": termination_reason,
        "raw_row_count": len(all_rows),
        "row_count": len(rows),
        "duplicate_removed_count": len(all_rows) - len(rows),
        "_rows": rows,
    }


def deduplicate_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (row["product_key"], row["keyword"], row["item_id"] or row["product_url"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def deduplicate_across_products(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        identity = row["item_id"] or row["product_url"] or json.dumps(row, ensure_ascii=False, sort_keys=True)
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(row)
    return deduped


def combined_output_path(output_dir: Path, collected_at: str) -> Path:
    collect_date = collected_at.split("T", 1)[0]
    return output_dir / f"nonghyupmall_all_{collect_date}.csv"


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=CSV_ENCODING, newline="") as file:
        writer = csv.DictWriter(file, fieldnames=OUTPUT_COLUMNS, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def collect_products(product_keys: list[str], output_dir: Path, max_pages: int, list_size: int) -> dict[str, Any]:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required for Nonghyup Mall collection.") from exc

    collected_at = datetime.now().replace(microsecond=0).isoformat()
    summaries: list[dict[str, Any]] = []
    collected_rows: list[dict[str, str]] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=False)
        context = browser.new_context(locale="ko-KR")
        page = context.new_page()
        home_response = page.goto(MAIN_URL, wait_until="domcontentloaded", timeout=PLAYWRIGHT_TIMEOUT_MS)
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(2000)

        for product_key in product_keys:
            summary = collect_product_with_chrome(
                page,
                product=product_request(product_key),
                collected_at=collected_at,
                max_pages=max_pages,
                list_size=list_size,
            )
            collected_rows.extend(summary.pop("_rows"))
            summaries.append(summary)
        browser.close()

    rows = deduplicate_across_products(collected_rows)
    path = combined_output_path(output_dir, collected_at)
    write_rows(path, rows)
    duplicate_removed_count = len(collected_rows) - len(rows)
    combined_output = str(path.relative_to(ROOT))
    for summary in summaries:
        summary["output"] = combined_output
    return {
        "execution_environment": {
            "playwright": "python",
            "channel": "chrome",
            "headless": False,
            "profile": "ephemeral new_context",
            "logged_in": False,
            "captcha_or_stealth": False,
        },
        "source": SOURCE_NAME,
        "channel": CHANNEL_NAME,
        "response_status": home_response.status if home_response else None,
        "output": combined_output,
        "row_count": len(rows),
        "duplicate_removed_count": duplicate_removed_count,
        "summaries": summaries,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Nonghyup Mall online product prices with local Chrome.")
    parser.add_argument("--product", nargs="+", choices=sorted(PRODUCTS), default=sorted(PRODUCTS))
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="0 means collect all detected pages.")
    parser.add_argument("--list-size", type=int, default=DEFAULT_LIST_SIZE)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()
    if args.max_pages < 0:
        parser.error("--max-pages must be greater than or equal to zero.")
    if args.list_size <= 0:
        parser.error("--list-size must be greater than zero.")
    return args


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    result = collect_products(
        product_keys=args.product,
        output_dir=resolve_path(args.output_dir),
        max_pages=args.max_pages,
        list_size=args.list_size,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
