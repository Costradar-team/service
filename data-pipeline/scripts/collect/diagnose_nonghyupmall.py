from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[2]
MAIN_URL = "https://www.nonghyupmall.com/BC31010R/main.nh?basketCnt=0&cdnAplYn=N"
QUERY = "우유"
REPORT_PATH = ROOT / "reports" / "nonghyupmall_diagnosis.json"


def search_url(query: str, page: int = 1) -> str:
    return "https://www.nonghyupmall.com/BC1F010M/srchTotalList.nh?" + urlencode(
        {
            "searchTerm_main": query,
            "searchTerm": query,
            "CHAN_C": "1101",
            "chanC": "1101",
            "page": str(page),
            "listDiv": "60",
        }
    )


def inspect_page(page: Any) -> dict[str, Any]:
    return page.evaluate(
        """() => {
          const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
          const items = [];
          const candidates = [...document.querySelectorAll('li, div, article, a')];
          const seen = new Set();
          for (const node of candidates) {
            const text = clean(node.innerText || node.textContent);
            const href = node.tagName === 'A' ? node.href : node.querySelector('a')?.href || '';
            if (!text || !/\\d[\\d,]*\\s*원/.test(text)) continue;
            const key = href + '|' + text.slice(0, 120);
            if (seen.has(key)) continue;
            seen.add(key);
            items.push({
              tag: node.tagName,
              className: node.className || '',
              href,
              text: text.slice(0, 700),
              html: node.outerHTML.slice(0, 1600)
            });
            if (items.length >= 30) break;
          }
          return {
            url: location.href,
            title: document.title,
            body_text: clean(document.body?.innerText || document.body?.textContent).slice(0, 5000),
            inputs: [...document.querySelectorAll('input')].map((node) => ({
              type: node.type,
              name: node.name,
              id: node.id,
              placeholder: node.placeholder,
              value: node.value,
              className: node.className || ''
            })).slice(0, 80),
            forms: [...document.querySelectorAll('form')].map((node) => ({
              id: node.id,
              name: node.getAttribute('name'),
              action: node.action,
              method: node.method
            })).slice(0, 30),
            links: [...document.querySelectorAll('a')].map((node) => ({
              text: clean(node.innerText || node.textContent).slice(0, 120),
              href: node.href
            })).filter((item) => item.text || item.href).slice(0, 160),
            priced_item_candidates: items,
            pagination_candidates: [...document.querySelectorAll('a, button')].map((node) => ({
              text: clean(node.innerText || node.textContent),
              href: node.href || '',
              onclick: node.getAttribute('onclick') || '',
              className: node.className || ''
            })).filter((item) => /다음|next|[2-9]/i.test(item.text + ' ' + item.href + ' ' + item.onclick)).slice(0, 80),
            pagination_html: [...document.querySelectorAll('[class*=page], [class*=Page], [class*=paging], [class*=Paging], [class*=pagination], [class*=Pagination]')]
              .map((node) => node.outerHTML.slice(0, 2500))
              .slice(0, 20),
            page_scripts: [...document.scripts]
              .map((node) => node.textContent || '')
              .filter((text) => /page|paging|goPage|movePage|fnSearch|SearchForm/.test(text))
              .map((text) => text.slice(0, 4000))
              .slice(0, 20)
          };
        }"""
    )


def try_search(page: Any) -> None:
    inputs = page.locator(
        'input:visible[name*="srch"], input:visible[name*="search"], input:visible[name*="keyword"], input:visible[name*="query"], input:visible[type="search"], input:visible[type="text"]'
    )
    for index in range(inputs.count()):
        search_input = inputs.nth(index)
        try:
            search_input.fill(QUERY, timeout=3000)
            search_input.press("Enter", timeout=3000)
            return
        except Exception:
            continue


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    responses: list[dict[str, Any]] = []
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
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=False)
        context = browser.new_context(locale="ko-KR")
        page = context.new_page()

        def on_response(response: Any) -> None:
            if response.request.resource_type not in {"document", "xhr", "fetch"}:
                return
            responses.append(
                {
                    "url": response.url,
                    "status": response.status,
                    "resource_type": response.request.resource_type,
                    "content_type": response.headers.get("content-type", ""),
                }
            )

        page.on("response", on_response)
        response = page.goto(MAIN_URL, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(3000)
        report["home"] = {"status": response.status if response else None, **inspect_page(page)}

        try_search(page)
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(5000)
        report["search"] = {**inspect_page(page)}

        next_link = page.locator('.pagination a[href*="pageNavigate(2)"]').first
        if next_link.count() > 0:
            try:
                next_link.click(timeout=5000)
                try:
                    page.wait_for_load_state("networkidle", timeout=30000)
                except PlaywrightTimeoutError:
                    pass
                page.wait_for_timeout(3000)
                report["next_page"] = {**inspect_page(page)}
            except Exception as exc:
                report["next_page"] = {"error": str(exc)}
        else:
            report["next_page"] = {"error": "next link not found"}

        direct_page_2_response = page.goto(search_url(QUERY, 2), wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(3000)
        report["direct_page_2"] = {
            "status": direct_page_2_response.status if direct_page_2_response else None,
            **inspect_page(page),
        }

        report["responses"] = responses[-120:]
        browser.close()

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
