from __future__ import annotations

import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "collect"
    / "collect_kca_store_regions_kakao.py"
)
SPEC = importlib.util.spec_from_file_location("collect_kca_store_regions_kakao", SCRIPT_PATH)
assert SPEC is not None
collect = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(collect)


BASE_ROW = {
    "source_store_name": "(주)농협유통 창동점",
    "retailer_name": "(주)농협유통",
    "store_branch_name": "창동점",
    "row_count": "706",
}

DOCUMENT = {
    "place_name": "채선당행복가마솥밥 하나로마트창동점",
    "category_name": "음식점 > 한식 > 채선당행복가마솥밥",
    "address_name": "서울 도봉구 창동 1-10",
    "road_address_name": "서울 도봉구 마들로11길 20",
    "x": "127.05076937299367",
    "y": "37.65514007473494",
    "place_url": "http://place.map.kakao.com/247194489",
}
VALID_GS_DOCUMENT = {
    "place_name": "GS더프레시 강남대치점",
    "category_name": "가정,생활 > 슈퍼마켓",
    "address_name": "서울 강남구 대치동 622",
    "road_address_name": "서울 강남구 남부순환로 2927",
    "x": "127.063",
    "y": "37.493",
    "place_url": "http://place.map.kakao.com/gs",
}
INVALID_EMART24_DOCUMENT = {
    "place_name": "이마트24 광주월계점",
    "category_name": "가정,생활 > 편의점 > 이마트24",
    "address_name": "전남광주통합특별시 광산구 월계동 768-7",
    "road_address_name": "전남광주통합특별시 광산구 월계로140번길 2",
    "x": "126.8406",
    "y": "35.2132",
    "place_url": "http://place.map.kakao.com/emart24",
}

VERIFIED_OVERRIDE_STORES = {
    "롯데슈퍼G고양삼송점",
    "롯데슈퍼G대림점",
    "롯데슈퍼G마포점",
    "롯데슈퍼G보라점",
    "롯데슈퍼G복대점",
    "롯데슈퍼G부곡점",
    "롯데슈퍼G소사점",
    "롯데슈퍼G속초점",
    "롯데슈퍼G속초조양점",
    "롯데슈퍼G신곡점",
    "롯데슈퍼G운정점",
    "롯데슈퍼G유진점",
    "롯데슈퍼G은마점",
    "롯데슈퍼G은평점",
    "롯데슈퍼G전곡점",
    "롯데슈퍼G중동점",
    "롯데슈퍼G철원점",
    "롯데슈퍼G춘천점",
    "롯데슈퍼부산연산점",
    "롯데슈퍼세종나성프레시점",
    "롯데슈퍼수지점",
    "롯데슈퍼프리미엄서초점",
    "롯데슈퍼프리미엄용호점",
    "롯데슈퍼프리미엄황금점",
    "신세계백화점본점",
    "신세계백화점영등포점",
    "신세계백화점죽전점",
    "이마트가든5점",
    "이마트월계점",
    "현대백화점대구점",
    "현대백화점부산점",
    "현대백화점울산동구점",
    "현대백화점중동점",
}
NUMERIC_SUFFIX_PENDING_STORES = {
}

