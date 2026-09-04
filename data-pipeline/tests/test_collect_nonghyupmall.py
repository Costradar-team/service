from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "collect" / "collect_nonghyupmall.py"

spec = importlib.util.spec_from_file_location("collect_nonghyupmall", SCRIPT_PATH)
collect_nonghyupmall = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = collect_nonghyupmall
spec.loader.exec_module(collect_nonghyupmall)


def test_output_columns_match_emart_schema() -> None:
    assert collect_nonghyupmall.OUTPUT_COLUMNS == [
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


def test_build_search_url_uses_nonghyup_search_endpoint() -> None:
    url = collect_nonghyupmall.build_search_url("우유")

    assert url.startswith("https://www.nonghyupmall.com/BC1F010M/srchTotalList.nh?")
    assert "searchTerm_main=%EC%9A%B0%EC%9C%A0" in url
    assert "CHAN_C=1101" in url
    assert "chanC=1101" in url


def test_parse_price_block_handles_original_and_sale_price() -> None:
    assert collect_nonghyupmall.parse_price_block("정가 : 21,900원 판매가 : 20,360원") == (
        "20360",
        "21900",
        "20360",
    )


def test_parse_price_block_handles_sale_price_only() -> None:
    assert collect_nonghyupmall.parse_price_block("판매가 : 10,900원") == ("10900", "", "10900")


def test_extract_capacity_from_product_name() -> None:
    capacity = collect_nonghyupmall.extract_capacity("[동원] 덴마크목장 무항생제인증우유 120mL 32개")

    assert "120mL" in capacity
    assert "32개" in capacity


def test_total_count_from_text() -> None:
    assert collect_nonghyupmall.total_count_from_text("총 555개의 상품이 있습니다.") == 555


def test_filter_product_rows_removes_irrelevant_milk_matches() -> None:
    product = collect_nonghyupmall.product_request("milk")
    rows = [
        {"brand_name": "서울우유", "item_name": "서울우유 1L"},
        {"brand_name": "브랜드", "item_name": "초코 우유맛 과자"},
    ]

    assert collect_nonghyupmall.filter_product_rows(product, rows) == [rows[0]]
