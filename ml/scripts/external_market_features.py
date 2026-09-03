from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Iterable

import pandas as pd


EXTERNAL_FEATURE_COLUMNS = [
    "external_market_available",
    "external_market_price",
    "external_market_change_7d_pct",
    "external_market_change_14d_pct",
    "external_market_change_28d_pct",
    "external_market_price_vs_mean_28d_pct",
    "external_market_age_days",
]

FIS_ITEM_FILENAME = "fis_item.csv"
FIS_OBSERVATION_FILENAME = "fis_price_observation.csv"
KAMIS_ITEM_FILENAME = "kamis_item.csv"
KAMIS_OBSERVATION_FILENAME = "kamis_price_observation.csv"


@dataclass(frozen=True)
class MarketPoint:
    observed_date: date
    price: float


def _read_csv(path: Path, required_columns: set[str]) -> pd.DataFrame:
    frame = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
    missing = required_columns.difference(frame.columns)
    if missing:
        raise ValueError(f"Missing columns in {path}: {sorted(missing)}")
    return frame


def _collapse_points(rows: Iterable[tuple[str, date, float]]) -> dict[str, list[MarketPoint]]:
    grouped: dict[tuple[str, date], list[float]] = {}
    for canonical_item, observed_date, price in rows:
        if price > 0:
            grouped.setdefault((canonical_item, observed_date), []).append(price)

    by_item: dict[str, list[MarketPoint]] = {}
    for (canonical_item, observed_date), prices in sorted(grouped.items()):
        by_item.setdefault(canonical_item, []).append(
            MarketPoint(observed_date, float(median(prices)))
        )
    return by_item


def load_fis_series(processed_dir: Path | None) -> dict[str, list[MarketPoint]]:
    if processed_dir is None:
        return {}
    item_path = processed_dir / FIS_ITEM_FILENAME
    observation_path = processed_dir / FIS_OBSERVATION_FILENAME
    if not item_path.is_file() or not observation_path.is_file():
        return {}

    items = _read_csv(item_path, {"item_key", "canonical_item"})
    observations = _read_csv(
        observation_path,
        {"item_key", "trade_date", "close_price", "converted_price"},
    )
    joined = observations.merge(
        items[["item_key", "canonical_item"]].drop_duplicates(),
        on="item_key",
        how="inner",
        validate="many_to_one",
    )
    joined["observed_date"] = pd.to_datetime(joined["trade_date"], errors="coerce")
    converted = pd.to_numeric(joined["converted_price"], errors="coerce")
    close = pd.to_numeric(joined["close_price"], errors="coerce")
    joined["normalized_price"] = converted.fillna(close)
    joined = joined.dropna(subset=["observed_date", "normalized_price"])
    return _collapse_points(
        (
            str(row.canonical_item).strip(),
            row.observed_date.date(),
            float(row.normalized_price),
        )
        for row in joined.itertuples(index=False)
    )


def load_kamis_series(processed_dir: Path | None) -> dict[str, list[MarketPoint]]:
    if processed_dir is None:
        return {}
    item_path = processed_dir / KAMIS_ITEM_FILENAME
    observation_path = processed_dir / KAMIS_OBSERVATION_FILENAME
    if not item_path.is_file() or not observation_path.is_file():
        return {}

    key_columns = ["item_category_code", "item_code", "kind_code", "rank_code"]
    items = _read_csv(
        item_path,
        {"canonical_item", "quantity", "unit", *key_columns},
    )
    observations = _read_csv(
        observation_path,
        {"observed_date", "price_scope_type", "price", *key_columns},
    )
    observations = observations.loc[
        observations["price_scope_type"].str.upper() == "AVERAGE"
    ].copy()
    joined = observations.merge(
        items[[*key_columns, "canonical_item", "quantity", "unit"]].drop_duplicates(),
        on=key_columns,
        how="inner",
        validate="many_to_one",
    )
    joined["observed_date"] = pd.to_datetime(joined["observed_date"], errors="coerce")
    joined["price"] = pd.to_numeric(joined["price"], errors="coerce")
    joined["quantity"] = pd.to_numeric(joined["quantity"], errors="coerce")
    joined = joined.dropna(subset=["observed_date", "price", "quantity"])
    joined = joined.loc[joined["quantity"] > 0].copy()

    # KAMIS eggs are sold in 10/30-count packs. Convert both to KRW/10ea.
    # Milk is already a one-litre series and is normalized to KRW/L.
    egg_mask = joined["canonical_item"] == "계란"
    joined["normalized_price"] = joined["price"].astype(float)
    joined.loc[egg_mask, "normalized_price"] = (
        joined.loc[egg_mask, "price"] / joined.loc[egg_mask, "quantity"] * 10
    )
    return _collapse_points(
        (
            str(row.canonical_item).strip(),
            row.observed_date.date(),
            float(row.normalized_price),
        )
        for row in joined.itertuples(index=False)
    )


def load_external_market_series(
    fis_dir: Path | None,
    kamis_dir: Path | None,
) -> tuple[dict[str, list[MarketPoint]], dict[str, Any]]:
    fis = load_fis_series(fis_dir)
    kamis = load_kamis_series(kamis_dir)
    combined = {**fis, **kamis}
    summary = {
        "fis_available": bool(fis),
        "kamis_available": bool(kamis),
        "items": {
            item: {
                "source": "FIS" if item in fis else "KAMIS",
                "observation_count": len(points),
                "date_min": points[0].observed_date.isoformat(),
                "date_max": points[-1].observed_date.isoformat(),
            }
            for item, points in sorted(combined.items())
        },
    }
    return combined, summary


def _point_on_or_before(
    points: list[MarketPoint],
    observed_dates: list[date],
    cutoff: date,
) -> MarketPoint | None:
    index = bisect.bisect_right(observed_dates, cutoff) - 1
    return points[index] if index >= 0 else None


def features_as_of(points: list[MarketPoint], as_of_date: date) -> dict[str, float]:
    empty = {column: 0.0 for column in EXTERNAL_FEATURE_COLUMNS}
    if not points:
        return empty

    dates = [point.observed_date for point in points]
    current = _point_on_or_before(points, dates, as_of_date)
    if current is None:
        return empty

    features = empty | {
        "external_market_available": 1.0,
        "external_market_price": current.price,
        "external_market_age_days": float((as_of_date - current.observed_date).days),
    }
    for days in (7, 14, 28):
        previous = _point_on_or_before(points, dates, as_of_date - timedelta(days=days))
        if previous is not None and previous.price > 0:
            features[f"external_market_change_{days}d_pct"] = (
                (current.price - previous.price) / previous.price * 100
            )

    window_start = as_of_date - timedelta(days=28)
    window_prices = [
        point.price
        for point in points
        if window_start <= point.observed_date <= current.observed_date
    ]
    if window_prices:
        window_mean = sum(window_prices) / len(window_prices)
        if window_mean > 0:
            features["external_market_price_vs_mean_28d_pct"] = (
                (current.price - window_mean) / window_mean * 100
            )
    return features


def enrich_rows_with_external_market(
    rows: list[dict[str, Any]],
    series_by_item: dict[str, list[MarketPoint]],
) -> int:
    enriched_count = 0
    for row in rows:
        survey_date = pd.Timestamp(row["survey_date"]).date()
        features = features_as_of(
            series_by_item.get(str(row["canonical_item"]), []),
            survey_date,
        )
        row.update(features)
        enriched_count += int(features["external_market_available"] == 1.0)
    return enriched_count