CORRECTED_OVERRIDE_STORES = {
    "GS더프레시아산탕정점": "충남 아산시 탕정면 탕정면로22번길 7",
    "롯데슈퍼G복대2점": "충북 청주시 흥덕구 죽천로 67",
    "롯데슈퍼G목동점": "서울 양천구 목동서로 100",
    "롯데슈퍼G방배점": "서울 서초구 방배로33길 29",
    "롯데슈퍼G수지점": "경기 용인시 수지구 진산로 24",
    "롯데슈퍼G홍제점": "서울 서대문구 통일로 397",
    "롯데슈퍼G화곡점": "서울 강서구 등촌로5길 80",
    "롯데슈퍼수지점": "경기 용인시 수지구 수지로 62",
    "롯데슈퍼용인점": "경기 용인시 처인구 백옥대로 1066",
    "GS더프레시마산점": "경남 창원시 마산합포구 월영동서로 20",
    "롯데슈퍼성주점": "경북 성주군 성주읍3길 41",
    "롯데슈퍼역촌점": "서울 은평구 진흥로 103",
    "이마트경산점": "경북 경산시 옥산로 227",
    "이마트동탄점": "경기 화성시 동탄구 동탄중앙로 376",
    "이마트에코시티점": "전북특별자치도 전주시 덕진구 세병서로 9",
}
MANUAL_OVERRIDE_REGION_3DEPTH = {
    "롯데슈퍼역촌점": "역촌동",
    "롯데슈퍼인천청라2점": "청라동",
    "롯데슈퍼프리미엄공덕점": "공덕동",
    "이마트경산점": "중산동",
}


