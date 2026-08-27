from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = REPO_ROOT / "artifacts" / "ml" / "model_dataset.csv"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "artifacts" / "ml" / "model"

SUBTYPE_GROUP_COLUMNS = ["canonical_item", "subtype", "unit_price_basis"]
PRODUCT_GROUP_COLUMNS = [
    "canonical_item",
    "subtype",
    "product_name",
    "unit_price_basis",
]
NUMERIC_FEATURES = [
    "date_ordinal",
    "month_sin",
    "month_cos",
    "lag_1",
    "lag_2",
    "lag_4",
    "rolling_mean_2",
    "rolling_mean_4",
    "rolling_std_4",
]
TARGET_COLUMN = "median_unit_price"
TRAINING_TARGET_COLUMN = "target_change_ratio"

MODEL_FILENAME = "price_model.joblib"
METADATA_FILENAME = "training_report.json"
BACKTEST_FILENAME = "backtest_predictions.csv"
FORECAST_FILENAME = "future_predictions.csv"


def resolve_cli_path(path_text: str) -> Path:
    path = Path(path_text).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def group_columns_for(series_level: str) -> list[str]:
    if series_level == "subtype":
        return SUBTYPE_GROUP_COLUMNS
    if series_level == "product":
        return PRODUCT_GROUP_COLUMNS
    raise ValueError("series_level must be 'subtype' or 'product'")


def smape(actual: np.ndarray, predicted: np.ndarray) -> float:
    denominator = np.abs(actual) + np.abs(predicted)
    valid = denominator > 0
    if not np.any(valid):
        return 0.0
    values = 200 * np.abs(predicted[valid] - actual[valid]) / denominator[valid]
    return float(np.mean(values))


def direction_accuracy(
    actual: np.ndarray,
    predicted: np.ndarray,
    previous: np.ndarray,
) -> float:
    return float(np.mean(np.sign(predicted - previous) == np.sign(actual - previous)))


def calculate_metrics(
    actual: np.ndarray,
    predicted: np.ndarray,
    previous: np.ndarray,
) -> dict[str, float | int]:
    return {
        "sample_count": int(len(actual)),
        "mae": round(float(np.mean(np.abs(predicted - actual))), 4),
        "smape_percent": round(smape(actual, predicted), 4),
        "direction_accuracy": round(
            direction_accuracy(actual, predicted, previous),
            4,
        ),
    }


def build_supervised_dataset(
    model_dataset: pd.DataFrame,
    minimum_series_points: int = 6,
    series_level: str = "subtype",
) -> pd.DataFrame:
    group_columns = group_columns_for(series_level)
    required_columns = {
        "survey_date",
        *group_columns,
        TARGET_COLUMN,
    }
    missing = required_columns.difference(model_dataset.columns)
    if missing:
        raise ValueError(f"Missing model dataset columns: {sorted(missing)}")

    frame = model_dataset.copy()
    frame["survey_date"] = pd.to_datetime(frame["survey_date"], errors="raise")
    frame[TARGET_COLUMN] = pd.to_numeric(frame[TARGET_COLUMN], errors="raise")
    frame = frame.sort_values(group_columns + ["survey_date"]).reset_index(drop=True)

    series_sizes = frame.groupby(group_columns, observed=True)[TARGET_COLUMN].transform(
        "size"
    )
    frame = frame.loc[series_sizes >= minimum_series_points].copy()
    if frame.empty:
        raise ValueError("No series has enough observations for training.")

    grouped = frame.groupby(group_columns, observed=True)[TARGET_COLUMN]
    for lag in (1, 2, 4):
        frame[f"lag_{lag}"] = grouped.shift(lag)

    frame["rolling_mean_2"] = grouped.transform(
        lambda series: series.shift(1).rolling(2).mean()
    )
    frame["rolling_mean_4"] = grouped.transform(
        lambda series: series.shift(1).rolling(4).mean()
    )
    frame["rolling_std_4"] = grouped.transform(
        lambda series: series.shift(1).rolling(4).std(ddof=0)
    )
    frame["date_ordinal"] = frame["survey_date"].map(pd.Timestamp.toordinal)
    month_angle = 2 * math.pi * frame["survey_date"].dt.month / 12
    frame["month_sin"] = np.sin(month_angle)
    frame["month_cos"] = np.cos(month_angle)

    frame = frame.dropna(subset=NUMERIC_FEATURES).reset_index(drop=True)
    frame[TRAINING_TARGET_COLUMN] = (
        frame[TARGET_COLUMN] - frame["lag_1"]
    ) / frame["lag_1"]
    return frame


