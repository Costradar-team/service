from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from ml.scripts.export_backend_forecasts import export_backend_forecasts


class ExportBackendForecastsTest(unittest.TestCase):
    def test_exports_only_item_price_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            item_input = root / "item.csv"
            output_dir = root / "backend"
            row = {
                "forecast_date": "2026-08-07",
                "forecast_horizon_step": "1",
                "as_of_date": "2026-07-24",
                "canonical_item": "밀가루",
                "unit_price_basis": "KRW/kg",
                "current_median_unit_price": "2080",
                "model_predicted_unit_price": "2075",
                "model_predicted_change_percent": "-0.2404",
                "prediction_strategy": "direct_multi_horizon",
                "forecast_method": "direct_model",
                "model_weight": "1",
                "selected_feature_set": "retail_history",
                "signal": "WAIT",
                "drop_probability": "0.72",
            }
            with item_input.open(
                "w", encoding="utf-8-sig", newline=""
            ) as output_file:
                writer = csv.DictWriter(output_file, fieldnames=list(row))
                writer.writeheader()
                writer.writerow(row)

            summary = export_backend_forecasts(item_input, output_dir)

            self.assertEqual(summary["item_forecast_count"], 1)
            self.assertEqual(set(summary["outputs"]), {"item", "summary"})
            self.assertEqual(
                {path.name for path in output_dir.iterdir()},
                {"item_forecasts.json", "export_summary.json"},
            )
            payload = json.loads(
                (output_dir / "item_forecasts.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["granularity"], "item")
            self.assertEqual(payload["forecastCount"], 1)
            forecast = payload["forecasts"][0]
            self.assertEqual(forecast["canonicalItem"], "밀가루")
            self.assertEqual(forecast["modelPredictedUnitPrice"], 2075.0)
            self.assertNotIn("subtype", forecast)
            self.assertNotIn("productName", forecast)
            self.assertNotIn("brandName", forecast)
            self.assertNotIn("storeName", forecast)
            self.assertNotIn("signal", forecast)
            self.assertNotIn("dropProbability", forecast)


if __name__ == "__main__":
    unittest.main()
