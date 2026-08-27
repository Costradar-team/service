# ML 가격예측 파이프라인 및 백엔드 연동 규격

## 1. 목적

한국소비자원 생필품 가격 데이터를 이용해 개인 카페 원재료의 단위가격 추이를
정리하고 다음 조사 시점의 가격을 예측한다. 사용 목적이 다른 두 가지 예측 단위를
동시에 제공한다.

- **세부유형별 예측**: 강력분, 중력분, 일반우유처럼 시장의 대표 가격 추이를 본다.
- **상품명별 예측**: 곰표 밀가루, 백설 설탕처럼 실제 구매 후보의 가격을 비교한다.

현재 결과는 제한된 조사일로 만든 MVP다. 서비스에 노출할 때는 반드시
`asOfDate`, 성능 지표, 권장 예측기 정보를 함께 확인한다.

## 2. 데이터 흐름

```text
한국소비자원 원천 CSV 13개, 3,062,819행
  -> 데이터 품질 검사와 품목 매핑
  -> 밀가루·설탕·버터·계란·우유 204,036행
  -> 단위가격 표준화
  -> 세부유형 데이터 463행 / 상품명 데이터 895행
  -> 기준선 평가
  -> Gradient Boosting 또는 LightGBM 학습
  -> 시간순 백테스트와 다음 시점 예측
  -> 백엔드용 JSON 요청 본문 생성
```

`data-pipeline/config/item_mapping.csv`에 포함 대상으로 표시된 상품만 사용한다.
프로파일링 테스트 파일인 `data-pipeline/data/test/kca_profile_test.csv`는 학습에
사용하지 않는다.

단위가격 기준은 다음과 같다.

| 품목 | 표준 단위 |
|---|---|
| 밀가루·설탕·버터 | KRW/kg |
| 우유 | KRW/L |
| 계란 | KRW/10ea |

동일 조사일에 여러 판매점에서 수집된 가격은 중앙값으로 집계한다. 세부유형 모델은
상품과 판매점을 모두 합치고, 상품명 모델은 동일 상품의 판매점만 합친다.

## 3. 모델 입력과 예측 대상

두 모델은 여러 가격 시계열을 하나의 글로벌 회귀 모델로 함께 학습한다.

공통 입력 특성:

- 품목, 세부유형, 단위가격 기준
- 날짜 순번과 월 주기 정보
- 직전 1·2·4회 가격
- 최근 2·4회 이동평균
- 최근 4회 가격 표준편차

상품명 모델에는 `product_name`이 추가된다. 학습 대상은 절대가격이 아니라 직전
조사 대비 가격 변화율이다.

```text
target_change_ratio = (현재 중앙 단위가격 - 직전 중앙 단위가격)
                      / 직전 중앙 단위가격
```

예측 단위가격은 다음과 같이 복원한다.

```text
예측 단위가격 = 직전 중앙 단위가격 * (1 + 예측 변화율)
```

기본 학습기는 scikit-learn `GradientBoostingRegressor`이며, 선택적으로 LightGBM을
사용할 수 있다.

## 4. 검증 방식과 현재 결과

무작위 분할을 사용하지 않는다. 과거 날짜로 학습하고 가장 최근 20% 날짜로
검증하는 시간순 백테스트를 사용한다. 학습 모델은 다음 기준선과 비교한다.

- `naive_last_value`: 다음 가격이 직전 가격과 같다고 예측
- `rolling_mean`: 다음 가격을 최근 4회 평균으로 예측

2026-08-28 실행 결과이며 원천 데이터의 마지막 조사일은 2026-07-24다.

| 예측 단위 | 시계열 수 | ML sMAPE | 직전 가격 sMAPE | 권장 예측기 |
|---|---:|---:|---:|---|
| 세부유형 | 19 | 1.2258% | 0.8824% | `naive_last_value` |
| 상품명 | 39 | 1.4338% | 1.0822% | `naive_last_value` |

sMAPE는 실제값과 예측값의 평균 상대 오차이며 낮을수록 좋다. 현재는 가격 변화가
적고 조사일이 최대 25개뿐이라 두 단위 모두 직전 가격 기준선이 더 정확하다.
`future_predictions.csv`에는 ML 예측값을 보존하되, `recommended_unit_price`에는
백테스트에서 더 정확한 예측기의 값을 넣는다.

## 5. 산출물

산출물은 `.gitignore`에 포함된 `artifacts/` 아래에 생성되며 GitHub에는 올리지 않는다.

