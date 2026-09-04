from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from db import ping
from forecasts import FORECAST_PATH, predicted_unit_price
from queries import (
    BASKET_DISCLAIMER,
    DISCLAIMER,
    brand_basket,
    brand_history,
    brand_names_for_item,
    build_signals,
    latest_unit_price,
    list_items,
    resolve_brand,
    store_history,
)

app = FastAPI(
    title="CostRadar API",
    description="KCA 1차. 발주는 농협·이마트·롯데 브랜드 안 조합 TOP 3. 시세는 공개 참고용입니다.",
    version="0.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5000",
        "http://127.0.0.1:5000",
        "http://localhost:5001",
        "http://127.0.0.1:5001",
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
    mode: Literal["today", "forecast"] = "today"


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
        "forecast": "ok" if FORECAST_PATH.is_file() else "missing",
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


def signal_from_forecast(current: int, predicted: int) -> tuple[str, str, float]:
    if current <= 0:
        return "HOLD", "큰 변동은 없습니다. 필요할 때 사도 무방합니다.", 0.4
    change = (predicted - current) / current
    if change >= 0.03:
        return (
            "BUY",
            "2주 뒤 가격이 오를 것으로 보입니다. 이번 조사일 기준으로 사두면 유리합니다.",
            0.35,
        )
    if change <= -0.03:
        return (
            "WAIT",
            "2주 뒤 가격이 내릴 것으로 보입니다. 급할 필요 없습니다.",
            0.65,
        )
    return "HOLD", "큰 변동은 없습니다. 필요할 때 사도 무방합니다.", 0.4


@app.post("/predict")
def predict(body: PredictRequest) -> dict:
    require_item(body.item_name)
    latest = latest_unit_price(body.item_name)
    if latest is None:
        error(404, "NO_PRICE", f"시세가 없습니다: {body.item_name}")
    survey_date, current = latest
    forecast = predicted_unit_price(body.item_name, brand=body.brand)
    if forecast is None:
        error(404, "NO_FORECAST", f"예측값이 없습니다: {body.item_name}")
    predicted = forecast["unit_price"]
    if forecast.get("signal"):
        signal = forecast["signal"]
        message = forecast["message"]
        drop = forecast.get("drop_probability")
        if drop is None:
            _, _, drop = signal_from_forecast(current, predicted)
    else:
        signal, message, drop = signal_from_forecast(current, predicted)
    return {
        "item_name": body.item_name,
        "brand": forecast["brand"] or body.brand,
        "store_name": body.store_name,
        "survey_date": survey_date,
        "as_of_date": forecast["as_of_date"],
        "forecast_date": forecast["forecast_date"],
        "current_price": current,
        "predicted_price_2weeks": predicted,
        "pred_low": forecast["pred_low"],
        "pred_high": forecast["pred_high"],
        "drop_probability": drop,
        "signal": signal,
        "message": message,
        "disclaimer": DISCLAIMER,
        "source": forecast["source"],
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
    if not body.items:
        error(400, "EMPTY_ITEMS", "품목이 없습니다.")
    for item in body.items:
        require_item(item.item_name)
    payload = brand_basket(
        [{"item_name": item.item_name, "quantity": item.quantity} for item in body.items],
        mode=body.mode,
    )
    if not payload["brands"]:
        error(404, "NO_PRICE", "선택한 품목 조합을 채울 브랜드 시세가 없습니다.")
    baseline = quote(body)
    cheapest = payload["brands"][0]["total"]
    savings = baseline["total"] - cheapest
    percent = round(savings / baseline["total"] * 100, 1) if baseline["total"] else 0
    return {
        "disclaimer": BASKET_DISCLAIMER,
        "mode": payload["mode"],
        "survey_date": baseline["survey_date"],
        "as_of_date": payload["as_of_date"],
        "forecast_date": payload["forecast_date"],
        "forecast_horizon_step": payload["forecast_horizon_step"],
        "baseline_total": baseline["total"],
        "optimized_total": cheapest,
        "savings": savings,
        "savings_percent": percent,
        "brands": payload["brands"],
    }