def split_by_date(
    frame: pd.DataFrame,
    test_fraction: float,
) -> tuple[pd.DataFrame, pd.DataFrame, list[pd.Timestamp]]:
    if not 0 < test_fraction < 0.5:
        raise ValueError("test_fraction must be between 0 and 0.5")

    unique_dates = sorted(frame["survey_date"].unique())
    if len(unique_dates) < 5:
        raise ValueError("At least five model-ready dates are required.")
    test_date_count = max(2, math.ceil(len(unique_dates) * test_fraction))
    test_dates = unique_dates[-test_date_count:]
    cutoff = test_dates[0]
    train = frame.loc[frame["survey_date"] < cutoff].copy()
    test = frame.loc[frame["survey_date"] >= cutoff].copy()
    if train.empty or test.empty:
        raise ValueError("Chronological split produced an empty partition.")
    return train, test, test_dates


def create_estimator(name: str, random_state: int) -> Any:
    if name == "gradient-boosting":
        return GradientBoostingRegressor(
            learning_rate=0.05,
            n_estimators=300,
            max_depth=3,
            min_samples_leaf=5,
            loss="huber",
            random_state=random_state,
        )
    if name == "lightgbm":
        try:
            from lightgbm import LGBMRegressor
        except ImportError as exc:
            raise RuntimeError(
                "LightGBM is not installed. Run: "
                "python -m pip install -r requirements-lightgbm.txt"
            ) from exc
        return LGBMRegressor(
            objective="regression_l1",
            n_estimators=300,
            learning_rate=0.03,
            num_leaves=15,
            min_child_samples=10,
            subsample=0.9,
            colsample_bytree=0.9,
            reg_lambda=1.0,
            random_state=random_state,
            verbosity=-1,
        )
    raise ValueError(f"Unsupported estimator: {name}")


def create_pipeline(
    estimator_name: str,
    random_state: int,
    series_level: str = "subtype",
) -> Pipeline:
    categorical_features = group_columns_for(series_level)
    preprocessor = ColumnTransformer(
        transformers=[
            (
                "categorical",
                OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                categorical_features,
            ),
            ("numeric", "passthrough", NUMERIC_FEATURES),
        ],
        remainder="drop",
    )
    return Pipeline(
        steps=[
            ("preprocessor", preprocessor),
            ("regressor", create_estimator(estimator_name, random_state)),
        ]
    )


