# backend

KCA 1차 FastAPI. 시세는 Docker MySQL `unit_price` 조회. KAMIS·FIS 테이블은 적재용이며 이 API는 쓰지 않는다.

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
- CORS: 3000 / 5000 / 5173

| 주소 | 역할 |
|------|------|
| `GET /health` | 서버·DB |
| `GET /signals` | 품목별 BUY / WAIT / HOLD |
| `GET /prices/history?item_name=밀가루` | 브랜드 평균 시계열 (`retailer.name`) |
| `GET /prices/history?item_name=밀가루&brand=이마트` | 지점 표 + 최저/평균/최고 |
| `POST /predict` | 향후 가격 (숫자는 목, 현재가는 DB) |
| `POST /orders/quote` | 최근 조사일 평균 단가 발주 금액 |
| `POST /optimize/basket` | 품목별 최저 지점 조합 + 절감액 |

`grain` 은 `brand` 또는 `store`. 지역 쿼리는 없다. 비교는 `unit_price`.
`롯데마트` → `롯데슈퍼`, `GS` → `GS더프레시`.
