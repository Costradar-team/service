from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = REPO_ROOT / "artifacts" / "ml" / "model_dataset.csv"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "artifacts" / "ml"

METRICS_FILENAME = "baseline_metrics.json"
PREDICTIONS_FILENAME = "baseline_predictions.csv"

SUBTYPE_SERIES_COLUMNS = ("canonical_item", "subtype", "unit_price_basis")
PRODUCT_SERIES_COLUMNS = (
    "canonical_item",
    "subtype",
    "product_name",
    "unit_price_basis",
)
BRAND_SERIES_COLUMNS = (
    "canonical_item",
    "subtype",
    "product_name",
    "brand_name",
    "unit_price_basis",
)
STORE_SERIES_COLUMNS = (
    "canonical_item",
    "subtype",
    "product_name",
    "brand_name",
    "store_name",
    "unit_price_basis",
)
BASE_PREDICTION_COLUMNS = [
    "as_of_date",
    "naive_next_price",
    "rolling_mean_next_price",
    "rolling_window",
]


def resolve_cli_path(path_text: str) -> Path:
    path = Path(path_text).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def direction(value: float, tolerance: float = 1e-9) -> int:
    if value > tolerance:
        return 1
    if value < -tolerance:
        return -1
    return 0


def calculate_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {
            "sample_count": 0,
            "mae": None,
            "smape_percent": None,
            "direction_accuracy": None,
        }

    absolute_errors = []
    smape_values = []
    direction_hits = []
    for record in records:
        actual = record["actual"]
        predicted = record["predicted"]
        previous = record["previous"]
        absolute_errors.append(abs(predicted - actual))
        denominator = abs(predicted) + abs(actual)
        if denominator:
            smape_values.append(200 * abs(predicted - actual) / denominator)
        direction_hits.append(
            direction(predicted - previous) == direction(actual - previous)
        )

    return {
        "sample_count": len(records),
        "mae": round(mean(absolute_errors), 4),
        "smape_percent": (
            round(mean(smape_values), 4) if smape_values else None
        ),
        "direction_accuracy": round(mean(direction_hits), 4),
    }


