from __future__ import annotations

import json
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


BASE_URL = "https://emart.ssg.com/search.ssg?query=%EC%9A%B0%EC%9C%A0"


def wait_search_page(page: Any) -> None:
    page.mouse.wheel(0, 1200)
    page.wait_for_timeout(2500)


def snapshot(page: Any) -> dict[str, Any]:
    html = page.content()
    return {
        "url": page.url,
        "title": page.title(),
        "item_view_link_count": html.count("itemView.ssg"),
        "cart_json_marker_count": html.count("장바구니 담기"),
        "forbidden_marker": "403 Forbidden" in html or "Access Denied" in html,
    }


def pagination_links(page: Any) -> list[dict[str, str]]:
    return page.evaluate(
        """() => [...document.querySelectorAll('a, button')]
          .map((element) => ({
            text: (element.innerText || element.textContent || element.getAttribute('aria-label') || '')
              .replace(/\\s+/g, ' ').trim(),
            href: element.href || '',
            onclick: element.getAttribute('onclick') || '',
            class_name: String(element.className || '')
          }))
          .filter((entry) => /(다음|next|2|3|page|페이지|›|>|&gt;)/i
            .test(`${entry.text} ${entry.href} ${entry.onclick} ${entry.class_name}`))
          .slice(0, 80)"""
    )


def direct_sequence(playwright: Any) -> list[dict[str, Any]]:
    browser = playwright.chromium.launch(channel="chrome", headless=False)
    context = browser.new_context(locale="ko-KR")
    page = context.new_page()
    results: list[dict[str, Any]] = []
    try:
        for page_number in [1, 2, 3]:
            url = BASE_URL if page_number == 1 else f"{BASE_URL}&page={page_number}"
            response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
            wait_search_page(page)
            results.append(
                {
                    "requested_page": page_number,
                    "status": response.status if response else None,
                    **snapshot(page),
                }
            )
            page.wait_for_timeout(1500)
    finally:
        browser.close()
    return results


def click_sequence(playwright: Any) -> list[dict[str, Any]]:
    browser = playwright.chromium.launch(channel="chrome", headless=False)
    context = browser.new_context(locale="ko-KR")
    page = context.new_page()
    results: list[dict[str, Any]] = []
    try:
        response = page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
        wait_search_page(page)
        results.append(
            {
                "step": "page1",
                "status": response.status if response else None,
                **snapshot(page),
                "pagination_links": pagination_links(page),
            }
        )
        for page_number in [2, 3]:
            click_error = ""
            click_ok = False
            candidates = [
                f'a[href*="page={page_number}"]',
                f'a:has-text("{page_number}")',
                f'button:has-text("{page_number}")',
            ]
            for selector in candidates:
                locator = page.locator(selector).last
                if locator.count() == 0:
                    continue
                try:
                    locator.click(timeout=15000)
                    click_ok = True
                    break
                except Exception as exc:
                    click_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            wait_search_page(page)
            results.append(
                {
                    "step": f"click_page_{page_number}",
                    "click_ok": click_ok,
                    "click_error": click_error,
                    **snapshot(page),
                    "pagination_links": pagination_links(page),
                }
            )
            page.wait_for_timeout(1500)
    finally:
        browser.close()
    return results


def main() -> int:
    with sync_playwright() as playwright:
        result = {
            "execution_environment": {
                "playwright": "python",
                "channel": "chrome",
                "headless": False,
                "profile": "ephemeral new_context",
                "logged_in": False,
            },
            "direct_url_pages": direct_sequence(playwright),
            "clicked_pages": click_sequence(playwright),
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
