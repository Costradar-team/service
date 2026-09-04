# ML 가격예측 파이프라인 및 백엔드 연동 규격

## 1. 범위

운영 가격예측 모델은 하나다. 밀가루·설탕·버터·계란·우유의 조사일별 대표
단위가격을 입력으로 받아 약 2·4·6·8주 후 품목 대표가격을 직접 예측한다.

- 세부유형별 모델 없음
- 상품별 모델 없음
- 판매처 브랜드별 모델 없음
- 지점별 모델 없음
- 백엔드 전달 파일은 `item_forecasts.json` 하나

상품, 판매처와 지점 정보는 원천 가격 정제 및 화면의 현재가 조회에 사용할 수 있지만
별도 ML 모델을 만들지 않는다.

## 2. 데이터 흐름

```text
한국소비자원 KCA 원천 CSV
  -> 밀가루·설탕·버터·계란·우유 선별
  -> 상품 용량을 표준 단위가격으로 환산
  -> 품목×조사일 중앙 단위가격 113행
  + FIS 밀·설탕 / KAMIS 계란·우유 외부시장 특성
  -> 품목 직접 다중기간 모델 학습
  -> price_model.joblib 하나 저장
  -> item_forecasts.json 하나 생성
```

표준 단위는 다음과 같다.

| 품목 | 표준 단위 |
|---|---|
| 밀가루·설탕·버터 | `KRW/kg` |
| 우유 | `KRW/L` |
| 계란 | `KRW/10ea` |

FIS·KAMIS는 각 KCA 조사일 당시 공개된 값만 as-of 방식으로 결합한다. 미래에 공개된
외부시장 값이 과거 학습 행에 들어가지 않는다. 버터는 대응 외부시장 원천이 없어
사용 가능 여부를 0으로 둔다.

## 3. 모델 입력과 예측 방식

품목 모델의 입력은 `artifacts/ml/item/model_dataset.csv`다.

주요 필드:

- `survey_date`, `canonical_item`, `unit_price_basis`
- `median_unit_price`, `observation_count`, `store_count`, `sku_count`
- 직전 1·2·4회 가격, 이동평균, 변동성, 현재 가격 분포
- FIS·KAMIS 최신값과 7·14·28일 변화율

1~4단계는 서로 다른 미래 목표를 직접 학습한다.

```text
현재 실제 이력 -> 약 2주 목표
현재 실제 이력 -> 약 4주 목표
현재 실제 이력 -> 약 6주 목표
현재 실제 이력 -> 약 8주 목표
```

이전 단계 예측값을 다음 단계 입력으로 사용하지 않으므로 재귀 오차가 누적되지 않는다.
저장 파일은 하나지만 내부에는 1~4단계별 학습 모델과 검증 메타데이터가 들어 있다.

기본 학습기는 scikit-learn `GradientBoostingRegressor`다. 선택적으로 LightGBM을
사용할 수 있다. 소매 이력 특성과 외부시장 추가 특성을 워크포워드 검증으로 비교하고,
기간별로 직접 모델과 직전가 기준선 사이의 검증 가중치를 선택한다.

## 4. 검증 결과

무작위 분할 대신 목표 날짜 이전 데이터만 학습하는 확장형 워크포워드 검증을 사용한다.
마지막 KCA 조사일은 2026-07-24이며 전체 조사일은 최대 25개, 계란은 13개다.

| 예측 기간 | 검증 행 | 직접 ML MAPE | 채택 MAPE | 직전가 MAPE |
|---|---:|---:|---:|---:|
| 약 2주 | 30 | 0.5289% | 0.1796% | 0.1796% |
| 약 4주 | 30 | 0.5542% | 0.3267% | 0.3267% |
| 약 6주 | 30 | 0.9614% | 0.4801% | 0.4801% |
| 약 8주 | 29 | 1.3276% | 0.6554% | 0.6554% |

현재 검증 구간은 가격 변화가 작아 네 기간 모두 직전가 기준선이 직접 ML보다
정확하다. 이 결과를 모델 성능 개선으로 표현하지 않는다. 새 조사 데이터가 쌓이면
같은 시간순 검증으로 다시 평가한다.

## 5. 파일 구조

```text
artifacts/ml/item/
├─ model_dataset.csv
├─ baseline_metrics.json
└─ model/
   ├─ price_model.joblib
   ├─ training_report.json
   ├─ backtest_predictions.csv
   ├─ prediction_report.json
   └─ future_predictions.csv

artifacts/ml/backend/
├─ item_forecasts.json
└─ export_summary.json
```

`artifacts/`는 Git에서 제외된다. 백엔드 배포에는 학습된 `price_model.joblib`과 같은
버전의 추론 코드를 함께 전달해야 한다.

## 6. 실행 방법

의존성 설치:

```powershell
python -m pip install -r requirements.txt
```

최초 학습 또는 의도한 재학습:

```powershell
.\run_local.ps1 -SkipProfiling -TrainModel -ForecastHorizon 4
```

저장 모델로 예측만 갱신:

```powershell
.\run_local.ps1 -SkipProfiling -ForecastHorizon 4
```

개별 학습과 추론:

```powershell
python ml\scripts\train_advanced_item_model.py `
  --input artifacts\ml\item\model_dataset.csv `
  --output-dir artifacts\ml\item\model `
  --max-forecast-horizon 4

python ml\scripts\predict_advanced_item_prices.py `
  --input artifacts\ml\item\model_dataset.csv `
  --model artifacts\ml\item\model\price_model.joblib `
  --output-dir artifacts\ml\item\model `
  --forecast-horizon 4

python ml\scripts\export_backend_forecasts.py `
  --item-input artifacts\ml\item\model\future_predictions.csv `
  --output-dir artifacts\ml\backend
```

테스트:

```powershell
python -m unittest discover -s ml\tests -v
```

## 7. 백엔드 출력

`item_forecasts.json`은 품목명, 마지막 실제가격, 예측가격, 변동률, 기준일과 예측일을
제공한다. BUY/HOLD/WAIT, 하락확률과 예측 하한·상한은 이 ML 계약에서 제공하지 않는다.
세부 필드는 [백엔드 전달용 품목 가격예측 JSON](backend-forecast-json.md)을 참고한다.
