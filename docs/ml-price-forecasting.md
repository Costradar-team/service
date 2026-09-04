# ML 가격예측 파이프라인 및 백엔드 연동 규격

## 1. 목적

한국소비자원 생필품 가격 데이터를 이용해 개인 카페 원재료의 단위가격 추이를
정리하고 다음 조사 시점의 가격을 예측한다. 현재 화면에서 사용하는 품목 전체 예측과
분석 호환용 기존 예측 단위를 함께 제공한다.

- **품목 전체 예측**: 밀가루·설탕·버터·계란·우유의 전체 대표 가격 추이를 본다.
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
  + FIS 밀·설탕 / KAMIS 계란·우유 외부시장 시계열
  -> 조사일 기준 as-of 결합
  -> 품목 113행 / 세부유형 463행 / 상품명 895행 / 브랜드 4,612행 / 지점 204,036행
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

동일 조사일에 여러 판매점에서 수집된 가격은 중앙값으로 집계한다. 품목 모델은 같은
품목의 모든 상품과 판매점을 합쳐 조사일마다 대표가격 하나를 만든다. 세부유형 모델은
상품과 판매점을 모두 합치고, 상품명 모델은 동일 상품의 판매점만 합친다. 브랜드
모델은 같은 상품과 브랜드에 속한 지점 가격의 중앙값을 사용한다. 지점 모델은
`상품명 × 브랜드 × 판매업소`의 관측 단위가격을 직접 사용한다. 현재 원천 데이터는
상품명·판매업소·조사일 중복이 없으며, 향후 중복이 생길 경우 같은 키의 중앙값 하나를
직접가격으로 사용한다.

## 3. 모델 입력과 예측 대상

다섯 모델은 각 예측 단위의 여러 가격 시계열을 하나의 글로벌 회귀 모델로 함께
학습한다. 지점마다 별도 모델을 만들지 않는다.

공통 입력 특성:

- 품목과 단위가격 기준
- 날짜 순번과 월 주기 정보
- 직전 1·2·4회 가격
- 최근 2·4회 이동평균
- 최근 4회 가격 표준편차
- 외부시장 최신가격, 7·14·28일 변화율, 28일 평균 대비 차이
- 외부시장 관측 사용 가능 여부와 기준일로부터 경과일

외부시장 특성은 밀가루·설탕에 FIS, 계란·우유에 KAMIS를 사용한다. KAMIS 계란
10구와 30구는 모두 `KRW/10ea`로 환산한다. 각 소매 조사일보다 늦은 외부 관측은
절대 참조하지 않으며, 다음 조사일 예측에는 직전 소매 조사 시점까지 확인된 외부값만
사용한다. 버터처럼 대응 원천이 없는 품목은 사용 가능 여부를 0으로 둔다.

학습 시 `retail_history`와 `retail_history_plus_external_market` 두 특성 구성을 같은
시간순 검증 구간에서 비교한다. sMAPE가 더 낮은 구성을 예측 단위별로 선택한 뒤 전체
모델링 가능 이력으로 다시 학습한다. 외부 데이터가 있다는 이유만으로 운영 모델에
강제로 넣지 않는다.

세부유형 모델에는 `subtype`, 상품명 모델에는 `subtype`과 `product_name`, 브랜드
모델에는 `product_name`과 `brand_name`,
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

화면용 품목 모델은 `forecast_horizon` 1·2·3·4에 대해 약 2·4·6·8주 목표를 각각 직접
학습한다. 세 모델 모두 마지막 실제 이력에서 특성을 만들며 이전 단계의 예측값을
입력으로 사용하지 않는다. 따라서 재귀 예측의 단계별 오차 누적을 피한다.

```text
실제 이력 -> 약 2주 직접 모델
실제 이력 -> 약 4주 직접 모델
실제 이력 -> 약 6주 직접 모델
실제 이력 -> 약 8주 직접 모델
```

각 기간은 확장형 워크포워드 검증으로 소매 이력 모델과 FIS·KAMIS 추가 모델을
비교한다. 선택된 직접 모델 예측은 직전가 기준선과 0~100% 사이로 혼합하며, 검증
sMAPE가 가장 낮은 가중치를 저장한다. 모델이 기준선을 이기지 못하면 가중치 0의
`validated_baseline_fallback`을 사용한다. 별도 직전가 예측 필드를 내보내지는 않고
`forecastMethod`와 `modelWeight`로 선택 결과를 밝힌다.

