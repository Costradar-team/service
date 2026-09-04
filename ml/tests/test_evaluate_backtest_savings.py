from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from ml.scripts.evaluate_backtest_savings import (
    evaluate_artifact_root,
    evaluate_backtest_frame,
)


class EvaluateBacktestSavingsTest(unittest.TestCase):
    def test_calculates_mape_and_purchase_timing_savings(self) -> None:
        frame = pd.DataFrame(
            {
                "survey_date": ["2026-02-01", "2026-02-01"],
                "canonical_item": ["밀가루", "우유"],
                "median_unit_price": [120.0, 80.0],
                "lag_1": [100.0, 100.0],
                "model_prediction": [110.0, 90.0],
                "naive_prediction": [100.0, 100.0],
            }
        )

        metrics, decisions = evaluate_backtest_frame(
            frame,
            "median_unit_price",
        )

        self.assertEqual(metrics["sample_count"], 2)
        self.assertAlmostEqual(metrics["model"]["mape_percent"], 10.4167, places=4)
        timing = metrics["purchase_timing_backtest"]
        self.assertEqual(timing["buy_now_count"], 1)
        self.assertEqual(timing["wait_count"], 1)
        self.assertEqual(timing["baseline_cost"], 200.0)
        self.assertEqual(timing["strategy_cost"], 180.0)
        self.assertEqual(timing["savings_percent"], 10.0)
        self.assertEqual(decisions["decision"].tolist(), ["BUY_NOW", "WAIT"])
        self.assertEqual(decisions["realized_savings"].tolist(), [0.0, 20.0])

    def test_reports_negative_savings_for_a_wrong_wait_decision(self) -> None:
        frame = pd.DataFrame(
            {
                "actual_unit_price": [120.0],
                "lag_1": [100.0],
                "model_prediction": [90.0],
                "naive_prediction": [100.0],
            }
        )

        metrics, _ = evaluate_backtest_frame(frame, "actual_unit_price")

        timing = metrics["purchase_timing_backtest"]
        self.assertEqual(timing["strategy_cost"], 120.0)
        self.assertEqual(timing["savings_amount"], -20.0)
        self.assertEqual(timing["savings_percent"], -20.0)

    def test_writes_summary_and_decision_files_for_all_levels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for level, actual_column in (
                ("item", "median_unit_price"),
                ("product", "median_unit_price"),
                ("brand", "median_unit_price"),
                ("store", "actual_unit_price"),
            ):
                model_dir = root / level / "model"
                model_dir.mkdir(parents=True)
                pd.DataFrame(
                    {
                        "survey_date": ["2026-02-01"],
                        "canonical_item": ["밀가루"],
                        actual_column: [90.0],
                        "lag_1": [100.0],
                        "model_prediction": [95.0],
                        "naive_prediction": [100.0],
                    }
                ).to_csv(
                    model_dir / "backtest_predictions.csv",
                    index=False,
                    encoding="utf-8-sig",
                )

            output_path = root / "backtest_business_metrics.json"
            payload = evaluate_artifact_root(root, output_path)

            self.assertEqual(payload["total_sample_count"], 4)
            self.assertEqual(
                set(payload["results"]),
                {"item", "product", "brand", "store"},
            )
            self.assertEqual(
                payload["results"]["store"]["purchase_timing_backtest"][
                    "savings_percent"
                ],
                10.0,
            )
            self.assertTrue(output_path.is_file())
            saved = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["schema_version"], "1.0")
            for level in ("item", "product", "brand", "store"):
                self.assertTrue(
                    (root / level / "model" / "backtest_purchase_decisions.csv").is_file()
                )

    def test_rejects_negative_decision_threshold(self) -> None:
        frame = pd.DataFrame(
            {
                "median_unit_price": [100.0],
                "lag_1": [100.0],
                "model_prediction": [100.0],
                "naive_prediction": [100.0],
            }
        )
        with self.assertRaisesRegex(ValueError, "decision_threshold_percent"):
            evaluate_backtest_frame(
                frame,
                "median_unit_price",
                decision_threshold_percent=-0.1,
            )


if __name__ == "__main__":
    unittest.main()
