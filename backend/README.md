# backend

KCA 1차 FastAPI. 시세는 Docker MySQL `unit_price` 조회. 발주는 Notion 9/2 기준: 농협·이마트·롯데 브랜드 안에서 조합 TOP 3. 온라인 가정. 지점 분산 없음.

예측 2주와 BUY/WAIT/HOLD는 `data/brand_forecasts.json` (승빈 신호 JSON). 품목 카드는 농협·이마트·롯데 행의 대표 신호.

JWT, 저장 리스트, 지역 필터는 만들지 않는다.

## 스키마

- `sql/001_kca_schema.sql` — KCA 7테이블. `retailer` 포함.
- `sql/002_kamis_fis_schema.sql` — KAMIS 2 + FIS 2.

적재된 행이 있으면 `apply_schema.ps1`을 다시 돌리지 않는다.

## 서버

저장소 루트 `.env`의 `MYSQL_PORT=3307` 을 쓴다.

```powershell
cd backend
python -m pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

- Swagger: http://127.0.0.1:8000/docs
- CORS: 3000 / 5000 / 5001 / 5173

| 주소 | 역할 |
|------|------|
| `GET /health` | 서버·DB |
| `GET /signals` | 품목별 BUY / WAIT / HOLD. 신호는 brand JSON |
| `GET /prices/history?item_name=밀가루` | 브랜드 평균 시계열 (`retailer.name`) |
| `GET /prices/history?item_name=밀가루&brand=이마트` | 지점 표 + 최저/평균/최고 |
| `POST /predict` | 2주 예측. 현재가는 DB, 예측가·신호는 brand JSON |
| `POST /orders/quote` | 최근 조사일 전체 평균 단가 발주 금액 (기준선) |
| `POST /optimize/basket` | 농협·이마트·롯데 조합 합계 TOP 3. `mode=today` 또는 `forecast` |

`grain` 은 `brand` 또는 `store`. 지역 쿼리는 없다. 비교는 `unit_price`.
화면 브랜드: 농협·이마트·롯데. DB는 `(주)농협유통`/`(주)농협하나로유통`, `이마트`, `롯데슈퍼`. JSON은 `농협하나로마트`, `이마트`, `롯데마트·슈퍼`.

장바구니 예:

```json
{
  "mode": "today",
  "items": [
    {"item_name": "밀가루", "quantity": 10},
    {"item_name": "버터", "quantity": 2}
  ]
}
```

`mode`를 `forecast`로 두면 2주 예측가로 같은 TOP 3를 계산한다.
