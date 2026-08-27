from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SUBTYPE_INPUT = REPO_ROOT / "artifacts" / "ml" / "model" / "future_predictions.csv"
DEFAULT_PRODUCT_INPUT = (
    REPO_ROOT
    / "artifacts"
    / "ml"
    / "product"
    / "model"
    / "future_predictions.csv"
)
DEFAULT_OUTPUT_DIR = REPO_ROOT / "artifacts" / "ml" / "backend"

SUBTYPE_OUTPUT_FILENAME = "subtype_forecasts.json"
PRODUCT_OUTPUT_FILENAME = "product_forecasts.json"
SUMMARY_FILENAME = "export_summary.json"

COMMON_COLUMNS = [
    "forecast_date",
    "as_of_date",
    "canonical_item",
    "subtype",
    "unit_price_basis",
    "current_median_unit_price",
    "model_predicted_unit_price",
    "model_predicted_change_percent",
    "naive_predicted_unit_price",
    "recommended_unit_price",
    "recommended_forecaster",
]
NUMERIC_COLUMNS = {
    "current_median_unit_price",
    "model_predicted_unit_price",
    "model_predicted_change_percent",
    "naive_predicted_unit_price",
    "recommended_unit_price",
}
JSON_FIELD_NAMES = {
    "forecast_date": "forecastDate",
    "as_of_date": "asOfDate",
    "canonical_item": "canonicalItem",
    "subtype": "subtype",
    "product_name": "productName",
    "unit_price_basis": "unitPriceBasis",
    "current_median_unit_price": "currentUnitPrice",
    "model_predicted_unit_price": "modelPredictedUnitPrice",
    "model_predicted_change_percent": "modelPredictedChangePercent",
    "naive_predicted_unit_price": "naivePredictedUnitPrice",
    "recommended_unit_price": "recommendedUnitPrice",
    "recommended_forecaster": "recommendedForecaster",
}


def resolve_cli_path(path_text: str) -> Path:
    path = Path(path_text).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def load_forecasts(path: Path, granularity: str) -> list[dict[str, Any]]:
    if granularity not in {"subtype", "product"}:
        raise ValueError("granularity must be 'subtype' or 'product'")
    if not path.is_file():
        raise FileNotFoundError(f"Forecast CSV not found: {path}")

    columns = [*COMMON_COLUMNS]
    if granularity == "product":
        columns.insert(4, "product_name")

    forecasts = []
    with path.open("r", encoding="utf-8-sig", newline="") as source_file:
        reader = csv.DictReader(source_file)
        missing_columns = set(columns).difference(reader.fieldnames or [])
        if missing_columns:
            raise ValueError(
                f"Missing {granularity} forecast columns: {sorted(missing_columns)}"
            )

        for row in reader:
            forecast = {}
            for column in columns:
                value: Any = row[column]
                if column in NUMERIC_COLUMNS:
                    value = float(value)
                forecast[JSON_FIELD_NAMES[column]] = value
            forecasts.append(forecast)

    return forecasts


def write_payload(
    output_path: Path,
    granularity: str,
    forecasts: list[dict[str, Any]],
) -> None:
    payload = {
        "schemaVersion": "1.0",
        "granularity": granularity,
        "forecastCount": len(forecasts),
        "forecasts": forecasts,
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def export_backend_forecasts(
    subtype_input: Path,
    product_input: Path,
    output_dir: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    subtype_output = output_dir / SUBTYPE_OUTPUT_FILENAME
    product_output = output_dir / PRODUCT_OUTPUT_FILENAME
    summary_output = output_dir / SUMMARY_FILENAME

    subtype_forecasts = load_forecasts(subtype_input, "subtype")
    product_forecasts = load_forecasts(product_input, "product")
    write_payload(subtype_output, "subtype", subtype_forecasts)
    write_payload(product_output, "product", product_forecasts)

    summary = {
        "schema_version": "1.0",
        "subtype_forecast_count": len(subtype_forecasts),
        "product_forecast_count": len(product_forecasts),
        "outputs": {
            "subtype": str(subtype_output),
            "product": str(product_output),
            "summary": str(summary_output),
        },
    }
    summary_output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export subtype and product forecasts as backend API payloads."
    )
    parser.add_argument(
        "--subtype-input",
        default=str(DEFAULT_SUBTYPE_INPUT),
        help="Path to subtype future_predictions.csv.",
    )
    parser.add_argument(
        "--product-input",
        default=str(DEFAULT_PRODUCT_INPUT),
        help="Path to product future_predictions.csv.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for backend JSON payloads.",
    )
    args = parser.parse_args()

    summary = export_backend_forecasts(
        resolve_cli_path(args.subtype_input),
        resolve_cli_path(args.product_input),
        resolve_cli_path(args.output_dir),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
