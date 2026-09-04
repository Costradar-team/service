from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


HOME_URL = "https://lottemartzetta.com/"
QUERY = "우유"
REPORT_PATH = Path("data-pipeline/reports/lottemart_zetta_milk.json")


def normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def inspect_page(page: Any) -> dict[str, Any]:
    return page.evaluate(
        """() => {
          const text = (node) => (node?.textContent || '').replace(/\\s+/g, ' ').trim();
          const inputs = [...document.querySelectorAll('input')].map((node) => ({
            type: node.type, name: node.name, placeholder: node.placeholder, value: node.value
          }));
          const links = [...document.querySelectorAll('a')].map((node) => ({
            text: text(node), href: node.href
          })).filter((x) => x.text || x.href).slice(0, 100);
          return {
            url: location.href,
            title: document.title,
            body_text: text(document.body).slice(0, 3000),
            inputs,
            links,
            scripts: [...document.scripts].map((node) => ({
              id: node.id, type: node.type, text: (node.textContent || '').slice(0, 500)
            })).slice(0, 30)
          };
        }"""
    )


def inspect_products(page: Any) -> dict[str, Any]:
    return page.evaluate(
        """() => {
          const text = (node) => (node?.textContent || '').replace(/\\s+/g, ' ').trim();
          const candidates = [...document.querySelectorAll('a, article, li, [class*=product], [class*=Product]')];
          const rows = [];
          const seen = new Set();
          for (const node of candidates) {
            const href = node.tagName === 'A' ? node.href : node.querySelector('a')?.href || '';
            const value = text(node);
            if (!href || !value || !/\\d[\\d,]*\\s*원/.test(value)) continue;
            const key = href + '|' + value.slice(0, 100);
            if (seen.has(key)) continue;
            seen.add(key);
            rows.push({href, text: value.slice(0, 500), html: node.outerHTML.slice(0, 1200)});
            if (rows.length >= 10) break;
          }
          const html = document.documentElement.outerHTML;
          return {
            url: location.href,
            body_text: text(document.body).slice(0, 5000),
            product_count_candidates: rows.length,
            rows,
            has_product_json_keys: {
              productId: html.includes('productId'),
              productName: html.includes('productName'),
              salePrice: html.includes('salePrice'),
              sellingPrice: html.includes('sellingPrice'),
              storeId: html.includes('storeId')
            },
            scripts: [...document.scripts].map((node, index) => ({
              index, id: node.id, type: node.type, text: node.textContent || ''
            })).filter((x) => /productId|productName|salePrice|sellingPrice|storeId/i.test(x.text)).map((x) => ({
              index: x.index, id: x.id, type: x.type, text: x.text.slice(0, 2000)
            })).slice(0, 20)
          };
        }"""
    )


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    report: dict[str, Any] = {
        "execution_environment": {
            "playwright": "python",
            "channel": "chrome",
            "headless": False,
            "profile": "ephemeral new_context",
            "logged_in": False,
            "captcha_or_stealth": False,
        }
    }
    responses: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=False)
        context = browser.new_context(locale="ko-KR")
        page = context.new_page()

        def on_response(response: Any) -> None:
            if response.request.resource_type not in {"document", "xhr", "fetch"}:
                return
            entry = {
                "url": response.url,
                "status": response.status,
                "resource_type": response.request.resource_type,
                "content_type": response.headers.get("content-type", ""),
            }
            if entry["resource_type"] in {"xhr", "fetch"}:
                try:
                    body = response.text()
                except Exception:
                    body = ""
                entry["contains"] = {
                    key: key in body
                    for key in ["productId", "productName", "salePrice", "sellingPrice", "storeId"]
                }
                if any(entry["contains"].values()):
                    entry["body_prefix"] = body[:2000]
            responses.append(entry)

        page.on("response", on_response)
        response = page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(3000)
        report["home"] = {
            "response_status": response.status if response else None,
            **inspect_page(page),
        }

        search_input = page.locator('input[placeholder*="상품"], input[type="search"], input').first
        if search_input.count() == 0:
            report["search"] = {"error": "search input not found", "responses": responses}
        else:
            search_input.fill(QUERY)
            search_input.press("Enter")
            try:
                page.wait_for_load_state("networkidle", timeout=30000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(5000)
            report["search"] = {
                "query": QUERY,
                "response_status": response.status if response else None,
                **inspect_products(page),
                "responses": responses,
            }
        browser.close()

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