기본 학습기는 scikit-learn `GradientBoostingRegressor`이며, 선택적으로 LightGBM을
사용할 수 있다.

학습과 예측은 분리되어 있다. 학습 명령은 시간순 백테스트가 끝난 뒤 전체
모델링 가능 이력으로 운영 모델을 적합하여 `price_model.joblib`에 저장한다. 예측
명령은 해당 파일을 불러오며 모델 파라미터를 갱신하지 않는다. 새 CSV의 가격으로
lag와 이동통계만 다시 계산하는 것은 재학습이 아니라 예측 입력 생성이다.

## 4. 검증 방식과 현재 결과

무작위 분할을 사용하지 않는다. 화면용 품목 모델은 최근 약 30% 목표일마다 그 이전
날짜만으로 다시 학습하는 확장형 워크포워드 검증을 사용한다. 기존 분석 호환 모델은
가장 최근 20% 날짜를 홀드아웃하는 시간순 백테스트를 유지한다. 학습 모델은 다음
기준선과 비교한다.

- `naive_last_value`: 다음 가격이 직전 가격과 같다고 예측
- `rolling_mean`: 다음 가격을 최근 4회 평균으로 예측

2026-09-04 실행 결과이며 원천 데이터의 마지막 조사일은 2026-07-24다.

화면용 품목 직접 다중기간 모델 결과:

| 예측 기간 | 검증 행 | 직접 ML MAPE | 채택 예측 MAPE | 직전가 MAPE | 채택 방식 |
|---|---:|---:|---:|---:|---|
| 약 2주 | 30 | 0.5289% | 0.1796% | 0.1796% | 기준선 전환 |
| 약 4주 | 30 | 0.5542% | 0.3267% | 0.3267% | 기준선 전환 |
| 약 6주 | 30 | 0.9614% | 0.4801% | 0.4801% | 기준선 전환 |
| 약 8주 | 29 | 1.3276% | 0.6554% | 0.6554% | 기준선 전환 |

현재는 네 기간 모두 직접 ML이 기준선을 이기지 못해 모델 가중치 0이 선택됐다.
복잡한 모델이라는 이유로 성능이 낮은 값을 강제로 서비스하지 않는 것이 현재 데이터에
맞는 결과다. 이후 새 조사 데이터에서 검증 성능이 좋아지면 혼합 가중치가 자동으로
증가한다.

아래 표는 분석 호환용 기존 1단계 모델 결과다.

| 예측 단위 | 시계열 수 | ML sMAPE | 직전 가격 기준선 sMAPE |
|---|---:|---:|---:|
| 품목 전체(직접 모델 채택값) | 5 | 0.1788% | 0.1788% |
| 세부유형 | 19 | 1.2258% | 0.8824% |
| 상품명 | 39 | 1.4338% | 1.0822% |
| 브랜드 | 222 | 0.8904% | 0.8062% |
| 지점 직접가격 | 9,615 | 1.4694% | 1.2062% |

sMAPE는 실제값과 예측값의 평균 상대 오차이며 낮을수록 좋다. 현재는 가격 변화가
적고 조사일이 최대 25개뿐이라 다섯 단위 모두 직전 가격 기준선이 더 정확하다.
분석 호환용 모델은 직전 가격 기준선을 성능 비교에만 사용한다. 화면용 품목 모델은
검증 안전장치 안에서 기준선을 내부적으로 혼합할 수 있지만, 별도 기준선 필드를
JSON에 중복해서 넣지 않는다.

MAPE와 구매 시점 모의 절감률은 `evaluate_backtest_savings.py`로 별도 계산한다.
상승 예측이면 직전 실제가격에 현재 구매하고, 그 외에는 다음 조사일까지 기다려
해당 시점 실제가격에 구매한다고 가정한다. 기준 전략은 모든 행을 직전 실제가격에
구매하는 `always_buy_now`다.

```text
절감률 = (항상 현재 구매 비용 - 예측 기반 구매 비용)
         / 항상 현재 구매 비용 × 100
```

현재 시간순 1단계 백테스트 결과는 다음과 같다.

| 예측 단위 | 표본 수 | ML MAPE | 직전가격 MAPE | 모의 절감률 |
|---|---:|---:|---:|---:|
| 품목 전체 | 30 | 0.1796% | 0.1796% | 0.0393% |
| 상품명 | 188 | 1.5375% | 1.1706% | 0.0262% |
| 브랜드 | 966 | 0.9580% | 0.8740% | 0.0839% |
| 지점 직접가격 | 40,110 | 1.5469% | 1.2835% | 0.2023% |

