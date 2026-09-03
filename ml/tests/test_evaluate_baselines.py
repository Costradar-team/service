from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from ml.scripts.evaluate_baselines import evaluate_baselines


MODEL_COLUMNS = [
    "survey_date",
    "canonical_item",
    "subtype",
    "unit_price_basis",
    "observation_count",
    "store_count",
    "sku_count",
    "min_unit_price",
    "median_unit_price",
    "max_unit_price",
]


class EvaluateBaselinesTest(unittest.TestCase):
    def test_evaluates_series_in_date_order(self) -> None:
        rows = []
        for survey_date, price in [
            ("2026-01-15", 1200),
            ("2026-01-01", 1000),
            ("2026-01-08", 1100),
        ]:
            rows.append(
                {
                    "survey_date": survey_date,
                    "canonical_item": "밀가루",
                    "subtype": "박력분",
                    "unit_price_basis": "KRW/kg",
                    "observation_count": 1,
                    "store_count": 1,
                    "sku_count": 1,
                    "min_unit_price": price,
                    "median_unit_price": price,
                    "max_unit_price": price,
                }
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "model_dataset.csv"
            output_dir = temp_path / "output"
            with input_path.open(
                "w", encoding="utf-8-sig", newline=""
            ) as input_file:
                writer = csv.DictWriter(input_file, fieldnames=MODEL_COLUMNS)
                writer.writeheader()
                writer.writerows(rows)

            metrics = evaluate_baselines(
                input_path,
                output_dir,
                rolling_window=2,
                minimum_points=3,
            )

            naive = metrics["overall"]["naive_last_value"]
            self.assertEqual(naive["sample_count"], 2)
            self.assertEqual(naive["mae"], 100)
            self.assertTrue((output_dir / "baseline_metrics.json").is_file())
            self.assertTrue((output_dir / "baseline_predictions.csv").is_file())

    def test_evaluates_product_series(self) -> None:
        rows = []
        for product_name, start_price in [("밀가루 A", 1000), ("밀가루 B", 2000)]:
            for index, survey_date in enumerate(
                ["2026-01-01", "2026-01-08", "2026-01-15"]
            ):
                rows.append(
                    {
                        "survey_date": survey_date,
                        "product_name": product_name,
                        "canonical_item": "밀가루",
                        "subtype": "박력분",
                        "unit_price_basis": "KRW/kg",
                        "median_unit_price": start_price + index * 100,
                    }
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "model_dataset.csv"
            output_dir = temp_path / "output"
            with input_path.open(
                "w", encoding="utf-8-sig", newline=""
            ) as input_file:
                writer = csv.DictWriter(input_file, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)

            metrics = evaluate_baselines(
                input_path,
                output_dir,
                minimum_points=3,
                series_level="product",
            )

            self.assertEqual(metrics["series_level"], "product")
            self.assertEqual(metrics["series_count"], 2)
            with (output_dir / "baseline_predictions.csv").open(
                "r", encoding="utf-8-sig", newline=""
            ) as predictions_file:
                predictions = list(csv.DictReader(predictions_file))
            self.assertEqual({row["product_name"] for row in predictions}, {
                "밀가루 A",
                "밀가루 B",
            })


if __name__ == "__main__":
    unittest.main()
