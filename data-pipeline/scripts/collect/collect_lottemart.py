from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[2]
HOME_URL = "https://lottemartzetta.com/"
BASE_SEARCH_URL = "https://lottemartzetta.com/products/search"
BASE_SEARCH_API_URL = "https://lottemartzetta.com/api/webproductpagews/v6/product-pages/search"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "raw" / "lottemart"
DEFAULT_MAX_PAGES = 0
DEFAULT_API_PAGE_SIZE = 300
CSV_ENCODING = "utf-8-sig"
SOURCE_NAME = "LOTTEMART_ZETTA"
CHANNEL_NAME = "ONLINE"
logger = logging.getLogger(__name__)

PRODUCTS = {
    "egg": {
        "product_name": "계란",
        "search_keyword": "계란",
    },
    "milk": {
        "product_name": "우유",
        "search_keyword": "우유",
    },
    "sugar": {
        "product_name": "설탕",
        "search_keyword": "설탕",
    },
    "flour": {
        "product_name": "밀가루",
        "search_keyword": "밀가루",
    },
    "butter": {
        "product_name": "버터",
        "search_keyword": "버터",
    },
}

RELEVANT_PRODUCT_PATTERNS = {
    "milk": re.compile(r"우유|밀크", re.IGNORECASE),
    "butter": re.compile(r"버터", re.IGNORECASE),
    "egg": re.compile(r"계란|달걀|에그", re.IGNORECASE),
    "sugar": re.compile(r"설탕|슈가", re.IGNORECASE),
    "flour": re.compile(r"밀가루|중력분|박력분|강력분|부침가루|튀김가루", re.IGNORECASE),
}

