# 백엔드 전달용 품목 가격예측 JSON

현재 화면에서 사용하는 ML 전달 파일은
`artifacts/ml/backend/item_forecasts.json`이다. 밀가루·설탕·버터·계란·우유의
전체 판매 상품과 판매점을 표준 단위가격으로 환산한 뒤 조사일별 중앙값을 예측한다.

브랜드별 현재가격은 DB에서 조회한다. 미래 브랜드 가격이 필요하면 백엔드가 품목
예측 변동률을 최신 브랜드 실제가격에 공통 적용한다. 기존 상품·브랜드·지점 JSON은
분석 호환용으로 생성하지만 현재 화면의 필수 전달 대상은 아니다.

## 가격 기준 단위

| 품목 | 기준 단위 | `unitPriceBasis` |
|---|---:|---|
| 밀가루·설탕·버터 | 1kg당 원화 가격 | `KRW/kg` |
| 우유 | 1L당 원화 가격 | `KRW/L` |
| 계란 | 10개당 원화 가격 | `KRW/10ea` |

`currentUnitPrice`와 `modelPredictedUnitPrice`는 모두 위 기준으로 환산된
대표가격이다. 실제 포장상품 가격으로 표시하려면 백엔드가 용량을 다시 적용해야 한다.

## 파일 구조

4단계 예측 시 품목 5개 × 약 2·4·6·8주로 총 20건이 생성된다.

```json
{
  "schemaVersion": "1.5",
  "granularity": "item",
  "forecastHorizon": 4,
  "forecastCount": 20,
  "forecasts": []
}
```

| 항목 | 의미 |
|---|---|
| `schemaVersion` | JSON 형식 버전 |
| `granularity` | 현재 파일은 `item` |
| `forecastHorizon` | 포함된 미래 예측 단계 수 |
| `forecastCount` | 전체 예측 행 수 |
| `forecasts` | 품목별 예측 목록 |

## 예측 항목

```json
{
  "forecastDate": "2026-08-07",
  "asOfDate": "2026-07-24",
  "canonicalItem": "밀가루",
  "unitPriceBasis": "KRW/kg",
  "currentUnitPrice": 2080.0,
  "modelPredictedUnitPrice": 2080.0,
  "modelPredictedChangePercent": 0.0,
  "forecastHorizonStep": 1,
  "predictionStrategy": "direct_multi_horizon",
  "forecastMethod": "validated_baseline_fallback",
  "modelWeight": 0.0,
  "selectedFeatureSet": "retail_history"
}
```

| 항목 | 의미 |
|---|---|
| `forecastDate` | 예측 대상 조사일 |
| `asOfDate` | 사용한 마지막 KCA 실제가격 조사일 |
| `canonicalItem` | 밀가루, 설탕, 버터, 계란, 우유 중 하나 |
| `unitPriceBasis` | 표준 가격 단위 |
| `currentUnitPrice` | 마지막 조사일의 품목 전체 중앙 단위가격 |
| `modelPredictedUnitPrice` | 검증된 직접 모델과 안전 기준선을 혼합한 최종 미래 대표 단위가격 |
| `modelPredictedChangePercent` | 마지막 실제 대표가격 대비 누적 예상 변동률 |
| `forecastHorizonStep` | 1·2·3·4단계. 현재 데이터에서는 약 2·4·6·8주 후 |
| `predictionStrategy` | 품목 모델은 `direct_multi_horizon` |
| `forecastMethod` | `direct_model`, `validated_direct_ensemble`, `validated_baseline_fallback` 중 하나 |
| `modelWeight` | 최종값에서 직접 ML 예측이 차지하는 비율. 0~1 |
| `selectedFeatureSet` | 검증으로 선택한 `retail_history` 또는 외부시장 추가 구성 |

BUY/HOLD/WAIT 판정과 확률 계산은 별도 담당 범위다. 이 파일은 판정의 입력으로 사용할
`modelPredictedChangePercent`까지만 제공하며, 예측 하한·상한이나 구매 신호를 포함하지
않는다.

## 브랜드별 예상가격과 화면 절감액

품목별 미래 변동률을 브랜드 최신 실제가격에 적용한다.

```text
브랜드 예상가격 = 브랜드 최신 실제가격
                  × (1 + modelPredictedChangePercent / 100)
```

발주 목록에서는 품목별 수량을 곱해 브랜드 합계를 만든다. 화면의 `최고가 대비
203원 절감`은 ML 성능 백테스트 절감률이 아니라 같은 시점의 비교 브랜드 합계 차이다.

```text
화면 절감액 = 비교 브랜드 중 최고 합계 - 선택 브랜드 합계
```

모든 품목 가격이 존재하는 브랜드끼리 비교해야 한다. `오늘 구매` 탭은 DB 실제가격,
`2주 예측` 탭은 위 방식으로 계산한 예상가격을 사용한다.

## 생성 명령

전체 실행:

```powershell
.\run_local.ps1 -SkipProfiling -TrainModel -ForecastHorizon 4
```

품목 모델만 다시 학습하고 예측할 때는 README의 개별 실행 명령을 사용한다. 산출물은
`artifacts/` 아래에 생성되며 GitHub에는 포함되지 않는다.

현재 워크포워드 MAPE는 약 2주 `0.1796%`, 약 4주 `0.3267%`, 약 6주
`0.4801%`, 약 8주 `0.6554%`다. 직접 ML이 직전가 기준선을 이기지 못해 네 기간 모두
`validated_baseline_fallback`, `modelWeight=0`이 선택됐다. 새 데이터로 재학습할 때
ML 성능이 개선되면 가중치가 자동으로 바뀐다. 구매 신호를 확정적 표현으로 제공하지
않는다.
