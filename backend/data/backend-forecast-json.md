# 백엔드 전달용 가격예측 JSON

ML 파이프라인은 백엔드에 상품, 브랜드, 지점 예측 JSON 세 개를 전달한다.
DB 테이블과 적재 방식, API 구현은 백엔드에서 결정한다.

## 생성 파일과 가격 의미

기본 생성 위치는 `artifacts/ml/backend/`다.

| 파일 | 예측 단위 | 가격 의미 |
|---|---|---|
| `product_forecasts.json` | 상품 | 곰표 밀가루 1kg의 전체 판매점 대표가격 |
| `brand_forecasts.json` | 상품 × 마트 브랜드 | 곰표 밀가루의 이마트 대표가격 |
| `store_forecasts.json` | 상품 × 마트 × 지점 | 곰표 밀가루의 이마트 월계점 가격 |

## 가격 기준 단위

상품마다 포장 용량이 다르므로 모든 현재가격과 예측가격을 비교 가능한 기준 단위로
환산한다.

| 품목 | 기준 단위 | JSON의 `unitPriceBasis` |
|---|---:|---|
| 밀가루 | 1kg당 원화 가격 | `KRW/kg` |
| 설탕 | 1kg당 원화 가격 | `KRW/kg` |
| 버터 | 1kg당 원화 가격 | `KRW/kg` |
| 우유 | 1L당 원화 가격 | `KRW/L` |
| 계란 | 10개당 원화 가격 | `KRW/10ea` |

예를 들어 900g 밀가루가 1,800원이면 `2,000 KRW/kg`, 2L 우유가 5,600원이면
`2,800 KRW/L`, 계란 30개가 9,000원이면 `3,000 KRW/10ea`로 사용한다.

`currentUnitPrice`, `lastActualUnitPrice`, `modelPredictedUnitPrice`,
`recursiveInputUnitPrice`는 모두 해당 행의 `unitPriceBasis`로 환산된 값이다. 따라서
원래 포장 가격으로 화면에 표시하려면 상품 용량을 이용한 별도 역환산이 필요하다.

## 파일 공통 구조

```json
{
  "schemaVersion": "1.3",
  "granularity": "product",
  "forecastHorizon": 3,
  "forecastCount": 117,
  "forecasts": []
}
```

| 항목 | 의미 |
|---|---|
| `schemaVersion` | JSON 형식 버전. 현재 `1.3` |
| `granularity` | 예측 단위. `product`, `brand`, `store` 중 하나 |
| `forecastHorizon` | 포함된 미래 예측 단계 수 |
| `forecastCount` | `forecasts` 배열의 전체 행 수 |
| `forecasts` | 실제 예측 결과 목록 |

## 예측 결과 항목

| 항목 | 포함 파일 | 의미 |
|---|---|---|
| `forecastDate` | 공통 | 예측 대상 날짜 |
| `asOfDate` | 공통 | 예측에 사용한 마지막 실제가격 조사일 |
| `forecastHorizonStep` | 공통 | 미래 예측 순번. 현재 1·2·3단계는 약 2·4·6주 후 |
| `canonicalItem` | 공통 | 대표 품목명. 예: 밀가루, 계란 |
| `subtype` | 공통 | 상품 세부유형. 예: 중력분, 일반계란 |
| `productName` | 공통 | 실제 상품명 |
| `brandName` | brand, store | 이마트, 롯데마트 같은 마트 브랜드 |
| `storeName` | store | 실제 조사 지점명 |
| `unitPriceBasis` | 공통 | `KRW/kg`, `KRW/L`, `KRW/10ea` 중 하나 |
| `currentUnitPrice` | product, brand | 마지막 조사일의 상품 또는 브랜드 대표가격 |
| `lastActualUnitPrice` | store | 해당 지점에서 마지막으로 조사된 실제가격 |
| `modelPredictedUnitPrice` | 공통 | ML 모델이 계산한 예측 단위가격 |
| `modelPredictedChangePercent` | 공통 | 마지막 실제가격 대비 누적 예상 변동률 |
| `recursiveInputUnitPrice` | 공통 | 해당 단계 계산에 사용한 실제가격 또는 직전 단계 예측가격 |
| `recursiveInputSource` | 공통 | `observed`는 실제값, `model_prediction`은 직전 단계 예측값 사용 |
| `modelPredictedStepChangePercent` | 공통 | 직전 재귀 입력가격 대비 해당 단계의 예상 변동률 |

`modelPredictedUnitPrice`가 서비스에 제공할 예측값이다. `currentUnitPrice`와
`lastActualUnitPrice`는 예측값이 아니라 비교 기준이 되는 마지막 실제가격이다.
`recursiveInput*`과 `modelPredictedStepChangePercent`는 다단계 예측 계산을 확인하기
위한 내부 추적 정보이므로 화면에 표시하지 않아도 된다.

## 파일별 예시

상품 예측:

```json
{
  "forecastDate": "2026-08-07",
  "asOfDate": "2026-07-24",
  "forecastHorizonStep": 1,
  "canonicalItem": "밀가루",
  "subtype": "중력분",
  "productName": "곰표 밀가루 중력다목적용(1kg)",
  "unitPriceBasis": "KRW/kg",
  "currentUnitPrice": 2010.0,
  "modelPredictedUnitPrice": 2009.2506,
  "modelPredictedChangePercent": -0.0373,
  "recursiveInputUnitPrice": 2010.0,
  "recursiveInputSource": "observed",
  "modelPredictedStepChangePercent": -0.0373
}
```

브랜드 예측에는 위 항목과 함께 `brandName`이 추가된다.

```json
{
  "brandName": "이마트"
}
```

지점 예측에는 `brandName`, `storeName`이 포함되고 `currentUnitPrice` 대신
`lastActualUnitPrice`를 사용한다.

```json
{
  "brandName": "이마트",
  "storeName": "이마트월계점",
  "lastActualUnitPrice": 1980.0,
  "modelPredictedUnitPrice": 2010.0
}
```

## 현재 생성량

3단계 예측 기준이다.

| 파일 | 예측 행 수 |
|---|---:|
| `product_forecasts.json` | 117 |
| `brand_forecasts.json` | 618 |
| `store_forecasts.json` | 27,249 |


