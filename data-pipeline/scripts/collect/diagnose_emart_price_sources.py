from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


QUERY = "우유"
SEARCH_URL = f"https://emart.ssg.com/search.ssg?query={QUERY}"
OUTPUT_PATH = Path("data-pipeline/reports/emart_price_sources.json")


def parse_price(text: str) -> int | None:
    match = re.search(r"(\d[\d,]*)\s*원", text.replace("\xa0", " "))
    if not match:
        match = re.search(r"^\s*(\d[\d,]*)\s*$", text.replace(",", ""))
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def product_rows_from_dom(page: Any) -> dict[str, Any]:
    return page.evaluate(
        """() => {
          const linkSelector = 'a[href*="itemView.ssg"]';
          const rows = [];
          const seen = new Set();

          function normalizedText(element) {
            return (element?.textContent || '').replace(/\\s+/g, ' ').trim();
          }

          function itemIdFromUrl(url) {
            try {
              return new URL(url).searchParams.get('itemId') || '';
            } catch (_error) {
              return '';
            }
          }

          function bestCard(link) {
            let cursor = link.closest('li');
            while (cursor && cursor !== document.body) {
              const text = normalizedText(cursor);
              if (/\\d[\\d,]*\\s*원/.test(text) && cursor.querySelector(linkSelector)) {
                return cursor;
              }
              cursor = cursor.parentElement;
            }
            return link.closest('li') || link.parentElement;
          }

          function priceCandidates(card) {
            const selectors = [
              '[class*="ssg_price"]',
              '[class*="price"]',
              '[class*="prc"]',
              '[class*="benefit"]',
              'em',
              'span'
            ];
            const candidates = [];
            const seenText = new Set();
            for (const selector of selectors) {
              for (const element of Array.from(card.querySelectorAll(selector))) {
                const text = normalizedText(element);
                if (!text || !/\\d[\\d,]*\\s*원|^\\d[\\d,]*$/.test(text)) {
                  continue;
                }
                const key = selector + '|' + text;
                if (seenText.has(key)) {
                  continue;
                }
                seenText.add(key);
                candidates.push({
                  selector,
                  text,
                  class_name: element.className ? String(element.className) : '',
                  aria_label: element.getAttribute('aria-label') || '',
                  parent_class_name: element.parentElement?.className ? String(element.parentElement.className) : '',
                  parent_text: normalizedText(element.parentElement).slice(0, 160)
                });
              }
            }
            return candidates;
          }

          for (const link of Array.from(document.querySelectorAll(linkSelector))) {
            const productUrl = new URL(link.getAttribute('href'), location.href).href;
            const itemId = itemIdFromUrl(productUrl);
            const key = itemId || productUrl;
            if (!key || seen.has(key)) {
              continue;
            }
            seen.add(key);
            const card = bestCard(link);
            const imageAlt = link.querySelector('img[alt]')?.getAttribute('alt') || '';
            const candidates = priceCandidates(card);
            rows.push({
              product_name: imageAlt.replace(/\\s+/g, ' ').trim() || normalizedText(link),
              item_id: itemId,
              product_url: productUrl,
              card_class_name: card?.className ? String(card.className) : '',
              card_text: normalizedText(card).slice(0, 600),
              price_candidates: candidates
            });
            if (rows.length >= 3) {
              break;
            }
          }

          return {
            final_url: location.href,
            content_has_displayPrc: document.documentElement.outerHTML.includes('displayPrc'),
            content_has_itemId: document.documentElement.outerHTML.includes('itemId'),
            product_count: new Set(
              Array.from(document.querySelectorAll(linkSelector))
                .map((link) => itemIdFromUrl(new URL(link.getAttribute('href'), location.href).href))
                .filter(Boolean)
            ).size,
            first3: rows
          };
        }"""
    )


def attribute_sources(page: Any) -> list[dict[str, Any]]:
    return page.evaluate(
        """() => {
          const rows = [];
          for (const element of Array.from(document.querySelectorAll('*'))) {
            for (const attribute of Array.from(element.attributes || [])) {
              if (attribute.value.includes('displayPrc') || attribute.value.includes('itemId')) {
                rows.push({
                  tag: element.tagName.toLowerCase(),
                  class_name: element.className ? String(element.className) : '',
                  attribute_name: attribute.name,
                  attribute_value: attribute.value.slice(0, 1000)
                });
              }
            }
          }
          return rows.slice(0, 20);
        }"""
    )


def script_price_sources(page: Any, item_ids: list[str]) -> list[dict[str, Any]]:
    return page.evaluate(
        """(itemIds) => {
          const result = [];
          for (const [index, script] of Array.from(document.querySelectorAll('script')).entries()) {
            const text = script.textContent || '';
            if (!text) {
              continue;
            }
            for (const itemId of itemIds) {
              const pos = text.indexOf(itemId);
              if (pos === -1) {
                continue;
              }
              const start = Math.max(0, pos - 500);
              const end = Math.min(text.length, pos + 1000);
              result.push({
                script_index: index,
                item_id: itemId,
                type: script.getAttribute('type') || '',
                id: script.id || '',
                snippet: text.slice(start, end).replace(/\\s+/g, ' ').trim()
              });
            }
          }
          return result.slice(0, 10);
        }""",
        item_ids,
    )


