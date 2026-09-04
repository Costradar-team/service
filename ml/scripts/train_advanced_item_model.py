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
    from .train_price_model import calculate_metrics, create_pipeline
except ImportError:
    from external_market_features import EXTERNAL_FEATURE_COLUMNS  # type: ignore[no-redef]
    from train_price_model import calculate_metrics, create_pipeline  # type: ignore[no-redef]


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = REPO_ROOT / "artifacts" / "ml" / "item" / "model_dataset.csv"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "artifacts" / "ml" / "item" / "model"

GROUP_COLUMNS = ["canonical_item", "unit_price_basis"]
TARGET_COLUMN = "median_unit_price"
MODEL_FILENAME = "price_model.joblib"
REPORT_FILENAME = "training_report.json"
BACKTEST_FILENAME = "backtest_predictions.csv"
DIRECT_BACKTEST_FILENAME = "direct_backtest_predictions.csv"

BASE_NUMERIC_FEATURES = [
    "date_ordinal",
    "month_sin",
    "month_cos",
    "forecast_gap_days",
    "lag_1",
    "lag_2",
    "lag_4",
    "rolling_mean_2",
    "rolling_mean_4",
    "rolling_std_4",
    "change_1_pct",
    "change_4_pct",
    "observation_count",
    "store_count",
    "sku_count",
    "price_spread_pct",
]
EXTERNAL_DIRECT_FEATURES = [
    *EXTERNAL_FEATURE_COLUMNS,
    "external_market_age_at_forecast_days",
    "external_market_price_previous_1",
    "external_market_price_previous_2",
    "external_market_price_previous_4",
]


