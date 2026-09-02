from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


LOAD_DIR = Path(__file__).resolve().parents[1] / "scripts" / "load"
SCRIPT_PATH = LOAD_DIR / "load_kca_store_mysql.py"
sys.path.insert(0, str(LOAD_DIR))
SPEC = importlib.util.spec_from_file_location("load_kca_store_mysql", SCRIPT_PATH)
assert SPEC is not None
load_kca_store = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = load_kca_store
assert SPEC.loader is not None
SPEC.loader.exec_module(load_kca_store)


class StoreMasterRowTests(unittest.TestCase):
    def test_missing_region_branch_remains_branch(self) -> None:
        row = load_kca_store.StoreMasterRow(
            row_number=1,
            source_store_name="롯데슈퍼G목동점",
            retailer_name="롯데슈퍼",
            store_branch_name="G목동점",
            region_1depth_name="",
            region_2depth_name="",
            region_3depth_name="",
            store_status="unknown",
        )

        self.assertEqual(row.store_type, "BRANCH")

    def test_headquarters_remains_chain_level(self) -> None:
        row = load_kca_store.StoreMasterRow(
            row_number=1,
            source_store_name="CU(본사)",
            retailer_name="CU",
            store_branch_name="본사",
            region_1depth_name="",
            region_2depth_name="",
            region_3depth_name="",
            store_status="open",
        )

        self.assertEqual(row.store_type, "CHAIN_LEVEL")

    def test_headquarters_payload_is_not_applicable_without_region(self) -> None:
        row = load_kca_store.StoreMasterRow(
            row_number=1,
            source_store_name="CU(본사)",
            retailer_name="CU",
            store_branch_name="(본사)",
            region_1depth_name="서울",
            region_2depth_name="강남구",
            region_3depth_name="역삼동",
            match_status="unmatched",
            validation_status="not_applicable",
            store_status="",
        )

        payload = load_kca_store.store_row_payload(row, retailer_id=10, region_ids={})

        self.assertEqual(payload["name"], "(본사)")
        self.assertEqual(payload["store_type"], "CHAIN_LEVEL")
        self.assertEqual(payload["store_status"], "unknown")
        self.assertEqual(payload["match_status"], "not_applicable")
        self.assertEqual(payload["validation_status"], "not_applicable")
        self.assertIsNone(payload["region_id"])

    def test_combined_sigungu_column_splits_into_region_hierarchy(self) -> None:
        row = load_kca_store.StoreMasterRow(
            row_number=1,
            source_store_name="GS더프레시마산점",
            retailer_name="GS더프레시",
            store_branch_name="마산점",
            region_1depth_name="경남",
            region_2depth_name="창원시 마산합포구",
            region_3depth_name="해운동",
            match_status="matched",
            validation_status="valid",
            store_status="open",
        )

        self.assertEqual(
            row.region_names,
            [
                ("SIDO", "경남"),
                ("SI", "창원시"),
                ("GU", "마산합포구"),
                ("DONG", "해운동"),
            ],
        )
        self.assertEqual(row.region_path_key, ("경남", "창원시", "마산합포구", "해운동"))

    def test_branch_payload_uses_most_specific_region_id(self) -> None:
        row = load_kca_store.StoreMasterRow(
            row_number=1,
            source_store_name="GS더프레시마산점",
            retailer_name="GS더프레시",
            store_branch_name="마산점",
            region_1depth_name="경남",
            region_2depth_name="창원시 마산합포구",
            region_3depth_name="해운동",
            match_status="matched",
            validation_status="valid",
            store_status="open",
        )

        payload = load_kca_store.store_row_payload(
            row,
            retailer_id=20,
            region_ids={("경남", "창원시", "마산합포구", "해운동"): 99},
        )

        self.assertEqual(payload["name"], "마산점")
        self.assertEqual(payload["store_type"], "BRANCH")
        self.assertEqual(payload["match_status"], "matched")
        self.assertEqual(payload["region_id"], 99)


if __name__ == "__main__":
    unittest.main()