백테스트 기간은 2026-05-22부터 2026-07-24까지다. 절감률은 각 백테스트 행에서
기준 단위 1개를 동일 가중한 모의 값이다. 실제 주문 수량, 재고 보유비, 폐기,
배송비와 품절은 반영하지 않으므로 실제 절감 보장값으로 표현하지 않는다.

특성 구성 비교 결과 품목·세부유형·상품명·브랜드는 소매가격 이력만 선택했고, 지점
직접가격 모델만 외부시장 특성을 선택했다. 지점 모델의 sMAPE는 외부 특성 없이
`1.5455%`, 외부 특성 포함 시 `1.4694%`였다. FIS·KAMIS 검증 데이터는
2026-07-31까지만 있으므로 운영 전 최신 수집이 필요하다.

품목 직접 ML의 KCA 이력 / FIS·KAMIS 추가 sMAPE는 약 2주
`0.5306% / 0.5439%`, 약 4주 `0.5517% / 1.0118%`, 약 6주
`0.9664% / 1.1747%`, 약 8주 `1.3332% / 1.5335%`였다. 세 데이터 원천을 모두 모델 데이터셋에 연결하고
검증하지만, 외부 특성이 실제로 성능을 낮춘 현재 모델에는 강제로 넣지 않았다.

## 5. 산출물

산출물은 `.gitignore`에 포함된 `artifacts/` 아래에 생성되며 GitHub에는 올리지 않는다.

```text
artifacts/ml/
├─ backtest_business_metrics.json     # MAPE와 구매 시점 모의 절감률
├─ normalized_prices.csv
├─ dataset_summary.json
├─ model_dataset.csv                  # 세부유형 학습 데이터
├─ baseline_metrics.json
├─ baseline_predictions.csv
├─ item/                              # 화면용 품목 전체 대표가격
│  ├─ model_dataset.csv
│  ├─ baseline_metrics.json
│  └─ model/
│     ├─ price_model.joblib
│     ├─ training_report.json
│     ├─ backtest_predictions.csv
│     ├─ backtest_purchase_decisions.csv
│     ├─ prediction_report.json
│     └─ future_predictions.csv
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
│     ├─ backtest_purchase_decisions.csv
│     ├─ prediction_report.json
│     └─ future_predictions.csv
├─ brand/
│  ├─ model_dataset.csv               # 상품·브랜드별 중앙가격 데이터
│  ├─ baseline_metrics.json
│  └─ model/
│     ├─ price_model.joblib
│     ├─ training_report.json
│     ├─ backtest_predictions.csv
│     ├─ backtest_purchase_decisions.csv
│     ├─ prediction_report.json
│     └─ future_predictions.csv
├─ store/
│  ├─ model_dataset.csv               # 상품·지점별 실제가격 데이터
│  ├─ baseline_metrics.json
│  └─ model/
│     ├─ price_model.joblib
│     ├─ training_report.json
│     ├─ backtest_predictions.csv
│     ├─ backtest_purchase_decisions.csv
│     ├─ prediction_report.json
│     └─ future_predictions.csv
└─ backend/
   ├─ item_forecasts.json             # 현재 화면용 핵심 전달 파일
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

화면용 품목은 다음 4개 조사 시점을 직접 예측:

```powershell
.\run_local.ps1 -SkipProfiling -ForecastHorizon 4
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

macOS / Linux 실행:

```bash
# 전체 파이프라인 (재학습 포함)
./run_local.sh --skip-profiling --train-model

# 예측만 갱신 및 테스트 실행
./run_local.sh --skip-profiling --run-tests
```

테스트:

```bash
python -m unittest discover -s ml/tests -v
```

기존 학습 백테스트에서 MAPE와 구매 시점 모의 절감률만 다시 계산:

```powershell
python ml\scripts\evaluate_backtest_savings.py
```

결과 요약은 `artifacts/ml/backtest_business_metrics.json`, 행별 구매·대기 판단은
각 `item|product|brand|store/model/backtest_purchase_decisions.csv`에 저장된다.

## 7. 백엔드 전달 계약

