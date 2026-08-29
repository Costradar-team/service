# ML 가격예측 파이프라인 및 백엔드 연동 규격

## 1. 목적

한국소비자원 생필품 가격 데이터를 이용해 개인 카페 원재료의 단위가격 추이를
정리하고 다음 조사 시점의 가격을 예측한다. 사용 목적이 다른 네 가지 예측 단위를
동시에 제공한다.

- **세부유형별 예측**: 강력분, 중력분, 일반우유처럼 시장의 대표 가격 추이를 본다.
- **상품명별 예측**: 곰표 밀가루, 백설 설탕처럼 실제 구매 후보의 가격을 비교한다.
- **브랜드별 예측**: 같은 상품을 이마트, 롯데마트·슈퍼, GS더프레시처럼 묶어
  브랜드 가격 추이를 비교한다.
- **지점별 직접예측**: 브랜드 보정계수를 사용하지 않고 각 상품과 지점에서 관측된
  실제 단위가격 이력으로 미래 지점 가격을 직접 예측한다.

현재 결과는 제한된 조사일로 만든 MVP다. 서비스에 노출할 때는 반드시
`asOfDate`와 성능 지표를 함께 확인한다.

## 2. 데이터 흐름

```text
한국소비자원 원천 CSV 13개, 3,062,819행
  -> 데이터 품질 검사와 품목 매핑
  -> 밀가루·설탕·버터·계란·우유 204,036행
  -> 단위가격 표준화
  -> 세부유형 463행 / 상품명 895행 / 브랜드 4,612행 / 지점 204,036행
  -> 기준선 평가
  -> Gradient Boosting 또는 LightGBM 학습과 시간순 백테스트
  -> 운영 모델 저장
  -> 새 데이터에서는 저장된 모델로 다음 시점 예측
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
상품과 판매점을 모두 합치고, 상품명 모델은 동일 상품의 판매점만 합친다. 브랜드
모델은 같은 상품과 브랜드에 속한 지점 가격의 중앙값을 사용한다. 지점 모델은
`상품명 × 브랜드 × 판매업소`의 관측 단위가격을 직접 사용한다. 현재 원천 데이터는
상품명·판매업소·조사일 중복이 없으며, 향후 중복이 생길 경우 같은 키의 중앙값 하나를
직접가격으로 사용한다.

## 3. 모델 입력과 예측 대상

네 모델은 각 예측 단위의 여러 가격 시계열을 하나의 글로벌 회귀 모델로 함께
학습한다. 지점마다 별도 모델을 만들지 않는다.

공통 입력 특성:

- 품목, 세부유형, 단위가격 기준
- 날짜 순번과 월 주기 정보
- 직전 1·2·4회 가격
- 최근 2·4회 이동평균
- 최근 4회 가격 표준편차

상품명 모델에는 `product_name`, 브랜드 모델에는 `product_name`과 `brand_name`,
지점 모델에는 `product_name`, `brand_name`, `store_name`이 추가된다. 지점 이름은
고유값이 많으므로 기본 Gradient Boosting 경로에서는 교차적합 Target Encoding을
사용한다. 학습 대상은 절대가격이 아니라 직전 조사 대비 가격 변화율이다.

```text
target_change_ratio = (현재 중앙 단위가격 - 직전 중앙 단위가격)
                      / 직전 중앙 단위가격