def build_future_features(
    raw_model_dataset: pd.DataFrame,
    minimum_series_points: int,
    series_level: str = "subtype",
) -> pd.DataFrame:
    group_columns = group_columns_for(series_level)
    frame = raw_model_dataset.copy()
    frame["survey_date"] = pd.to_datetime(frame["survey_date"], errors="raise")
    frame[TARGET_COLUMN] = pd.to_numeric(frame[TARGET_COLUMN], errors="raise")
    rows = []

    for key, group in frame.groupby(group_columns, observed=True):
        group = group.sort_values("survey_date")
        if len(group) < minimum_series_points:
            continue
        prices = group[TARGET_COLUMN].tolist()
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
        row.update(
            {
                "forecast_date": forecast_date,
                "as_of_date": dates[-1],
                "current_median_unit_price": prices[-1],
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
        rows.append(row)
    return pd.DataFrame(rows)


def train_price_model(
    input_path: Path,
    output_dir: Path,
    estimator_name: str = "gradient-boosting",
    test_fraction: float = 0.2,
    minimum_series_points: int = 6,
    random_state: int = 42,
    series_level: str = "subtype",
) -> dict[str, Any]:
    if not input_path.is_file():
        raise FileNotFoundError(f"Model dataset not found: {input_path}")

    group_columns = group_columns_for(series_level)
    feature_columns = group_columns + NUMERIC_FEATURES
    raw = pd.read_csv(input_path, encoding="utf-8-sig")
    supervised = build_supervised_dataset(
        raw,
        minimum_series_points,
        series_level=series_level,
    )
    train, test, test_dates = split_by_date(supervised, test_fraction)
    pipeline = create_pipeline(estimator_name, random_state, series_level)
    pipeline.fit(train[feature_columns], train[TRAINING_TARGET_COLUMN])

    predicted_change_ratios = pipeline.predict(test[feature_columns])
    model_predictions = test["lag_1"].to_numpy(dtype=float) * (
        1 + predicted_change_ratios
    )
    actual = test[TARGET_COLUMN].to_numpy(dtype=float)
    previous = test["lag_1"].to_numpy(dtype=float)
    naive_predictions = previous.copy()

    metrics = {
        "model": calculate_metrics(actual, model_predictions, previous),
        "naive_last_value": calculate_metrics(
            actual,
            naive_predictions,
            previous,
        ),
    }
    model_beats_naive = (
        metrics["model"]["smape_percent"]
        < metrics["naive_last_value"]["smape_percent"]
    )
    recommended_forecaster = (
        "trained_model" if model_beats_naive else "naive_last_value"
    )
    by_item = {}
    for item in sorted(test["canonical_item"].unique()):
        mask = test["canonical_item"].to_numpy() == item
        by_item[item] = {
            "model": calculate_metrics(
                actual[mask], model_predictions[mask], previous[mask]
            ),
            "naive_last_value": calculate_metrics(
                actual[mask], naive_predictions[mask], previous[mask]
            ),
        }

    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / MODEL_FILENAME
    metadata_path = output_dir / METADATA_FILENAME
    backtest_path = output_dir / BACKTEST_FILENAME
    forecast_path = output_dir / FORECAST_FILENAME
    joblib.dump(
        {
            "pipeline": pipeline,
            "series_level": series_level,
            "group_columns": group_columns,
            "feature_columns": feature_columns,
            "prediction_target": TRAINING_TARGET_COLUMN,
            "price_reconstruction": "lag_1 * (1 + predicted_change_ratio)",
        },
        model_path,
    )

    backtest = test[
        ["survey_date", *group_columns, TARGET_COLUMN, "lag_1"]
    ].copy()
    backtest["model_prediction"] = np.round(model_predictions, 4)
    backtest["naive_prediction"] = np.round(naive_predictions, 4)
    backtest["model_absolute_error"] = np.round(
        np.abs(model_predictions - actual),
        4,
    )
    backtest.to_csv(backtest_path, index=False, encoding="utf-8-sig")

    future = build_future_features(
        raw,
        minimum_series_points,
        series_level=series_level,
    )
    if future.empty:
        raise ValueError("No series is eligible for a future forecast.")
    future_change_ratios = pipeline.predict(future[feature_columns])
    future_predictions = future["lag_1"].to_numpy(dtype=float) * (
        1 + future_change_ratios
    )
    future_output = future[
        [
            "forecast_date",
            "as_of_date",
            *group_columns,
            "current_median_unit_price",
        ]
    ].copy()
    future_output["model_predicted_unit_price"] = np.round(future_predictions, 4)
    future_output["model_predicted_change_percent"] = np.round(
        (future_predictions - future["current_median_unit_price"].to_numpy())
        / future["current_median_unit_price"].to_numpy()
        * 100,
        4,
    )
    future_output["naive_predicted_unit_price"] = future_output[
        "current_median_unit_price"
    ]
    if model_beats_naive:
        future_output["recommended_unit_price"] = future_output[
            "model_predicted_unit_price"
        ]
    else:
        future_output["recommended_unit_price"] = future_output[
            "naive_predicted_unit_price"
        ]
    future_output["recommended_forecaster"] = recommended_forecaster
    future_output.to_csv(forecast_path, index=False, encoding="utf-8-sig")

    report = {
        "series_level": series_level,
        "group_columns": group_columns,
        "estimator": estimator_name,
        "random_state": random_state,
        "input_row_count": int(len(raw)),
        "supervised_row_count": int(len(supervised)),
        "train_row_count": int(len(train)),
        "test_row_count": int(len(test)),
        "train_date_min": train["survey_date"].min().date().isoformat(),
        "train_date_max": train["survey_date"].max().date().isoformat(),
        "test_dates": [pd.Timestamp(value).date().isoformat() for value in test_dates],
        "feature_columns": feature_columns,
        "source_target_column": TARGET_COLUMN,
        "training_target_column": TRAINING_TARGET_COLUMN,
        "metrics": metrics,
        "metrics_by_canonical_item": by_item,
        "model_beats_naive_by_smape": model_beats_naive,
        "recommended_forecaster": recommended_forecaster,
        "limitations": [
            "The dataset has few unique survey dates, so this is an MVP prototype.",
            "Backtesting is chronological and one-step-ahead, not a multi-step production forecast.",
            "Predictions must not be presented as reliable short-term forecasts without more frequent data.",
        ],
        "outputs": {
            "model": str(model_path),
            "report": str(metadata_path),
            "backtest_predictions": str(backtest_path),
            "future_predictions": str(forecast_path),
        },
    }
    metadata_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Train and chronologically evaluate a CostRadar global price model."
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT),
        help="Path to model_dataset.csv.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for the trained model and evaluation artifacts.",
    )
    parser.add_argument(
        "--estimator",
        choices=["gradient-boosting", "lightgbm"],
        default="gradient-boosting",
    )
    parser.add_argument(
        "--series-level",
        choices=["subtype", "product"],
        default="subtype",
        help="Prediction grain used to identify independent price series.",
    )
    parser.add_argument("--test-fraction", type=float, default=0.2)
    parser.add_argument("--minimum-series-points", type=int, default=6)
    parser.add_argument("--random-state", type=int, default=42)
    args = parser.parse_args()

    report = train_price_model(
        resolve_cli_path(args.input),
        resolve_cli_path(args.output_dir),
        estimator_name=args.estimator,
        test_fraction=args.test_fraction,
        minimum_series_points=args.minimum_series_points,
        random_state=args.random_state,
        series_level=args.series_level,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
