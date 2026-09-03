from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from ml.scripts.predict_prices import predict_prices
from ml.scripts.train_price_model import train_price_model


class PredictPricesTest(unittest.TestCase):
    def build_dataset(self, path: Path) -> None:
        rows = []
        for item, subtype, start_price in [
            ("밀가루", "박력분", 2000),
            ("설탕", "백설탕", 2500),
        ]:
            for index, survey_date in enumerate(
                pd.date_range("2026-01-01", periods=12, freq="14D")
            ):
                rows.append(
                    {
                        "survey_date": survey_date.date().isoformat(),
                        "canonical_item": item,
                        "subtype": subtype,
                        "unit_price_basis": "KRW/kg",
                        "median_unit_price": start_price + index * 10,
                    }
                )
        pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")

    def build_store_dataset(self, path: Path) -> None:
        rows = []
        for store_name, start_price in [
            ("이마트A점", 2000),
            ("이마트B점", 2500),
        ]:
            for index, survey_date in enumerate(
                pd.date_range("2026-01-01", periods=12, freq="14D")
            ):
                rows.append(
                    {
                        "survey_date": survey_date.date().isoformat(),
                        "canonical_item": "밀가루",
                        "subtype": "박력분",
                        "product_name": "밀가루 테스트 상품",
                        "brand_name": "이마트",
                        "store_name": store_name,
                        "unit_price_basis": "KRW/kg",
                        "actual_unit_price": start_price + index * 10,
                    }
                )
        pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")

    def test_saved_model_predicts_without_retraining(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "model_dataset.csv"
            model_dir = root / "model"
            output_dir = root / "prediction"
            self.build_dataset(input_path)

            training_report = train_price_model(input_path, model_dir)
            self.assertEqual(
                training_report["selected_feature_set"],
                "retail_history",
            )
            model_modified_at = (model_dir / "price_model.joblib").stat().st_mtime_ns
            prediction_report = predict_prices(
                input_path,
                model_dir / "price_model.joblib",
                output_dir,
            )

            self.assertEqual(prediction_report["forecast_count"], 2)
            self.assertEqual(
                (model_dir / "price_model.joblib").stat().st_mtime_ns,
                model_modified_at,
            )
            self.assertEqual(
                prediction_report["model_trained_through_date"],
                training_report["model_trained_through_date"],
            )
            forecasts = pd.read_csv(output_dir / "future_predictions.csv")
            self.assertEqual(len(forecasts), 2)
            self.assertEqual(set(forecasts["as_of_date"]), {"2026-06-04"})
            saved_report = json.loads(
                (output_dir / "prediction_report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved_report["forecast_count"], 2)

    def test_recursive_forecast_uses_previous_prediction_as_next_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "model_dataset.csv"
            model_dir = root / "model"
            output_dir = root / "prediction"
            self.build_dataset(input_path)
            train_price_model(input_path, model_dir)

            report = predict_prices(
                input_path,
                model_dir / "price_model.joblib",
                output_dir,
                forecast_horizon=3,
            )

            self.assertEqual(report["forecast_series_count"], 2)
            self.assertEqual(report["forecast_horizon"], 3)
            self.assertEqual(report["forecast_count"], 6)

            forecasts = pd.read_csv(output_dir / "future_predictions.csv")
            self.assertEqual(set(forecasts["forecast_horizon_step"]), {1, 2, 3})
            self.assertEqual(set(forecasts["as_of_date"]), {"2026-06-04"})
            self.assertEqual(
                set(
                    forecasts.loc[
                        forecasts["forecast_horizon_step"] == 1,
                        "recursive_input_source",
                    ]
                ),
                {"observed"},
            )
            self.assertEqual(
                set(
                    forecasts.loc[
                        forecasts["forecast_horizon_step"] > 1,
                        "recursive_input_source",
                    ]
                ),
                {"model_prediction"},
            )

            flour = forecasts.loc[forecasts["canonical_item"] == "밀가루"].sort_values(
                "forecast_horizon_step"
            )
            self.assertEqual(
                flour["forecast_date"].tolist(),
                ["2026-06-18", "2026-07-02", "2026-07-16"],
            )
            self.assertAlmostEqual(
                flour.iloc[1]["recursive_input_unit_price"],
                flour.iloc[0]["model_predicted_unit_price"],
                places=3,
            )
            self.assertAlmostEqual(
                flour.iloc[2]["recursive_input_unit_price"],
                flour.iloc[1]["model_predicted_unit_price"],
                places=3,
            )

    def test_forecast_horizon_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "forecast_horizon"):
            predict_prices(
                Path("missing.csv"),
                Path("missing.joblib"),
                Path("missing-output"),
                forecast_horizon=0,
            )

    def test_item_model_outputs_one_forecast_per_item(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "item_model_dataset.csv"
            model_dir = root / "item_model"
            output_dir = root / "item_prediction"
            self.build_dataset(input_path)

            train_price_model(
                input_path,
                model_dir,
                series_level="item",
            )
            report = predict_prices(
                input_path,
                model_dir / "price_model.joblib",
                output_dir,
                series_level="item",
                forecast_horizon=2,
            )

            self.assertEqual(report["forecast_series_count"], 2)
            self.assertEqual(report["forecast_count"], 4)
            forecasts = pd.read_csv(output_dir / "future_predictions.csv")
            self.assertNotIn("subtype", forecasts.columns)
            self.assertEqual(set(forecasts["canonical_item"]), {"밀가루", "설탕"})
            self.assertIn("model_predicted_change_percent", forecasts.columns)

    def test_store_forecast_predicts_direct_prices_recursively(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "store_model_dataset.csv"
            model_dir = root / "store_model"
            output_dir = root / "store_prediction"
            self.build_store_dataset(input_path)
            train_price_model(
                input_path,
                model_dir,
                series_level="store",
            )

            report = predict_prices(
                input_path,
                model_dir / "price_model.joblib",
                output_dir,
                series_level="store",
                forecast_horizon=2,
            )

            self.assertEqual(report["forecast_series_count"], 2)
            self.assertEqual(report["forecast_count"], 4)
            forecasts = pd.read_csv(output_dir / "future_predictions.csv")
            self.assertIn("brand_name", forecasts.columns)
            self.assertIn("store_name", forecasts.columns)
            self.assertIn("last_actual_unit_price", forecasts.columns)
            self.assertNotIn("current_median_unit_price", forecasts.columns)
            self.assertNotIn("naive_predicted_unit_price", forecasts.columns)
            self.assertNotIn("recommended_unit_price", forecasts.columns)
            self.assertNotIn("recommended_forecaster", forecasts.columns)
            self.assertEqual(set(forecasts["forecast_horizon_step"]), {1, 2})
            second = forecasts.loc[
                (forecasts["store_name"] == "이마트A점")
                & (forecasts["forecast_horizon_step"] == 2)
            ].iloc[0]
            first = forecasts.loc[
                (forecasts["store_name"] == "이마트A점")
                & (forecasts["forecast_horizon_step"] == 1)
            ].iloc[0]
            self.assertAlmostEqual(
                second["recursive_input_unit_price"],
                first["model_predicted_unit_price"],
                places=3,
            )


if __name__ == "__main__":
    unittest.main()
