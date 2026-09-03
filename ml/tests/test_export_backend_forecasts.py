from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from ml.scripts.export_backend_forecasts import export_backend_forecasts


COMMON_ROW = {
    "forecast_date": "2026-08-07",
    "forecast_horizon_step": "1",
    "as_of_date": "2026-07-24",
    "canonical_item": "밀가루",
    "subtype": "중력분",
    "unit_price_basis": "KRW/kg",
    "current_median_unit_price": "2010",
    "recursive_input_unit_price": "2010",
    "recursive_input_source": "observed",
    "model_predicted_unit_price": "1999.1595",
    "model_predicted_step_change_percent": "-0.5393",
    "model_predicted_change_percent": "-0.5393",
}


class ExportBackendForecastsTest(unittest.TestCase):
    def write_csv(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def test_exports_item_and_legacy_api_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            item_input = temp_path / "item.csv"
            subtype_input = temp_path / "subtype.csv"
            product_input = temp_path / "product.csv"
            brand_input = temp_path / "brand.csv"
            store_input = temp_path / "store.csv"
            output_dir = temp_path / "backend"
            item_row = COMMON_ROW.copy()
            item_row.pop("subtype")
            item_row.update(
                {
                    "prediction_strategy": "direct_multi_horizon",
                    "forecast_method": "validated_direct_ensemble",
                    "model_weight": "0.25",
                    "selected_feature_set": "retail_history",
                    "drop_probability": "0.5",
                    "signal": "HOLD",
                    "signal_message": "2주 뒤 유의미한 가격 변동이 예상되지 않습니다.",
                }
            )
            self.write_csv(item_input, [item_row])
            self.write_csv(subtype_input, [COMMON_ROW])
            self.write_csv(
                product_input,
                [COMMON_ROW | {"product_name": "곰표 밀가루(1kg)"}],
            )
            self.write_csv(
                brand_input,
                [
                    COMMON_ROW
                    | {
                        "product_name": "곰표 밀가루(1kg)",
                        "brand_name": "이마트",
                    }
                ],
            )
            store_row = COMMON_ROW | {
                "product_name": "곰표 밀가루(1kg)",
                "brand_name": "이마트",
                "store_name": "이마트월계점",
                "last_actual_unit_price": "2010",
            }
            store_row.pop("current_median_unit_price")
            self.write_csv(store_input, [store_row])

            summary = export_backend_forecasts(
                subtype_input,
                product_input,
                output_dir,
                brand_input=brand_input,
                store_input=store_input,
                item_input=item_input,
            )

            self.assertEqual(summary["item_forecast_count"], 1)
            self.assertEqual(summary["subtype_forecast_count"], 1)
            self.assertEqual(summary["product_forecast_count"], 1)
            self.assertEqual(summary["brand_forecast_count"], 1)
            self.assertEqual(summary["store_forecast_count"], 1)
            item_payload = json.loads(
                (output_dir / "item_forecasts.json").read_text(
                    encoding="utf-8"
                )
            )
            subtype_payload = json.loads(
                (output_dir / "subtype_forecasts.json").read_text(
                    encoding="utf-8"
                )
            )
            product_payload = json.loads(
                (output_dir / "product_forecasts.json").read_text(
                    encoding="utf-8"
                )
            )
            brand_payload = json.loads(
                (output_dir / "brand_forecasts.json").read_text(
                    encoding="utf-8"
                )
            )
            store_payload = json.loads(
                (output_dir / "store_forecasts.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(item_payload["granularity"], "item")
            self.assertEqual(item_payload["schemaVersion"], "1.5")
            self.assertEqual(item_payload["forecasts"][0]["canonicalItem"], "밀가루")
            self.assertNotIn("subtype", item_payload["forecasts"][0])
            self.assertNotIn(
                "predictionIntervalLower", item_payload["forecasts"][0]
            )
            self.assertNotIn(
                "predictionIntervalUpper", item_payload["forecasts"][0]
            )
            self.assertNotIn(
                "predictionIntervalConfidenceLevel", item_payload["forecasts"][0]
            )
            self.assertEqual(
                item_payload["forecasts"][0]["predictionStrategy"],
                "direct_multi_horizon",
            )
            self.assertEqual(item_payload["forecasts"][0]["dropProbability"], 0.5)
            self.assertEqual(item_payload["forecasts"][0]["signal"], "HOLD")
            self.assertEqual(subtype_payload["granularity"], "subtype")
            self.assertEqual(subtype_payload["schemaVersion"], "1.5")
            self.assertEqual(subtype_payload["forecastHorizon"], 1)
            self.assertEqual(
                subtype_payload["forecasts"][0]["currentUnitPrice"],
                2010.0,
            )
            self.assertEqual(
                subtype_payload["forecasts"][0]["forecastHorizonStep"],
                1,
            )
            self.assertEqual(
                subtype_payload["forecasts"][0]["recursiveInputSource"],
                "observed",
            )
            self.assertNotIn("productName", subtype_payload["forecasts"][0])
            self.assertEqual(
                product_payload["forecasts"][0]["productName"],
                "곰표 밀가루(1kg)",
            )
            self.assertEqual(brand_payload["granularity"], "brand")
            self.assertEqual(
                brand_payload["forecasts"][0]["brandName"],
                "이마트",
            )
            self.assertEqual(store_payload["granularity"], "store")
            self.assertEqual(
                store_payload["forecasts"][0]["storeName"],
                "이마트월계점",
            )
            self.assertEqual(
                store_payload["forecasts"][0]["lastActualUnitPrice"],
                2010.0,
            )
            self.assertNotIn(
                "currentUnitPrice",
                store_payload["forecasts"][0],
            )
            self.assertNotIn(
                "naivePredictedUnitPrice",
                store_payload["forecasts"][0],
            )
            self.assertNotIn(
                "recommendedUnitPrice",
                store_payload["forecasts"][0],
            )

    def test_exports_signals_and_probabilities(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            subtype_input = temp_path / "subtype.csv"
            product_input = temp_path / "product.csv"
            output_dir = temp_path / "backend"

            row_with_signals = COMMON_ROW | {
                "pred_low": "1950.0",
                "pred_high": "2050.0",
                "drop_probability": "0.6542",
                "signal": "WAIT",
                "signal_message": "2주 뒤 가격이 내릴 것으로 보입니다.",
            }
            self.write_csv(subtype_input, [row_with_signals])
            self.write_csv(
                product_input,
                [row_with_signals | {"product_name": "곰표 밀가루(1kg)"}],
            )

            export_backend_forecasts(
                subtype_input,
                product_input,
                output_dir,
            )

            subtype_payload = json.loads(
                (output_dir / "subtype_forecasts.json").read_text(
                    encoding="utf-8"
                )
            )
            item = subtype_payload["forecasts"][0]
            self.assertNotIn("predLow", item)
            self.assertNotIn("predHigh", item)
            self.assertEqual(item["dropProbability"], 0.6542)
            self.assertEqual(item["signal"], "WAIT")
            self.assertEqual(item["signalMessage"], "2주 뒤 가격이 내릴 것으로 보입니다.")


if __name__ == "__main__":
    unittest.main()
