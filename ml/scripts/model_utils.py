from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder


ITEM_GROUP_COLUMNS = ["canonical_item", "unit_price_basis"]


def smape(actual: np.ndarray, predicted: np.ndarray) -> float:
    denominator = np.abs(actual) + np.abs(predicted)
    valid = denominator > 0
    if not np.any(valid):
        return 0.0
    values = 200 * np.abs(predicted[valid] - actual[valid]) / denominator[valid]
    return float(np.mean(values))


def mape(actual: np.ndarray, predicted: np.ndarray) -> float:
    valid = actual != 0
    if not np.any(valid):
        return 0.0
    values = 100 * np.abs(predicted[valid] - actual[valid]) / np.abs(actual[valid])
    return float(np.mean(values))


def wmape(actual: np.ndarray, predicted: np.ndarray) -> float:
    denominator = float(np.sum(np.abs(actual)))
    if denominator == 0:
        return 0.0
    return float(100 * np.sum(np.abs(predicted - actual)) / denominator)


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
        "mape_percent": round(mape(actual, predicted), 4),
        "wmape_percent": round(wmape(actual, predicted), 4),
        "smape_percent": round(smape(actual, predicted), 4),
        "direction_accuracy": round(
            direction_accuracy(actual, predicted, previous),
            4,
        ),
    }


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


def create_item_pipeline(
    estimator_name: str,
    random_state: int,
    numeric_features: list[str],
) -> Pipeline:
    preprocessor = ColumnTransformer(
        transformers=[
            (
                "categorical",
                OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                ITEM_GROUP_COLUMNS,
            ),
            ("numeric", "passthrough", numeric_features),
        ],
        remainder="drop",
    )
    return Pipeline(
        steps=[
            ("preprocessor", preprocessor),
            ("regressor", create_estimator(estimator_name, random_state)),
        ]
    )