class StoreRegionOutputSchemaTest(unittest.TestCase):
    def assert_schema(self, row: dict[str, str]) -> None:
        self.assertEqual(list(row.keys()), collect.OUTPUT_COLUMNS)

    def read_current_master_rows(self) -> dict[str, dict[str, str]]:
        master_path = Path(__file__).resolve().parents[1] / "data" / "processed" / "kca" / "kca_store_master.csv"
        with master_path.open("r", encoding="utf-8-sig", newline="") as f:
            return {row["source_store_name"]: row for row in csv.DictReader(f)}

    def test_primary_category_row_keeps_status_columns_aligned(self) -> None:
        row = collect.enriched_row(
            {
                "source_store_name": "GS더프레시강남대치점",
                "retailer_name": "GS더프레시",
                "store_branch_name": "강남대치점",
                "row_count": "1",
            },
            VALID_GS_DOCUMENT,
            "GS더프레시 강남대치점",
            "MT1",
            "primary_category",
            "matched",
        )

        self.assert_schema(row)
        self.assertEqual(row["category_group_code"], "MT1")
        self.assertEqual(row["search_stage"], "primary_category")
        self.assertEqual(row["validation_status"], "valid")
        self.assertEqual(row["match_status"], "matched")
        self.assertEqual(row["place_name"], VALID_GS_DOCUMENT["place_name"])
        self.assertEqual(row["region_1depth_name"], "서울")
        self.assertEqual(row["region_2depth_name"], "강남구")
        self.assertEqual(row["region_3depth_name"], "대치동")

    def test_fallback_no_category_row_keeps_empty_category_code(self) -> None:
        row = collect.enriched_row(
            BASE_ROW,
            DOCUMENT,
            "농협유통 창동점",
            "",
            "fallback_no_category",
            "matched",
        )

        self.assert_schema(row)
        self.assertEqual(row["category_group_code"], "")
        self.assertEqual(row["search_stage"], "fallback_no_category")
        self.assertEqual(row["validation_status"], "review")
        self.assertEqual(row["match_status"], "review")
        self.assertEqual(row["place_name"], "")
        self.assertEqual(row["region_3depth_name"], "")

    def test_fallback_alias_row_keeps_status_columns_aligned(self) -> None:
        row = collect.enriched_row(
            BASE_ROW,
            DOCUMENT,
            "하나로마트 창동점",
            "",
            "fallback_alias",
            "matched",
        )

        self.assert_schema(row)
        self.assertEqual(row["category_group_code"], "")
        self.assertEqual(row["search_stage"], "fallback_alias")
        self.assertEqual(row["validation_status"], "review")
        self.assertEqual(row["match_status"], "review")
        self.assertEqual(row["place_name"], "")
        self.assertEqual(row["region_3depth_name"], "")

    def test_invalid_candidate_is_downgraded_to_review(self) -> None:
        row = collect.enriched_row(
            {
                "source_store_name": "이마트월계점",
                "retailer_name": "이마트",
                "store_branch_name": "월계점",
                "row_count": "1",
            },
            INVALID_EMART24_DOCUMENT,
            "이마트 월계점",
            "",
            "fallback_no_category",
            "matched",
        )

        self.assert_schema(row)
        self.assertEqual(row["match_status"], "review")
        self.assertEqual(row["validation_status"], "review")
        self.assertEqual(row["place_name"], "")

    def test_document_region_fields_are_preferred_over_address_parsing(self) -> None:
        row = collect.enriched_row(
            BASE_ROW,
            {
                **DOCUMENT,
                "place_name": "하나로마트 창동점",
                "category_name": "가정,생활 > 슈퍼마켓 > 대형슈퍼 > 하나로마트",
                "address": {
                    "region_1depth_name": "서울",
                    "region_2depth_name": "서초구",
                    "region_3depth_name": "양재동",
                },
                "address_name": "서울 서초구 원지동 1",
                "road_address_name": "서울 서초구 청계산로 10",
            },
            "농협유통 양재점",
            "MT1",
            "primary_category",
            "matched",
        )

        self.assert_schema(row)
        self.assertEqual(row["region_1depth_name"], "서울")
        self.assertEqual(row["region_2depth_name"], "서초구")
        self.assertEqual(row["region_3depth_name"], "양재동")

    def test_region_falls_back_to_address_name_not_road_address_name(self) -> None:
        row = collect.enriched_row(
            BASE_ROW,
            {
                **DOCUMENT,
                "place_name": "하나로마트 창동점",
                "category_name": "가정,생활 > 슈퍼마켓 > 대형슈퍼 > 하나로마트",
                "address_name": "대전 중구 안영동 703",
                "road_address_name": "대전 중구 대둔산로199번길 43",
            },
            "농협유통 대전점",
            "MT1",
            "primary_category",
            "matched",
        )

        self.assert_schema(row)
        self.assertEqual(row["region_1depth_name"], "대전")
        self.assertEqual(row["region_2depth_name"], "중구")
        self.assertEqual(row["region_3depth_name"], "안영동")

    def test_sejong_address_keeps_missing_depth_empty_and_drops_parcel_number(self) -> None:
        row = collect.enriched_row(
            BASE_ROW,
            {
                **DOCUMENT,
                "place_name": "하나로마트 창동점",
                "category_name": "가정,생활 > 슈퍼마켓 > 대형슈퍼 > 하나로마트",
                "address_name": "세종특별자치시 아름동 1287",
                "road_address_name": "세종특별자치시 보듬3로 104-7",
            },
            "GS더프레시 세종아름점",
            "MT1",
            "primary_category",
            "matched",
        )

        self.assert_schema(row)
        self.assertEqual(row["region_1depth_name"], "세종특별자치시")
        self.assertEqual(row["region_2depth_name"], "")
        self.assertEqual(row["region_3depth_name"], "아름동")

    def test_region_fallback_does_not_fill_missing_depth_with_parcel_number(self) -> None:
        self.assertEqual(
            collect.split_region("세종특별자치시 1287"),
            ("세종특별자치시", "", ""),
        )

    def test_manual_override_row_uses_fixed_schema(self) -> None:
        row = collect.store_override_row(
            BASE_ROW,
            {
                "decision": "verified_match",
                "canonical_place_name": "수동 매장",
                "address_name": "서울 강남구 대치동 1",
                "road_address_name": "서울 강남구 테헤란로 1",
                "region_1depth_name": "서울",
                "region_2depth_name": "강남구",
                "region_3depth_name": "대치동",
                "x": "127.0",
                "y": "37.0",
                "place_url": "",
                "store_status": "open",
            },
        )

        self.assert_schema(row)
        self.assertEqual(row["category_group_code"], "")
        self.assertEqual(row["search_stage"], "manual_override")
        self.assertEqual(row["validation_status"], "valid")
        self.assertEqual(row["match_status"], "matched")
        self.assertEqual(row["place_name"], "수동 매장")
        self.assertEqual(row["category_name"], "")
        self.assertEqual(row["store_status"], "open")

    def test_headquarters_row_uses_fixed_schema(self) -> None:
        row = collect.enriched_row(
            {
                "source_store_name": "CU(본사)",
                "retailer_name": "CU",
                "store_branch_name": "(본사)",
                "row_count": "1",
            },
            None,
            "CU(본사)",
            "",
            "not_applicable",
            "unmatched",
        )

        self.assert_schema(row)
        self.assertEqual(row["category_group_code"], "")
        self.assertEqual(row["search_stage"], "not_applicable")
        self.assertEqual(row["validation_status"], "not_applicable")
        self.assertEqual(row["match_status"], "unmatched")
        self.assertEqual(row["place_name"], "")

    def test_no_search_result_is_unmatched_and_not_applicable(self) -> None:
        row = collect.enriched_row(
            BASE_ROW,
            None,
            "농협유통 창동점",
            "MT1",
            "primary_category",
            "api_not_found",
        )

        self.assert_schema(row)
        self.assertEqual(row["match_status"], "api_not_found")
        self.assertEqual(row["validation_status"], "not_applicable")
        self.assertEqual(row["place_name"], "")

    def test_review_candidate_does_not_write_place_fields(self) -> None:
        row = collect.enriched_row(
            {
                "source_store_name": "이마트에코시티점",
                "retailer_name": "이마트",
                "store_branch_name": "에코시티점",
                "row_count": "1",
            },
            {
                "place_name": "이마트24 진영에코시티점",
                "category_name": "가정,생활 > 편의점 > 이마트24",
                "address_name": "경남 김해시 진영읍 진산대로 59",
                "road_address_name": "경남 김해시 진영읍 진산대로 59",
                "x": "128.7",
                "y": "35.3",
                "place_url": "http://place.map.kakao.com/wrong",
            },
            "이마트 에코시티점",
            "MT1",
            "primary_category",
            "review",
        )

        self.assert_schema(row)
        self.assertEqual(row["validation_status"], "review")
        self.assertEqual(row["match_status"], "review")
        self.assertEqual(row["place_name"], "")
        self.assertEqual(row["address_name"], "")
        self.assertEqual(row["region_1depth_name"], "")

    def test_api_not_found_status_when_no_candidate_exists(self) -> None:
        original_fetch_keyword = collect.fetch_keyword
        collect.fetch_keyword = lambda session, api_key, query, category_group_code: []
        try:
            document, query, category, stage, match_status, debug_rows = collect.find_best_match(
                object(),
                "api-key",
                {
                    "source_store_name": "GS더프레시강남대치점",
                    "retailer_name": "GS더프레시",
                    "store_branch_name": "강남대치점",
                    "row_count": "1",
                },
            )
        finally:
            collect.fetch_keyword = original_fetch_keyword

        self.assertIsNone(document)
        self.assertEqual(query, "GS더프레시 강남대치점")
        self.assertEqual(category, "MT1")
        self.assertEqual(stage, "primary_category")
        self.assertEqual(match_status, "api_not_found")
        self.assertTrue(debug_rows)

    def test_wrong_top_api_candidates_are_review_not_matched(self) -> None:
        cases = [
            (
                {
                    "source_store_name": "GS더프레시아산탕정점",
                    "retailer_name": "GS더프레시",
                    "store_branch_name": "아산탕정점",
                    "row_count": "1",
                },
                "GS더프레시 아산아이유쉘점",
                "가정,생활 > 슈퍼마켓",
            ),
            (
                {
                    "source_store_name": "롯데슈퍼G복대2점",
                    "retailer_name": "롯데슈퍼",
                    "store_branch_name": "G복대2점",
                    "row_count": "1",
                },
                "롯데슈퍼 복대점",
                "가정,생활 > 슈퍼마켓",
            ),
            (
                {
                    "source_store_name": "롯데슈퍼성주점",
                    "retailer_name": "롯데슈퍼",
                    "store_branch_name": "성주점",
                    "row_count": "1",
                },
                "롯데슈퍼 대구대실점",
                "가정,생활 > 슈퍼마켓",
            ),
            (
                {
                    "source_store_name": "롯데슈퍼역촌점",
                    "retailer_name": "롯데슈퍼",
                    "store_branch_name": "역촌점",
                    "row_count": "1",
                },
                "롯데슈퍼 범서점",
                "가정,생활 > 슈퍼마켓",
            ),
            (
                {
                    "source_store_name": "이마트동탄점",
                    "retailer_name": "이마트",
                    "store_branch_name": "동탄점",
                    "row_count": "1",
                },
                "트레이더스 홀세일클럽 동탄점",
                "가정,생활 > 대형마트",
            ),
            (
                {
                    "source_store_name": "이마트에코시티점",
                    "retailer_name": "이마트",
                    "store_branch_name": "에코시티점",
                    "row_count": "1",
                },
                "이마트24 진영에코시티점",
                "가정,생활 > 편의점 > 이마트24",
            ),
        ]

        for row, place_name, category_name in cases:
            with self.subTest(source_store_name=row["source_store_name"]):
                document = {
                    "place_name": place_name,
                    "category_name": category_name,
                    "address_name": "서울 강남구 대치동 1",
                    "road_address_name": "서울 강남구 테헤란로 1",
                    "x": "127.0",
                    "y": "37.0",
                    "place_url": "http://place.map.kakao.com/wrong",
                }
                decision = collect.evaluate_document_match(row, document)
                output_row = collect.enriched_row(
                    row,
                    document,
                    collect.base_search_query(row),
                    collect.category_group_code(row["retailer_name"]),
                    "primary_category",
                    decision["match_status"],
                )

                self.assertEqual(decision["match_status"], "review")
                self.assertEqual(output_row["match_status"], "review")
                self.assertEqual(output_row["validation_status"], "review")
                self.assertEqual(output_row["place_name"], "")
                self.assertEqual(output_row["address_name"], "")

    def test_find_best_match_reviews_wrong_candidate_instead_of_saving_it(self) -> None:
        wrong_document = {
            "place_name": "GS더프레시 아산아이유쉘점",
            "category_name": "가정,생활 > 슈퍼마켓",
            "address_name": "충남 아산시 탕정면 매곡리 1",
            "road_address_name": "충남 아산시 탕정면 매곡중앙로 1",
            "x": "127.0",
            "y": "36.0",
            "place_url": "http://place.map.kakao.com/wrong",
        }
        original_fetch_keyword = collect.fetch_keyword
        collect.fetch_keyword = lambda session, api_key, query, category_group_code: [wrong_document]
        try:
            document, query, category, stage, match_status, _ = collect.find_best_match(
                object(),
                "api-key",
                {
                    "source_store_name": "GS더프레시아산탕정점",
                    "retailer_name": "GS더프레시",
                    "store_branch_name": "아산탕정점",
                    "row_count": "1",
                },
            )
        finally:
            collect.fetch_keyword = original_fetch_keyword

        self.assertIsNone(document)
        self.assertEqual(query, "GS더프레시 아산탕정점")
        self.assertEqual(category, "MT1")
        self.assertEqual(stage, "primary_category")
        self.assertEqual(match_status, "review")

    def test_valid_store_still_matches_after_stricter_validation(self) -> None:
        row = {
            "source_store_name": "GS더프레시강남대치점",
            "retailer_name": "GS더프레시",
            "store_branch_name": "강남대치점",
            "row_count": "1",
        }

        decision = collect.evaluate_document_match(row, VALID_GS_DOCUMENT)

        self.assertEqual(decision["match_status"], "matched")
        self.assertEqual(decision["validation_status"], "valid")

    def test_manual_override_non_matched_status_maps_to_unmatched(self) -> None:
        row = collect.store_override_row(
            BASE_ROW,
            {
                "decision": "pending_review",
                "canonical_place_name": "",
                "store_status": "historical",
            },
        )

        self.assert_schema(row)
        self.assertEqual(row["search_stage"], "manual_override")
        self.assertEqual(row["match_status"], "review")
        self.assertEqual(row["validation_status"], "review")
        self.assertEqual(row["store_status"], "historical")

    def test_department_store_row_uses_fixed_schema(self) -> None:
        row = collect.enriched_row(
            {
                "source_store_name": "현대백화점울산동구점",
                "retailer_name": "현대백화점",
                "store_branch_name": "울산동구점",
                "row_count": "1",
            },
            {**DOCUMENT, "place_name": "현대백화점 울산동구점", "category_name": "가정,생활 > 백화점"},
            "현대백화점 울산동구점",
            "",
            "primary_category",
            "matched",
        )

        self.assert_schema(row)
        self.assertEqual(row["category_group_code"], "")
        self.assertEqual(row["search_stage"], "primary_category")
        self.assertEqual(row["validation_status"], "valid")
        self.assertEqual(row["match_status"], "matched")
        self.assertEqual(row["place_name"], "현대백화점 울산동구점")

    def test_write_rows_rejects_unexpected_columns_before_csv_save(self) -> None:
        row = collect.enriched_row(BASE_ROW, DOCUMENT, "농협유통 창동점", "", "fallback_no_category", "matched")
        row["unexpected"] = "value"

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                collect.write_rows(Path(directory) / "out.csv", [row])

    def test_write_rows_rejects_semantically_shifted_status_values(self) -> None:
        row = collect.enriched_row(BASE_ROW, DOCUMENT, "농협유통 창동점", "", "fallback_no_category", "matched")
        row["category_group_code"] = "fallback_no_category"
        row["search_stage"] = "not_checked"
        row["validation_status"] = "matched"
        row["match_status"] = DOCUMENT["place_name"]

        with tempfile.TemporaryDirectory() as directory:
            accepted, rejected = collect.write_rows(Path(directory) / "out.csv", [row])
        self.assertEqual((accepted, rejected), (0, 1))

    def test_write_rows_rejects_matched_without_valid_validation(self) -> None:
        row = collect.enriched_row(
            {
                "source_store_name": "GS더프레시강남대치점",
                "retailer_name": "GS더프레시",
                "store_branch_name": "강남대치점",
                "row_count": "1",
            },
            VALID_GS_DOCUMENT,
            "GS더프레시 강남대치점",
            "MT1",
            "primary_category",
            "matched",
        )
        row["validation_status"] = "not_checked"

        with tempfile.TemporaryDirectory() as directory:
            accepted, rejected = collect.write_rows(Path(directory) / "out.csv", [row])
        self.assertEqual((accepted, rejected), (0, 1))

    def test_existing_matched_invalid_row_needs_refresh(self) -> None:
        row = collect.blank_output_row(
            {
                "source_store_name": "이마트에코시티점",
                "retailer_name": "이마트",
                "store_branch_name": "에코시티점",
                "row_count": "1",
                "validation_status": "invalid",
                "match_status": "matched",
                "place_name": "이마트24 진영에코시티점",
                "category_name": "가정,생활 > 편의점 > 이마트24",
            }
        )

        self.assertTrue(collect.existing_master_row_needs_refresh(row))

    def test_existing_valid_matched_row_does_not_need_refresh(self) -> None:
        row = collect.enriched_row(
            {
                "source_store_name": "GS더프레시강남대치점",
                "retailer_name": "GS더프레시",
                "store_branch_name": "강남대치점",
                "row_count": "1",
            },
            VALID_GS_DOCUMENT,
            "GS더프레시 강남대치점",
            "MT1",
            "primary_category",
            "matched",
        )

        self.assertFalse(collect.existing_master_row_needs_refresh(row))

    def test_g_bokdae_and_g_bokdae2_do_not_collapse(self) -> None:
        bokdae = {
            "source_store_name": "롯데슈퍼G복대점",
            "retailer_name": "롯데슈퍼",
            "store_branch_name": "G복대점",
            "row_count": "1",
        }
        bokdae2 = {
            "source_store_name": "롯데슈퍼G복대2점",
            "retailer_name": "롯데슈퍼",
            "store_branch_name": "G복대2점",
            "row_count": "1",
        }

        self.assertFalse(collect.branch_name_matches(bokdae2, "롯데프레시&델리 복대점"))
        self.assertFalse(collect.branch_name_matches(bokdae, "롯데슈퍼프레시 G복대2점"))

    def test_emart_rejects_emart24_and_traders_candidates(self) -> None:
        row = {
            "source_store_name": "이마트동탄점",
            "retailer_name": "이마트",
            "store_branch_name": "동탄점",
            "row_count": "1",
        }

        for place_name in ["이마트24 진영에코시티점", "트레이더스 홀세일클럽 동탄점"]:
            with self.subTest(place_name=place_name):
                decision = collect.evaluate_document_match(
                    row,
                    {
                        "place_name": place_name,
                        "category_name": "가정,생활 > 대형마트",
                    },
                )
                self.assertEqual(decision["match_status"], "review")
                self.assertIn("retailer_mismatch", decision["reject_reasons"])

    def test_store_override_csv_rejects_duplicate_source_store_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "overrides.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=collect.OVERRIDE_COLUMNS)
                writer.writeheader()
                for _ in range(2):
                    writer.writerow(
                        {
                            "source_store_name": "중복매장",
                            "decision": "pending_review",
                        }
                    )

            with self.assertRaises(ValueError):
                collect.read_store_overrides(path)

    def test_match_override_requires_address_fields(self) -> None:
        row = {
            "source_store_name": "주소누락매장",
            "decision": "verified_match",
            "canonical_place_name": "주소누락매장",
        }

        with self.assertRaises(ValueError):
            collect.validate_store_override_row(row)

    def test_store_override_csv_has_unique_source_store_names_and_required_fields(self) -> None:
        overrides = collect.read_store_overrides(collect.DEFAULT_OVERRIDE_PATH)

        self.assertEqual(len(overrides), len(set(overrides)))
        self.assertIn("롯데슈퍼G복대점", overrides)
        self.assertIn("롯데슈퍼G복대2점", overrides)

    def test_write_rows_rejects_road_name_region_3depth(self) -> None:
        row = collect.enriched_row(BASE_ROW, DOCUMENT, "농협유통 창동점", "MT1", "primary_category", "matched")
        row["region_3depth_name"] = "대둔산로199번길"

        with tempfile.TemporaryDirectory() as directory:
            accepted, rejected = collect.write_rows(Path(directory) / "out.csv", [row])
        self.assertEqual((accepted, rejected), (0, 1))

    def test_write_rows_rejects_parcel_number_region_depth(self) -> None:
        row = collect.enriched_row(BASE_ROW, DOCUMENT, "농협유통 창동점", "MT1", "primary_category", "matched")
        row["region_3depth_name"] = "1287"

        with tempfile.TemporaryDirectory() as directory:
            accepted, rejected = collect.write_rows(Path(directory) / "out.csv", [row])
        self.assertEqual((accepted, rejected), (0, 1))

    def test_current_master_csv_has_no_semantically_shifted_rows(self) -> None:
        master_path = Path(__file__).resolve().parents[1] / "data" / "processed" / "kca" / "kca_store_master.csv"
        if not master_path.exists():
            self.skipTest("kca_store_master.csv does not exist")

        with master_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            collect.validate_output_header(reader.fieldnames, master_path)
            for row_number, row in enumerate(reader, start=2):
                collect.validate_output_row_schema(row, row_number)
                collect.validate_output_row_values(row, row_number)

    def test_verified_override_stores_are_matched_valid_in_current_master(self) -> None:
        rows = self.read_current_master_rows()

        for source_store_name in VERIFIED_OVERRIDE_STORES:
            with self.subTest(source_store_name=source_store_name):
                row = rows[source_store_name]
                self.assertEqual(row["match_status"], "matched")
                self.assertEqual(row["validation_status"], "valid")
                self.assertTrue(row["address_name"])
                self.assertTrue(row["x"])
                self.assertTrue(row["y"])

    def test_corrected_override_stores_use_corrected_locations_in_current_master(self) -> None:
        rows = self.read_current_master_rows()

        for source_store_name, expected_address in CORRECTED_OVERRIDE_STORES.items():
            with self.subTest(source_store_name=source_store_name):
                row = rows[source_store_name]
                self.assertEqual(row["match_status"], "matched")
                self.assertEqual(row["validation_status"], "valid")
                self.assertEqual(row["address_name"], expected_address)
                self.assertTrue(row["x"])
                self.assertTrue(row["y"])

    def test_manual_override_region_3depth_is_populated_in_current_master(self) -> None:
        rows = self.read_current_master_rows()

        for source_store_name, expected_region_3depth in MANUAL_OVERRIDE_REGION_3DEPTH.items():
            with self.subTest(source_store_name=source_store_name):
                row = rows[source_store_name]
                self.assertEqual(row["search_stage"], "manual_override")
                self.assertEqual(row["region_3depth_name"], expected_region_3depth)

    def test_manual_override_category_name_is_empty_in_current_master(self) -> None:
        rows = self.read_current_master_rows().values()

        for row in rows:
            if row["search_stage"] == "manual_override":
                self.assertEqual(row["category_name"], "")

    def test_gs_masan_is_corrected_in_current_master(self) -> None:
        row = self.read_current_master_rows()["GS더프레시마산점"]

        self.assertEqual(row["match_status"], "matched")
        self.assertEqual(row["validation_status"], "valid")
        self.assertEqual(row["place_name"], "GS더프레시 마산점")
        self.assertEqual(row["address_name"], "경남 창원시 마산합포구 월영동서로 20")
        self.assertEqual(row["store_status"], "open")

    def test_numeric_suffix_override_stores_remain_review_in_current_master(self) -> None:
        rows = self.read_current_master_rows()

        for source_store_name in NUMERIC_SUFFIX_PENDING_STORES:
            with self.subTest(source_store_name=source_store_name):
                row = rows[source_store_name]
                self.assertEqual(row["match_status"], "review")
                self.assertEqual(row["validation_status"], "review")
                self.assertEqual(row["place_name"], "")
                self.assertEqual(row["address_name"], "")

    def test_current_master_matched_count_does_not_drop_excessively(self) -> None:
        rows = self.read_current_master_rows().values()

        self.assertGreaterEqual(
            sum(1 for row in rows if row["match_status"] == "matched"),
            450,
        )

    def test_write_rows_writes_expected_header_order(self) -> None:
        row = collect.enriched_row(BASE_ROW, DOCUMENT, "농협유통 창동점", "", "fallback_no_category", "matched")

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "out.csv"
            collect.write_rows(output_path, [row])
            with output_path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.reader(f)
                self.assertEqual(next(reader), collect.OUTPUT_COLUMNS)

    def test_write_rows_separates_row_level_rejections(self) -> None:
        valid = collect.enriched_row(BASE_ROW, DOCUMENT, "농협유통 창동점", "", "fallback_no_category", "matched")
        invalid = dict(valid)
        invalid["region_3depth_name"] = "1287"

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "out.csv"
            rejected_path = Path(directory) / "rejected.csv"
            accepted, rejected = collect.write_rows(output_path, [valid, invalid], rejected_path)
            with output_path.open("r", encoding="utf-8-sig", newline="") as stream:
                processed_rows = list(csv.DictReader(stream))
            with rejected_path.open("r", encoding="utf-8-sig", newline="") as stream:
                rejected_rows = list(csv.DictReader(stream))

        self.assertEqual((accepted, rejected), (1, 1))
        self.assertEqual(len(processed_rows), 1)
        self.assertEqual(rejected_rows[0]["source_row_number"], "2")
        self.assertIn("region_3depth_name", rejected_rows[0]["reject_reason"])


if __name__ == "__main__":
    unittest.main()