```text
artifacts/ml/
├─ normalized_prices.csv
├─ dataset_summary.json
├─ model_dataset.csv                  # 세부유형 학습 데이터
├─ baseline_metrics.json
├─ baseline_predictions.csv
├─ model/                             # 세부유형 ML 결과
│  ├─ price_model.joblib
│  ├─ training_report.json
│  ├─ backtest_predictions.csv
│  └─ future_predictions.csv
├─ product/
│  ├─ model_dataset.csv               # 상품명 학습 데이터
│  ├─ baseline_metrics.json
│  ├─ baseline_predictions.csv
│  └─ model/
│     ├─ price_model.joblib
│     ├─ training_report.json
│     ├─ backtest_predictions.csv
│     └─ future_predictions.csv
└─ backend/
   ├─ subtype_forecasts.json
   ├─ product_forecasts.json
   └─ export_summary.json
```

## 6. 실행 방법

의존성을 설치한다.

```powershell
python -m pip install -r requirements.txt
```

데이터 품질 검사를 포함한 전체 실행:

```powershell
.\run_local.ps1 -TrainModel
```

이미 프로파일링을 완료한 경우:

```powershell
.\run_local.ps1 -SkipProfiling -TrainModel
```

LightGBM 실행:

```powershell
python -m pip install -r requirements-lightgbm.txt
.\run_local.ps1 -SkipProfiling -TrainModel -Estimator lightgbm
```

테스트:

```powershell
python -m unittest discover -s ml\tests -v
```

## 7. 백엔드 전달 계약

현재 구현 범위는 백엔드가 받을 수 있는 JSON 요청 본문을 생성하는 단계까지다.
실제 HTTP API와 DB 적재는 `backend/` 구현 후 연결한다.

권장 내부 API:

```http
POST /internal/v1/price-forecasts/batch
Content-Type: application/json
```

상품명 예측 요청 예시:

```json
{
  "schemaVersion": "1.0",
  "granularity": "product",
  "forecastCount": 1,
  "forecasts": [
    {
      "forecastDate": "2026-08-07",
      "asOfDate": "2026-07-24",
      "canonicalItem": "밀가루",
      "subtype": "중력분",
      "productName": "곰표 밀가루 중력다목적용(1kg)",
      "unitPriceBasis": "KRW/kg",
      "currentUnitPrice": 2010.0,
      "modelPredictedUnitPrice": 1999.1595,
      "modelPredictedChangePercent": -0.5393,
      "naivePredictedUnitPrice": 2010.0,
      "recommendedUnitPrice": 2010.0,
      "recommendedForecaster": "naive_last_value"
    }
  ]
}
```

세부유형 요청은 `granularity`가 `subtype`이고 `productName`이 없다. 두 JSON 파일을
각각 같은 API로 전송한다.

| 필드 | 의미 |
|---|---|
| `forecastDate` | 예측 대상 조사일 |
| `asOfDate` | 예측에 사용한 마지막 실제 조사일 |
| `unitPriceBasis` | KRW/kg, KRW/L, KRW/10ea |
| `modelPredictedUnitPrice` | ML 모델 예측값 |
| `naivePredictedUnitPrice` | 직전 가격 기준선 예측값 |
| `recommendedUnitPrice` | 백테스트 성능에 따라 선택한 권장값 |
| `recommendedForecaster` | 권장값을 만든 예측기 |

백엔드는 다음 조회 API로 프론트엔드에 제공할 수 있다.

```http
GET /api/v1/price-forecasts/subtypes?item=밀가루
GET /api/v1/price-forecasts/products?item=밀가루
```

DB 중복 방지 키는 `granularity + series_key + forecast_date`를 권장한다.
`series_key`는 세부유형이면 subtype ID, 상품명이면 product ID로 구성한다.

## 8. 제한 및 후속 작업

- 현재 조사일은 최대 25개이며 계란은 13개뿐이다.
- 마지막 원천 조사일보다 시간이 지난 경우 생성된 `forecastDate`도 과거일 수 있다.
  백엔드는 `asOfDate`를 반드시 노출하고 최신 원천 데이터 여부를 검사해야 한다.
- 현재 모델은 판매점, 제조사, 세일, 원플러스원 여부를 직접 특성으로 사용하지 않는다.
- 실제 API 인증, 재시도, 멱등성 키, DB 마이그레이션은 백엔드 구현 범위다.
- 더 많은 조사일이 쌓이기 전에는 서비스용 확정 예측으로 표현하지 않는다.
