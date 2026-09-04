from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

try:
    from .model_utils import calculate_metrics
except ImportError:
    from model_utils import calculate_metrics  # type: ignore[no-redef]


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / "artifacts" / "ml"
DEFAULT_OUTPUT = DEFAULT_ARTIFACT_ROOT / "backtest_business_metrics.json"

ITEM_BACKTEST_INPUT = Path("item/model/backtest_predictions.csv")

IDENTIFIER_COLUMNS = [
    "survey_date",
    "canonical_item",
    "product_name",
    "brand_name",
    "store_name",
    "unit_price_basis",
]


def resolve_cli_path(path_text: str) -> Path:
    path = Path(path_text).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def _numeric_column(frame: pd.DataFrame, column: str) -> np.ndarray:
    if column not in frame.columns:
        raise ValueError(f"Missing backtest column: {column}")
    values = pd.to_numeric(frame[column], errors="coerce")
    if values.isna().any():
        invalid_count = int(values.isna().sum())
        raise ValueError(f"Column {column} contains {invalid_count} non-numeric values")
    array = values.to_numpy(dtype=float)
    if not np.all(np.isfinite(array)):
        raise ValueError(f"Column {column} contains non-finite values")
    return array


def evaluate_backtest_frame(
    frame: pd.DataFrame,
    actual_column: str,
    decision_threshold_percent: float = 0.0,
) -> tuple[dict[str, Any], pd.DataFrame]:
    if frame.empty:
        raise ValueError("Backtest data is empty")
    if decision_threshold_percent < 0:
        raise ValueError("decision_threshold_percent must be zero or greater")

    actual = _numeric_column(frame, actual_column)
    previous = _numeric_column(frame, "lag_1")
    predicted = _numeric_column(frame, "model_prediction")
    naive = _numeric_column(frame, "naive_prediction")
    if np.any(actual <= 0) or np.any(previous <= 0):
        raise ValueError("Actual and previous prices must be positive")

    predicted_change_percent = 100 * (predicted - previous) / previous
    buy_now = predicted_change_percent > decision_threshold_percent
    strategy_cost = np.where(buy_now, previous, actual)
    always_buy_now_cost = previous
    always_wait_cost = actual
    oracle_cost = np.minimum(previous, actual)

    total_always_buy_now = float(np.sum(always_buy_now_cost))
    total_always_wait = float(np.sum(always_wait_cost))
    total_strategy = float(np.sum(strategy_cost))
    total_oracle = float(np.sum(oracle_cost))
    savings_amount = total_always_buy_now - total_strategy
    savings_percent = 100 * savings_amount / total_always_buy_now
    wait_savings_amount = total_always_wait - total_strategy
    wait_savings_percent = 100 * wait_savings_amount / total_always_wait
    available_savings = total_always_buy_now - total_oracle
    captured_savings_percent = (
        100 * savings_amount / available_savings if available_savings > 0 else 0.0
    )

    metrics: dict[str, Any] = {
        "sample_count": int(len(frame)),
        "decision_threshold_percent": round(float(decision_threshold_percent), 4),
        "model": calculate_metrics(actual, predicted, previous),
        "naive_last_value": calculate_metrics(actual, naive, previous),
        "purchase_timing_backtest": {
            "policy": (
                "buy_now_if_predicted_change_percent_is_greater_than_threshold; "
                "otherwise_wait_until_next_observation"
            ),
            "baseline": "always_buy_now",
            "weighting": "one_standardized_unit_per_backtest_row",
            "buy_now_count": int(np.sum(buy_now)),
            "wait_count": int(np.sum(~buy_now)),
            "buy_now_rate_percent": round(float(np.mean(buy_now) * 100), 4),
            "baseline_cost": round(total_always_buy_now, 4),
            "strategy_cost": round(total_strategy, 4),
            "savings_amount": round(savings_amount, 4),
            "savings_percent": round(savings_percent, 4),
            "always_wait_cost": round(total_always_wait, 4),
            "savings_vs_always_wait_amount": round(wait_savings_amount, 4),
            "savings_vs_always_wait_percent": round(wait_savings_percent, 4),
            "oracle_cost": round(total_oracle, 4),
            "captured_available_savings_percent": round(
                captured_savings_percent,
                4,
            ),
        },
    }
    if "survey_date" in frame.columns:
        survey_dates = pd.to_datetime(frame["survey_date"], errors="raise")
        metrics["backtest_date_min"] = survey_dates.min().date().isoformat()
        metrics["backtest_date_max"] = survey_dates.max().date().isoformat()

    decisions = frame[
        [column for column in IDENTIFIER_COLUMNS if column in frame.columns]
    ].copy()
    decisions["actual_unit_price"] = actual
    decisions["previous_unit_price"] = previous
    decisions["model_predicted_unit_price"] = predicted
    decisions["model_predicted_change_percent"] = np.round(
        predicted_change_percent,
        4,
    )
    decisions["decision"] = np.where(buy_now, "BUY_NOW", "WAIT")
    decisions["baseline_cost"] = always_buy_now_cost
    decisions["strategy_cost"] = strategy_cost
    decisions["realized_savings"] = always_buy_now_cost - strategy_cost
    return metrics, decisions


