from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

FORECAST_PATH = Path(__file__).resolve().parent / "data" / "brand_forecasts.json"
FORECAST_STEP_2WEEKS = 1
HOLD_MESSAGE = "2주 뒤 유의미한 가격 변동이 예상되지 않습니다. 통상적인 주기에 맞춰 구매해도 무방합니다."

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
BASKET_FORECAST_NAMES = frozenset(
    name for spec in BASKET_BRANDS for name in spec["forecast_names"]
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
    basket_only: bool | None = None,
) -> list[dict]:
    payload = load_brand_forecasts()
    forecasts = payload.get("forecasts") or []
    names = None
    if brand is not None:
        spec = brand_spec(brand)
        if spec is None:
            return []
        names = set(spec["forecast_names"])
    elif basket_only is not False:
        names = set(BASKET_FORECAST_NAMES)
    out = []
    for row in forecasts:
        if row.get("canonicalItem") != item_name:
            continue
        if int(row.get("forecastHorizonStep") or 0) != step:
            continue
        if names is not None and row.get("brandName") not in names:
            continue
        out.append(row)
    return out


def _floats(rows: list[dict], key: str) -> list[float]:
    values = []
    for row in rows:
        if row.get(key) is not None:
            values.append(float(row[key]))
    return values


def pick_signal_row(rows: list[dict]) -> dict:
    """품목(또는 브랜드) 카드용 대표 행. BUY/WAIT가 있으면 HOLD로 덮지 않는다."""
    scored = []
    for row in rows:
        signal = str(row.get("signal") or "HOLD").upper()
        if signal not in {"BUY", "WAIT", "HOLD"}:
            signal = "HOLD"
        change = abs(float(row.get("modelPredictedChangePercent") or 0))
        scored.append((signal, change, row))
    active = [item for item in scored if item[0] != "HOLD"]
    pool = active or scored
    pool.sort(key=lambda item: (item[1], 1 if item[0] == "WAIT" else 0), reverse=True)
    return pool[0][2]


def predicted_unit_price(
    item_name: str,
    brand: str | None = None,
    step: int = FORECAST_STEP_2WEEKS,
) -> dict | None:
    rows = forecast_rows(item_name, brand=brand, step=step)
    prices = _floats(rows, "modelPredictedUnitPrice")
    mean = _mean(prices)
    if mean is None:
        return None
    picked = pick_signal_row(rows)
    lows = _floats(rows, "predLow") or prices
    highs = _floats(rows, "predHigh") or prices
    drop = picked.get("dropProbability")
    signal = str(picked.get("signal") or "HOLD").upper()
    if signal not in {"BUY", "WAIT", "HOLD"}:
        signal = "HOLD"
    message = picked.get("signalMessage") or HOLD_MESSAGE
    changes = _floats(rows, "modelPredictedChangePercent")
    return {
        "unit_price": mean,
        "pred_low": int(round(min(lows))),
        "pred_high": int(round(max(highs))),
        "as_of_date": picked.get("asOfDate") or rows[0].get("asOfDate"),
        "forecast_date": picked.get("forecastDate") or rows[0].get("forecastDate"),
        "forecast_horizon_step": step,
        "change_percent": round(sum(changes) / len(changes), 4) if changes else 0.0,
        "brand": brand_spec(brand)["display"] if brand and brand_spec(brand) else brand,
        "signal": signal,
        "message": message,
        "drop_probability": round(float(drop), 4) if drop is not None else None,
        "source": "brand_forecasts.json",
    }
