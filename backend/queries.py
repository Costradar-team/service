from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from db import fetch_all, fetch_one

DISCLAIMER = "공개 시세 기준 참고용이며 실시간 마트 가격이 아닙니다."
BASKET_DISCLAIMER = "공개 시세 기준 참고용이며 배송비·거리는 반영하지 않았습니다."

# 화면에서 쓰던 이름 → retailer.name
BRAND_ALIASES = {
    "롯데마트": "롯데슈퍼",
    "GS": "GS더프레시",
}

SIGNAL_MESSAGES = {
    "BUY": "이번 조사일 기준 가격이 올랐습니다. 더 오르기 전에 사두면 유리합니다.",
    "WAIT": "이번 조사일 기준 가격이 내렸습니다. 급할 필요 없습니다.",
    "HOLD": "큰 변동은 없습니다. 필요할 때 사도 무방합니다.",
}

ITEM_SQL = """
SELECT c.name
FROM canonical_item c
ORDER BY c.name
"""

ITEM_MEAN_SQL = """
SELECT po.survey_date, AVG(po.unit_price) AS avg_price
FROM price_observation po
JOIN product p ON p.product_id = po.product_id
JOIN item_subtype s ON s.subtype_id = p.subtype_id
JOIN canonical_item c ON c.canonical_item_id = s.canonical_item_id
WHERE c.name = :item_name
GROUP BY po.survey_date
ORDER BY po.survey_date
"""

BRAND_MEAN_SQL = """
SELECT r.name AS brand, po.survey_date, AVG(po.unit_price) AS avg_price
FROM price_observation po
JOIN product p ON p.product_id = po.product_id
JOIN item_subtype s ON s.subtype_id = p.subtype_id
JOIN canonical_item c ON c.canonical_item_id = s.canonical_item_id
JOIN store st ON st.store_id = po.store_id
JOIN retailer r ON r.retailer_id = st.retailer_id
WHERE c.name = :item_name
GROUP BY r.name, po.survey_date
ORDER BY r.name, po.survey_date
"""

STORE_MEAN_SQL = """
SELECT st.name AS store_name, po.survey_date, AVG(po.unit_price) AS avg_price
FROM price_observation po
JOIN product p ON p.product_id = po.product_id
JOIN item_subtype s ON s.subtype_id = p.subtype_id
JOIN canonical_item c ON c.canonical_item_id = s.canonical_item_id
JOIN store st ON st.store_id = po.store_id
JOIN retailer r ON r.retailer_id = st.retailer_id
WHERE c.name = :item_name AND r.name = :brand
GROUP BY st.store_id, st.name, po.survey_date
ORDER BY st.name, po.survey_date
"""

LATEST_STORE_SQL = """
SELECT st.name AS store_name, AVG(po.unit_price) AS avg_price
FROM price_observation po
JOIN product p ON p.product_id = po.product_id
JOIN item_subtype s ON s.subtype_id = p.subtype_id
JOIN canonical_item c ON c.canonical_item_id = s.canonical_item_id
JOIN store st ON st.store_id = po.store_id
WHERE c.name = :item_name AND po.survey_date = :survey_date
GROUP BY st.store_id, st.name
ORDER BY avg_price ASC
"""


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _iso(value) -> str:
    return _as_date(value).isoformat()