def evaluate_artifact_root(
    artifact_root: Path,
    output_path: Path,
    decision_threshold_percent: float = 0.0,
) -> dict[str, Any]:
    input_path = artifact_root / ITEM_BACKTEST_INPUT
    if not input_path.is_file():
        raise FileNotFoundError(f"Backtest predictions not found: {input_path}")
    frame = pd.read_csv(input_path, encoding="utf-8-sig")
    metrics, decisions = evaluate_backtest_frame(
        frame,
        "median_unit_price",
        decision_threshold_percent=decision_threshold_percent,
    )
    decision_path = input_path.parent / "backtest_purchase_decisions.csv"
    decisions.to_csv(decision_path, index=False, encoding="utf-8-sig")
    metrics["input"] = str(input_path)
    metrics["decisions_output"] = str(decision_path)

    by_item: dict[str, Any] = {}
    if "canonical_item" in frame.columns:
        for item_name, item_frame in frame.groupby("canonical_item", observed=True):
            item_metrics, _ = evaluate_backtest_frame(
                item_frame,
                "median_unit_price",
                decision_threshold_percent=decision_threshold_percent,
            )
            by_item[str(item_name)] = item_metrics
    metrics["by_canonical_item"] = by_item

    payload = {
        "schema_version": "1.0",
        "evaluation_type": "chronological_one_step_backtest",
        "decision_threshold_percent": round(
            float(decision_threshold_percent),
            4,
        ),
        "assumptions": [
            "Each backtest row represents one standardized unit of equal weight.",
            "A predicted increase triggers BUY_NOW; otherwise the strategy waits.",
            "The baseline buys every row at its previous observed price.",
            "Inventory, waste, delivery, stockouts, and actual order quantities are excluded.",
        ],
        "total_sample_count": int(metrics["sample_count"]),
        "results": {"item": metrics},
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Calculate MAPE and simulated purchase-timing savings from backtests."
    )
    parser.add_argument(
        "--artifact-root",
        default=str(DEFAULT_ARTIFACT_ROOT),
        help="Root containing the item model backtest CSV file.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output JSON path.",
    )
    parser.add_argument(
        "--decision-threshold-percent",
        type=float,
        default=0.0,
        help="Buy now only when the predicted increase exceeds this percent.",
    )
    args = parser.parse_args()

    payload = evaluate_artifact_root(
        resolve_cli_path(args.artifact_root),
        resolve_cli_path(args.output),
        decision_threshold_percent=args.decision_threshold_percent,
    )
    summary = {
        level: {
            "mape_percent": result["model"]["mape_percent"],
            "naive_mape_percent": result["naive_last_value"]["mape_percent"],
            "savings_percent": result["purchase_timing_backtest"][
                "savings_percent"
            ],
        }
        for level, result in payload["results"].items()
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