IRRELEVANT_PRODUCT_PATTERNS = {
    "milk": re.compile(r"우유맛|우유향", re.IGNORECASE),
    "butter": re.compile(r"버터(?:쿠키|오징어|맛|향|스콘|롤|칩|와플|비스킷|크래커)", re.IGNORECASE),
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


def search_url(query: str) -> str:
    return f"{BASE_SEARCH_URL}?q={quote(query)}"


def search_api_url(query: str, *, page_token: str = "", page_size: int = DEFAULT_API_PAGE_SIZE) -> str:
    params = {
        "includeAdditionalPageInfo": "true",
        "maxPageSize": str(page_size),
        "maxProductsToDecorate": str(page_size),
        "q": query,
        "tag": "web",
    }
    if page_token:
        params["pageToken"] = page_token
    return f"{BASE_SEARCH_API_URL}?{urlencode(params)}"


def parse_price(value: Any) -> int | None:
    if value is None:
        return None
    match = re.search(r"\d[\d,]*", str(value))
    return int(match.group(0).replace(",", "")) if match else None


def product_config(product_key: str) -> dict[str, str]:
    return PRODUCTS[product_key]


def is_relevant_product(product_key: str, name: str) -> bool:
    relevant = RELEVANT_PRODUCT_PATTERNS.get(product_key)
    irrelevant = IRRELEVANT_PRODUCT_PATTERNS.get(product_key)
    if relevant is not None and not relevant.search(name):
        return False
    if irrelevant is not None and irrelevant.search(name):
        return False
    return True


def extract_brand_name(product_name: str) -> str:
    name = product_name.strip()
    if not name:
        return ""
    bracket_match = re.match(r"^\[([^\]]+)\]", name)
    if bracket_match:
        return bracket_match.group(1).strip()
    first_token = re.split(r"\s+", name, maxsplit=1)[0]
    return first_token if first_token and len(first_token) <= 20 else ""


def unit_price_text(product: dict[str, Any]) -> str:
    unit_price = product.get("unitPrice")
    if not isinstance(unit_price, dict):
        return ""
    price = unit_price.get("price")
    if not isinstance(price, dict):
        return ""
    amount = price.get("amount")
    if amount is None:
        return ""
    unit_name = str(unit_price.get("unitName") or unit_price.get("unit") or "")
    unit_labels = {
        "PER_10G": "10g당",
        "PER_100G": "100g당",
        "PER_1KG": "1kg당",
        "PER_100ML": "100ml당",
        "PER_1L": "1L당",
        "PER_1M": "1m당",
        "EACH": "개당",
        "fop.price.per.10gram": "10g당",
        "fop.price.per.100gram": "100g당",
        "fop.price.per.kilogram": "1kg당",
        "fop.price.per.100milliliter": "100ml당",
        "fop.price.per.liter": "1L당",
        "fop.price.per.meter": "1m당",
        "fop.price.per.each": "개당",
    }
    label = unit_labels.get(unit_name, unit_name)
    if not label:
        return ""
    return f"{label} {amount}원"


def product_price_amount(product: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = product.get(key)
        if isinstance(value, dict):
            amount = value.get("amount")
            if amount is not None:
                return str(amount)
        elif value is not None:
            parsed = parse_price(value)
            if parsed is not None:
                return str(parsed)
    return ""


def product_url(product_id: str) -> str:
    return f"https://lottemartzetta.com/products/{product_id}/details" if product_id else ""


def iter_api_products(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        products = value.get("products")
        if isinstance(products, list):
            found.extend(item for item in products if isinstance(item, dict))
        decorated = value.get("decoratedProducts")
        if isinstance(decorated, list):
            found.extend(item for item in decorated if isinstance(item, dict))
        for child in value.values():
            found.extend(iter_api_products(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(iter_api_products(child))
    return found


def api_product_row(
    product: dict[str, Any],
    *,
    product_key: str,
    product_name: str,
    query: str,
    collected_at: str,
    source_url: str,
    selected_store: str,
    store_id_or_code: str,
    page_number: int,
) -> dict[str, Any] | None:
    name = str(product.get("name") or "").strip()
    item_id = str(product.get("retailerProductId") or product.get("productId") or "").strip()
    if not name or not item_id or not is_relevant_product(product_key, name):
        return None
    display_price = product_price_amount(product, "price", "basePrice")
    promotions = product.get("promotions")
    promotion_type = ""
    if isinstance(promotions, list):
        promotion_type = " / ".join(
            str(item.get("description") or "").strip()
            for item in promotions
            if isinstance(item, dict) and item.get("description")
        )
    raw_product = {
        **product,
        "selected_store": selected_store,
        "store_id_or_code": store_id_or_code,
    }
    url = product_url(item_id)
    return {
        "collected_at": collected_at,
        "source": SOURCE_NAME,
        "channel": CHANNEL_NAME,
        "product_key": product_key,
        "product_name": product_name,
        "query": query,
        "keyword": query,
        "page": str(page_number),
        "source_url": source_url,
        "item_id": item_id,
        "uitem_id": "",
        "item_name": name,
        "brand_name": str(product.get("brand") or extract_brand_name(name)).strip(),
        "display_price": display_price,
        "original_price": product_price_amount(product, "previousPrice", "wasPrice"),
        "sale_price": display_price,
        "unit_price": unit_price_text(product),
        "promotion_type": promotion_type,
        "price_source": "search_api.price.amount",
        "site_no": "",
        "salestr_no": store_id_or_code,
        "shipping_type": "",
        "shipping_detail_type": "",
        "shipping_type_code": "",
        "shipping_type_detail_code": "",
        "deal_item_yn": "",
        "product_url": url,
        "item_url": url,
        "raw_cart_json": json.dumps(raw_product, ensure_ascii=False, separators=(",", ":")),
    }


def extract_api_rows(
    body: Any,
    *,
    product_key: str,
    product_name: str,
    query: str,
    collected_at: str,
    source_url: str,
    selected_store: str,
    store_id_or_code: str,
    page_number: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for product in iter_api_products(body):
        row = api_product_row(
            product,
            product_key=product_key,
            product_name=product_name,
            query=query,
            collected_at=collected_at,
            source_url=source_url,
            selected_store=selected_store,
            store_id_or_code=store_id_or_code,
            page_number=page_number,
        )
        if row is None:
            continue
        identity = str(row["item_id"] or row["product_url"])
        if identity in seen:
            continue
        seen.add(identity)
        rows.append(row)
    return rows


def extract_product_rows(
    page: Any,
    *,
    product_key: str,
    product_name: str,
    query: str,
    collected_at: str,
    source_url: str,
) -> list[dict[str, str | int | None]]:
    cards = page.evaluate(
        """() => {
          const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
          return [...document.querySelectorAll('[data-test="fop-product-link"]')]
            .map((link) => {
              const card = link.closest('.product-card-container') || link.closest('li') || link.parentElement;
              const text = clean(card?.innerText || card?.textContent);
              const href = new URL(link.getAttribute('href'), location.href).href;
              const name = clean(card?.querySelector('img[alt]')?.getAttribute('alt')) || clean(link.textContent);
              const priceMatch = text.match(/가격\\s*([\\d,]+)\\s*원/);
              const unitMatch = text.match(/\\(([^()]*(?:당|\\/)[^()]*)\\)/);
              return {
                product_name: name,
                product_url: href,
                display_price: priceMatch ? priceMatch[1] : null,
                unit_price: unitMatch ? unitMatch[1] : null,
                card_text: text
              };
            });
        }"""
    )
    rows: list[dict[str, str | int | None]] = []
    seen: set[str] = set()
    for card in cards:
        name = str(card.get("product_name") or "").strip()
        url = str(card.get("product_url") or "").strip()
        if not name or not url or url in seen:
            continue
        if not is_relevant_product(product_key, name):
            continue
        seen.add(url)
        product_id = url.rstrip("/").split("/")[-2] if "/details" in url else ""
        display_price = parse_price(card.get("display_price"))
        rows.append(
            {
                "collected_at": collected_at,
                "source": SOURCE_NAME,
                "channel": CHANNEL_NAME,
                "product_key": product_key,
                "product_name": product_name,
                "query": query,
                "keyword": query,
                "page": "1",
                "source_url": source_url,
                "item_id": product_id,
                "uitem_id": "",
                "item_name": name,
                "brand_name": extract_brand_name(name),
                "display_price": display_price,
                "original_price": None,
                "sale_price": display_price,
                "unit_price": card.get("unit_price"),
                "promotion_type": "",
                "price_source": "rendered_product_card",
                "site_no": "",
                "salestr_no": "",
                "shipping_type": "",
                "shipping_detail_type": "",
                "shipping_type_code": "",
                "shipping_type_detail_code": "",
                "deal_item_yn": "",
                "product_url": url,
                "item_url": url,
                "raw_cart_json": json.dumps(card, ensure_ascii=False, separators=(",", ":")),
            }
        )
    return rows


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=CSV_ENCODING, newline="") as file:
        writer = csv.DictWriter(file, fieldnames=OUTPUT_COLUMNS, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def collect_product(page: Any, *, product_key: str, collected_at: str) -> dict[str, Any]:
    return collect_product_pages(
        page,
        product_key=product_key,
        collected_at=collected_at,
        max_pages=1,
        api_page_size=DEFAULT_API_PAGE_SIZE,
    )


def collect_product_pages(
    page: Any,
    *,
    product_key: str,
    collected_at: str,
    max_pages: int,
    api_page_size: int,
) -> dict[str, Any]:
    product = product_config(product_key)
    product_name = product["product_name"]
    query = product["search_keyword"]
    target_url = search_url(query)
    search_api_status: int | None = None
    selected_store = ""
    store_id_or_code = ""
    api_sources: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    page_stats: list[dict[str, Any]] = []
    page_token = ""
    seen_page_tokens: set[str] = set()
    termination_reason = "max_pages"

    body_text = page.locator("body").inner_text()
    store_match = re.search(r"배송점포:\s*([^\n]+)", body_text)
    selected_store = store_match.group(1).strip() if store_match else ""
    page_number = 1
    while max_pages <= 0 or page_number <= max_pages:
        api_url = search_api_url(query, page_token=page_token, page_size=api_page_size)
        response = page.request.get(api_url, headers={"Referer": HOME_URL}, timeout=60000)
        search_api_status = response.status
        api_sources.append(
            {
                "page": page_number,
                "url": api_url,
                "status": response.status,
                "content_type": response.headers.get("content-type", ""),
            }
        )
        page_rows: list[dict[str, Any]] = []
        next_page_token = ""
        if response.ok:
            body = response.json()
            page_rows = extract_api_rows(
                body,
                product_key=product_key,
                product_name=product_name,
                query=query,
                collected_at=collected_at,
                source_url=target_url,
                selected_store=selected_store,
                store_id_or_code=store_id_or_code,
                page_number=page_number,
            )
            metadata = body.get("metadata") if isinstance(body, dict) else None
            if isinstance(metadata, dict):
                next_page_token = str(metadata.get("nextPageToken") or "").strip()
        rows.extend(page_rows)
        page_stats.append(
            {
                "page": page_number,
                "status": response.status,
                "url": api_url,
                "rows": len(page_rows),
                "has_next_page_token": bool(next_page_token),
            }
        )
        logger.info(
            "Lottemart ZETTA page collected: product=%s page=%s rows=%s",
            product_key,
            page_number,
            len(page_rows),
        )
        if not response.ok:
            termination_reason = "api_error"
            break
        if not next_page_token:
            termination_reason = "last_page"
            break
        if next_page_token in seen_page_tokens:
            termination_reason = "repeated_page_token"
            break
        if max_pages > 0 and page_number >= max_pages:
            termination_reason = "max_pages"
            break
        seen_page_tokens.add(next_page_token)
        page_token = next_page_token
        page_number += 1

    if not rows and max_pages in (0, 1):
        page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        rows = extract_product_rows(
            page,
            product_key=product_key,
            product_name=product_name,
            query=query,
            collected_at=collected_at,
            source_url=page.url,
        )
    for row in rows:
        row["salestr_no"] = store_id_or_code
        raw_card = json.loads(str(row["raw_cart_json"]))
        raw_card["selected_store"] = selected_store
        raw_card["store_id_or_code"] = store_id_or_code
        row["raw_cart_json"] = json.dumps(raw_card, ensure_ascii=False, separators=(",", ":"))

    return {
        "product_key": product_key,
        "product_name": product_name,
        "keyword": query,
        "source": SOURCE_NAME,
        "channel": CHANNEL_NAME,
        "target_url": target_url,
        "search_api_status": search_api_status,
        "max_pages": max_pages,
        "api_page_size": api_page_size,
        "page_stats": page_stats,
        "visited_page_count": len(page_stats),
        "termination_reason": termination_reason,
        "row_count": len(rows),
        "selected_store": selected_store,
        "store_id_or_code": store_id_or_code,
        "api_sources": [
            {key: value for key, value in source.items() if key != "body"}
            for source in api_sources
        ],
        "_rows": rows,
    }


def combined_output_path(output_dir: Path, collected_at: str) -> Path:
    collect_date = collected_at.split("T", 1)[0]
    return output_dir / f"lottemart_zetta_all_{collect_date}.csv"


def deduplicate_across_products(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = str(row.get("item_id") or row.get("product_url") or row.get("item_url") or "")
        if not identity:
            identity = json.dumps(row, ensure_ascii=False, sort_keys=True)
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(row)
    return deduped


def collect_products(output_dir: Path, product_keys: list[str], max_pages: int, api_page_size: int, headless: bool = False) -> dict[str, Any]:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required for Lottemart ZETTA collection.") from exc

    collected_at = datetime.now().replace(microsecond=0).isoformat()
    response_status: int | None = None
    summaries: list[dict[str, Any]] = []
    collected_rows: list[dict[str, Any]] = []

    with sync_playwright() as playwright:
        executable_path = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        launch_options: dict[str, Any] = {"headless": headless}
        if executable_path:
            launch_options["executable_path"] = executable_path
        else:
            launch_options["channel"] = "chrome"
        browser = playwright.chromium.launch(**launch_options)
        context = browser.new_context(locale="ko-KR")
        page = context.new_page()
        response = page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
        response_status = response.status if response else None
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(3000)
        for product_key in product_keys:
            summary = collect_product_pages(
                page,
                product_key=product_key,
                collected_at=collected_at,
                max_pages=max_pages,
                api_page_size=api_page_size,
            )
            collected_rows.extend(summary.pop("_rows"))
            summaries.append(summary)
        browser.close()

    rows = deduplicate_across_products(collected_rows)
    if not rows:
        api_statuses = [summary.get("search_api_status") for summary in summaries]
        raise RuntimeError(
            "Lottemart ZETTA collection returned zero products; raw CSV was not written "
            f"(search_api_statuses={api_statuses})."
        )
    path = combined_output_path(output_dir, collected_at)
    write_rows(path, rows)
    logger.info(
        "Lottemart ZETTA collected: status=%s products=%s rows=%s output=%s",
        response_status,
        len(product_keys),
        len(rows),
        path,
    )
    return {
        "execution_environment": {
            "playwright": "python",
            "channel": "chromium" if executable_path else "chrome",
            "headless": headless,
            "profile": "ephemeral new_context",
            "logged_in": False,
            "captcha_or_stealth": False,
        },
        "source": SOURCE_NAME,
        "channel": CHANNEL_NAME,
        "response_status": response_status,
        "row_count": len(rows),
        "duplicate_removed_count": len(collected_rows) - len(rows),
        "summaries": summaries,
        "output": str(path.relative_to(ROOT)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Lottemart ZETTA online product prices.")
    parser.add_argument(
        "--product",
        nargs="+",
        choices=sorted(PRODUCTS),
        default=sorted(PRODUCTS),
        help="Product keys to collect. Defaults to all registered products.",
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="0 means follow all API page tokens.")
    parser.add_argument("--api-page-size", type=int, default=DEFAULT_API_PAGE_SIZE)
    parser.add_argument("--headless", action="store_true", help="Run the browser without a display (required in containers).")
    args = parser.parse_args()
    if args.max_pages < 0:
        parser.error("--max-pages must be greater than or equal to zero.")
    if args.api_page_size <= 0:
        parser.error("--api-page-size must be greater than zero.")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = collect_products(
        Path(args.output_dir) if Path(args.output_dir).is_absolute() else ROOT / args.output_dir,
        args.product,
        args.max_pages,
        args.api_page_size,
        args.headless,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