def _money(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return int(value.quantize(Decimal("1")))
    return int(round(float(value)))


def period_label(survey: date) -> str:
    week = (survey.day - 1) // 7 + 1
    return f"{survey.month}월 {week}주"


def list_items() -> list[str]:
    return [row["name"] for row in fetch_all(ITEM_SQL)]


def resolve_brand(brand: str, known: list[str]) -> str | None:
    wanted = BRAND_ALIASES.get(brand, brand)
    if wanted in known:
        return wanted
    return None


def item_means(item_name: str) -> list[dict]:
    rows = fetch_all(ITEM_MEAN_SQL, {"item_name": item_name})
    return [
        {"survey_date": _as_date(row["survey_date"]), "avg_price": _money(row["avg_price"])}
        for row in rows
    ]


def signal_from_change(prev: int, latest: int) -> str:
    if prev <= 0:
        return "HOLD"
    change = (latest - prev) / prev
    if change >= 0.03:
        return "BUY"
    if change <= -0.03:
        return "WAIT"
    return "HOLD"


def build_signals() -> dict:
    items = []
    as_of = None
    for name in list_items():
        series = item_means(name)
        if not series:
            continue
        latest = series[-1]
        prev = series[-2] if len(series) >= 2 else latest
        signal = signal_from_change(prev["avg_price"], latest["avg_price"])
        as_of = _iso(latest["survey_date"])
        drop = 0.65 if signal == "BUY" else 0.55 if signal == "WAIT" else 0.40
        items.append(
            {
                "item_name": name,
                "current_price": latest["avg_price"],
                "survey_date": as_of,
                "signal": signal,
                "message": SIGNAL_MESSAGES[signal],
                "drop_probability": drop,
            }
        )
    return {
        "as_of": as_of,
        "disclaimer": DISCLAIMER,
        "items": items,
    }


def _align(series_by_key: dict[str, dict[str, int]], dates: list[date]) -> list[dict]:
    iso_dates = [_iso(d) for d in dates]
    out = []
    for key, by_date in series_by_key.items():
        prices = [by_date.get(iso) for iso in iso_dates]
        latest = next((p for p in reversed(prices) if p is not None), None)
        out.append({"name": key, "prices": prices, "latest_price": latest})
    return out


def brand_history(item_name: str) -> dict:
    rows = fetch_all(BRAND_MEAN_SQL, {"item_name": item_name})
    dates: list[date] = []
    seen: set[str] = set()
    by_brand: dict[str, dict[str, int]] = {}
    for row in rows:
        survey = _as_date(row["survey_date"])
        iso = _iso(survey)
        if iso not in seen:
            seen.add(iso)
            dates.append(survey)
        brand = row["brand"]
        by_brand.setdefault(brand, {})[iso] = _money(row["avg_price"])
    dates.sort()
    aligned = _align(by_brand, dates)
    brands = [
        {"brand": row["name"], "prices": row["prices"], "latest_price": row["latest_price"]}
        for row in aligned
        if row["latest_price"] is not None
    ]
    brands.sort(key=lambda row: row["brand"])
    return {
        "item_name": item_name,
        "grain": "brand",
        "disclaimer": DISCLAIMER,
        "period_labels": [period_label(d) for d in dates],
        "survey_dates": [_iso(d) for d in dates],
        "brands": brands,
    }


def store_history(item_name: str, brand: str) -> dict:
    rows = fetch_all(STORE_MEAN_SQL, {"item_name": item_name, "brand": brand})
    dates: list[date] = []
    seen: set[str] = set()
    by_store: dict[str, dict[str, int]] = {}
    for row in rows:
        survey = _as_date(row["survey_date"])
        iso = _iso(survey)
        if iso not in seen:
            seen.add(iso)
            dates.append(survey)
        by_store.setdefault(row["store_name"], {})[iso] = _money(row["avg_price"])
    dates.sort()
    aligned = _align(by_store, dates)
    stores = [
        {
            "store_name": row["name"],
            "prices": row["prices"],
            "latest_price": row["latest_price"],
        }
        for row in aligned
        if row["latest_price"] is not None
    ]
    stores.sort(key=lambda row: (row["latest_price"], row["store_name"]))
    latest_values = [row["latest_price"] for row in stores]
    n = len(dates)
    avg_prices = []
    for i in range(n):
        col = [row["prices"][i] for row in stores if row["prices"][i] is not None]
        avg_prices.append(round(sum(col) / len(col)) if col else None)
    min_store = stores[0]
    max_store = stores[-1]
    return {
        "item_name": item_name,
        "grain": "store",
        "brand": brand,
        "disclaimer": DISCLAIMER,
        "period_labels": [period_label(d) for d in dates],
        "survey_dates": [_iso(d) for d in dates],
        "stores": stores,
        "series_extrema": {
            "min": {
                "store_name": min_store["store_name"],
                "prices": min_store["prices"],
                "latest_price": min_store["latest_price"],
            },
            "avg": {
                "store_name": None,
                "prices": avg_prices,
                "latest_price": round(sum(latest_values) / len(latest_values)),
            },
            "max": {
                "store_name": max_store["store_name"],
                "prices": max_store["prices"],
                "latest_price": max_store["latest_price"],
            },
        },
    }


def latest_unit_price(item_name: str) -> tuple[str, int] | None:
    series = item_means(item_name)
    if not series:
        return None
    last = series[-1]
    return _iso(last["survey_date"]), last["avg_price"]


def cheapest_store(item_name: str, survey_date: str) -> dict | None:
    row = fetch_one(
        LATEST_STORE_SQL,
        {"item_name": item_name, "survey_date": survey_date},
    )
    if not row:
        return None
    return {"store_name": row["store_name"], "unit_price": _money(row["avg_price"])}


def brand_names_for_item(item_name: str) -> list[str]:
    rows = fetch_all(
        """
        SELECT DISTINCT r.name AS brand
        FROM price_observation po
        JOIN product p ON p.product_id = po.product_id
        JOIN item_subtype s ON s.subtype_id = p.subtype_id
        JOIN canonical_item c ON c.canonical_item_id = s.canonical_item_id
        JOIN store st ON st.store_id = po.store_id
        JOIN retailer r ON r.retailer_id = st.retailer_id
        WHERE c.name = :item_name
        ORDER BY r.name
        """,
        {"item_name": item_name},
    )
    return [row["brand"] for row in rows]
