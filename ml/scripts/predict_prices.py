from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

try:
    from .external_market_features import EXTERNAL_FEATURE_COLUMNS
    from .price_signals import enrich_predictions_with_signals
except ImportError:
    from external_market_features import EXTERNAL_FEATURE_COLUMNS  # type: ignore[no-redef]
    from price_signals import enrich_predictions_with_signals  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = REPO_ROOT / "artifacts" / "ml" / "model_dataset.csv"
DEFAULT_MODEL = REPO_ROOT / "artifacts" / "ml" / "model" / "price_model.joblib"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "artifacts" / "ml" / "model"

FORECAST_FILENAME = "future_predictions.csv"
REPORT_FILENAME = "prediction_report.json"

SUBTYPE_GROUP_COLUMNS = ["canonical_item", "subtype", "unit_price_basis"]
ITEM_GROUP_COLUMNS = ["canonical_item", "unit_price_basis"]
PRODUCT_GROUP_COLUMNS = [
    "canonical_item",
    "subtype",
    "product_name",
    "unit_price_basis",
]
BRAND_GROUP_COLUMNS = [
    "canonical_item",
    "subtype",
    "product_name",
    "brand_name",
    "unit_price_basis",
]
STORE_GROUP_COLUMNS = [
    "canonical_item",
    "subtype",
    "product_name",
    "brand_name",
    "store_name",
    "unit_price_basis",
]

REQUIRED_MODEL_KEYS = {
    "pipeline",
    "series_level",
    "group_columns",
    "feature_columns",
    "minimum_series_points",
    "trained_through_date",
}


def group_columns_for(series_level: str) -> list[str]:
    if series_level == "item":
        return ITEM_GROUP_COLUMNS
    if series_level == "subtype":
        return SUBTYPE_GROUP_COLUMNS
    if series_level == "product":
        return PRODUCT_GROUP_COLUMNS
    if series_level == "brand":
        return BRAND_GROUP_COLUMNS
    if series_level == "store":
        return STORE_GROUP_COLUMNS
    raise ValueError(
        "series_level must be 'item', 'subtype', 'product', 'brand', or 'store'"
    )


def target_column_for(series_level: str) -> str:
    if series_level == "store":
        return "actual_unit_price"
    group_columns_for(series_level)
    return "median_unit_price"


def current_price_column_for(series_level: str) -> str:
    if series_level == "store":
        return "last_actual_unit_price"
    group_columns_for(series_level)
    return "current_median_unit_price"


def resolve_cli_path(path_text: str) -> Path:
    path = Path(path_text).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def build_future_features(
    raw_model_dataset: pd.DataFrame,
    minimum_series_points: int,
    series_level: str = "subtype",
) -> pd.DataFrame:
    group_columns = group_columns_for(series_level)
    target_column = target_column_for(series_level)
    current_price_column = current_price_column_for(series_level)
    required_columns = {"survey_date", target_column, *group_columns}
    missing = required_columns.difference(raw_model_dataset.columns)
    if missing:
        raise ValueError(f"Missing model dataset columns: {sorted(missing)}")

    frame = raw_model_dataset.copy()
    frame["survey_date"] = pd.to_datetime(frame["survey_date"], errors="raise")
    frame[target_column] = pd.to_numeric(frame[target_column], errors="raise")
    for column in EXTERNAL_FEATURE_COLUMNS:
        if column not in frame.columns:
            frame[column] = 0.0
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    rows = []

    for key, group in frame.groupby(group_columns, observed=True):
        group = group.sort_values("survey_date")
        if len(group) < minimum_series_points:
            continue
        prices = group[target_column].tolist()
        dates = group["survey_date"].tolist()
        intervals = [
            (dates[index] - dates[index - 1]).days
            for index in range(1, len(dates))
            if (dates[index] - dates[index - 1]).days > 0
        ]
        next_interval_days = int(np.median(intervals)) if intervals else 14
        forecast_date = dates[-1] + pd.Timedelta(days=next_interval_days)
        month_angle = 2 * math.pi * forecast_date.month / 12
        row = dict(zip(group_columns, key))
        latest = group.iloc[-1]
        row.update(
            {
                "forecast_date": forecast_date,
                "as_of_date": dates[-1],
                current_price_column: prices[-1],
                "date_ordinal": forecast_date.toordinal(),
                "month_sin": math.sin(month_angle),
                "month_cos": math.cos(month_angle),
                "lag_1": prices[-1],
                "lag_2": prices[-2],
                "lag_4": prices[-4],
                "rolling_mean_2": float(np.mean(prices[-2:])),
                "rolling_mean_4": float(np.mean(prices[-4:])),
                "rolling_std_4": float(np.std(prices[-4:])),
            }
        )
        row.update(
            {column: float(latest[column]) for column in EXTERNAL_FEATURE_COLUMNS}
        )
        if row["external_market_available"] > 0:
            row["external_market_age_days"] += next_interval_days
        rows.append(row)
    return pd.DataFrame(rows)


def load_model_bundle(model_path: Path, series_level: str) -> dict[str, Any]:
    if not model_path.is_file():
        raise FileNotFoundError(
            f"Trained model not found: {model_path}. Run model training first."
        )
    bundle = joblib.load(model_path)
    if not isinstance(bundle, dict):
        raise ValueError("Unsupported trained model format.")
    missing = REQUIRED_MODEL_KEYS.difference(bundle)
    if missing:
        raise ValueError(
            "Trained model is missing prediction metadata: "
            f"{sorted(missing)}. Retrain the model once."
        )
    if bundle["series_level"] != series_level:
        raise ValueError(
            f"Model series level is {bundle['series_level']}, not {series_level}."
        )
    expected_group_columns = group_columns_for(series_level)
    if bundle["group_columns"] != expected_group_columns:
        raise ValueError("Model group columns do not match the current predictor.")
    return bundle


