from __future__ import annotations

import html as html_lib
import json
import re
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


URL = "https://emart.ssg.com/search.ssg?query=%EC%9A%B0%EC%9C%A0"
NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(?P<payload>.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
KEY_RE = re.compile(r"page|pg|count|total|offset|size|srch|query|sort", re.IGNORECASE)


def walk(value: Any, path: str = "") -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if KEY_RE.search(str(key)):
                hits.append(
                    {
                        "path": child_path,
                        "value": child if isinstance(child, (str, int, float, bool)) or child is None else type(child).__name__,
                    }
                )
            hits.extend(walk(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value[:5]):
            hits.extend(walk(child, f"{path}[{index}]"))
    return hits


def main() -> int:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=False)
        page = browser.new_page(locale="ko-KR")
        response = page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except PlaywrightTimeoutError:
            pass
        content = page.content()
        browser.close()

    match = NEXT_DATA_RE.search(content)
    payload = json.loads(html_lib.unescape(match.group("payload"))) if match else {}
    print(
        json.dumps(
            {
                "status": response.status if response else None,
                "url": URL,
                "hits": walk(payload)[:300],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
