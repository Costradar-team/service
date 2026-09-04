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
DEFAULT_BRAND_INPUT = (
    REPO_ROOT
    / "artifacts"
    / "ml"
    / "brand"
    / "model"
    / "future_predictions.csv"
)
DEFAULT_STORE_INPUT = (
    REPO_ROOT
    / "artifacts"
    / "ml"
    / "store"
    / "model"
    / "future_predictions.csv"
)
DEFAULT_OUTPUT_DIR = REPO_ROOT / "artifacts" / "ml" / "backend"

SUBTYPE_OUTPUT_FILENAME = "subtype_forecasts.json"
PRODUCT_OUTPUT_FILENAME = "product_forecasts.json"
BRAND_OUTPUT_FILENAME = "brand_forecasts.json"
STORE_OUTPUT_FILENAME = "store_forecasts.json"
SUMMARY_FILENAME = "export_summary.json"

PRICE_COLUMNS = [
    "model_predicted_unit_price",
    "model_predicted_change_percent",
]
OPTIONAL_COMMON_COLUMNS = [
    "forecast_horizon_step",
    "recursive_input_unit_price",
    "recursive_input_source",
    "model_predicted_step_change_percent",
    "pred_low",
    "pred_high",
    "drop_probability",
    "signal",
    "signal_message",
]
NUMERIC_COLUMNS = {
    "current_median_unit_price",
    "last_actual_unit_price",
    "model_predicted_unit_price",
    "model_predicted_change_percent",
    "recursive_input_unit_price",
    "model_predicted_step_change_percent",
    "pred_low",
    "pred_high",
    "drop_probability",
}
INTEGER_COLUMNS = {"forecast_horizon_step"}
JSON_FIELD_NAMES = {
    "forecast_date": "forecastDate",
    "forecast_horizon_step": "forecastHorizonStep",
    "as_of_date": "asOfDate",
    "canonical_item": "canonicalItem",
    "subtype": "subtype",
    "product_name": "productName",
    "brand_name": "brandName",
    "store_name": "storeName",
    "unit_price_basis": "unitPriceBasis",
    "current_median_unit_price": "currentUnitPrice",
    "last_actual_unit_price": "lastActualUnitPrice",
    "model_predicted_unit_price": "modelPredictedUnitPrice",
    "model_predicted_change_percent": "modelPredictedChangePercent",
    "recursive_input_unit_price": "recursiveInputUnitPrice",
    "recursive_input_source": "recursiveInputSource",
    "model_predicted_step_change_percent": "modelPredictedStepChangePercent",
    "pred_low": "predLow",
    "pred_high": "predHigh",
    "drop_probability": "dropProbability",
    "signal": "signal",
    "signal_message": "signalMessage",
}


def resolve_cli_path(path_text: str) -> Path:
    path = Path(path_text).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def load_forecasts(path: Path, granularity: str) -> list[dict[str, Any]]:
    if granularity not in {"subtype", "product", "brand", "store"}:
        raise ValueError(
            "granularity must be 'subtype', 'product', 'brand', or 'store'"
        )
    if not path.is_file():
        raise FileNotFoundError(f"Forecast CSV not found: {path}")

    columns = [
        "forecast_date",
        "as_of_date",
        "canonical_item",
        "subtype",
    ]
    if granularity in {"product", "brand", "store"}:
        columns.append("product_name")
    if granularity in {"brand", "store"}:
        columns.append("brand_name")
    if granularity == "store":
        columns.append("store_name")
    columns.extend(
        [
            "unit_price_basis",
            (
                "last_actual_unit_price"
                if granularity == "store"
                else "current_median_unit_price"
            ),
            *PRICE_COLUMNS,
        ]
    )

    forecasts = []
    with path.open("r", encoding="utf-8-sig", newline="") as source_file:
        reader = csv.DictReader(source_file)
        missing_columns = set(columns).difference(reader.fieldnames or [])
        if missing_columns:
            raise ValueError(
                f"Missing {granularity} forecast columns: {sorted(missing_columns)}"
            )
        available_optional_columns = [
            column
            for column in OPTIONAL_COMMON_COLUMNS
            if column in (reader.fieldnames or [])
        ]

        for row in reader:
            forecast = {}
            for column in [*columns, *available_optional_columns]:
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
    granularity: str,
    forecasts: list[dict[str, Any]],
) -> None:
    forecast_horizon = max(
        (int(forecast.get("forecastHorizonStep", 1)) for forecast in forecasts),
        default=0,
    )
    payload = {
        "schemaVersion": "1.3",
        "granularity": granularity,
        "forecastHorizon": forecast_horizon,
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
    brand_input: Path | None = None,
    store_input: Path | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    subtype_output = output_dir / SUBTYPE_OUTPUT_FILENAME
    product_output = output_dir / PRODUCT_OUTPUT_FILENAME
    brand_output = output_dir / BRAND_OUTPUT_FILENAME
    store_output = output_dir / STORE_OUTPUT_FILENAME
    summary_output = output_dir / SUMMARY_FILENAME

    subtype_forecasts = load_forecasts(subtype_input, "subtype")
    product_forecasts = load_forecasts(product_input, "product")
    brand_forecasts = (
        load_forecasts(brand_input, "brand") if brand_input is not None else []
    )
    store_forecasts = (
        load_forecasts(store_input, "store") if store_input is not None else []
    )
    write_payload(subtype_output, "subtype", subtype_forecasts)
    write_payload(product_output, "product", product_forecasts)
    if brand_input is not None:
        write_payload(brand_output, "brand", brand_forecasts)
    if store_input is not None:
        write_payload(store_output, "store", store_forecasts)

    summary = {
        "schema_version": "1.3",
        "subtype_forecast_count": len(subtype_forecasts),
        "product_forecast_count": len(product_forecasts),
        "brand_forecast_count": len(brand_forecasts),
        "store_forecast_count": len(store_forecasts),
        "outputs": {
            "subtype": str(subtype_output),
            "product": str(product_output),
            **({"brand": str(brand_output)} if brand_input is not None else {}),
            **({"store": str(store_output)} if store_input is not None else {}),
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
    parser.add_argument(
        "--brand-input",
        default=str(DEFAULT_BRAND_INPUT),
        help="Path to brand future_predictions.csv.",
    )
    parser.add_argument(
        "--store-input",
        default=str(DEFAULT_STORE_INPUT),
        help="Path to direct store future_predictions.csv.",
    )
    args = parser.parse_args()

    summary = export_backend_forecasts(
        resolve_cli_path(args.subtype_input),
        resolve_cli_path(args.product_input),
        resolve_cli_path(args.output_dir),
        brand_input=resolve_cli_path(args.brand_input),
        store_input=resolve_cli_path(args.store_input),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
