from collections import defaultdict

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from db import ping
from queries import (
    BASKET_DISCLAIMER,
    DISCLAIMER,
    brand_history,
    brand_names_for_item,
    build_signals,
    cheapest_store,
    latest_unit_price,
    list_items,
    resolve_brand,
    store_history,
)

app = FastAPI(
    title="CostRadar API",
    description="KCA 1차. 시세는 공개 참고용이며 실시간 마트 가격이 아닙니다.",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5000",
        "http://127.0.0.1:5000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class QuoteItem(BaseModel):
    item_name: str
    quantity: float = Field(gt=0)


class QuoteRequest(BaseModel):
    items: list[QuoteItem]
    unit: str | None = None


class PredictRequest(BaseModel):
    item_name: str
    store_name: str | None = None
    brand: str | None = None


def error(status: int, code: str, message: str) -> None:
    raise HTTPException(status_code=status, detail={"error_code": code, "message": message})


def require_item(item_name: str) -> str:
    names = list_items()
    if item_name not in names:
        error(400, "ITEM_NOT_FOUND", f"지원하지 않는 품목입니다: {item_name}")
    return item_name


@app.get("/health")
def health() -> dict:
    db_ok = ping()
    return {
        "status": "ok" if db_ok else "degraded",
        "source": "kca",
        "db": "ok" if db_ok else "error",
    }


@app.get("/signals")
def signals() -> dict:
    return build_signals()


@app.get("/prices/history")
def prices_history(
    item_name: str = Query(..., description="canonical_item 한글명. 예: 밀가루"),
    brand: str | None = Query(None, description="retailer.name. 없으면 브랜드 평균 시계열"),
) -> dict:
    require_item(item_name)
    if brand is None:
        payload = brand_history(item_name)
        if not payload["brands"]:
            error(404, "NO_PRICE", f"시세가 없습니다: {item_name}")
        return payload

    known = brand_names_for_item(item_name)
    resolved = resolve_brand(brand, known)
    if resolved is None:
        error(400, "BRAND_NOT_FOUND", f"지원하지 않는 브랜드입니다: {brand}")
    payload = store_history(item_name, resolved)
    if not payload["stores"]:
        error(404, "NO_PRICE", f"해당 브랜드 시세가 없습니다: {resolved}")
    return payload


@app.post("/predict")
def predict(body: PredictRequest) -> dict:
    require_item(body.item_name)
    latest = latest_unit_price(body.item_name)
    if latest is None:
        error(404, "NO_PRICE", f"시세가 없습니다: {body.item_name}")
    survey_date, current = latest
    signal_row = next(
        (row for row in build_signals()["items"] if row["item_name"] == body.item_name),
        None,
    )
    signal = signal_row["signal"] if signal_row else "HOLD"
    message = signal_row["message"] if signal_row else ""
    drop = signal_row["drop_probability"] if signal_row else 0.4
    predicted = round(current * (0.97 if signal == "BUY" else 1.01))
    return {
        "item_name": body.item_name,
        "brand": body.brand,
        "store_name": body.store_name,
        "survey_date": survey_date,
        "current_price": current,
        "predicted_price_2weeks": predicted,
        "pred_low": predicted - 300,
        "pred_high": predicted + 250,
        "drop_probability": drop,
        "signal": signal,
        "message": message,
        "disclaimer": DISCLAIMER,
    }


@app.post("/orders/quote")
def quote(body: QuoteRequest) -> dict:
    if not body.items:
        error(400, "EMPTY_ITEMS", "품목이 없습니다.")
    lines = []
    total = 0
    survey_date = None
    for item in body.items:
        require_item(item.item_name)
        latest = latest_unit_price(item.item_name)
        if latest is None:
            error(404, "NO_PRICE", f"시세가 없습니다: {item.item_name}")
        survey_date, unit_price = latest
        amount = round(unit_price * item.quantity)
        total += amount
        lines.append(
            {
                "item_name": item.item_name,
                "quantity": item.quantity,
                "unit_price": unit_price,
                "amount": amount,
                "survey_date": survey_date,
            }
        )
    return {
        "disclaimer": DISCLAIMER,
        "survey_date": survey_date,
        "total": total,
        "items": lines,
    }


@app.post("/optimize/basket")
def optimize_basket(body: QuoteRequest) -> dict:
    baseline = quote(body)
    grouped: dict[str, list] = defaultdict(list)
    optimized_total = 0
    for item in body.items:
        latest = latest_unit_price(item.item_name)
        if latest is None:
            error(404, "NO_PRICE", f"시세가 없습니다: {item.item_name}")
        item_date, _current = latest
        cheapest = cheapest_store(item.item_name, item_date)
        if cheapest is None:
            error(404, "NO_PRICE", f"시세가 없습니다: {item.item_name}")
        amount = round(cheapest["unit_price"] * item.quantity)
        optimized_total += amount
        grouped[cheapest["store_name"]].append(
            {
                "item_name": item.item_name,
                "quantity": item.quantity,
                "unit_price": cheapest["unit_price"],
                "amount": amount,
            }
        )
    savings = baseline["total"] - optimized_total
    stores = [{"store_name": name, "items": items} for name, items in grouped.items()]
    percent = round(savings / baseline["total"] * 100, 1) if baseline["total"] else 0
    return {
        "disclaimer": BASKET_DISCLAIMER,
        "survey_date": baseline["survey_date"],
        "baseline_total": baseline["total"],
        "optimized_total": optimized_total,
        "savings": savings,
        "savings_percent": percent,
        "stores": stores,
    }
