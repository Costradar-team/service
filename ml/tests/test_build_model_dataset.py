from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from ml.scripts.build_model_dataset import (
    build_model_dataset,
    infer_brand_name,
    normalize_unit_price,
)


PROCESSED_COLUMNS = [
    "상품명",
    "조사일",
    "판매가격",
    "판매업소",
    "제조사",
    "세일여부",
    "원플러스원",
    "canonical_item",
    "subtype",
    "spec",
]


class NormalizeUnitPriceTest(unittest.TestCase):
    def test_mass_volume_and_count_units(self) -> None:
        flour = normalize_unit_price("밀가루", "900g", 1800)
        milk = normalize_unit_price("우유", "900ml", 2700)
        eggs = normalize_unit_price("계란", "15개", 6000)

        self.assertAlmostEqual(flour.unit_price, 2000)
        self.assertEqual(flour.unit_price_basis, "KRW/kg")
        self.assertAlmostEqual(milk.unit_price, 3000)
        self.assertEqual(milk.unit_price_basis, "KRW/L")
        self.assertAlmostEqual(eggs.unit_price, 4000)
        self.assertEqual(eggs.unit_price_basis, "KRW/10ea")

    def test_rejects_mismatched_unit(self) -> None:
        with self.assertRaisesRegex(ValueError, "unit_item_mismatch"):
            normalize_unit_price("우유", "1kg", 3000)


class BuildModelDatasetTest(unittest.TestCase):
    def test_infers_supported_store_brands(self) -> None:
        self.assertEqual(infer_brand_name("이마트월계점"), "이마트")
        self.assertEqual(
            infer_brand_name("롯데슈퍼G강남점"),
            "롯데마트·슈퍼",
        )
        self.assertEqual(
            infer_brand_name("GS더프레시강남대치점"),
            "GS더프레시",
        )
        self.assertEqual(infer_brand_name("알 수 없는 매장"), "기타")

    def test_builds_date_level_median(self) -> None:
        rows = [
            {
                "상품명": "밀가루 A(1kg)",
                "조사일": "2026-01-01",
                "판매가격": "2000",
                "판매업소": "이마트A점",
                "제조사": "A",
                "세일여부": "",
                "원플러스원": "",
                "canonical_item": "밀가루",
                "subtype": "박력분",
                "spec": "1kg",
            },
            {
                "상품명": "밀가루 B(500g)",
                "조사일": "2026-01-01",
                "판매가격": "1200",
                "판매업소": "롯데슈퍼B점",
                "제조사": "B",
                "세일여부": "",
                "원플러스원": "",
                "canonical_item": "밀가루",
                "subtype": "박력분",
                "spec": "500g",
            },
            {
                "상품명": "밀가루 A(1kg)",
                "조사일": "2026-01-08",
                "판매가격": "2500",
                "판매업소": "이마트A점",
                "제조사": "A",
                "세일여부": "",
                "원플러스원": "",
                "canonical_item": "밀가루",
                "subtype": "박력분",
                "spec": "1kg",
            },
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "processed.csv"
            output_dir = temp_path / "output"
            with input_path.open(
                "w", encoding="utf-8-sig", newline=""
            ) as input_file:
                writer = csv.DictWriter(input_file, fieldnames=PROCESSED_COLUMNS)
                writer.writeheader()
                writer.writerows(rows)

            summary = build_model_dataset(input_path, output_dir)
            with (output_dir / "model_dataset.csv").open(
                "r", encoding="utf-8-sig", newline=""
            ) as model_file:
                model_rows = list(csv.DictReader(model_file))
            with (output_dir / "item" / "model_dataset.csv").open(
                "r", encoding="utf-8-sig", newline=""
            ) as item_model_file:
                item_rows = list(csv.DictReader(item_model_file))
            with (output_dir / "product" / "model_dataset.csv").open(
                "r", encoding="utf-8-sig", newline=""
            ) as product_model_file:
                product_rows = list(csv.DictReader(product_model_file))
            with (output_dir / "brand" / "model_dataset.csv").open(
                "r", encoding="utf-8-sig", newline=""
            ) as brand_model_file:
                brand_rows = list(csv.DictReader(brand_model_file))
            with (output_dir / "store" / "model_dataset.csv").open(
                "r", encoding="utf-8-sig", newline=""
            ) as store_model_file:
                store_rows = list(csv.DictReader(store_model_file))

            self.assertEqual(summary["normalized_row_count"], 3)
            self.assertEqual(summary["unique_survey_date_count"], 2)
            self.assertEqual(len(model_rows), 2)
            self.assertEqual(model_rows[0]["median_unit_price"], "2200")
            self.assertEqual(model_rows[0]["store_count"], "2")
            self.assertEqual(summary["item_model_row_count"], 2)
            self.assertEqual(summary["item_series_count"], 1)
            self.assertEqual(len(item_rows), 2)
            self.assertEqual(item_rows[0]["canonical_item"], "밀가루")
            self.assertEqual(item_rows[0]["median_unit_price"], "2200")
            self.assertNotIn("subtype", item_rows[0])
            self.assertEqual(summary["product_model_row_count"], 3)
            self.assertEqual(summary["product_series_count"], 2)
            self.assertEqual(len(product_rows), 3)
            self.assertEqual(product_rows[0]["product_name"], "밀가루 A(1kg)")
            self.assertEqual(product_rows[0]["median_unit_price"], "2000")
            self.assertEqual(summary["brand_model_row_count"], 3)
            self.assertEqual(summary["brand_series_count"], 2)
            self.assertEqual(summary["store_model_row_count"], 3)
            self.assertEqual(summary["store_series_count"], 2)
            self.assertEqual(summary["brand_count"], 2)
            self.assertEqual(summary["store_count"], 2)
            self.assertEqual(len(brand_rows), 3)
            self.assertEqual(len(store_rows), 3)
            self.assertEqual(store_rows[0]["brand_name"], "이마트")
            self.assertEqual(store_rows[0]["store_name"], "이마트A점")
            self.assertEqual(store_rows[0]["actual_unit_price"], "2000")
            persisted_summary = json.loads(
                (output_dir / "dataset_summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(persisted_summary["rejected_row_count"], 0)


if __name__ == "__main__":
    unittest.main()