```

지점 모델에서는 위 식의 중앙 단위가격 대신 해당 지점의 실제 단위가격을 사용한다.

예측 단위가격은 다음과 같이 복원한다.

```text
예측 단위가격 = 직전 중앙 단위가격 * (1 + 예측 변화율)
```

지점 모델의 복원 기준은 직전 지점 실제가격이며 결과 필드는 원화 단위의
`modelPredictedUnitPrice`다. 브랜드 평균에 보정계수를 곱한 값이 아니다.

기본 예측 범위는 다음 조사 시점 1개다. `forecast_horizon`을 2 이상으로 지정하면
재귀형 다단계 예측을 수행한다. 1단계는 마지막 실제 가격으로 특성을 만들고,
2단계부터는 직전 모델 예측값을 임시 이력에 추가한 뒤 lag와 이동통계를 다시
계산한다. 이 임시 이력은 예측 프로세스 안에서만 사용하며 원천 데이터, 모델링
데이터셋 또는 저장된 모델에는 반영하지 않는다.

```text
실제 이력 -> 1단계 예측
실제 이력 + 1단계 예측 -> 2단계 예측
실제 이력 + 1·2단계 예측 -> 3단계 예측
```

기본 학습기는 scikit-learn `GradientBoostingRegressor`이며, 선택적으로 LightGBM을
사용할 수 있다.

학습과 예측은 분리되어 있다. 학습 명령은 시간순 백테스트가 끝난 뒤 전체
모델링 가능 이력으로 운영 모델을 적합하여 `price_model.joblib`에 저장한다. 예측
명령은 해당 파일을 불러오며 모델 파라미터를 갱신하지 않는다. 새 CSV의 가격으로
lag와 이동통계만 다시 계산하는 것은 재학습이 아니라 예측 입력 생성이다.

## 4. 검증 방식과 현재 결과

무작위 분할을 사용하지 않는다. 과거 날짜로 학습하고 가장 최근 20% 날짜로
검증하는 시간순 백테스트를 사용한다. 학습 모델은 다음 기준선과 비교한다.

- `naive_last_value`: 다음 가격이 직전 가격과 같다고 예측
- `rolling_mean`: 다음 가격을 최근 4회 평균으로 예측

2026-08-29 실행 결과이며 원천 데이터의 마지막 조사일은 2026-07-24다.

| 예측 단위 | 시계열 수 | ML sMAPE | 직전 가격 기준선 sMAPE |
|---|---:|---:|---:|
| 세부유형 | 19 | 1.2258% | 0.8824% |
| 상품명 | 39 | 1.4338% | 1.0822% |
| 브랜드 | 222 | 0.8904% | 0.8062% |
| 지점 직접가격 | 9,615 | 1.5455% | 1.2062% |

sMAPE는 실제값과 예측값의 평균 상대 오차이며 낮을수록 좋다. 현재는 가격 변화가
적고 조사일이 최대 25개뿐이라 네 단위 모두 직전 가격 기준선이 더 정확하다.
직전 가격 기준선은 성능 비교에만 사용한다. `future_predictions.csv`와 백엔드
JSON에는 이를 예측값으로 넣지 않으며, ML이 계산한 `modelPredictedUnitPrice`만
외부 예측값으로 제공한다.

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
│  ├─ prediction_report.json
│  └─ future_predictions.csv
├─ product/
│  ├─ model_dataset.csv               # 상품명 학습 데이터
│  ├─ baseline_metrics.json
│  ├─ baseline_predictions.csv
│  └─ model/
│     ├─ price_model.joblib
│     ├─ training_report.json
│     ├─ backtest_predictions.csv
│     ├─ prediction_report.json
│     └─ future_predictions.csv
├─ brand/
│  ├─ model_dataset.csv               # 상품·브랜드별 중앙가격 데이터
│  ├─ baseline_metrics.json
│  └─ model/
│     ├─ price_model.joblib
│     └─ future_predictions.csv
├─ store/
│  ├─ model_dataset.csv               # 상품·지점별 실제가격 데이터
│  ├─ baseline_metrics.json
│  └─ model/
│     ├─ price_model.joblib
│     └─ future_predictions.csv
└─ backend/
   ├─ subtype_forecasts.json
   ├─ product_forecasts.json
   ├─ brand_forecasts.json
   ├─ store_forecasts.json
   └─ export_summary.json
```

## 6. 실행 방법

의존성을 설치한다.

```powershell
python -m pip install -r requirements.txt
```

최초 모델 학습 또는 의도한 재학습:

```powershell
.\run_local.ps1 -TrainModel
```

이미 프로파일링을 완료한 상태에서 최초 모델 학습 또는 재학습:

```powershell
.\run_local.ps1 -SkipProfiling -TrainModel
```

새 CSV 추가 후 저장된 모델로 예측만 갱신:

```powershell
.\run_local.ps1 -SkipProfiling
```

다음 3개 조사 시점을 재귀적으로 예측:

```powershell
.\run_local.ps1 -SkipProfiling -ForecastHorizon 3
```

예측 전 실행되는 데이터 변환과 lag·이동통계 갱신은 모델 학습이 아니다. 저장된
모델 파일이 없거나 구형 형식이면 먼저 `-TrainModel`로 한 번 학습해야 한다.