def predict_prices(
    input_path: Path,
    model_path: Path,
    output_dir: Path,
    series_level: str = "subtype",
    forecast_horizon: int = 1,
) -> dict[str, Any]:
    if forecast_horizon < 1:
        raise ValueError("forecast_horizon must be at least 1")
    if not input_path.is_file():
        raise FileNotFoundError(f"Model dataset not found: {input_path}")

    bundle = load_model_bundle(model_path, series_level)
    raw = pd.read_csv(input_path, encoding="utf-8-sig")
    working_history = raw.copy()
    first_future = build_future_features(
        raw,
        int(bundle["minimum_series_points"]),
        series_level=series_level,
    )
    if first_future.empty:
        raise ValueError("No series is eligible for a future forecast.")

    feature_columns = list(bundle["feature_columns"])
    missing_features = set(feature_columns).difference(first_future.columns)
    if missing_features:
        raise ValueError(f"Missing prediction features: {sorted(missing_features)}")

    group_columns = group_columns_for(series_level)
    target_column = target_column_for(series_level)
    current_price_column = current_price_column_for(series_level)
    actual_baseline = first_future[
        [*group_columns, "as_of_date", current_price_column]
    ].rename(columns={current_price_column: "baseline_unit_price"})
    output_frames = []

    for horizon_step in range(1, forecast_horizon + 1):
        future = build_future_features(
            working_history,
            int(bundle["minimum_series_points"]),
            series_level=series_level,
        )
        missing_features = set(feature_columns).difference(future.columns)
        if missing_features:
            raise ValueError(
                f"Missing prediction features: {sorted(missing_features)}"
            )

        predicted_change_ratios = bundle["pipeline"].predict(
            future[feature_columns]
        )
        recursive_input_prices = future[
            current_price_column
        ].to_numpy(dtype=float)
        model_predictions = recursive_input_prices * (
            1 + predicted_change_ratios
        )

        step_output = future[
            ["forecast_date", *group_columns, current_price_column]
        ].rename(
            columns={
                current_price_column: "recursive_input_unit_price"
            }
        )
        step_output = step_output.merge(
            actual_baseline,
            on=group_columns,
            how="left",
            validate="one_to_one",
        )
        step_output.insert(1, "forecast_horizon_step", horizon_step)
        step_output["recursive_input_source"] = (
            "observed" if horizon_step == 1 else "model_prediction"
        )
        step_output["model_predicted_unit_price"] = np.round(
            model_predictions,
            4,
        )
        step_output["model_predicted_step_change_percent"] = np.round(
            (model_predictions - recursive_input_prices)
            / recursive_input_prices
            * 100,
            4,
        )
        actual_prices = step_output["baseline_unit_price"].to_numpy(dtype=float)
        step_output["model_predicted_change_percent"] = np.round(
            (model_predictions - actual_prices) / actual_prices * 100,
            4,
        )
        step_output = step_output.rename(
            columns={"baseline_unit_price": current_price_column}
        )
        output_frames.append(step_output)

        predicted_history_rows = future[
            ["forecast_date", *group_columns, *EXTERNAL_FEATURE_COLUMNS]
        ].rename(columns={"forecast_date": "survey_date"})
        predicted_history_rows[target_column] = model_predictions
        working_history = pd.concat(
            [working_history, predicted_history_rows],
            ignore_index=True,
            sort=False,
        )

    output = pd.concat(output_frames, ignore_index=True)
    output = enrich_predictions_with_signals(
        output,
        history_df=raw,
        series_level=series_level,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    forecast_path = output_dir / FORECAST_FILENAME
    report_path = output_dir / REPORT_FILENAME
    output.to_csv(forecast_path, index=False, encoding="utf-8-sig")

    report = {
        "series_level": series_level,
        "input_row_count": int(len(raw)),
        "forecast_series_count": int(len(first_future)),
        "forecast_horizon": forecast_horizon,
        "forecast_count": int(len(output)),
        "model_trained_through_date": str(bundle["trained_through_date"]),
        "prediction_as_of_date": pd.to_datetime(output["as_of_date"])
        .max()
        .date()
        .isoformat(),
        "outputs": {
            "future_predictions": str(forecast_path),
            "report": str(report_path),
        },
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Predict prices with an already trained CostRadar model."
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT),
        help="Path to the latest model_dataset.csv.",
    )
    parser.add_argument(
        "--model",
        default=str(DEFAULT_MODEL),
        help="Path to price_model.joblib.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for prediction artifacts.",
    )
    parser.add_argument(
        "--series-level",
        choices=["item", "subtype", "product", "brand", "store"],
        default="subtype",
    )
    parser.add_argument(
        "--forecast-horizon",
        type=int,
        default=1,
        help="Number of recursive future survey points to predict.",
    )
    args = parser.parse_args()

    report = predict_prices(
        resolve_cli_path(args.input),
        resolve_cli_path(args.model),
        resolve_cli_path(args.output_dir),
        series_level=args.series_level,
        forecast_horizon=args.forecast_horizon,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
