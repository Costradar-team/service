from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import BigInteger, Column, Computed, Integer, MetaData, String, Table, UniqueConstraint, create_engine, func, insert, select


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


class StoreLoadConventionTests(unittest.TestCase):
    def test_declared_region_and_store_unique_grains(self) -> None:
        region_constraints = {
            constraint.name: [column.name for column in constraint.columns]
            for constraint in load_kca_store.region.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        store_constraints = {
            constraint.name: [column.name for column in constraint.columns]
            for constraint in load_kca_store.store.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        self.assertEqual(region_constraints["uq_region_parent_name"], ["parent_region_id", "name"])
        self.assertEqual(region_constraints["uq_region_root_name"], ["root_region_name"])
        self.assertEqual(
            store_constraints["uq_store_retailer_source_store_name"],
            ["retailer_id", "source_store_name"],
        )

    def test_root_region_and_store_grains_are_idempotent(self) -> None:
        metadata = MetaData()
        test_region = Table(
            "region", metadata,
            Column("region_id", Integer, primary_key=True),
            Column("parent_region_id", Integer),
            Column("name", String(50), nullable=False),
            Column("root_region_name", String(100), Computed("CASE WHEN parent_region_id IS NULL THEN name ELSE NULL END")),
            UniqueConstraint("parent_region_id", "name"),
            UniqueConstraint("root_region_name"),
        )
        test_store = Table(
            "store", metadata,
            Column("store_id", Integer, primary_key=True),
            Column("retailer_id", BigInteger, nullable=False),
            Column("source_store_name", String(255), nullable=False),
            UniqueConstraint("retailer_id", "source_store_name"),
        )
        engine = create_engine("sqlite://")
        metadata.create_all(engine)
        with engine.begin() as conn:
            for _ in range(2):
                conn.execute(insert(test_region).prefix_with("OR IGNORE"), {"parent_region_id": None, "name": "서울"})
                conn.execute(insert(test_store).prefix_with("OR IGNORE"), {"retailer_id": 1, "source_store_name": "롯데마트 서울점"})
            self.assertEqual(conn.scalar(select(func.count()).select_from(test_region)), 1)
            self.assertEqual(conn.scalar(select(func.count()).select_from(test_store)), 1)

    def test_store_failure_preserves_committed_retailer_and_region(self) -> None:
        state: list[str] = []
        transaction_count = 0

        class Transaction:
            def __enter__(self):
                return object()

            def __exit__(self, exc_type, exc, traceback):
                return False

        class Engine:
            def begin(self):
                nonlocal transaction_count
                transaction_count += 1
                return Transaction()

        with (
            patch.object(load_kca_store, "load_retailers", side_effect=lambda *args: state.append("retailer") or {"R": 1}),
            patch.object(load_kca_store, "load_regions", side_effect=lambda *args: state.append("region") or {("서울",): 1}),
            patch.object(load_kca_store, "upsert_stores", side_effect=RuntimeError("store failed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "store failed"):
                load_kca_store.load_store_master_staged(
                    Engine(), load_kca_store.LoadReport(), [], 1000, Path("failures.jsonl")
                )
        self.assertEqual(state, ["retailer", "region"])
        self.assertEqual(transaction_count, 3)

    def test_processed_input_missing_required_value_fails_load(self) -> None:
        import csv
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "stores.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=load_kca_store.REQUIRED_COLUMNS)
                writer.writeheader()
                writer.writerow({column: "" for column in load_kca_store.REQUIRED_COLUMNS})
            with self.assertRaises(load_kca_store.MissingRequiredValueError):
                load_kca_store.read_store_master_rows(path, Path(temp) / "failures.jsonl")


if __name__ == "__main__":
    unittest.main()
