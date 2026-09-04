from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ITEM_INPUT = (
    REPO_ROOT / "artifacts" / "ml" / "item" / "model" / "future_predictions.csv"
)
DEFAULT_OUTPUT_DIR = REPO_ROOT / "artifacts" / "ml" / "backend"

ITEM_OUTPUT_FILENAME = "item_forecasts.json"
SUMMARY_FILENAME = "export_summary.json"

REQUIRED_COLUMNS = [
    "forecast_date",
    "as_of_date",
    "canonical_item",
    "unit_price_basis",
    "current_median_unit_price",
    "model_predicted_unit_price",
    "model_predicted_change_percent",
]
OPTIONAL_COLUMNS = [
    "forecast_horizon_step",
    "prediction_strategy",
    "forecast_method",
    "model_weight",
    "selected_feature_set",
]
NUMERIC_COLUMNS = {
    "current_median_unit_price",
    "model_predicted_unit_price",
    "model_predicted_change_percent",
    "model_weight",
}
INTEGER_COLUMNS = {"forecast_horizon_step"}
JSON_FIELD_NAMES = {
    "forecast_date": "forecastDate",
    "forecast_horizon_step": "forecastHorizonStep",
    "as_of_date": "asOfDate",
    "canonical_item": "canonicalItem",
    "unit_price_basis": "unitPriceBasis",
    "current_median_unit_price": "currentUnitPrice",
    "model_predicted_unit_price": "modelPredictedUnitPrice",
    "model_predicted_change_percent": "modelPredictedChangePercent",
    "prediction_strategy": "predictionStrategy",
    "forecast_method": "forecastMethod",
    "model_weight": "modelWeight",
    "selected_feature_set": "selectedFeatureSet",
}


def resolve_cli_path(path_text: str) -> Path:
    path = Path(path_text).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def load_item_forecasts(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"Item forecast CSV not found: {path}")

    forecasts: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as source_file:
        reader = csv.DictReader(source_file)
        fieldnames = reader.fieldnames or []
        missing_columns = set(REQUIRED_COLUMNS).difference(fieldnames)
        if missing_columns:
            raise ValueError(
                f"Missing item forecast columns: {sorted(missing_columns)}"
            )
        available_columns = [
            *REQUIRED_COLUMNS,
            *[column for column in OPTIONAL_COLUMNS if column in fieldnames],
        ]
        for row in reader:
            forecast: dict[str, Any] = {}
            for column in available_columns:
                value: Any = row[column]
                if column in NUMERIC_COLUMNS:
                    value = float(value)
                elif column in INTEGER_COLUMNS:
                    value = int(value)
                forecast[JSON_FIELD_NAMES[column]] = value
            forecasts.append(forecast)
    return forecasts


def write_payload(
    output_path: Path,
    forecasts: list[dict[str, Any]],
) -> None:
    forecast_horizon = max(
        (int(forecast.get("forecastHorizonStep", 1)) for forecast in forecasts),
        default=0,
    )
    payload = {
        "schemaVersion": "1.5",
        "granularity": "item",
        "forecastHorizon": forecast_horizon,
        "forecastCount": len(forecasts),
        "forecasts": forecasts,
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def export_backend_forecasts(
    item_input: Path,
    output_dir: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    item_output = output_dir / ITEM_OUTPUT_FILENAME
    summary_output = output_dir / SUMMARY_FILENAME
    item_forecasts = load_item_forecasts(item_input)
    write_payload(item_output, item_forecasts)

    summary = {
        "schema_version": "1.5",
        "item_forecast_count": len(item_forecasts),
        "outputs": {
            "item": str(item_output),
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
        description="Export the item forecast as a backend API payload."
    )
    parser.add_argument(
        "--item-input",
        default=str(DEFAULT_ITEM_INPUT),
        help="Path to item-level future_predictions.csv.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for the backend JSON payload.",
    )
    args = parser.parse_args()

    summary = export_backend_forecasts(
        resolve_cli_path(args.item_input),
        resolve_cli_path(args.output_dir),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
