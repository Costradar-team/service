from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

FORECAST_PATH = Path(__file__).resolve().parent / "data" / "brand_forecasts.json"
FORECAST_STEP_2WEEKS = 1

# 화면 이름(9/2) → DB retailer.name / 대원 JSON brandName
BASKET_BRANDS = (
    {
        "display": "농협",
        "db_names": ("(주)농협유통", "(주)농협하나로유통"),
        "forecast_names": ("농협하나로마트",),
    },
    {
        "display": "이마트",
        "db_names": ("이마트",),
        "forecast_names": ("이마트",),
    },
    {
        "display": "롯데",
        "db_names": ("롯데슈퍼",),
        "forecast_names": ("롯데마트·슈퍼",),
    },
)


def display_brands() -> list[str]:
    return [row["display"] for row in BASKET_BRANDS]


def brand_spec(name: str) -> dict | None:
    wanted = name.strip()
    for row in BASKET_BRANDS:
        aliases = {row["display"], *row["db_names"], *row["forecast_names"]}
        if wanted in aliases:
            return row
    return None


@lru_cache(maxsize=1)
def load_brand_forecasts() -> dict:
    if not FORECAST_PATH.is_file():
        return {}
    return json.loads(FORECAST_PATH.read_text(encoding="utf-8"))


def _mean(values: list[float]) -> int | None:
    if not values:
        return None
    return int(round(sum(values) / len(values)))


def forecast_rows(
    item_name: str,
    brand: str | None = None,
    step: int = FORECAST_STEP_2WEEKS,
) -> list[dict]:
    payload = load_brand_forecasts()
    forecasts = payload.get("forecasts") or []
    names = None
    if brand is not None:
        spec = brand_spec(brand)
        if spec is None:
            return []
        names = set(spec["forecast_names"])
    out = []
    for row in forecasts:
        if row.get("canonicalItem") != item_name:
            continue
        if int(row.get("forecastHorizonStep") or 0) != step:
            continue
        if names is not None and row.get("brandName") not in names:
            continue
        out.append(row)
    if not out:
        return []
    latest = max(str(row.get("asOfDate") or "") for row in out)
    return [row for row in out if str(row.get("asOfDate") or "") == latest]


def predicted_unit_price(
    item_name: str,
    brand: str | None = None,
    step: int = FORECAST_STEP_2WEEKS,
) -> dict | None:
    rows = forecast_rows(item_name, brand=brand, step=step)
    prices = [float(row["modelPredictedUnitPrice"]) for row in rows if row.get("modelPredictedUnitPrice") is not None]
    mean = _mean(prices)
    if mean is None:
        return None
    first = rows[0]
    lows = [float(row["modelPredictedUnitPrice"]) for row in rows]
    return {
        "unit_price": mean,
        "pred_low": int(round(min(lows))),
        "pred_high": int(round(max(lows))),
        "as_of_date": first.get("asOfDate"),
        "forecast_date": first.get("forecastDate"),
        "forecast_horizon_step": step,
        "change_percent": round(sum(float(row.get("modelPredictedChangePercent") or 0) for row in rows) / len(rows), 4),
        "brand": brand_spec(brand)["display"] if brand and brand_spec(brand) else brand,
        "source": "brand_forecasts.json",
    }
