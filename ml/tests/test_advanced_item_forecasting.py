from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from ml.scripts.predict_advanced_item_prices import predict_advanced_item_prices
from ml.scripts.train_advanced_item_model import (
    build_direct_horizon_dataset,
    train_advanced_item_model,
)


class AdvancedItemForecastingTest(unittest.TestCase):
    def build_dataset(self, path: Path) -> pd.DataFrame:
        rows = []
        for item, start_price in [("밀가루", 2000), ("설탕", 3000)]:
            for index, survey_date in enumerate(
                pd.date_range("2026-01-01", periods=16, freq="14D")
            ):
                price = start_price + index * 10
                rows.append(
                    {
                        "survey_date": survey_date.date().isoformat(),
                        "canonical_item": item,
                        "unit_price_basis": "KRW/kg",
                        "observation_count": 20,
                        "store_count": 5,
                        "sku_count": 3,
                        "min_unit_price": price - 100,
                        "median_unit_price": price,
                        "max_unit_price": price + 100,
                        "external_market_available": 1,
                        "external_market_price": 100 + index,
                        "external_market_change_7d_pct": 0.1,
                        "external_market_change_14d_pct": 0.2,
                        "external_market_change_28d_pct": 0.4,
                        "external_market_price_vs_mean_28d_pct": 0.2,
                        "external_market_age_days": 1,
                    }
                )
        frame = pd.DataFrame(rows)
        frame.to_csv(path, index=False, encoding="utf-8-sig")
        return frame

    def test_direct_horizon_target_does_not_use_recursive_prediction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "items.csv"
            raw = self.build_dataset(input_path)
            direct = build_direct_horizon_dataset(raw, horizon_step=2)
            first = direct.loc[direct["canonical_item"] == "밀가루"].iloc[0]
            self.assertEqual(first["current_median_unit_price"], 2040)
            self.assertEqual(first["median_unit_price"], 2060)
            self.assertEqual(first["lag_2"], 2030)
            self.assertEqual(first["lag_4"], 2000)
            self.assertEqual(first["forecast_horizon_step"], 2)
            self.assertEqual(first["external_market_price"], 104)

    def test_training_and_prediction_emit_direct_horizons(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "items.csv"
            model_dir = root / "model"
            self.build_dataset(input_path)
            report = train_advanced_item_model(
                input_path,
                model_dir,
                max_forecast_horizon=4,
            )
            self.assertEqual(report["forecast_strategy"], "direct_multi_horizon")
            self.assertEqual(set(report["horizons"]), {"1", "2", "3", "4"})

            model_modified_at = (model_dir / "price_model.joblib").stat().st_mtime_ns
            prediction_report = predict_advanced_item_prices(
                input_path,
                model_dir / "price_model.joblib",
                model_dir,
                forecast_horizon=4,
            )
            self.assertEqual(
                (model_dir / "price_model.joblib").stat().st_mtime_ns,
                model_modified_at,
            )
            self.assertEqual(prediction_report["forecast_count"], 8)
            forecasts = pd.read_csv(model_dir / "future_predictions.csv")
            self.assertEqual(set(forecasts["forecast_horizon_step"]), {1, 2, 3, 4})
            self.assertEqual(
                set(forecasts["prediction_strategy"]), {"direct_multi_horizon"}
            )
            self.assertNotIn("recursive_input_unit_price", forecasts.columns)
            self.assertTrue(
                (
                    forecasts.groupby("canonical_item")["current_median_unit_price"]
                    .nunique()
                    == 1
                ).all()
            )
            self.assertNotIn("prediction_interval_lower", forecasts.columns)
            self.assertNotIn("prediction_interval_upper", forecasts.columns)
            self.assertNotIn(
                "prediction_interval_confidence_level", forecasts.columns
            )
            self.assertNotIn("pred_low", forecasts.columns)
            self.assertNotIn("pred_high", forecasts.columns)
            self.assertTrue(
                {"drop_probability", "signal", "signal_message"}.issubset(
                    forecasts.columns
                )
            )
            saved_report = json.loads(
                (model_dir / "prediction_report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved_report["forecast_horizon"], 4)

    def test_prediction_rejects_untrained_horizon(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "items.csv"
            model_dir = root / "model"
            self.build_dataset(input_path)
            train_advanced_item_model(
                input_path,
                model_dir,
                max_forecast_horizon=2,
            )
            with self.assertRaisesRegex(ValueError, "exceeds"):
                predict_advanced_item_prices(
                    input_path,
                    model_dir / "price_model.joblib",
                    model_dir,
                    forecast_horizon=3,
                )


if __name__ == "__main__":
    unittest.main()
