from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "collect"
    / "collect_emartmall.py"
)
SPEC = importlib.util.spec_from_file_location("collect_emartmall", SCRIPT_PATH)
assert SPEC is not None
collect = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = collect
assert SPEC.loader is not None
SPEC.loader.exec_module(collect)


class FakeResponse:
    def __init__(self, status_code: int, text: str = "", headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.request_count = 0
        self.cookies: dict[str, str] = {}

    def get(self, *args: object, **kwargs: object) -> FakeResponse:
        response = self.responses[self.request_count]
        self.request_count += 1
        return response


class EmartMallCollectTests(unittest.TestCase):
    def emart_item_html(self, item_id: str, item_name: str = "우유 테스트 상품") -> str:
        return (
            '장바구니 담기 '
            f'{{"displayPrc":"1000","itemNm":"{item_name}","itemId":"{item_id}",'
            '"siteNo":"6001","salestrNo":"2037"}}'
        )

    def test_parse_item_rows_from_cart_json(self) -> None:
        html = '''
        <li>
          장바구니 담기 {"displayPrc":"10980","itemNm":"1등급 30구 (특란, 1800g)","siteNo":"6001","brandNm":"파머스픽","itemLnkd":"https://emart.ssg.com/item/itemView.ssg?itemId=1000220072503&siteNo=6001&salestrNo=2037","itemId":"1000220072503","uitemId":"00000","salestrNo":"2037","shppTypeCd":"10","shppTypeDtlCd":"11","dealItemYn":"N"}
          판매가격 10,980원
        </li>
        '''

        rows = collect.parse_item_rows(
            html=html,
            collected_at="2026-09-03T12:00:00",
            product_key="egg",
            product_name="계란",
            keyword="계란",
            page=1,
            source_url="https://emart.ssg.com/search.ssg?query=%EA%B3%84%EB%9E%80",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(list(rows[0].keys()), collect.OUTPUT_COLUMNS)
        self.assertEqual(rows[0]["source"], "EMART")
        self.assertEqual(rows[0]["channel"], "ONLINE")
        self.assertEqual(rows[0]["product_key"], "egg")
        self.assertEqual(rows[0]["product_name"], "계란")
        self.assertEqual(rows[0]["query"], "계란")
        self.assertEqual(rows[0]["keyword"], "계란")
        self.assertEqual(rows[0]["item_id"], "1000220072503")
        self.assertEqual(rows[0]["item_name"], "1등급 30구 (특란, 1800g)")
        self.assertEqual(rows[0]["brand_name"], "파머스픽")
        self.assertEqual(rows[0]["display_price"], "10980")
        self.assertEqual(rows[0]["shipping_type"], "10")
        self.assertEqual(rows[0]["shipping_detail_type"], "11")
        self.assertEqual(
            rows[0]["product_url"],
            "https://emart.ssg.com/item/itemView.ssg?itemId=1000220072503&siteNo=6001&salestrNo=2037",
        )

    def test_item_url_falls_back_from_codes(self) -> None:
        row = collect.normalize_payload(
            payload={
                "itemId": "100",
                "itemNm": "테스트 상품",
                "displayPrc": "3980",
                "siteNo": "7009",
                "salestrNo": "2551",
            },
            raw_json="{}",
            collected_at="2026-09-03T12:00:00",
            product_key="milk",
            product_name="우유",
            keyword="우유",
            page=1,
            source_url="https://emart.ssg.com/search.ssg?query=%EC%9A%B0%EC%9C%A0",
        )

        self.assertEqual(
            row["item_url"],
            "https://emart.ssg.com/item/itemView.ssg?itemId=100&siteNo=7009&salestrNo=2551",
        )

    def test_deduplicate_rows_keeps_keyword_specific_items(self) -> None:
        base = {column: "" for column in collect.OUTPUT_COLUMNS}
        row_a = {
            **base,
            "product_key": "egg",
            "keyword": "계란",
            "item_id": "1",
            "uitem_id": "00000",
            "salestr_no": "2551",
        }
        row_b = {**row_a}
        row_c = {**row_a, "keyword": "달걀"}

        rows = collect.deduplicate_rows([row_a, row_b, row_c])

        self.assertEqual(rows, [row_a, row_c])

    def test_filter_product_rows_excludes_obvious_irrelevant_products(self) -> None:
        product = collect.product_request("butter")
        base = {column: "" for column in collect.OUTPUT_COLUMNS}
        relevant = {**base, "item_id": "1", "item_name": "무염 버터 450g"}
        irrelevant = {**base, "item_id": "2", "item_name": "버터쿠키 200g"}

        self.assertEqual(
            collect.filter_product_rows(product, [relevant, irrelevant]),
            [relevant],
        )

    def test_each_registered_product_parses_at_least_one_row(self) -> None:
        for product_key, product_config in collect.PRODUCTS.items():
            keyword = product_config["search_keyword"]
            html = f'''장바구니 담기 {{"displayPrc":"1000","itemNm":"{keyword} 테스트 상품","itemId":"{product_key}-1","siteNo":"6001","salestrNo":"2037"}}'''
            rows = collect.parse_item_rows(
                html=html,
                collected_at="2026-09-03T12:00:00",
                product_key=product_key,
                product_name=product_config["product_name"],
                keyword=keyword,
                page=1,
                source_url=collect.build_search_url(keyword, 1),
            )

            self.assertGreaterEqual(len(rows), 1, product_key)
            self.assertEqual(rows[0]["display_price"], "1000")
            self.assertEqual(list(rows[0]), collect.OUTPUT_COLUMNS)

    def test_deduplicate_across_products_uses_item_id(self) -> None:
        base = {column: "" for column in collect.OUTPUT_COLUMNS}
        row_a = {**base, "product_key": "milk", "item_id": "1"}
        row_b = {**base, "product_key": "butter", "item_id": "1"}
        row_c = {**base, "product_key": "butter", "item_id": "2"}

        self.assertEqual(
            collect.deduplicate_across_products([row_a, row_b, row_c]),
            [row_a, row_c],
        )

    def test_build_search_url_encodes_korean_keyword(self) -> None:
        url = collect.build_search_url("계란 30구", 2)

        self.assertTrue(url.startswith("https://emart.ssg.com/search.ssg?query="))
        self.assertIn("query=%EA%B3%84%EB%9E%80+30%EA%B5%AC", url)
        self.assertIn("page=2", url)

    def test_default_max_pages_collects_until_last_page(self) -> None:
        self.assertEqual(collect.DEFAULT_MAX_PAGES, 0)

    def test_collect_product_stops_at_configured_max_pages(self) -> None:
        session = FakeSession(
            [
                FakeResponse(200, text=self.emart_item_html("milk-1")),
                FakeResponse(200, text=self.emart_item_html("milk-2")),
                FakeResponse(200, text=self.emart_item_html("milk-3")),
            ]
        )
        throttler = collect.RequestThrottler(
            0,
            sleep_func=lambda seconds: None,
            clock_func=lambda: 0.0,
        )

        summary = collect.collect_product(
            session=session,
            product=collect.product_request("milk"),
            collected_at="2026-09-03T12:00:00",
            output_dir=collect.ROOT / "data" / "raw" / "emart",
            max_pages=2,
            throttler=throttler,
            max_retries=0,
            backoff_seconds=0,
            write_output=False,
        )

        self.assertEqual(session.request_count, 2)
        self.assertEqual(summary["visited_page_count"], 2)
        self.assertEqual(summary["termination_reason"], "max_pages")
        self.assertEqual(summary["row_count"], 2)
        self.assertIn("page=2", summary["page_stats"][1]["url"])

    def test_collect_product_with_zero_max_pages_stops_on_empty_page(self) -> None:
        session = FakeSession(
            [
                FakeResponse(200, text=self.emart_item_html("milk-1")),
                FakeResponse(200, text=self.emart_item_html("milk-2")),
                FakeResponse(200, text="검색 결과가 없습니다."),
            ]
        )
        throttler = collect.RequestThrottler(
            0,
            sleep_func=lambda seconds: None,
            clock_func=lambda: 0.0,
        )

        summary = collect.collect_product(
            session=session,
            product=collect.product_request("milk"),
            collected_at="2026-09-03T12:00:00",
            output_dir=collect.ROOT / "data" / "raw" / "emart",
            max_pages=0,
            throttler=throttler,
            max_retries=0,
            backoff_seconds=0,
            write_output=False,
        )

        self.assertEqual(session.request_count, 3)
        self.assertEqual(summary["visited_page_count"], 3)
        self.assertEqual(summary["termination_reason"], "last_page")
        self.assertEqual(summary["row_count"], 2)

    def test_output_path_uses_emart_raw_naming(self) -> None:
        path = collect.output_path(collect.ROOT / "data" / "raw" / "emart", "milk", "2026-09-03T12:00:00")

        self.assertEqual(path, collect.ROOT / "data" / "raw" / "emart" / "emart_milk_2026-09-03.csv")

    def test_combined_output_path_uses_all_product_naming(self) -> None:
        path = collect.combined_output_path(
            collect.ROOT / "data" / "raw" / "emart",
            "2026-09-03T12:00:00",
        )

        self.assertEqual(
            path,
            collect.ROOT / "data" / "raw" / "emart" / "emart_all_2026-09-03.csv",
        )

    def test_fetch_page_backs_off_on_429_before_retry(self) -> None:
        sleeps: list[float] = []
        session = FakeSession(
            [
                FakeResponse(429, headers={"Retry-After": "2.5"}),
                FakeResponse(200, text="ok"),
            ]
        )
        throttler = collect.RequestThrottler(
            0,
            sleep_func=sleeps.append,
            clock_func=lambda: 0.0,
        )

        with self.assertLogs(collect.logger, level="WARNING"):
            text = collect.fetch_page(
                session,
                "https://emart.ssg.com/search.ssg?query=%EC%9A%B0%EC%9C%A0",
                throttler=throttler,
                max_retries=1,
                backoff_seconds=5,
            )

        self.assertEqual(text, "ok")
        self.assertEqual(session.request_count, 2)
        self.assertEqual(sleeps, [2.5])

    def test_request_throttler_waits_between_requests(self) -> None:
        sleeps: list[float] = []
        clock_values = iter([10.0, 10.25, 11.0])
        throttler = collect.RequestThrottler(
            1,
            sleep_func=sleeps.append,
            clock_func=lambda: next(clock_values),
        )

        throttler.wait()
        throttler.wait()

        self.assertEqual(sleeps, [0.75])

    def test_warm_up_session_gets_main_page_without_raising_for_403(self) -> None:
        session = FakeSession([FakeResponse(403, text="blocked")])
        throttler = collect.RequestThrottler(
            0,
            sleep_func=lambda seconds: None,
            clock_func=lambda: 0.0,
        )

        collect.warm_up_session(session, throttler=throttler)

        self.assertEqual(session.request_count, 1)


if __name__ == "__main__":
    unittest.main()