현재 구현 범위는 백엔드가 받을 수 있는 JSON 요청 본문을 생성하는 단계까지다.
실제 HTTP API와 DB 적재는 `backend/` 구현 후 연결한다.
백엔드에 전달할 파일과 필드 의미는
[백엔드 전달용 가격예측 JSON](backend-forecast-json.md)에 정리한다. DB 테이블과
적재 방식, API 구현은 백엔드에서 결정한다.

권장 내부 API:

```http
POST /internal/v1/price-forecasts/batch
Content-Type: application/json
```

화면용 품목 예측 요청 예시:

```json
{
  "schemaVersion": "1.5",
  "granularity": "item",
  "forecastHorizon": 1,
  "forecastCount": 1,
  "forecasts": [
    {
      "forecastDate": "2026-08-07",
      "forecastHorizonStep": 1,
      "asOfDate": "2026-07-24",
      "canonicalItem": "밀가루",
      "unitPriceBasis": "KRW/kg",
      "currentUnitPrice": 2080.0,
      "modelPredictedUnitPrice": 2080.0,
      "modelPredictedChangePercent": 0.0,
      "predictionStrategy": "direct_multi_horizon",
      "forecastMethod": "validated_baseline_fallback",
      "modelWeight": 0.0,
      "selectedFeatureSet": "retail_history"
    }
  ]
}
```

현재 화면은 `item_forecasts.json`만 필수로 사용한다. 기존 상품·브랜드·지점 JSON은
분석 호환용이며 BUY/HOLD/WAIT 판정에는 사용하지 않는다.

| 필드 | 의미 |
|---|---|
| `forecastDate` | 예측 대상 조사일 |
| `forecastHorizonStep` | 최신 실제 조사일로부터 몇 번째 예측인지 나타내는 1~4 순번 |
| `asOfDate` | 예측에 사용한 마지막 실제 조사일 |
| `currentUnitPrice` | 마지막 조사일의 품목 전체 중앙 단위가격 |
| `unitPriceBasis` | KRW/kg, KRW/L, KRW/10ea |
| `modelPredictedUnitPrice` | 검증된 직접 모델과 안전 기준선을 혼합한 최종 예측값 |
| `modelPredictedChangePercent` | `asOfDate`의 실제 가격 대비 누적 예측 변화율 |
| `predictionStrategy` | `direct_multi_horizon` |
| `forecastMethod` | 직접 모델, 혼합 모델 또는 검증 기반 기준선 전환 여부 |
| `modelWeight` | 최종 예측에서 직접 ML이 차지하는 비율. 0~1 |
| `selectedFeatureSet` | 소매 이력 또는 외부시장 추가 특성 중 검증으로 선택한 구성 |

현재 품목 예측 JSON은 가격과 변동률까지만 제공한다. 구매 신호·하락확률과 예측
하한·상한 구간은 포함하지 않는다.

백엔드는 다음 조회 API로 프론트엔드에 제공할 수 있다.

```http
GET /api/v1/price-forecasts/items?item=밀가루&forecastStep=1
```

DB 중복 방지 키는 `granularity + series_key + forecast_date`를 권장한다.
화면용 품목 예측의 `series_key`는 canonical item ID와 단위가격 기준으로 구성한다.
분석 호환용 기존 JSON은 외부 조회 API에 한 번에 반환하지 않는다.

## 8. 제한 및 후속 작업

- 현재 조사일은 최대 25개이며 계란은 13개뿐이다.
- 품목 2·4·6·8주 모델은 직접 예측이지만 기간이 멀수록 검증 오차가 커질 수 있다.
- 마지막 원천 조사일보다 시간이 지난 경우 생성된 `forecastDate`도 과거일 수 있다.
  백엔드는 `asOfDate`를 반드시 노출하고 최신 원천 데이터 여부를 검사해야 한다.
- 지점 모델은 판매점과 브랜드를 직접 특성으로 사용한다. 제조사, 세일,
  원플러스원 여부는 아직 직접 특성으로 사용하지 않는다.
- 지점 시계열 9,615개 중 최소 6회 이상 실제가격이 있는 9,083개만 현재 예측한다.
- 3단계 지점 예측은 27,249건이며 조회 API에는 상품·브랜드·예측단계 필터와
  페이지네이션이 필요하다.
- 실제 API 인증, 재시도, 멱등성 키, DB 마이그레이션은 백엔드 구현 범위다.
- 더 많은 조사일이 쌓이기 전에는 서비스용 확정 예측으로 표현하지 않는다.