def evaluate_baselines(
    input_path: Path,
    output_dir: Path,
    rolling_window: int = 4,
    minimum_points: int = 3,
    series_level: str = "subtype",
) -> dict[str, Any]:
    if rolling_window < 1:
        raise ValueError("rolling_window must be at least 1")
    if minimum_points < 2:
        raise ValueError("minimum_points must be at least 2")
    if not input_path.is_file():
        raise FileNotFoundError(f"Model dataset not found: {input_path}")

    if series_level == "subtype":
        series_columns = SUBTYPE_SERIES_COLUMNS
    elif series_level == "product":
        series_columns = PRODUCT_SERIES_COLUMNS
    elif series_level == "brand":
        series_columns = BRAND_SERIES_COLUMNS
    elif series_level == "store":
        series_columns = STORE_SERIES_COLUMNS
    else:
        raise ValueError(
            "series_level must be 'subtype', 'product', 'brand', or 'store'"
        )
    target_column = (
        "actual_unit_price" if series_level == "store" else "median_unit_price"
    )
    current_price_column = (
        "last_actual_unit_price"
        if series_level == "store"
        else "current_median_unit_price"
    )

    series: dict[tuple[str, ...], list[tuple[str, float]]] = defaultdict(list)
    with input_path.open("r", encoding="utf-8-sig", newline="") as source_file:
        reader = csv.DictReader(source_file)
        missing_columns = {target_column, *series_columns}.difference(
            reader.fieldnames or []
        )
        if missing_columns:
            raise ValueError(
                f"Missing {series_level} series columns: {sorted(missing_columns)}"
            )
        for row in reader:
            key = tuple(row[column] for column in series_columns)
            series[key].append(
                (row["survey_date"], float(row[target_column]))
            )

    records_by_model: dict[str, list[dict[str, Any]]] = {
        "naive_last_value": [],
        "rolling_mean": [],
    }
    by_item_records: dict[
        str,
        dict[str, list[dict[str, Any]]],
    ] = defaultdict(lambda: {"naive_last_value": [], "rolling_mean": []})
    latest_predictions = []
    evaluated_series_count = 0
    skipped_series_count = 0

    for key, values in sorted(series.items()):
        values.sort(key=lambda item: item[0])
        if len(values) < minimum_points:
            skipped_series_count += 1
            continue
        evaluated_series_count += 1
        identifiers = dict(zip(series_columns, key))
        canonical_item = identifiers["canonical_item"]

        for index in range(1, len(values)):
            actual = values[index][1]
            previous = values[index - 1][1]
            history = [value for _, value in values[:index]]
            predictions = {
                "naive_last_value": previous,
                "rolling_mean": mean(history[-rolling_window:]),
            }
            for model_name, predicted in predictions.items():
                record = {
                    "actual": actual,
                    "predicted": predicted,
                    "previous": previous,
                }
                records_by_model[model_name].append(record)
                by_item_records[canonical_item][model_name].append(record)

        latest_date, latest_price = values[-1]
        latest_history = [value for _, value in values]
        latest_predictions.append(
            {
                "as_of_date": latest_date,
                **identifiers,
                current_price_column: round(latest_price, 4),
                "naive_next_price": round(latest_price, 4),
                "rolling_mean_next_price": round(
                    mean(latest_history[-rolling_window:]),
                    4,
                ),
                "rolling_window": min(rolling_window, len(latest_history)),
            }
        )

    metrics = {
        "series_level": series_level,
        "series_columns": list(series_columns),
        "rolling_window": rolling_window,
        "minimum_series_points": minimum_points,
        "series_count": len(series),
        "evaluated_series_count": evaluated_series_count,
        "skipped_series_count": skipped_series_count,
        "overall": {
            model: calculate_metrics(records)
            for model, records in records_by_model.items()
        },
        "by_canonical_item": {
            item: {
                model: calculate_metrics(records)
                for model, records in model_records.items()
            }
            for item, model_records in sorted(by_item_records.items())
        },
        "notes": [
            "Metrics use chronological one-step-ahead evaluation.",
            "These baselines are reference points, not production forecasts.",
            {
                "subtype": "Store and product rows are aggregated by subtype.",
                "product": "Store-level rows are aggregated by product.",
                "brand": "Store-level rows are aggregated by product and brand.",
                "store": "Targets are direct observed product prices for each store.",
            }[series_level],
        ],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = output_dir / METRICS_FILENAME
    predictions_path = output_dir / PREDICTIONS_FILENAME
    metrics_path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with predictions_path.open(
        "w", encoding="utf-8-sig", newline=""
    ) as predictions_file:
        writer = csv.DictWriter(
            predictions_file,
            fieldnames=[
                "as_of_date",
                *series_columns,
                current_price_column,
                *BASE_PREDICTION_COLUMNS[1:],
            ],
        )
        writer.writeheader()
        writer.writerows(latest_predictions)

    return metrics | {
        "outputs": {
            "metrics": str(metrics_path),
            "latest_predictions": str(predictions_path),
        }
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate chronological CostRadar price baselines."
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT),
        help="Path to model_dataset.csv.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for metrics and latest predictions.",
    )
    parser.add_argument(
        "--rolling-window",
        type=int,
        default=4,
        help="Number of previous observations used by the rolling baseline.",
    )
    parser.add_argument(
        "--minimum-points",
        type=int,
        default=3,
        help="Minimum observations required to evaluate a price series.",
    )
    parser.add_argument(
        "--series-level",
        choices=["subtype", "product", "brand", "store"],
        default="subtype",
        help="Prediction grain used to identify independent price series.",
    )
    args = parser.parse_args()

    result = evaluate_baselines(
        resolve_cli_path(args.input),
        resolve_cli_path(args.output_dir),
        rolling_window=args.rolling_window,
        minimum_points=args.minimum_points,
        series_level=args.series_level,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