현재 재학습은 자동 예약하지 않고 `-TrainModel`을 명시한 경우에만 수행한다.
데이터가 약 2주 간격이므로 새 조사일 6개, 즉 약 3개월치가 누적되었을 때 재학습을
검토한다. 실제값이 확보되면 직전 검증 sMAPE 대비 오차 증가를 함께 확인하여 필요할
경우 더 일찍 재학습한다. 데이터가 더 쌓인 후 이 기준은 다시 평가해야 한다.

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
  "schemaVersion": "1.3",
  "granularity": "product",
  "forecastHorizon": 1,
  "forecastCount": 1,
  "forecasts": [
    {
      "forecastDate": "2026-08-07",
      "forecastHorizonStep": 1,
      "asOfDate": "2026-07-24",
      "canonicalItem": "밀가루",
      "subtype": "중력분",
      "productName": "곰표 밀가루 중력다목적용(1kg)",
      "unitPriceBasis": "KRW/kg",
      "currentUnitPrice": 2010.0,
      "recursiveInputUnitPrice": 2010.0,
      "recursiveInputSource": "observed",
      "modelPredictedUnitPrice": 1999.1595,
      "modelPredictedStepChangePercent": -0.5393,
      "modelPredictedChangePercent": -0.5393
    }
  ]
}
```

세부유형 요청은 `granularity`가 `subtype`이고 `productName`이 없다. 브랜드 요청은
`granularity=brand`와 `brandName`을 포함한다. 지점 직접예측은
`granularity=store`, `brandName`, `storeName`, `lastActualUnitPrice`를 포함하고
`currentUnitPrice`는 사용하지 않는다. 네 JSON 파일을 각각 같은 내부 배치 API로
전송할 수 있다.

지점 직접예측 항목 예시:

```json
{
  "forecastDate": "2026-08-07",
  "forecastHorizonStep": 1,
  "asOfDate": "2026-07-24",
  "canonicalItem": "밀가루",
  "subtype": "중력분",
  "productName": "곰표 밀가루 중력다목적용(1kg)",
  "brandName": "이마트",
  "storeName": "이마트월계점",
  "unitPriceBasis": "KRW/kg",
  "lastActualUnitPrice": 1980.0,
  "recursiveInputUnitPrice": 1980.0,
  "recursiveInputSource": "observed",
  "modelPredictedUnitPrice": 2010.0
}
```

| 필드 | 의미 |
|---|---|
| `forecastDate` | 예측 대상 조사일 |
| `forecastHorizonStep` | 최신 실제 조사일로부터 몇 번째 예측인지 나타내는 1부터 시작하는 순번 |
| `asOfDate` | 예측에 사용한 마지막 실제 조사일 |
| `lastActualUnitPrice` | 지점 직접예측에 사용한 마지막 관측 지점가격 |
| `unitPriceBasis` | KRW/kg, KRW/L, KRW/10ea |
| `recursiveInputUnitPrice` | 해당 단계의 `lag_1`로 사용한 실제값 또는 직전 모델 예측값 |
| `recursiveInputSource` | 1단계는 `observed`, 이후 단계는 `model_prediction` |
| `modelPredictedUnitPrice` | ML 모델 예측값 |
| `modelPredictedStepChangePercent` | 직전 재귀 입력값 대비 단계별 예측 변화율 |
| `modelPredictedChangePercent` | `asOfDate`의 실제 가격 대비 누적 예측 변화율 |

백엔드는 다음 조회 API로 프론트엔드에 제공할 수 있다.

```http
GET /api/v1/price-forecasts/subtypes?item=밀가루
GET /api/v1/price-forecasts/products?item=밀가루
GET /api/v1/price-forecasts/brands?productId=...&forecastStep=1
GET /api/v1/price-forecasts/stores?productId=...&brand=이마트&forecastStep=1&page=0&size=30
```

DB 중복 방지 키는 `granularity + series_key + forecast_date`를 권장한다.
`series_key`는 세부유형이면 subtype ID, 상품명이면 product ID, 브랜드면
product ID와 brand ID, 지점이면 product ID와 store ID로 구성한다. 3단계 전체 지점
JSON은 현재 약 18MB이므로 외부 조회 API에서 전체 예측을 한 번에 반환하지 않는다.

## 8. 제한 및 후속 작업

- 현재 조사일은 최대 25개이며 계란은 13개뿐이다.
- 재귀형 다단계 예측은 이전 예측값을 다음 입력으로 사용하므로 단계가 멀어질수록
  오차가 누적될 수 있다. 현재 성능 평가는 1단계 백테스트 기준이다.
- 마지막 원천 조사일보다 시간이 지난 경우 생성된 `forecastDate`도 과거일 수 있다.
  백엔드는 `asOfDate`를 반드시 노출하고 최신 원천 데이터 여부를 검사해야 한다.
- 지점 모델은 판매점과 브랜드를 직접 특성으로 사용한다. 제조사, 세일,
  원플러스원 여부는 아직 직접 특성으로 사용하지 않는다.
- 지점 시계열 9,615개 중 최소 6회 이상 실제가격이 있는 9,083개만 현재 예측한다.
- 3단계 지점 예측은 27,249건이며 조회 API에는 상품·브랜드·예측단계 필터와
  페이지네이션이 필요하다.
- 실제 API 인증, 재시도, 멱등성 키, DB 마이그레이션은 백엔드 구현 범위다.
- 더 많은 조사일이 쌓이기 전에는 서비스용 확정 예측으로 표현하지 않는다.
