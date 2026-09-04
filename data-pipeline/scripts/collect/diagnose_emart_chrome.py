from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


SCRIPT_PATH = Path(__file__).resolve().parent / "collect_emartmall.py"
SPEC = importlib.util.spec_from_file_location("collect_emartmall", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
collect = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = collect
SPEC.loader.exec_module(collect)


QUERY = "우유"
URL = collect.build_search_url(QUERY, 1)
IMPORTANT_HEADERS = {
    "content-type",
    "server",
    "date",
    "cache-control",
    "expires",
    "pragma",
    "location",
    "x-cache",
    "x-frame-options",
    "x-content-type-options",
    "strict-transport-security",
}


def filtered_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in headers.items()
        if key.lower() in IMPORTANT_HEADERS
    }


def response_body_prefix(response: Any | None, page_content: str) -> str:
    if response is None:
        return page_content[:1000]
    try:
        return response.text()[:1000]
    except Exception:
        return page_content[:1000]


def product_rows_from_next_data(page: Any) -> list[dict[str, str]]:
    return page.evaluate(
        """() => {
          const scripts = Array.from(document.querySelectorAll('script'));
          const jsonTexts = scripts
            .map((script) => script.textContent || '')
            .filter((text) => text.trim().startsWith('{') || text.trim().startsWith('['));
          const products = [];
          const seen = new Set();

          function valueOf(object, keys) {
            for (const key of keys) {
              if (object && object[key] !== undefined && object[key] !== null) {
                return String(object[key]);
              }
            }
            return '';
          }

          function visit(value) {
            if (!value || typeof value !== 'object') {
              return;
            }
            if (Array.isArray(value)) {
              for (const item of value) {
                visit(item);
              }
              return;
            }

            const itemId = valueOf(value, ['itemId', 'item_id']);
            const itemName = valueOf(value, ['itemNm', 'itemName', 'item_name']);
            const displayPrice = valueOf(value, ['displayPrc', 'displayPrice', 'sellPrc', 'price']);
            if (itemId && itemName && !seen.has(itemId)) {
              seen.add(itemId);
              products.push({
                item_id: itemId,
                item_name: itemName,
                display_price: displayPrice,
                brand_name: valueOf(value, ['brandNm', 'brandName', 'brand_name']),
                product_url: valueOf(value, ['itemLnkd', 'itemUrl', 'item_url', 'productUrl']),
              });
            }

            for (const child of Object.values(value)) {
              visit(child);
            }
          }

          for (const text of jsonTexts) {
            try {
              visit(JSON.parse(text));
            } catch (_error) {
            }
          }
          return products;
        }"""
    )


