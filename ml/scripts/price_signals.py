from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


# Z-scores for standard confidence intervals
CONFIDENCE_Z_SCORES = {
    0.80: 1.28155,
    0.85: 1.43953,
    0.90: 1.64485,
    0.95: 1.95996,
}
DEFAULT_CONFIDENCE = 0.80
DEFAULT_VOLATILITY = 0.035  # 3.5% baseline relative volatility
MIN_VOLATILITY = 0.015      # 1.5% minimum floor to prevent zero-width intervals


def calculate_drop_probability(
    current_price: float,
    predicted_price: float,
    volatility: float,
) -> float:
    """Calculates the probability that the future price drops below current price.

    Assumes predicted price follows a normal distribution centered at predicted_price
    with standard deviation = predicted_price * volatility:
        Y ~ N(predicted_price, sigma^2)
        P(Y < current_price) = Phi((current_price - predicted_price) / sigma)
    """
    if current_price <= 0 or predicted_price <= 0:
        return 0.50

    vol = max(volatility, MIN_VOLATILITY)
    sigma = max(1e-4, predicted_price * vol)
    z_score = (current_price - predicted_price) / sigma

    # Standard normal cumulative distribution function using error function
    prob = 0.5 * (1.0 + math.erf(z_score / math.sqrt(2.0)))
    # Clip to avoid extreme 0 or 1 edge cases
    return round(float(np.clip(prob, 0.01, 0.99)), 4)


def calculate_prediction_interval(
    predicted_price: float,
    volatility: float,
    confidence_level: float = DEFAULT_CONFIDENCE,
) -> tuple[float, float]:
    """Computes symmetric prediction bounds [pred_low, pred_high] for the given confidence level."""
    if predicted_price <= 0:
        return 0.0, 0.0

    z_score = CONFIDENCE_Z_SCORES.get(confidence_level, 1.28155)
    vol = max(volatility, MIN_VOLATILITY)
    margin = predicted_price * vol * z_score

    pred_low = max(0.0, round(predicted_price - margin, 2))
    pred_high = round(predicted_price + margin, 2)
    return pred_low, pred_high


def classify_price_signal(
    current_price: float,
    predicted_price: float,
    drop_probability: float,
    change_percent: float,
    threshold_percent: float = 1.5,
) -> tuple[str, str]:
    """Determines BUY / WAIT / HOLD decision signal and explanation message.

    - BUY: Price is projected to increase (drop_probability <= 0.45 and change >= threshold).
    - WAIT: Price is projected to decrease (drop_probability >= 0.55 and change <= -threshold).
    - HOLD: Price fluctuation is within normal fluctuation range.
    """
    rise_prob_pct = int(round((1.0 - drop_probability) * 100))
    drop_prob_pct = int(round(drop_probability * 100))

    if drop_probability <= 0.45 and change_percent >= threshold_percent:
        signal = "BUY"
        message = (
            f"2주 뒤 가격이 오를 것으로 보입니다 (상승 확률 {rise_prob_pct}%). "
            "필요 수량을 미리 구매해 두는 것이 유리합니다."
        )
    elif drop_probability >= 0.55 and change_percent <= -threshold_percent:
        signal = "WAIT"
        message = (
            f"2주 뒤 가격이 내릴 것으로 보입니다 (하락 확률 {drop_prob_pct}%). "
            "대량 구매를 미루고 관망하는 것을 권장합니다."
        )
    else:
        signal = "HOLD"
        message = (
            "2주 뒤 유의미한 가격 변동이 예상되지 않습니다. "
            "통상적인 주기에 맞춰 구매해도 무방합니다."
        )

    return signal, message


def estimate_item_volatilities(
    history_df: pd.DataFrame,
    group_columns: list[str] | None = None,
    target_column: str = "median_unit_price",
    default_volatility: float = DEFAULT_VOLATILITY,
) -> dict[str, float]:
    """Estimates historical relative volatility (std of relative step changes) per canonical item."""
    if history_df is None or history_df.empty or "canonical_item" not in history_df.columns:
        return {}

    volatilities: dict[str, float] = {}
    price_col = target_column if target_column in history_df.columns else None
    if price_col is None:
        for candidate in ["median_unit_price", "actual_unit_price", "unit_price"]:
            if candidate in history_df.columns:
                price_col = candidate
                break

    if price_col is None:
        return {}

    sort_cols = ["survey_date"] if "survey_date" in history_df.columns else []
    for item, item_df in history_df.groupby("canonical_item"):
        item_str = str(item)
        if sort_cols:
            item_df = item_df.sort_values(sort_cols)

        prices = pd.to_numeric(item_df[price_col], errors="coerce").dropna().to_numpy()
        if len(prices) < 3:
            volatilities[item_str] = default_volatility
            continue

        pct_changes = np.diff(prices) / prices[:-1]
        valid_changes = pct_changes[np.isfinite(pct_changes)]
        if len(valid_changes) < 2:
            volatilities[item_str] = default_volatility
            continue

        std_dev = float(np.std(valid_changes))
        volatilities[item_str] = float(np.clip(std_dev, MIN_VOLATILITY, 0.20))

    return volatilities


def enrich_predictions_with_signals(
    predictions_df: pd.DataFrame,
    history_df: pd.DataFrame | None = None,
    series_level: str = "subtype",
    confidence_level: float = DEFAULT_CONFIDENCE,
) -> pd.DataFrame:
    """Enriches future_predictions DataFrame with signal, drop probability, and confidence intervals."""
    if predictions_df.empty:
        return predictions_df

    df = predictions_df.copy()
    volatility_map = estimate_item_volatilities(history_df) if history_df is not None else {}

    pred_price_col = "model_predicted_unit_price"
    change_pct_col = "model_predicted_change_percent"
    current_price_col = (
        "last_actual_unit_price"
        if series_level == "store" and "last_actual_unit_price" in df.columns
        else "current_median_unit_price"
    )

    if pred_price_col not in df.columns:
        return df

    pred_lows = []
    pred_highs = []
    drop_probs = []
    signals = []
    messages = []

    for _, row in df.iterrows():
        pred_price = float(row.get(pred_price_col) or 0.0)
        curr_price = float(row.get(current_price_col) or row.get("recursive_input_unit_price") or pred_price)
        change_pct = float(row.get(change_pct_col) or 0.0)
        canonical_item = str(row.get("canonical_item") or "")

        volatility = volatility_map.get(canonical_item, DEFAULT_VOLATILITY)

        low, high = calculate_prediction_interval(pred_price, volatility, confidence_level)
        drop_prob = calculate_drop_probability(curr_price, pred_price, volatility)
        signal, msg = classify_price_signal(curr_price, pred_price, drop_prob, change_pct)

        pred_lows.append(low)
        pred_highs.append(high)
        drop_probs.append(drop_prob)
        signals.append(signal)
        messages.append(msg)

    df["pred_low"] = pred_lows
    df["pred_high"] = pred_highs
    df["drop_probability"] = drop_probs
    df["signal"] = signals
    df["signal_message"] = messages

    return df