def resolve_cli_path(path_text: str) -> Path:
    path = Path(path_text).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def _safe_percent_change(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0
    return (current - previous) / previous * 100


def prepare_item_history(raw: pd.DataFrame) -> pd.DataFrame:
    required = {"survey_date", *GROUP_COLUMNS, TARGET_COLUMN}
    missing = required.difference(raw.columns)
    if missing:
        raise ValueError(f"Missing item model columns: {sorted(missing)}")

    frame = raw.copy()
    frame["survey_date"] = pd.to_datetime(frame["survey_date"], errors="raise")
    frame[TARGET_COLUMN] = pd.to_numeric(frame[TARGET_COLUMN], errors="raise")
    for column in ["observation_count", "store_count", "sku_count"]:
        if column not in frame.columns:
            frame[column] = 0.0
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    for column in ["min_unit_price", "max_unit_price"]:
        if column not in frame.columns:
            frame[column] = frame[TARGET_COLUMN]
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(
            frame[TARGET_COLUMN]
        )
    for column in EXTERNAL_FEATURE_COLUMNS:
        if column not in frame.columns:
            frame[column] = 0.0
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    return frame.sort_values(GROUP_COLUMNS + ["survey_date"]).reset_index(drop=True)


def _feature_row(
    group: pd.DataFrame,
    anchor_index: int,
    forecast_date: pd.Timestamp,
    horizon_step: int,
) -> dict[str, Any]:
    anchor = group.iloc[anchor_index]
    prices = group[TARGET_COLUMN].to_numpy(dtype=float)
    current_price = float(prices[anchor_index])
    recent_four = prices[anchor_index - 3 : anchor_index + 1]
    gap_days = int((forecast_date - anchor["survey_date"]).days)
    month_angle = 2 * math.pi * forecast_date.month / 12
    row: dict[str, Any] = {
        **{column: anchor[column] for column in GROUP_COLUMNS},
        "forecast_date": forecast_date,
        "as_of_date": anchor["survey_date"],
        "forecast_horizon_step": horizon_step,
        "current_median_unit_price": current_price,
        "date_ordinal": forecast_date.toordinal(),
        "month_sin": math.sin(month_angle),
        "month_cos": math.cos(month_angle),
        "forecast_gap_days": gap_days,
        "lag_1": current_price,
        "lag_2": float(prices[anchor_index - 1]),
        "lag_4": float(prices[anchor_index - 4]),
        "rolling_mean_2": float(np.mean(prices[anchor_index - 1 : anchor_index + 1])),
        "rolling_mean_4": float(np.mean(recent_four)),
        "rolling_std_4": float(np.std(recent_four)),
        "change_1_pct": _safe_percent_change(
            current_price, float(prices[anchor_index - 1])
        ),
        "change_4_pct": _safe_percent_change(
            current_price, float(prices[anchor_index - 4])
        ),
        "observation_count": float(anchor["observation_count"]),
        "store_count": float(anchor["store_count"]),
        "sku_count": float(anchor["sku_count"]),
        "price_spread_pct": (
            (float(anchor["max_unit_price"]) - float(anchor["min_unit_price"]))
            / current_price
            * 100
            if current_price != 0
            else 0.0
        ),
    }
    row.update({column: float(anchor[column]) for column in EXTERNAL_FEATURE_COLUMNS})
    row["external_market_age_at_forecast_days"] = (
        float(anchor["external_market_age_days"]) + gap_days
        if float(anchor["external_market_available"]) > 0
        else 0.0
    )
    for offset, suffix in ((1, "1"), (2, "2"), (4, "4")):
        row[f"external_market_price_previous_{suffix}"] = float(
            group.iloc[anchor_index - offset]["external_market_price"]
        )
    return row


def build_direct_horizon_dataset(
    raw: pd.DataFrame,
    horizon_step: int,
    minimum_series_points: int = 6,
) -> pd.DataFrame:
    if horizon_step < 1:
        raise ValueError("horizon_step must be at least 1")
    frame = prepare_item_history(raw)
    rows: list[dict[str, Any]] = []
    for _, group in frame.groupby(GROUP_COLUMNS, observed=True):
        group = group.sort_values("survey_date").reset_index(drop=True)
        if len(group) < max(minimum_series_points, horizon_step + 5):
            continue
        for anchor_index in range(4, len(group) - horizon_step):
            target = group.iloc[anchor_index + horizon_step]
            row = _feature_row(
                group,
                anchor_index,
                pd.Timestamp(target["survey_date"]),
                horizon_step,
            )
            actual = float(target[TARGET_COLUMN])
            current = float(row["current_median_unit_price"])
            row["survey_date"] = target["survey_date"]
            row[TARGET_COLUMN] = actual
            row["target_change_ratio"] = (actual - current) / current
            rows.append(row)
    if not rows:
        raise ValueError("No item series has enough observations for direct training.")
    return pd.DataFrame(rows).sort_values(
        ["survey_date", *GROUP_COLUMNS]
    ).reset_index(drop=True)


def build_direct_future_features(
    raw: pd.DataFrame,
    horizon_step: int,
    minimum_series_points: int = 6,
) -> pd.DataFrame:
    if horizon_step < 1:
        raise ValueError("horizon_step must be at least 1")
    frame = prepare_item_history(raw)
    rows: list[dict[str, Any]] = []
    for _, group in frame.groupby(GROUP_COLUMNS, observed=True):
        group = group.sort_values("survey_date").reset_index(drop=True)
        if len(group) < max(minimum_series_points, 5):
            continue
        intervals = group["survey_date"].diff().dt.days.dropna()
        positive_intervals = intervals.loc[intervals > 0]
        interval_days = int(positive_intervals.median()) if not positive_intervals.empty else 14
        forecast_date = pd.Timestamp(group.iloc[-1]["survey_date"]) + pd.Timedelta(
            days=interval_days * horizon_step
        )
        rows.append(
            _feature_row(group, len(group) - 1, forecast_date, horizon_step)
        )
    return pd.DataFrame(rows).sort_values(GROUP_COLUMNS).reset_index(drop=True)


def _validation_dates(frame: pd.DataFrame) -> list[pd.Timestamp]:
    unique_dates = [pd.Timestamp(value) for value in sorted(frame["survey_date"].unique())]
    if len(unique_dates) < 8:
        raise ValueError("At least eight direct-model target dates are required.")
    validation_count = max(4, math.ceil(len(unique_dates) * 0.3))
    return unique_dates[-validation_count:]


def _walk_forward_predictions(
    frame: pd.DataFrame,
    validation_dates: list[pd.Timestamp],
    estimator_name: str,
    numeric_features: list[str],
    random_state: int,
) -> pd.DataFrame:
    predictions: list[pd.DataFrame] = []
    feature_columns = GROUP_COLUMNS + numeric_features
    for target_date in validation_dates:
        train = frame.loc[frame["survey_date"] < target_date]
        test = frame.loc[frame["survey_date"] == target_date]
        if len(train) < 12 or test.empty:
            continue
        pipeline = create_pipeline(
            estimator_name,
            random_state,
            series_level="item",
            numeric_features=numeric_features,
        )
        pipeline.fit(train[feature_columns], train["target_change_ratio"])
        direct_ratios = pipeline.predict(test[feature_columns])
        fold = test[
            [
                "survey_date",
                *GROUP_COLUMNS,
                TARGET_COLUMN,
                "current_median_unit_price",
                "forecast_horizon_step",
            ]
        ].copy()
        fold["direct_model_prediction"] = (
            fold["current_median_unit_price"].to_numpy(dtype=float)
            * (1 + direct_ratios)
        )
        predictions.append(fold)
    if not predictions:
        raise ValueError("Walk-forward validation produced no predictions.")
    return pd.concat(predictions, ignore_index=True)


def _select_blend_weight(frame: pd.DataFrame) -> tuple[float, dict[str, float | int]]:
    actual = frame[TARGET_COLUMN].to_numpy(dtype=float)
    previous = frame["current_median_unit_price"].to_numpy(dtype=float)
    direct = frame["direct_model_prediction"].to_numpy(dtype=float)
    candidates: list[tuple[float, dict[str, float | int]]] = []
    for weight in np.linspace(0.0, 1.0, 21):
        blended = previous + float(weight) * (direct - previous)
        metrics = calculate_metrics(actual, blended, previous)
        candidates.append((float(weight), metrics))
    # On tied rounded sMAPE, prefer the safer smaller model contribution.
    return min(candidates, key=lambda item: (item[1]["smape_percent"], item[0]))


def train_advanced_item_model(
    input_path: Path,
    output_dir: Path,
    estimator_name: str = "gradient-boosting",
    max_forecast_horizon: int = 4,
    minimum_series_points: int = 6,
    random_state: int = 42,
) -> dict[str, Any]:
    if not input_path.is_file():
        raise FileNotFoundError(f"Item model dataset not found: {input_path}")
    if max_forecast_horizon < 1:
        raise ValueError("max_forecast_horizon must be at least 1")

    raw = pd.read_csv(input_path, encoding="utf-8-sig")
    history = prepare_item_history(raw)
    output_dir.mkdir(parents=True, exist_ok=True)
    horizon_bundles: dict[int, dict[str, Any]] = {}
    horizon_reports: dict[str, Any] = {}
    all_backtests: list[pd.DataFrame] = []

    for horizon_step in range(1, max_forecast_horizon + 1):
        direct = build_direct_horizon_dataset(
            history,
            horizon_step,
            minimum_series_points,
        )
        validation_dates = _validation_dates(direct)
        candidate_features = {"retail_history": BASE_NUMERIC_FEATURES}
        if direct["external_market_available"].max() > 0:
            candidate_features["retail_history_plus_external_market"] = [
                *BASE_NUMERIC_FEATURES,
                *EXTERNAL_DIRECT_FEATURES,
            ]

        candidate_results: dict[str, dict[str, Any]] = {}
        for candidate_name, numeric_features in candidate_features.items():
            oof = _walk_forward_predictions(
                direct,
                validation_dates,
                estimator_name,
                numeric_features,
                random_state,
            )
            weight, blended_metrics = _select_blend_weight(oof)
            previous = oof["current_median_unit_price"].to_numpy(dtype=float)
            oof["model_prediction"] = previous + weight * (
                oof["direct_model_prediction"].to_numpy(dtype=float) - previous
            )
            actual = oof[TARGET_COLUMN].to_numpy(dtype=float)
            candidate_results[candidate_name] = {
                "numeric_features": numeric_features,
                "model_weight": weight,
                "direct_model_metrics": calculate_metrics(
                    actual,
                    oof["direct_model_prediction"].to_numpy(dtype=float),
                    previous,
                ),
                "blended_metrics": blended_metrics,
                "naive_metrics": calculate_metrics(actual, previous, previous),
                "backtest": oof,
            }

        selected_name = min(
            candidate_results,
            key=lambda name: (
                candidate_results[name]["blended_metrics"]["smape_percent"],
                0 if name == "retail_history" else 1,
            ),
        )
        selected = candidate_results[selected_name]
        selected_backtest = selected["backtest"].copy()
        numeric_features = list(selected["numeric_features"])
        feature_columns = GROUP_COLUMNS + numeric_features
        pipeline = create_pipeline(
            estimator_name,
            random_state,
            series_level="item",
            numeric_features=numeric_features,
        )
        pipeline.fit(direct[feature_columns], direct["target_change_ratio"])
        weight = float(selected["model_weight"])
        method = (
            "validated_baseline_fallback"
            if weight == 0
            else "validated_direct_ensemble"
            if weight < 1
            else "direct_model"
        )
        horizon_bundles[horizon_step] = {
            "pipeline": pipeline,
            "feature_columns": feature_columns,
            "selected_feature_set": selected_name,
            "model_weight": weight,
            "forecast_method": method,
        }
        selected_backtest["naive_prediction"] = selected_backtest[
            "current_median_unit_price"
        ]
        selected_backtest["lag_1"] = selected_backtest[
            "current_median_unit_price"
        ]
        selected_backtest["selected_feature_set"] = selected_name
        selected_backtest["model_weight"] = weight
        selected_backtest["forecast_method"] = method
        all_backtests.append(selected_backtest)
        horizon_reports[str(horizon_step)] = {
            "training_row_count": int(len(direct)),
            "validation_dates": [date.date().isoformat() for date in validation_dates],
            "validation_row_count": int(len(selected_backtest)),
            "selected_feature_set": selected_name,
            "model_weight": weight,
            "forecast_method": method,
            "metrics": {
                "direct_model": selected["direct_model_metrics"],
                "validated_ensemble": selected["blended_metrics"],
                "naive_last_value": selected["naive_metrics"],
            },
            "feature_set_backtest": {
                name: {
                    "model_weight": result["model_weight"],
                    "direct_model": result["direct_model_metrics"],
                    "validated_ensemble": result["blended_metrics"],
                }
                for name, result in candidate_results.items()
            },
        }

    trained_through_date = history["survey_date"].max().date().isoformat()
    model_path = output_dir / MODEL_FILENAME
    report_path = output_dir / REPORT_FILENAME
    all_backtest_path = output_dir / DIRECT_BACKTEST_FILENAME
    one_step_backtest_path = output_dir / BACKTEST_FILENAME
    joblib.dump(
        {
            "format_version": 2,
            "forecast_strategy": "direct_multi_horizon",
            "series_level": "item",
            "group_columns": GROUP_COLUMNS,
            "source_target_column": TARGET_COLUMN,
            "minimum_series_points": minimum_series_points,
            "max_forecast_horizon": max_forecast_horizon,
            "trained_through_date": trained_through_date,
            "horizon_models": horizon_bundles,
        },
        model_path,
    )
    backtest = pd.concat(all_backtests, ignore_index=True)
    backtest.to_csv(all_backtest_path, index=False, encoding="utf-8-sig")
    backtest.loc[backtest["forecast_horizon_step"] == 1].to_csv(
        one_step_backtest_path,
        index=False,
        encoding="utf-8-sig",
    )
    report = {
        "series_level": "item",
        "forecast_strategy": "direct_multi_horizon",
        "estimator": estimator_name,
        "input_row_count": int(len(raw)),
        "model_trained_through_date": trained_through_date,
        "max_forecast_horizon": max_forecast_horizon,
        "minimum_series_points": minimum_series_points,
        "horizons": horizon_reports,
        "limitations": [
            "Only a small number of survey dates is available, especially for eggs.",
            "A horizon may fall back to the last observed price when ML does not beat it out of sample.",
            "External features are selected only when they improve chronological validation.",
        ],
        "outputs": {
            "model": str(model_path),
            "report": str(report_path),
            "one_step_backtest": str(one_step_backtest_path),
            "all_horizon_backtest": str(all_backtest_path),
        },
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Train direct 2/4/6-week item forecasts with validation gating."
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument(
        "--estimator",
        choices=["gradient-boosting", "lightgbm"],
        default="gradient-boosting",
    )
    parser.add_argument("--max-forecast-horizon", type=int, default=4)
    parser.add_argument("--minimum-series-points", type=int, default=6)
    parser.add_argument("--random-state", type=int, default=42)
    args = parser.parse_args()
    report = train_advanced_item_model(
        resolve_cli_path(args.input),
        resolve_cli_path(args.output_dir),
        estimator_name=args.estimator,
        max_forecast_horizon=args.max_forecast_horizon,
        minimum_series_points=args.minimum_series_points,
        random_state=args.random_state,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
