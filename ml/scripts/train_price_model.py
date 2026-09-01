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
from sklearn.preprocessing import OneHotEncoder, TargetEncoder

try:
    from .external_market_features import EXTERNAL_FEATURE_COLUMNS
except ImportError:
    from external_market_features import EXTERNAL_FEATURE_COLUMNS  # type: ignore[no-redef]


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
BASE_NUMERIC_FEATURES = [
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
NUMERIC_FEATURES = BASE_NUMERIC_FEATURES + EXTERNAL_FEATURE_COLUMNS
MEDIAN_TARGET_COLUMN = "median_unit_price"
STORE_TARGET_COLUMN = "actual_unit_price"
TRAINING_TARGET_COLUMN = "target_change_ratio"

MODEL_FILENAME = "price_model.joblib"
METADATA_FILENAME = "training_report.json"
BACKTEST_FILENAME = "backtest_predictions.csv"


def resolve_cli_path(path_text: str) -> Path:
    path = Path(path_text).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def group_columns_for(series_level: str) -> list[str]:
    if series_level == "subtype":
        return SUBTYPE_GROUP_COLUMNS
    if series_level == "product":
        return PRODUCT_GROUP_COLUMNS
    if series_level == "brand":
        return BRAND_GROUP_COLUMNS
    if series_level == "store":
        return STORE_GROUP_COLUMNS
    raise ValueError(
        "series_level must be 'subtype', 'product', 'brand', or 'store'"
    )


def target_column_for(series_level: str) -> str:
    if series_level == "store":
        return STORE_TARGET_COLUMN
    group_columns_for(series_level)
    return MEDIAN_TARGET_COLUMN


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
    target_column = target_column_for(series_level)
    required_columns = {
        "survey_date",
        *group_columns,
        target_column,
    }
    missing = required_columns.difference(model_dataset.columns)
    if missing:
        raise ValueError(f"Missing model dataset columns: {sorted(missing)}")

    frame = model_dataset.copy()
    frame["survey_date"] = pd.to_datetime(frame["survey_date"], errors="raise")
    frame[target_column] = pd.to_numeric(frame[target_column], errors="raise")
    for column in EXTERNAL_FEATURE_COLUMNS:
        if column not in frame.columns:
            frame[column] = 0.0
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    frame = frame.sort_values(group_columns + ["survey_date"]).reset_index(drop=True)

    series_sizes = frame.groupby(group_columns, observed=True)[target_column].transform(
        "size"
    )
    frame = frame.loc[series_sizes >= minimum_series_points].copy()
    if frame.empty:
        raise ValueError("No series has enough observations for training.")

    grouped = frame.groupby(group_columns, observed=True)[target_column]
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
    previous_dates = frame.groupby(group_columns, observed=True)["survey_date"].shift(1)
    forecast_gap_days = (frame["survey_date"] - previous_dates).dt.days
    for column in EXTERNAL_FEATURE_COLUMNS:
        frame[column] = frame.groupby(group_columns, observed=True)[column].shift(1)
    frame["external_market_age_days"] = np.where(
        frame["external_market_available"] > 0,
        frame["external_market_age_days"] + forecast_gap_days,
        0.0,
    )
    frame["date_ordinal"] = frame["survey_date"].map(pd.Timestamp.toordinal)
    month_angle = 2 * math.pi * frame["survey_date"].dt.month / 12
    frame["month_sin"] = np.sin(month_angle)
    frame["month_cos"] = np.cos(month_angle)

    frame = frame.dropna(subset=NUMERIC_FEATURES).reset_index(drop=True)
    frame[TRAINING_TARGET_COLUMN] = (
        frame[target_column] - frame["lag_1"]
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
    numeric_features: list[str] | None = None,
) -> Pipeline:
    categorical_features = group_columns_for(series_level)
    selected_numeric_features = numeric_features or NUMERIC_FEATURES
    categorical_encoder: Any
    if series_level == "store":
        # Store names are high-cardinality. Cross-fitted target encoding keeps the
        # shared direct-price model compact without assigning fake numeric order.
        categorical_encoder = TargetEncoder(
            target_type="continuous",
            smooth="auto",
            cv=5,
            shuffle=True,
            random_state=random_state,
        )
    else:
        categorical_encoder = OneHotEncoder(
            handle_unknown="ignore",
            sparse_output=False,
        )
    preprocessor = ColumnTransformer(
        transformers=[
            (
                "categorical",
                categorical_encoder,
                categorical_features,
            ),
            ("numeric", "passthrough", selected_numeric_features),
        ],
        remainder="drop",
    )
    return Pipeline(
        steps=[
            ("preprocessor", preprocessor),
            ("regressor", create_estimator(estimator_name, random_state)),
        ]
    )


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
    target_column = target_column_for(series_level)
    raw = pd.read_csv(input_path, encoding="utf-8-sig")
    supervised = build_supervised_dataset(
        raw,
        minimum_series_points,
        series_level=series_level,
    )
    train, test, test_dates = split_by_date(supervised, test_fraction)
    actual = test[target_column].to_numpy(dtype=float)
    previous = test["lag_1"].to_numpy(dtype=float)
    naive_predictions = previous.copy()

    candidate_numeric_features = {"retail_history": BASE_NUMERIC_FEATURES}
    if supervised["external_market_available"].max() > 0:
        candidate_numeric_features["retail_history_plus_external_market"] = (
            NUMERIC_FEATURES
        )
    candidate_results: dict[str, dict[str, Any]] = {}
    for candidate_name, numeric_features in candidate_numeric_features.items():
        candidate_feature_columns = group_columns + numeric_features
        candidate_pipeline = create_pipeline(
            estimator_name,
            random_state,
            series_level,
            numeric_features=numeric_features,
        )
        candidate_pipeline.fit(
            train[candidate_feature_columns],
            train[TRAINING_TARGET_COLUMN],
        )
        candidate_ratios = candidate_pipeline.predict(test[candidate_feature_columns])
        candidate_predictions = previous * (1 + candidate_ratios)
        candidate_results[candidate_name] = {
            "numeric_features": numeric_features,
            "metrics": calculate_metrics(actual, candidate_predictions, previous),
            "predictions": candidate_predictions,
        }

    selected_feature_set = min(
        candidate_results,
        key=lambda name: candidate_results[name]["metrics"]["smape_percent"],
    )
    selected_result = candidate_results[selected_feature_set]
    selected_numeric_features = list(selected_result["numeric_features"])
    feature_columns = group_columns + selected_numeric_features
    model_predictions = np.asarray(selected_result["predictions"], dtype=float)

    metrics = {
        "model": calculate_metrics(actual, model_predictions, previous),
        "naive_last_value": calculate_metrics(
            actual,
            naive_predictions,
            previous,
        ),
    }
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

    # Backtesting must only see the historical training partition. After evaluation,
    # fit the persisted production model on every model-ready historical row. New
    # observations can then be scored without fitting the model again.
    production_pipeline = create_pipeline(
        estimator_name,
        random_state,
        series_level,
        numeric_features=selected_numeric_features,
    )
    production_pipeline.fit(
        supervised[feature_columns],
        supervised[TRAINING_TARGET_COLUMN],
    )
    trained_through_date = supervised["survey_date"].max().date().isoformat()
    joblib.dump(
        {
            "pipeline": production_pipeline,
            "series_level": series_level,
            "group_columns": group_columns,
            "feature_columns": feature_columns,
            "selected_feature_set": selected_feature_set,
            "prediction_target": TRAINING_TARGET_COLUMN,
            "source_target_column": target_column,
            "price_reconstruction": "lag_1 * (1 + predicted_change_ratio)",
            "minimum_series_points": minimum_series_points,
            "trained_through_date": trained_through_date,
        },
        model_path,
    )

    backtest = test[
        ["survey_date", *group_columns, target_column, "lag_1"]
    ].copy()
    backtest["model_prediction"] = np.round(model_predictions, 4)
    backtest["naive_prediction"] = np.round(naive_predictions, 4)
    backtest["model_absolute_error"] = np.round(
        np.abs(model_predictions - actual),
        4,
    )
    backtest.to_csv(backtest_path, index=False, encoding="utf-8-sig")

    report = {
        "series_level": series_level,
        "group_columns": group_columns,
        "estimator": estimator_name,
        "random_state": random_state,
        "input_row_count": int(len(raw)),
        "supervised_row_count": int(len(supervised)),
        "train_row_count": int(len(train)),
        "test_row_count": int(len(test)),
        "production_fit_row_count": int(len(supervised)),
        "model_trained_through_date": trained_through_date,
        "minimum_series_points": minimum_series_points,
        "train_date_min": train["survey_date"].min().date().isoformat(),
        "train_date_max": train["survey_date"].max().date().isoformat(),
        "test_dates": [pd.Timestamp(value).date().isoformat() for value in test_dates],
        "feature_columns": feature_columns,
        "selected_feature_set": selected_feature_set,
        "feature_set_backtest": {
            name: result["metrics"] for name, result in candidate_results.items()
        },
        "source_target_column": target_column,
        "training_target_column": TRAINING_TARGET_COLUMN,
        "metrics": metrics,
        "metrics_by_canonical_item": by_item,
        "limitations": [
            "The dataset has few unique survey dates, so this is an MVP prototype.",
            "Backtesting is chronological and one-step-ahead, not a multi-step production forecast.",
            "Feature-set selection uses the same small holdout and needs confirmation on future observations.",
            "Predictions must not be presented as reliable short-term forecasts without more frequent data.",
        ],
        "outputs": {
            "model": str(model_path),
            "report": str(metadata_path),
            "backtest_predictions": str(backtest_path),
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
        choices=["subtype", "product", "brand", "store"],
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