def rows_from_next_data(page: Any) -> list[dict[str, Any]]:
    return page.evaluate(
        """() => {
          const script = document.querySelector('script#__NEXT_DATA__');
          if (!script) {
            return [];
          }
          const root = JSON.parse(script.textContent || '{}');
          const rows = [];
          const seen = new Set();

          function valueOf(object, keys) {
            for (const key of keys) {
              if (object && object[key] !== undefined && object[key] !== null && String(object[key]).trim()) {
                return String(object[key]).trim();
              }
            }
            return null;
          }

          function visit(value) {
            if (!value || typeof value !== 'object') {
              return;
            }
            if (Array.isArray(value)) {
              for (const child of value) visit(child);
              return;
            }
            const itemId = valueOf(value, ['itemId']);
            const itemName = valueOf(value, ['itemName', 'itemNm']);
            const itemUrl = valueOf(value, ['itemUrl', 'itemLnkd']);
            const priceInfo = value.priceInfo && typeof value.priceInfo === 'object' ? value.priceInfo : {};
            const displayPrice = valueOf(priceInfo, ['rawPrimaryPrice', 'primaryPrice']) || valueOf(value, ['displayPrc']);
            if (itemId && itemName && itemUrl && displayPrice && !seen.has(itemId)) {
              seen.add(itemId);
              rows.push({
                product_name: [valueOf(value, ['brandName', 'brandNm']), itemName].filter(Boolean).join(' '),
                item_id: itemId,
                product_url: itemUrl,
                display_price: displayPrice,
                original_price: valueOf(priceInfo, ['strikeOutPrice']),
                sale_price: displayPrice,
                unit_price: valueOf(priceInfo, ['unitPriceDescription']),
                promotion_type: [valueOf(priceInfo, ['discountRate']), valueOf(priceInfo, ['couponText', 'shortCouponText'])].filter(Boolean).join(' / ') || null,
                shipping_type: valueOf(value, ['shppTypeCd', 'shppTypeDtlCd'])
              });
            }
            for (const child of Object.values(value)) visit(child);
          }

          visit(root);
          return rows.slice(0, 5);
        }"""
    )


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    captured_responses: list[dict[str, Any]] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=False)
        context = browser.new_context(locale="ko-KR")
        page = context.new_page()

        def on_response(response: Any) -> None:
            resource_type = response.request.resource_type
            if resource_type not in {"xhr", "fetch", "document"}:
                return
            entry: dict[str, Any] = {
                "url": response.url,
                "status": response.status,
                "resource_type": resource_type,
                "content_type": response.headers.get("content-type", ""),
            }
            if resource_type in {"xhr", "fetch"}:
                try:
                    text = response.text()
                except Exception:
                    text = ""
                entry["contains"] = {
                    "itemId": "itemId" in text,
                    "itemNm": "itemNm" in text,
                    "itemName": "itemName" in text,
                    "displayPrc": "displayPrc" in text,
                    "priceInfo": "priceInfo" in text,
                }
                if any(entry["contains"].values()) or "itemView.ssg" in text or "sellPrc" in text:
                    entry["body_prefix"] = text[:1200]
            captured_responses.append(entry)

        page.on("response", on_response)
        response = page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except PlaywrightTimeoutError:
            pass
        page.mouse.wheel(0, 1200)
        page.wait_for_timeout(5000)

        dom = product_rows_from_dom(page)
        item_ids = [row["item_id"] for row in dom["first3"] if row.get("item_id")]
        scripts = script_price_sources(page, item_ids)

        browser_name = browser.browser_type.name
        result = {
            "execution_environment": {
                "playwright": "python",
                "browser_type": browser_name,
                "channel": "chrome",
                "headless": False,
                "profile": "ephemeral new_context",
                "logged_in": False,
                "captcha_or_stealth": False,
                "user_agent": page.evaluate("() => navigator.userAgent"),
                "user_agent_data": page.evaluate(
                    "() => navigator.userAgentData ? {brands: navigator.userAgentData.brands, mobile: navigator.userAgentData.mobile, platform: navigator.userAgentData.platform} : null"
                ),
            },
            "search": {
                "target_url": SEARCH_URL,
                "final_url": dom["final_url"],
                "response_status": response.status if response else None,
                "product_count": dom["product_count"],
                "content_has_displayPrc": dom["content_has_displayPrc"],
                "content_has_itemId": dom["content_has_itemId"],
                "first3_dom": dom["first3"],
                "first5_next_data": rows_from_next_data(page),
            },
            "attribute_sources": attribute_sources(page),
            "script_sources": scripts,
            "network_sources": captured_responses[:80],
        }

        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
