from __future__ import annotations

import argparse
import csv
import html as html_lib
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

import requests


ROOT = Path(__file__).resolve().parents[2]
MAIN_URL = "https://emart.ssg.com/"
BASE_SEARCH_URL = "https://emart.ssg.com/search.ssg"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "raw" / "emart"
DEFAULT_MAX_PAGES = 0
DEFAULT_REQUEST_INTERVAL_SECONDS = 1.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_SECONDS = 5.0
REQUEST_TIMEOUT_SECONDS = 30
PLAYWRIGHT_TIMEOUT_MS = 60000
CSV_ENCODING = "utf-8-sig"
ITEM_JSON_MARKER = "장바구니 담기"
NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(?P<payload>.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
SOURCE_NAME = "EMART"
CHANNEL_NAME = "ONLINE"

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

IRRELEVANT_PRODUCT_PATTERNS = {
    "milk": re.compile(r"우유맛|우유향|우유風味|우유맛과자", re.IGNORECASE),
    "butter": re.compile(r"버터(?:쿠키|오징어|맛|향|스콘|롤|칩|과자)", re.IGNORECASE),
    "egg": re.compile(r"계란(?:맛|향)|에그(?:타르트|쿠키|롤|샌드)", re.IGNORECASE),
    "sugar": re.compile(r"설탕(?:맛|향)|슈가(?:파우더|롤|쿠키)", re.IGNORECASE),
    "flour": re.compile(r"밀가루(?:맛|향)|플라워(?:케이크|쿠키)", re.IGNORECASE),
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


class RequestThrottler:
    def __init__(
        self,
        interval_seconds: float,
        *,
        sleep_func: Callable[[float], None] = time.sleep,
        clock_func: Callable[[], float] = time.monotonic,
    ) -> None:
        self.interval_seconds = interval_seconds
        self.sleep_func = sleep_func
        self.clock_func = clock_func
        self.last_request_at: float | None = None

    def wait(self) -> None:
        if self.last_request_at is not None:
            elapsed = self.clock_func() - self.last_request_at
            remaining = self.interval_seconds - elapsed
            if remaining > 0:
                self.sleep_func(remaining)
        self.last_request_at = self.clock_func()


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


def build_search_url(keyword: str, page: int) -> str:
    params = {
        "query": keyword,
    }
    if page > 1:
        params["page"] = str(page)
    return f"{BASE_SEARCH_URL}?{urlencode(params)}"


def retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def request_headers(*, referer: str | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.6,en;q=0.4",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
    }
    if referer:
        headers["Referer"] = referer
    return headers


def warm_up_session(session: requests.Session, *, throttler: RequestThrottler) -> None:
    throttler.wait()
    response = session.get(
        MAIN_URL,
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers=request_headers(),
    )
    logger.info(
        "Emart Mall session warm-up: status=%s url=%s cookie_count=%s",
        response.status_code,
        MAIN_URL,
        len(session.cookies),
    )


def fetch_page(
    session: requests.Session,
    url: str,
    *,
    throttler: RequestThrottler,
    max_retries: int,
    backoff_seconds: float,
) -> str:
    headers = request_headers(referer=MAIN_URL)

    for attempt in range(max_retries + 1):
        throttler.wait()
        response = session.get(
            url,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers=headers,
        )
        if response.status_code != 429:
            response.raise_for_status()
            response.encoding = response.apparent_encoding or response.encoding
            return response.text

        if attempt >= max_retries:
            response.raise_for_status()

        wait_seconds = retry_after_seconds(response.headers.get("Retry-After"))
        if wait_seconds is None:
            wait_seconds = backoff_seconds * (2**attempt)
        logger.warning(
            "Emart Mall request rate-limited: status=429 attempt=%s max_retries=%s backoff_seconds=%.2f url=%s",
            attempt + 1,
            max_retries,
            wait_seconds,
            url,
        )
        throttler.sleep_func(wait_seconds)

    raise RuntimeError("Unreachable Emart Mall fetch retry state.")


def find_json_object_end(text: str, start: int) -> int:
    depth = 0
    in_string = False
    escaped = False
    for idx in range(start, len(text)):
        char = text[idx]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return idx + 1
    raise ValueError("Unclosed JSON object in Emart Mall HTML.")


def iter_cart_json_strings(html: str) -> list[str]:
    json_strings: list[str] = []
    cursor = 0
    while True:
        marker_pos = html.find(ITEM_JSON_MARKER, cursor)
        if marker_pos == -1:
            break
        object_start = html.find("{", marker_pos + len(ITEM_JSON_MARKER))
        if object_start == -1:
            cursor = marker_pos + len(ITEM_JSON_MARKER)
            continue
        try:
            object_end = find_json_object_end(html, object_start)
        except ValueError:
            cursor = object_start + 1
            continue
        json_strings.append(html[object_start:object_end])
        cursor = object_end
    return json_strings


def item_url_from_payload(payload: dict[str, Any]) -> str:
    item_url = payload.get("itemLnkd") or payload.get("itemUrl")
    if isinstance(item_url, str) and item_url:
        return item_url

    item_id = payload.get("itemId")
    site_no = payload.get("siteNo")
    salestr_no = payload.get("salestrNo")
    if not item_id or not site_no:
        return ""

    params = {"itemId": item_id, "siteNo": site_no}
    if salestr_no:
        params["salestrNo"] = salestr_no
    return f"https://emart.ssg.com/item/itemView.ssg?{urlencode(params)}"


def first_text(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return str(value).strip()
    return ""


def normalize_price_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    match = re.search(r"(\d[\d,]*)\s*원?", text)
    if match:
        return match.group(1).replace(",", "")
    if re.fullmatch(r"\d[\d,]*", text):
        return text.replace(",", "")
    return text


def price_info(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("priceInfo")
    return value if isinstance(value, dict) else {}


def display_price_from_payload(payload: dict[str, Any]) -> tuple[str, str]:
    info = price_info(payload)
    raw_primary_price = normalize_price_text(info.get("rawPrimaryPrice"))
    if raw_primary_price:
        return raw_primary_price, "next_data.priceInfo.rawPrimaryPrice"

    primary_price = normalize_price_text(info.get("primaryPrice"))
    if primary_price:
        return primary_price, "next_data.priceInfo.primaryPrice"

    display_price = normalize_price_text(payload.get("displayPrc"))
    if display_price:
        return display_price, "cart_json.displayPrc"

    sell_price = normalize_price_text(payload.get("sellPrc"))
    if sell_price:
        return sell_price, "payload.sellPrc"

    return "", ""


def promotion_type_from_payload(payload: dict[str, Any]) -> str:
    info = price_info(payload)
    values = [
        first_text(info, "discountRate"),
        first_text(info, "couponText"),
        first_text(info, "shortCouponText"),
        first_text(payload, "festaName"),
        first_text(payload, "advertBadgeToolTip"),
    ]
    return " / ".join(value for value in values if value)


def normalize_payload(
    *,
    payload: dict[str, Any],
    raw_json: str,
    collected_at: str,
    product_key: str,
    product_name: str,
    keyword: str,
    page: int,
    source_url: str,
) -> dict[str, str]:
    info = price_info(payload)
    display_price, price_source = display_price_from_payload(payload)

    return {
        "collected_at": collected_at,
        "source": SOURCE_NAME,
        "channel": CHANNEL_NAME,
        "product_key": product_key,
        "product_name": product_name,
        "query": keyword,
        "keyword": keyword,
        "page": str(page),
        "source_url": source_url,
        "item_id": first_text(payload, "itemId"),
        "uitem_id": first_text(payload, "uitemId"),
        "item_name": first_text(payload, "itemNm", "itemName"),
        "brand_name": first_text(payload, "brandNm", "brandName"),
        "display_price": display_price,
        "original_price": normalize_price_text(info.get("strikeOutPrice")),
        "sale_price": display_price,
        "unit_price": first_text(info, "unitPriceDescription"),
        "promotion_type": promotion_type_from_payload(payload),
        "price_source": price_source,
        "site_no": first_text(payload, "siteNo"),
        "salestr_no": first_text(payload, "salestrNo"),
        "shipping_type": first_text(payload, "shppTypeCd", "shppTypeDtlCd"),
        "shipping_detail_type": first_text(payload, "shppTypeDtlCd"),
        "shipping_type_code": first_text(payload, "shppTypeCd"),
        "shipping_type_detail_code": first_text(payload, "shppTypeDtlCd"),
        "deal_item_yn": first_text(payload, "dealItemYn"),
        "product_url": item_url_from_payload(payload),
        "item_url": item_url_from_payload(payload),
        "raw_cart_json": raw_json,
    }


def extract_next_data_payload(html: str) -> dict[str, Any] | None:
    match = NEXT_DATA_RE.search(html)
    if not match:
        return None
    payload_text = html_lib.unescape(match.group("payload")).strip()
    if not payload_text:
        return None
    payload = json.loads(payload_text)
    return payload if isinstance(payload, dict) else None


def iter_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for child in value.values():
            found.extend(iter_dicts(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(iter_dicts(child))
    return found


def parse_next_data_rows(
    *,
    html: str,
    collected_at: str,
    product_key: str,
    product_name: str,
    keyword: str,
    page: int,
    source_url: str,
) -> list[dict[str, str]]:
    try:
        payload = extract_next_data_payload(html)
    except json.JSONDecodeError:
        logger.debug("Skipping invalid Emart Mall __NEXT_DATA__ JSON.")
        return []
    if payload is None:
        return []

    rows: list[dict[str, str]] = []
    seen_item_ids: set[str] = set()
    for item in iter_dicts(payload):
        item_id = first_text(item, "itemId")
        item_name = first_text(item, "itemName", "itemNm")
        item_url = item_url_from_payload(item)
        if not item_id or not item_name or not item_url:
            continue
        if item_id in seen_item_ids:
            continue
        display_price, _ = display_price_from_payload(item)
        if not display_price:
            continue
        seen_item_ids.add(item_id)
        rows.append(
            normalize_payload(
                payload=item,
                raw_json=json.dumps(item, ensure_ascii=False, separators=(",", ":")),
                collected_at=collected_at,
                product_key=product_key,
                product_name=product_name,
                keyword=keyword,
                page=page,
                source_url=source_url,
            )
        )
    return rows


def parse_item_rows(
    *,
    html: str,
    collected_at: str,
    product_key: str,
    product_name: str,
    keyword: str,
    page: int,
    source_url: str,
) -> list[dict[str, str]]:
    rows = parse_next_data_rows(
        html=html,
        collected_at=collected_at,
        product_key=product_key,
        product_name=product_name,
        keyword=keyword,
        page=page,
        source_url=source_url,
    )
    for raw_json in iter_cart_json_strings(html):
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            logger.debug("Skipping invalid Emart Mall cart JSON: %s", raw_json[:120])
            continue
        if not isinstance(payload, dict):
            continue
        if not payload.get("itemId") or not first_text(payload, "itemNm", "itemName"):
            continue
        rows.append(
            normalize_payload(
                payload=payload,
                raw_json=raw_json,
                collected_at=collected_at,
                product_key=product_key,
                product_name=product_name,
                keyword=keyword,
                page=page,
                source_url=source_url,
            )
        )
    return rows


def deduplicate_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        identity = row["item_id"] or row["product_url"] or row["item_url"]
        if not identity:
            identity = json.dumps(row, ensure_ascii=False, sort_keys=True)
        key = (row["product_key"], row["keyword"], identity)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def filter_product_rows(product: ProductRequest, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    pattern = IRRELEVANT_PRODUCT_PATTERNS.get(product.product_key)
    if pattern is None:
        return rows
    filtered: list[dict[str, str]] = []
    for row in rows:
        searchable = " ".join((row["brand_name"], row["item_name"]))
        if pattern.search(searchable):
            continue
        filtered.append(row)
    return filtered


def deduplicate_across_products(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        identity = row["item_id"] or row["product_url"] or row["item_url"]
        if not identity:
            identity = json.dumps(row, ensure_ascii=False, sort_keys=True)
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(row)
    return deduped


def output_path(output_dir: Path, product_key: str, collected_at: str) -> Path:
    collect_date = collected_at.split("T", 1)[0]
    return output_dir / f"emart_{product_key}_{collect_date}.csv"


def combined_output_path(output_dir: Path, collected_at: str) -> Path:
    collect_date = collected_at.split("T", 1)[0]
    return output_dir / f"emart_all_{collect_date}.csv"


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=CSV_ENCODING, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def collect_product(
    *,
    session: requests.Session,
    product: ProductRequest,
    collected_at: str,
    output_dir: Path,
    max_pages: int,
    throttler: RequestThrottler,
    max_retries: int,
    backoff_seconds: float,
    write_output: bool = True,
) -> dict[str, Any]:
    all_rows: list[dict[str, str]] = []
    parsed_row_count = 0
    page_stats: list[dict[str, Any]] = []
    visited_urls: set[str] = set()
    termination_reason = "max_pages"

    page = 1
    while max_pages == 0 or page <= max_pages:
        url = build_search_url(product.search_keyword, page)
        if url in visited_urls:
            termination_reason = "repeated_page"
            break
        visited_urls.add(url)
        try:
            html = fetch_page(
                session,
                url,
                throttler=throttler,
                max_retries=max_retries,
                backoff_seconds=backoff_seconds,
            )
        except Exception as exc:
            termination_reason = "load_failed"
            page_stats.append({"page": page, "status": None, "url": url, "rows": 0})
            logger.warning(
                "Emart Mall page load failed: product=%s page=%s error=%s",
                product.product_key,
                page,
                exc,
            )
            break
        rows = parse_item_rows(
            html=html,
            collected_at=collected_at,
            product_key=product.product_key,
            product_name=product.product_name,
            keyword=product.search_keyword,
            page=page,
            source_url=url,
        )
        accepted_rows = filter_product_rows(product, rows)
        parsed_row_count += len(rows)
        all_rows.extend(accepted_rows)
        page_stats.append({"page": page, "status": 200, "url": url, "rows": len(accepted_rows)})
        logger.info(
            "Emart Mall page collected: product=%s page=%s rows=%s",
            product.product_key,
            page,
            len(accepted_rows),
        )
        if not rows:
            termination_reason = "last_page"
            break
        page += 1

    rows = deduplicate_rows(all_rows)
    path = output_path(output_dir, product.product_key, collected_at)
    if write_output:
        write_rows(path, rows)
    failure_count = int(termination_reason == "load_failed")
    logger.info(
        "Emart Mall product summary: product=%s collected=%s failures=%s",
        product.product_key,
        len(rows),
        failure_count,
    )
    return {
        "product_key": product.product_key,
        "product_name": product.product_name,
        "keyword": product.search_keyword,
        "source": SOURCE_NAME,
        "channel": CHANNEL_NAME,
        "collected_at": collected_at,
        "max_pages": max_pages,
        "request_interval_seconds": throttler.interval_seconds,
        "max_retries": max_retries,
        "backoff_seconds": backoff_seconds,
        "page_stats": page_stats,
        "visited_page_count": len(page_stats),
        "termination_reason": termination_reason,
        "raw_row_count": len(all_rows),
        "parsed_row_count": parsed_row_count,
        "filtered_out_count": parsed_row_count - len(all_rows),
        "row_count": len(rows),
        "duplicate_removed_count": len(all_rows) - len(rows),
        "output": str(path.relative_to(ROOT)),
        "failure_count": failure_count,
        "_rows": rows,
    }


def fetch_page_with_chrome(url: str) -> tuple[str, int | None, str]:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required for --browser chrome.") from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=False)
        context = browser.new_context(locale="ko-KR")
        page = context.new_page()
        response = page.goto(url, wait_until="domcontentloaded", timeout=PLAYWRIGHT_TIMEOUT_MS)
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except PlaywrightTimeoutError:
            pass
        page.mouse.wheel(0, 1200)
        page.wait_for_timeout(5000)
        html = page.content()
        final_url = page.url
        status = response.status if response else None
        browser.close()
    return html, status, final_url


def fetch_search_page_with_chrome(page: Any, url: str) -> tuple[str, int | None, str]:
    """Load one search-result page only; product URLs are never visited."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    response = page.goto(url, wait_until="domcontentloaded", timeout=PLAYWRIGHT_TIMEOUT_MS)
    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except PlaywrightTimeoutError:
        pass
    page.mouse.wheel(0, 1200)
    page.wait_for_timeout(5000)
    return page.content(), response.status if response else None, page.url


def click_search_page_with_chrome(page: Any, page_number: int) -> tuple[str, int | None, str, bool]:
    """Move to another rendered search-result page by clicking pagination."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    before_url = page.url
    selectors = [
        f'a[href*="page={page_number}"]',
        f'a:has-text("{page_number}")',
        f'button:has-text("{page_number}")',
    ]
    clicked = False
    for selector in selectors:
        locator = page.locator(selector).last
        if locator.count() == 0:
            continue
        try:
            locator.click(timeout=15000)
            clicked = True
            break
        except Exception:
            continue
    if not clicked:
        return page.content(), None, page.url, False
    try:
        page.wait_for_url(lambda url: url != before_url, timeout=30000)
    except PlaywrightTimeoutError:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except PlaywrightTimeoutError:
        pass
    page.mouse.wheel(0, 1200)
    page.wait_for_timeout(5000)
    return page.content(), None, page.url, True


def collect_product_with_chrome(
    *,
    product: ProductRequest,
    collected_at: str,
    output_dir: Path,
    max_pages: int,
    request_interval_seconds: float,
    write_output: bool = True,
) -> dict[str, Any]:
    all_rows: list[dict[str, str]] = []
    parsed_row_count = 0
    statuses: list[int | None] = []

    page_stats: list[dict[str, Any]] = []
    visited_urls: set[str] = set()
    termination_reason = "max_pages"
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required for --browser chrome.") from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=False)
        context = browser.new_context(locale="ko-KR")
        browser_page = context.new_page()
        try:
            page_number = 1
            while max_pages == 0 or page_number <= max_pages:
                url = build_search_url(product.search_keyword, page_number)
                if url in visited_urls:
                    termination_reason = "repeated_page"
                    logger.warning("Emart Mall pagination stopped: repeated requested URL=%s", url)
                    break
                visited_urls.add(url)
                try:
                    if page_number > 1 and request_interval_seconds > 0:
                        time.sleep(request_interval_seconds)
                    html, status, final_url = fetch_search_page_with_chrome(browser_page, url)
                except Exception as exc:
                    termination_reason = "load_failed"
                    page_stats.append(
                        {"page": page_number, "status": None, "url": url, "rows": 0}
                    )
                    logger.warning(
                        "Emart Mall search page load failed: product=%s page=%s error=%s",
                        product.product_key,
                        page_number,
                        exc,
                    )
                    break
                statuses.append(status)
                if final_url in visited_urls and final_url != url:
                    termination_reason = "repeated_page"
                    logger.warning("Emart Mall pagination stopped: repeated final URL=%s", final_url)
                    break
                visited_urls.add(final_url)
                if status is not None and status >= 400:
                    termination_reason = "load_failed"
                    page_stats.append(
                        {"page": page_number, "status": status, "url": final_url, "rows": 0}
                    )
                    logger.warning(
                        "Emart Mall search page returned error: product=%s page=%s status=%s",
                        product.product_key,
                        page_number,
                        status,
                    )
                    break
                rows = parse_item_rows(
                    html=html,
                    collected_at=collected_at,
                    product_key=product.product_key,
                    product_name=product.product_name,
                    keyword=product.search_keyword,
                    page=page_number,
                    source_url=final_url,
                )
                accepted_rows = filter_product_rows(product, rows)
                parsed_row_count += len(rows)
                all_rows.extend(accepted_rows)
                page_stats.append(
                    {
                        "page": page_number,
                        "status": status,
                        "url": final_url,
                        "rows": len(accepted_rows),
                    }
                )
                logger.info(
                    "Emart Mall Chrome page collected: product=%s page=%s status=%s rows=%s cumulative=%s",
                    product.product_key,
                    page_number,
                    status,
                    len(accepted_rows),
                    len(all_rows),
                )
                if not rows:
                    termination_reason = "last_page"
                    break
                page_number += 1
        finally:
            browser.close()

    rows = deduplicate_rows(all_rows)
    path = output_path(output_dir, product.product_key, collected_at)
    if write_output:
        write_rows(path, rows)
    failure_count = int(termination_reason == "load_failed")
    logger.info(
        "Emart Mall product summary: product=%s collected=%s failures=%s",
        product.product_key,
        len(rows),
        failure_count,
    )
    return {
        "product_key": product.product_key,
        "product_name": product.product_name,
        "keyword": product.search_keyword,
        "source": SOURCE_NAME,
        "channel": CHANNEL_NAME,
        "browser": "chrome",
        "collected_at": collected_at,
        "max_pages": max_pages,
        "request_interval_seconds": request_interval_seconds,
        "response_statuses": statuses,
        "page_stats": page_stats,
        "visited_page_count": len(page_stats),
        "termination_reason": termination_reason,
        "raw_row_count": len(all_rows),
        "parsed_row_count": parsed_row_count,
        "filtered_out_count": parsed_row_count - len(all_rows),
        "row_count": len(rows),
        "duplicate_removed_count": len(all_rows) - len(rows),
        "output": str(path.relative_to(ROOT)),
        "failure_count": failure_count,
        "_rows": rows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect raw Emart Mall online product prices from SSG HTML pages."
    )
    parser.add_argument(
        "--product",
        nargs="+",
        choices=sorted(PRODUCTS),
        default=sorted(PRODUCTS),
        help="Product keys to collect. Defaults to all registered products.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=DEFAULT_MAX_PAGES,
        help="Maximum pages per product. Defaults to 0, which means continue until the last page.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for raw Emart Mall CSV files. Defaults to data/raw/emart.",
    )
    parser.add_argument(
        "--request-interval-seconds",
        type=float,
        default=DEFAULT_REQUEST_INTERVAL_SECONDS,
        help="Minimum interval between HTTP requests. Defaults to 1.0.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help="Maximum retries for HTTP 429 responses. Defaults to 3.",
    )
    parser.add_argument(
        "--backoff-seconds",
        type=float,
        default=DEFAULT_BACKOFF_SECONDS,
        help="Initial exponential backoff seconds for HTTP 429 responses. Defaults to 5.0.",
    )
    parser.add_argument(
        "--browser",
        choices=["requests", "chrome"],
        default="requests",
        help=(
            "Fetch mode. 'chrome' uses local Google Chrome via Playwright without a persisted profile. "
            "Defaults to requests."
        ),
    )
    args = parser.parse_args()
    if args.max_pages < 0:
        parser.error("--max-pages must be greater than or equal to zero.")
    if args.request_interval_seconds < 0:
        parser.error("--request-interval-seconds must be greater than or equal to zero.")
    if args.max_retries < 0:
        parser.error("--max-retries must be greater than or equal to zero.")
    if args.backoff_seconds < 0:
        parser.error("--backoff-seconds must be greater than or equal to zero.")
    return args


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()

    collected_at = datetime.now().replace(microsecond=0).isoformat()
    output_dir = resolve_path(args.output_dir)
    throttler = RequestThrottler(args.request_interval_seconds)
    summaries: list[dict[str, Any]] = []
    collected_rows: list[dict[str, str]] = []
    if args.browser == "chrome":
        for product_key in args.product:
            summary = collect_product_with_chrome(
                product=product_request(product_key),
                collected_at=collected_at,
                output_dir=output_dir,
                max_pages=args.max_pages,
                request_interval_seconds=args.request_interval_seconds,
                write_output=False,
            )
            collected_rows.extend(summary.pop("_rows"))
            summaries.append(summary)
    else:
        with requests.Session() as session:
            warm_up_session(session, throttler=throttler)
            for product_key in args.product:
                summary = collect_product(
                    session=session,
                    product=product_request(product_key),
                    collected_at=collected_at,
                    output_dir=output_dir,
                    max_pages=args.max_pages,
                    throttler=throttler,
                    max_retries=args.max_retries,
                    backoff_seconds=args.backoff_seconds,
                    write_output=False,
                )
                collected_rows.extend(summary.pop("_rows"))
                summaries.append(summary)

    combined_rows = deduplicate_across_products(collected_rows)
    combined_path = combined_output_path(output_dir, collected_at)
    duplicate_removed_count = len(collected_rows) - len(combined_rows)
    total_failure_count = sum(int(summary.get("failure_count", 0)) for summary in summaries)
    if combined_rows or total_failure_count == 0:
        write_rows(combined_path, combined_rows)
    else:
        logger.warning(
            "Emart Mall combined output skipped because every collected result was empty after failures: output=%s",
            combined_path,
        )
    combined_output = str(combined_path.relative_to(ROOT))
    for summary in summaries:
        summary["output"] = combined_output
    logger.info(
        "Emart Mall combined summary: products=%s collected=%s duplicate_removed=%s output=%s",
        len(summaries),
        len(combined_rows),
        duplicate_removed_count,
        combined_path,
    )

    print(
        json.dumps(
            {
                "source": SOURCE_NAME,
                "channel": CHANNEL_NAME,
                "output": combined_output,
                "row_count": len(combined_rows),
                "duplicate_removed_count": duplicate_removed_count,
                "output_written": bool(combined_rows or total_failure_count == 0),
                "summaries": summaries,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
