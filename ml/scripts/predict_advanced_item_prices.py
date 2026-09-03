from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

try:
    from .train_advanced_item_model import build_direct_future_features
except ImportError:
    from train_advanced_item_model import build_direct_future_features  # type: ignore[no-redef]


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = REPO_ROOT / "artifacts" / "ml" / "item" / "model_dataset.csv"
DEFAULT_MODEL = REPO_ROOT / "artifacts" / "ml" / "item" / "model" / "price_model.joblib"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "artifacts" / "ml" / "item" / "model"
FORECAST_FILENAME = "future_predictions.csv"
REPORT_FILENAME = "prediction_report.json"


def resolve_cli_path(path_text: str) -> Path:
    path = Path(path_text).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def load_advanced_bundle(model_path: Path) -> dict[str, Any]:
    if not model_path.is_file():
        raise FileNotFoundError(f"Advanced item model not found: {model_path}")
    bundle = joblib.load(model_path)
    required = {
        "forecast_strategy",
        "series_level",
        "minimum_series_points",
        "max_forecast_horizon",
        "trained_through_date",
        "horizon_models",
    }
    if not isinstance(bundle, dict) or required.difference(bundle):
        raise ValueError("Unsupported advanced item model format. Retrain the item model.")
    if bundle["forecast_strategy"] != "direct_multi_horizon":
        raise ValueError("The item model is not a direct multi-horizon model.")
    if bundle["series_level"] != "item":
        raise ValueError("The advanced predictor only supports item models.")
    return bundle


def predict_advanced_item_prices(
    input_path: Path,
    model_path: Path,
    output_dir: Path,
    forecast_horizon: int = 4,
) -> dict[str, Any]:
    if forecast_horizon < 1:
        raise ValueError("forecast_horizon must be at least 1")
    if not input_path.is_file():
        raise FileNotFoundError(f"Item model dataset not found: {input_path}")
    bundle = load_advanced_bundle(model_path)
    if forecast_horizon > int(bundle["max_forecast_horizon"]):
        raise ValueError(
            "forecast_horizon exceeds the trained direct horizons; retrain with a larger max horizon"
        )

    raw = pd.read_csv(input_path, encoding="utf-8-sig")
    output_frames: list[pd.DataFrame] = []
    for horizon_step in range(1, forecast_horizon + 1):
        model_info = bundle["horizon_models"][horizon_step]
        future = build_direct_future_features(
            raw,
            horizon_step,
            int(bundle["minimum_series_points"]),
        )
        feature_columns = list(model_info["feature_columns"])
        missing = set(feature_columns).difference(future.columns)
        if missing:
            raise ValueError(f"Missing direct prediction features: {sorted(missing)}")
        current = future["current_median_unit_price"].to_numpy(dtype=float)
        direct_ratios = model_info["pipeline"].predict(future[feature_columns])
        direct_predictions = current * (1 + direct_ratios)
        weight = float(model_info["model_weight"])
        predictions = current + weight * (direct_predictions - current)
        step = future[
            [
                "forecast_date",
                "forecast_horizon_step",
                "as_of_date",
                "canonical_item",
                "unit_price_basis",
                "current_median_unit_price",
            ]
        ].copy()
        step["model_predicted_unit_price"] = np.round(predictions, 4)
        step["model_predicted_change_percent"] = np.round(
            (predictions - current) / current * 100,
            4,
        )
        step["prediction_strategy"] = "direct_multi_horizon"
        step["forecast_method"] = str(model_info["forecast_method"])
        step["model_weight"] = weight
        step["selected_feature_set"] = str(model_info["selected_feature_set"])
        output_frames.append(step)

    output = pd.concat(output_frames, ignore_index=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    forecast_path = output_dir / FORECAST_FILENAME
    report_path = output_dir / REPORT_FILENAME
    output.to_csv(forecast_path, index=False, encoding="utf-8-sig")
    report = {
        "series_level": "item",
        "forecast_strategy": "direct_multi_horizon",
        "input_row_count": int(len(raw)),
        "forecast_series_count": int(output["canonical_item"].nunique()),
        "forecast_horizon": forecast_horizon,
        "forecast_count": int(len(output)),
        "model_trained_through_date": str(bundle["trained_through_date"]),
        "prediction_as_of_date": pd.to_datetime(output["as_of_date"])
        .max()
        .date()
        .isoformat(),
        "horizon_methods": {
            str(step): {
                "forecast_method": bundle["horizon_models"][step]["forecast_method"],
                "model_weight": bundle["horizon_models"][step]["model_weight"],
                "selected_feature_set": bundle["horizon_models"][step][
                    "selected_feature_set"
                ],
            }
            for step in range(1, forecast_horizon + 1)
        },
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
        description="Predict item prices with saved direct horizon models."
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--model", default=str(DEFAULT_MODEL))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--forecast-horizon", type=int, default=4)
    args = parser.parse_args()
    report = predict_advanced_item_prices(
        resolve_cli_path(args.input),
        resolve_cli_path(args.model),
        resolve_cli_path(args.output_dir),
        forecast_horizon=args.forecast_horizon,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
