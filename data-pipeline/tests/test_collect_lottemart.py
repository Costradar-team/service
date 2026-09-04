from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "collect" / "collect_lottemart.py"
SPEC = importlib.util.spec_from_file_location("collect_lottemart", SCRIPT_PATH)
assert SPEC is not None
collect = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = collect
assert SPEC.loader is not None
SPEC.loader.exec_module(collect)


class LottemartZettaTests(unittest.TestCase):
    def test_search_url_encodes_query(self) -> None:
        self.assertEqual(
            collect.search_url("우유"),
            "https://lottemartzetta.com/products/search?q=%EC%9A%B0%EC%9C%A0",
        )

    def test_search_api_url_encodes_query_and_page_token(self) -> None:
        url = collect.search_api_url("우유", page_token="next-token", page_size=50)

        self.assertIn("q=%EC%9A%B0%EC%9C%A0", url)
        self.assertIn("maxPageSize=50", url)
        self.assertIn("maxProductsToDecorate=50", url)
        self.assertIn("pageToken=next-token", url)

    def test_parse_price(self) -> None:
        self.assertEqual(collect.parse_price("가격 3,890원"), 3890)
        self.assertIsNone(collect.parse_price(None))

    def test_output_columns_include_required_fields(self) -> None:
        self.assertEqual(
            set(["product_key", "product_name", "item_id", "item_name", "product_url", "display_price"]),
            set(collect.OUTPUT_COLUMNS).intersection(
                {"product_key", "product_name", "item_id", "item_name", "product_url", "display_price"}
            ),
        )


if __name__ == "__main__":
    unittest.main()
