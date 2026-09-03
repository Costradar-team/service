# 백엔드 품목 가격예측 연동 계약

현재 화면의 `POST /predict`는 밀가루·설탕·버터·계란·우유 전체 대표가격을 예측하는
품목 모델 하나와 연결한다. 브랜드와 지점은 모델 입력이 아니다. 브랜드별 최신
실제가격은 DB에서 조회하고, 필요한 경우 품목 예측 변동률을 공통 적용한다.

## 모델과 실행 파일

```text
artifacts/ml/item/model/price_model.joblib
ml/scripts/predict_advanced_item_prices.py
```

`artifacts/`는 Git 제외 대상이므로 모델 파일은 배포 환경에서 학습하거나 별도 모델
저장소를 통해 전달해야 한다. 로딩에는 `joblib`, `scikit-learn`, `pandas`가 필요하다.

저장 모델은 2·4·6·8주를 따로 예측하는 Python `dict`다.

```text
format_version                 # 2
forecast_strategy              # direct_multi_horizon
series_level                   # item
group_columns                  # canonical_item, unit_price_basis
minimum_series_points
max_forecast_horizon           # 4
trained_through_date
horizon_models                 # 1, 2, 3, 4별 pipeline과 검증 메타데이터
```

각 `horizon_models[n]`에는 다음 값이 있다.

```text
pipeline
feature_columns
selected_feature_set
model_weight
forecast_method
```

백엔드가 이 내부 구조를 직접 복제해 계산하기보다 ML 추론 모듈의
`predict_advanced_item_prices()`를 호출하는 방식을 권장한다. 피처가 바뀌어도 모델과
추론 코드의 계약을 한곳에서 유지할 수 있기 때문이다.

## 입력 이력

ML 서비스는 DB에서 품목별 표준 단위가격 이력을 조회해야 한다.

```text
survey_date
canonical_item
unit_price_basis
median_unit_price
observation_count
store_count
sku_count
min_unit_price
max_unit_price
external_market_*             # 존재하면 포함
```

동일 조사일의 상품과 판매점 가격을 표준 단위로 환산한 뒤 중앙값 하나로 집계한다.
최소 6개 관측이 필요하다. 클라이언트가 lag, 이동평균, 외부시장 시차값을 직접
전송하지 않고 서버가 DB 이력으로 계산한다.

## 예측 방식

각 기간은 별도 목표로 직접 학습한다.

```text
2주 목표 변화율 = (2주 후 실제가격 - 현재 실제가격) / 현재 실제가격
4주 목표 변화율 = (4주 후 실제가격 - 현재 실제가격) / 현재 실제가격
6주 목표 변화율 = (6주 후 실제가격 - 현재 실제가격) / 현재 실제가격
8주 목표 변화율 = (8주 후 실제가격 - 현재 실제가격) / 현재 실제가격
```

KCA 이력 특성과 FIS·KAMIS 추가 특성을 확장형 워크포워드 검증으로 비교한다. 직접
모델과 직전가 기준선 사이에서 sMAPE가 가장 낮은 혼합 가중치를 0~1로 선택한다.

```text
최종 예측 = 현재 실제가격
            + model_weight × (직접 ML 예측 - 현재 실제가격)
```

ML이 기준선을 이기지 못하면 `model_weight=0`과
`validated_baseline_fallback`을 저장한다. 이는 실패를 숨긴 목 데이터가 아니라
과거 검증에서 더 안전한 값을 선택한 결과다. 새 조사 데이터로 재학습하면 가중치와
특성 구성은 자동으로 다시 선택된다.

## 로컬 추론 명령

최초 또는 의도한 재학습:

```powershell
python ml\scripts\train_advanced_item_model.py `
  --input artifacts\ml\item\model_dataset.csv `
  --output-dir artifacts\ml\item\model `
  --max-forecast-horizon 4
```

저장 모델 추론:

```powershell
python ml\scripts\predict_advanced_item_prices.py `
  --input artifacts\ml\item\model_dataset.csv `
  --model artifacts\ml\item\model\price_model.joblib `
  --output-dir artifacts\ml\item\model `
  --forecast-horizon 4
```

## 권장 API 응답

백엔드는 `item_forecasts.json`을 읽거나 같은 필드로 실시간 추론 결과를 반환한다.

```json
{
  "itemName": "밀가루",
  "unitPriceBasis": "KRW/kg",
  "asOfDate": "2026-07-24",
  "forecastDate": "2026-08-07",
  "forecastHorizonStep": 1,
  "currentUnitPrice": 2080.0,
  "modelPredictedUnitPrice": 2080.0,
  "modelPredictedChangePercent": 0.0,
  "forecastMethod": "validated_baseline_fallback",
  "modelWeight": 0.0
}
```

추론 결과는 `modelPredictedChangePercent`까지만 제공한다. BUY/HOLD/WAIT, 하락확률과
예측 하한·상한은 이 품목 예측 계약에서 반환하지 않는다.

전체 필드와 브랜드 예상가격 계산은
[백엔드 전달용 품목 가격예측 JSON](backend-forecast-json.md)을 참고한다.
