from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from ml.scripts.export_backend_forecasts import export_backend_forecasts


COMMON_ROW = {
    "forecast_date": "2026-08-07",
    "as_of_date": "2026-07-24",
    "canonical_item": "밀가루",
    "subtype": "중력분",
    "unit_price_basis": "KRW/kg",
    "current_median_unit_price": "2010",
    "model_predicted_unit_price": "1999.1595",
    "model_predicted_change_percent": "-0.5393",
    "naive_predicted_unit_price": "2010",
    "recommended_unit_price": "2010",
    "recommended_forecaster": "naive_last_value",
}


class ExportBackendForecastsTest(unittest.TestCase):
    def write_csv(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def test_exports_subtype_and_product_api_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            subtype_input = temp_path / "subtype.csv"
            product_input = temp_path / "product.csv"
            output_dir = temp_path / "backend"
            self.write_csv(subtype_input, [COMMON_ROW])
            self.write_csv(
                product_input,
                [COMMON_ROW | {"product_name": "곰표 밀가루(1kg)"}],
            )

            summary = export_backend_forecasts(
                subtype_input,
                product_input,
                output_dir,
            )

            self.assertEqual(summary["subtype_forecast_count"], 1)
            self.assertEqual(summary["product_forecast_count"], 1)
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
            self.assertEqual(subtype_payload["granularity"], "subtype")
            self.assertEqual(
                subtype_payload["forecasts"][0]["currentUnitPrice"],
                2010.0,
            )
            self.assertNotIn("productName", subtype_payload["forecasts"][0])
            self.assertEqual(
                product_payload["forecasts"][0]["productName"],
                "곰표 밀가루(1kg)",
            )


if __name__ == "__main__":
    unittest.main()