def product_rows_from_dom(page: Any) -> dict[str, Any]:
    return page.evaluate(
        """() => {
          const cardSelector = 'li';
          const linkSelector = 'a[href*="itemView.ssg"]';
          const nameSelector = [
            '[class*="item_tit"]',
            '[class*="itemName"]',
            '[class*="mnemitem_tit"]',
            '[class*="title"]',
            'a[href*="itemView.ssg"]'
          ].join(', ');
          const brandSelector = [
            '[class*="brand"]',
            '[class*="mnemitem_brand"]'
          ].join(', ');
          const priceSelectors = [
            '[class*="ssg_price"]',
            '[class*="price"] em',
            '[class*="price"]',
            'em',
            'span'
          ];

          function textOf(root, selector) {
            const element = root.querySelector(selector);
            return element ? element.textContent.replace(/\\s+/g, ' ').trim() : '';
          }

          function itemNameOf(root, link) {
            const imageAlt = link.querySelector('img[alt]')?.getAttribute('alt') || '';
            if (imageAlt) {
              return imageAlt.replace(/\\s+/g, ' ').trim();
            }

            const selectorText = textOf(root, nameSelector);
            if (selectorText) {
              return selectorText;
            }

            const candidates = Array.from(root.querySelectorAll(linkSelector))
              .flatMap((element) => [
                element.textContent,
                element.getAttribute('aria-label'),
                element.getAttribute('title')
              ])
              .map((value) => (value || '').replace(/\\s+/g, ' ').trim())
              .filter((value) => value && !/^(상품|상세|보기|담기|찜|장바구니)$/.test(value))
              .filter((value) => !/\\d[\\d,]*\\s*원/.test(value));
            if (candidates.length) {
              return candidates.sort((left, right) => right.length - left.length)[0];
            }

            const lines = root.textContent
              .split(/\\n|\\r/)
              .map((value) => value.replace(/\\s+/g, ' ').trim())
              .filter(Boolean)
              .filter((value) => !/판매가격|쿠폰|배송|장바구니|^\\d[\\d,]*\\s*원$/.test(value));
            return lines.sort((left, right) => right.length - left.length)[0] || '';
          }

          function brandNameOf(root, link) {
            const selectorText = textOf(root, brandSelector);
            if (selectorText) {
              return selectorText;
            }
            const imageAlt = link.querySelector('img[alt]')?.getAttribute('alt') || '';
            const normalized = imageAlt.replace(/\\s+/g, ' ').trim();
            const firstToken = normalized.split(' ')[0] || '';
            return firstToken && !firstToken.startsWith('[') ? firstToken : '';
          }

          function hrefOf(root) {
            const link = root.querySelector(linkSelector);
            if (!link) {
              return '';
            }
            return new URL(link.getAttribute('href'), location.href).href;
          }

          function itemIdFromUrl(url) {
            try {
              return new URL(url).searchParams.get('itemId') || '';
            } catch (_error) {
              return '';
            }
          }

          function priceOf(root) {
            for (const selector of priceSelectors) {
              const elements = Array.from(root.querySelectorAll(selector));
              for (const element of elements) {
                const text = element.textContent.replace(/\\s+/g, ' ').trim();
                if (/\\d[\\d,]*\\s*원/.test(text) || /^\\d[\\d,]*$/.test(text)) {
                  return { text, selector };
                }
              }
            }

            const textMatch = root.textContent.replace(/\\s+/g, ' ').match(/\\d[\\d,]*\\s*원/);
            return { text: textMatch ? textMatch[0] : '', selector: 'card.textContent regex /\\\\d[\\\\d,]*\\\\s*원/' };
          }

          const rows = [];
          const seen = new Set();
          for (const link of Array.from(document.querySelectorAll(linkSelector))) {
            let card = link.closest(cardSelector);
            let cursor = link.parentElement;
            while (!card && cursor && cursor !== document.body) {
              const text = cursor.textContent.replace(/\\s+/g, ' ').trim();
              if (text.length > 20 || /\\d[\\d,]*\\s*원/.test(text)) {
                card = cursor;
                break;
              }
              cursor = cursor.parentElement;
            }
            if (!card) {
              continue;
            }
            const productUrl = hrefOf(card);
            const itemId = itemIdFromUrl(productUrl);
            const key = itemId || productUrl;
            if (!key || seen.has(key)) {
              continue;
            }
            seen.add(key);
            const price = priceOf(card);
            rows.push({
              item_id: itemId,
              item_name: itemNameOf(card, link),
              display_price: price.text,
              brand_name: brandNameOf(card, link),
              product_url: productUrl,
              selectors: {
                card: cardSelector,
                link: linkSelector,
                item_name: 'a[href*="itemView.ssg"] img[alt]',
                brand_name: '[class*="brand"], [class*="mnemitem_brand"], fallback first token of img[alt]',
                display_price: price.selector
              }
            });
            if (rows.length >= 3) {
              break;
            }
          }

          return {
            product_count: new Set(
              Array.from(document.querySelectorAll(linkSelector))
                .map((link) => itemIdFromUrl(new URL(link.getAttribute('href'), location.href).href))
                .filter(Boolean)
            ).size,
            first3: rows
          };
        }"""
    )


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=False)
        page = browser.new_page(locale="ko-KR")
        try:
            response = page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_load_state("networkidle", timeout=30000)
            except PlaywrightTimeoutError:
                pass
            page.mouse.wheel(0, 1200)
            page.wait_for_timeout(5000)
            content = page.content()
            dom_result = product_rows_from_dom(page)
            product_rows = dom_result["first3"]
            if not product_rows:
                product_rows = product_rows_from_next_data(page)[:3]
            status = response.status if response else None

            if response is not None and response.ok and product_rows:
                result = {
                    "status": status,
                    "product_count": dom_result["product_count"],
                    "first3": product_rows,
                }
            else:
                result = {
                    "status": status,
                    "final_url": page.url,
                    "response_headers": filtered_headers(response.headers if response else {}),
                    "response_body_prefix": response_body_prefix(response, content),
                    "page_title": page.title(),
                    "product_count": len(product_rows),
                }
        except PlaywrightTimeoutError as exc:
            result = {
                "status": None,
                "final_url": page.url,
                "response_headers": {},
                "response_body_prefix": page.content()[:1000],
                "page_title": page.title(),
                "error": f"timeout: {exc}",
            }
        finally:
            browser.close()

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
