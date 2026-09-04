# CostRadar

개인 카페의 원재료 가격을 정제하고, 품목별 가격 추이와 구매 시점 판단에
사용할 모델링 데이터셋을 만드는 프로젝트입니다.

현재 데이터 파이프라인은 한국소비자원 생필품 가격 CSV에서 밀가루, 설탕,
버터, 계란, 우유를 선별합니다. 로컬 실행 파이프라인은 원천 데이터 품질 검사,
품목 매핑, 단위가격 표준화, 날짜별 대표가격 생성, 기준 모델 평가를 순서대로
수행합니다. 현재 화면용 핵심 결과는 밀가루·설탕·버터·계란·우유를 각각 하나로
묶은 품목 대표가격 예측입니다. 기존 세부유형, 개별 상품명, 브랜드, 지점 모델은
분석 호환용으로 함께 유지합니다. FIS의 밀·설탕 선물과 KAMIS의 계란·우유 가격이 준비되어
있으면 조사일 당시 공개된 값만 외부시장 특성으로 결합합니다.

데이터 출처, 모델 입력, 성능 해석과 백엔드 JSON 계약은
[ML 가격예측 파이프라인 문서](docs/ml-price-forecasting.md)에 정리되어 있습니다.
백엔드에 전달할 품목 JSON 파일과 필드 의미는
[백엔드 전달용 가격예측 JSON](docs/backend-forecast-json.md)에 정리되어 있습니다.
`POST /predict`에서 저장 모델을 호출할 때 필요한 품목 모델, 피처 순서와 가격 복원식은
[백엔드 ML 추론 연동 계약](docs/backend-ml-inference.md)에 정리되어 있습니다.

## 이번에 구현한 내용

화면의 구매 타이밍 판단에 맞춰 품목 전체 대표가격 모델을 추가했습니다. 브랜드별
현재가격은 DB에서 조회하고, 미래 비교가 필요하면 품목 예측 변동률을 공통 적용합니다.

```text
KCA 품목별 표준 단위가격 중앙값
  + 밀가루·설탕 FIS / 계란·우유 KAMIS 외부시장 특성
  -> 2·4·6·8주 직접 예측 모델을 각각 워크포워드 검증
  -> 검증 기반 모델 혼합 또는 안전 기준선 전환
  -> 대표가격·현재 대비 변동률
  -> 백엔드의 구매 판단 입력
```

구현된 핵심 기능은 다음과 같습니다.

- 판매업소 이름에서 이마트, 롯데마트·슈퍼, GS더프레시 등 10개 브랜드를 구분합니다.
- 466개 지점을 원본 판매업소 이름 그대로 구분합니다.
- 브랜드 가격은 `상품 × 브랜드 × 조사일`의 지점 가격 중앙값을 예측합니다.
- 지점 가격은 브랜드 평균에 보정계수를 곱하지 않습니다. 각 지점에서 실제로
  조사된 가격 이력을 학습하여 `상품 × 브랜드 × 지점` 가격을 직접 예측합니다.
- 지점마다 모델을 따로 만들지 않고 모든 지점 데이터를 하나의 글로벌 모델로
  학습하여 데이터가 적은 지점도 다른 지점의 공통 패턴을 활용합니다.
- 화면용 품목 모델은 2·4·6·8주를 서로 직접 예측합니다. 이전 단계 예측을 다음 단계에
  넣지 않으므로 재귀 오차가 누적되지 않습니다.
- FIS는 밀가루·설탕, KAMIS는 계란·우유에 연결합니다. 계란 10구와 30구는
  `KRW/10ea`로 환산하며 미래 관측값이 과거 학습 행에 섞이지 않게 합니다.
- 품목 모델은 확장형 워크포워드 검증으로 소매 이력, FIS·KAMIS 추가 특성, 직전가
  기준선을 비교합니다. 모델이 기준선을 이기지 못하면 해당 기간은 자동으로 안전
  기준선으로 전환합니다.
- 화면용 JSON은 `item_forecasts.json`이며 품목 5개의 대표가격과 변동률을 제공합니다.
  예측 하한·상한 구간과 구매 신호는 전달하지 않습니다.
- 기존 세부유형, 상품명, 브랜드, 지점 JSON도 분석 호환용으로 계속 생성합니다.

현재 데이터에서는 상품·지점 시계열 9,615개 중 실제가격이 최소 6회 이상 있는
9,083개를 지점 직접예측 대상으로 사용합니다. 3단계 예측을 실행하면 브랜드 예측
618건과 지점 예측 27,249건이 생성됩니다.

외부에 전달하는 미래 가격은 검증을 통과한 모델과 안전 기준선을 혼합한
`modelPredictedUnitPrice`입니다. 마지막 실제가격은 `currentUnitPrice` 또는
`lastActualUnitPrice`로 구분합니다. 기준선 값은 별도 예측 필드로 중복 제공하지 않고,
채택 방식은 `forecastMethod`와 `modelWeight`로 투명하게 표시합니다.

지점 3단계 전체 JSON은 약 18MB이므로 실제 조회 API에서는 전체 파일을 한 번에
반환하지 않고 상품, 브랜드, 예측 단계로 필터링한 뒤 페이지네이션해야 합니다.

## 로컬 실행

### 요구 환경

- Python 3.11 이상
- PowerShell 7 권장
- Docker Desktop은 MySQL을 사용할 때만 필요

가격 정제와 기준 모델 파이프라인은 Python 표준 라이브러리만으로 실행됩니다.
글로벌 가격예측 모델을 학습하려면 ML 패키지를 설치합니다.

```powershell
python -m pip install -r requirements.txt
```

LightGBM 모델까지 사용할 경우 다음 파일을 설치합니다.

```powershell
python -m pip install -r requirements-lightgbm.txt
```

### 전체 파이프라인

저장소 루트에서 최초 한 번은 모델을 학습합니다.

```powershell
.\run_local.ps1 -TrainModel
```

이미 데이터 품질 검사를 완료했다면 프로파일링을 생략할 수 있습니다.

```powershell
.\run_local.ps1 -SkipProfiling -TrainModel
```

최초 실행 또는 정기 재학습 시 품목, 세부유형, 상품명, 브랜드, 지점 가격예측 모델을
모두 학습합니다.
기본 학습기는 scikit-learn의 단일 프로세스 Gradient Boosting입니다.
시간순 백테스트에서 학습 모델을 직전 가격 기준선과 비교하지만, 기준선은 성능
평가에만 사용하고 예측 산출물에는 넣지 않습니다. 학습 후에는 전체 과거 데이터로
운영용 모델을 다시 적합하여 `price_model.joblib`에 저장합니다.

GitHub에 포함된 `data-pipeline/data/processed/fis/`와
`data-pipeline/data/processed/kamis/` 정제 CSV를 `run_local.ps1`이 자동으로 읽어
외부시장 특성을 포함합니다. 파일이 없는 환경에서는 소매가격 이력만으로도 실행됩니다.

새 원천 CSV를 추가한 뒤에는 `-TrainModel`을 사용하지 않습니다. 전처리와 최신
특징값 계산만 다시 수행하고 저장된 모델을 불러와 예측합니다.

```powershell
.\run_local.ps1 -SkipProfiling
```

화면용 품목 예측 기간은 1~4단계로 지정합니다. 각 단계는 현재 실제 이력에서 약
2·4·6·8주 가격을 직접 계산하며 예측값을 다음 단계 입력으로 사용하지 않습니다.
분석 호환용 세부유형·상품·브랜드·지점 모델은 기존 재귀 방식을 유지합니다.

```powershell
.\run_local.ps1 -SkipProfiling -ForecastHorizon 4
```

기본값은 `1`이고 최댓값은 `3`입니다.

즉, `-TrainModel`은 최초 학습이나 의도한 정기 재학습 때만 사용합니다. 현재는
자동 재학습 스케줄을 두지 않으며, 새 조사일 약 6개가 누적되는 시점(현재 수집
간격 기준 약 3개월)을 재학습 검토 기준으로 권장합니다.

LightGBM을 설치했다면 동일한 흐름에서 모델만 변경할 수 있습니다.

```powershell
.\run_local.ps1 -SkipProfiling -TrainModel -Estimator lightgbm
```

산출물은 Git에 포함되지 않는 `artifacts/` 아래에 생성됩니다.

```text
artifacts/
├─ data-quality/
├─ processed/kca_prices_processed.csv
└─ ml/
   ├─ normalized_prices.csv
   ├─ model_dataset.csv
   ├─ dataset_summary.json
   ├─ baseline_metrics.json
   ├─ baseline_predictions.csv
   ├─ item/                          # 화면용 품목 전체 대표가격 모델
   │  ├─ model_dataset.csv
   │  ├─ baseline_metrics.json
   │  └─ model/
   │     ├─ price_model.joblib
   │     ├─ training_report.json
   │     ├─ backtest_predictions.csv
   │     ├─ prediction_report.json
   │     └─ future_predictions.csv
   ├─ model/                         # 세부유형 대표가격 모델
   │  ├─ price_model.joblib
   │  ├─ training_report.json
   │  ├─ backtest_predictions.csv
   │  ├─ prediction_report.json
   │  └─ future_predictions.csv
   ├─ product/                       # 상품명별 가격 모델
   │  ├─ model_dataset.csv
   │  ├─ baseline_metrics.json
   │  ├─ baseline_predictions.csv
   │  └─ model/
   │     ├─ price_model.joblib
   │     ├─ training_report.json
   │     ├─ backtest_predictions.csv
   │     ├─ prediction_report.json
   │     └─ future_predictions.csv
   ├─ brand/                         # 상품·브랜드별 중앙가격 모델
   │  ├─ model_dataset.csv
   │  ├─ baseline_metrics.json
   │  └─ model/
   │     ├─ price_model.joblib
   │     └─ future_predictions.csv
   ├─ store/                         # 상품·지점별 실제가격 직접예측 모델
   │  ├─ model_dataset.csv
   │  ├─ baseline_metrics.json
   │  └─ model/
   │     ├─ price_model.joblib
   │     └─ future_predictions.csv
   └─ backend/                       # 백엔드 API 요청 본문
      ├─ item_forecasts.json         # 현재 화면용 핵심 전달 파일
      ├─ subtype_forecasts.json
      ├─ product_forecasts.json
      ├─ brand_forecasts.json
      ├─ store_forecasts.json
      └─ export_summary.json
```

`ml/item/`은 상품과 판매점을 모두 합쳐 조사일별 중앙값을 만든 품목 전체 결과입니다.
루트 `ml/model_dataset.csv`와 `ml/model/`은 품목·세부유형 단위 결과입니다.
`ml/product/` 아래 결과는 동일 상품의 여러 판매점 가격을 조사일별 중앙값으로
합친 상품명 단위 결과입니다.
`ml/brand/`는 동일 상품과 브랜드의 지점 가격 중앙값이며, `ml/store/`는
보정계수가 아닌 개별 지점의 관측 단위가격을 직접 목표값으로 사용한 결과입니다.

### 개별 실행

```powershell
python data-pipeline\scripts\profile\profile_kca.py data-pipeline\data\raw\kca `
  --output artifacts\data-quality\profiling_summary.json

python data-pipeline\scripts\transform\transform_kca.py data-pipeline\data\raw\kca `
  --output-dir artifacts\processed `
  --report-dir artifacts\data-quality\transform

python ml\scripts\build_model_dataset.py `
  --input artifacts\processed\kca_prices_processed.csv `
  --output-dir artifacts\ml `
  --fis-dir data-pipeline\data\processed\fis `
  --kamis-dir data-pipeline\data\processed\kamis

python ml\scripts\evaluate_baselines.py `
  --input artifacts\ml\item\model_dataset.csv `
  --output-dir artifacts\ml\item `
  --series-level item

python ml\scripts\train_advanced_item_model.py `
  --input artifacts\ml\item\model_dataset.csv `
  --output-dir artifacts\ml\item\model `
  --max-forecast-horizon 4

python ml\scripts\predict_advanced_item_prices.py `
  --input artifacts\ml\item\model_dataset.csv `
  --model artifacts\ml\item\model\price_model.joblib `
  --output-dir artifacts\ml\item\model `
  --forecast-horizon 4

python ml\scripts\evaluate_baselines.py `
  --input artifacts\ml\model_dataset.csv `
  --output-dir artifacts\ml

python ml\scripts\train_price_model.py `
  --input artifacts\ml\model_dataset.csv `
  --output-dir artifacts\ml\model

python ml\scripts\predict_prices.py `
  --input artifacts\ml\model_dataset.csv `
  --model artifacts\ml\model\price_model.joblib `
  --output-dir artifacts\ml\model `
  --forecast-horizon 3

python ml\scripts\train_price_model.py `
  --input artifacts\ml\brand\model_dataset.csv `
  --output-dir artifacts\ml\brand\model `
  --series-level brand

python ml\scripts\predict_prices.py `
  --input artifacts\ml\brand\model_dataset.csv `
  --model artifacts\ml\brand\model\price_model.joblib `
  --output-dir artifacts\ml\brand\model `
  --series-level brand `
  --forecast-horizon 3

python ml\scripts\train_price_model.py `
  --input artifacts\ml\store\model_dataset.csv `
  --output-dir artifacts\ml\store\model `
  --series-level store

python ml\scripts\predict_prices.py `
  --input artifacts\ml\store\model_dataset.csv `
  --model artifacts\ml\store\model\price_model.joblib `
  --output-dir artifacts\ml\store\model `
  --series-level store `
  --forecast-horizon 3

python ml\scripts\evaluate_baselines.py `
  --input artifacts\ml\product\model_dataset.csv `
  --output-dir artifacts\ml\product `
  --series-level product

python ml\scripts\train_price_model.py `
  --input artifacts\ml\product\model_dataset.csv `
  --output-dir artifacts\ml\product\model `
  --series-level product

python ml\scripts\predict_prices.py `
  --input artifacts\ml\product\model_dataset.csv `
  --model artifacts\ml\product\model\price_model.joblib `
  --output-dir artifacts\ml\product\model `
  --series-level product `
  --forecast-horizon 3

python ml\scripts\export_backend_forecasts.py `
  --item-input artifacts\ml\item\model\future_predictions.csv `
  --subtype-input artifacts\ml\model\future_predictions.csv `
  --product-input artifacts\ml\product\model\future_predictions.csv `
  --brand-input artifacts\ml\brand\model\future_predictions.csv `
  --store-input artifacts\ml\store\model\future_predictions.csv `
  --output-dir artifacts\ml\backend

python ml\scripts\evaluate_backtest_savings.py `
  --artifact-root artifacts\ml `
  --output artifacts\ml\backtest_business_metrics.json
```

### 테스트

```powershell
python -m unittest discover -s ml\tests -v
```

### MySQL 선택 실행

`.env.example`을 `.env`로 복사하고 비밀번호를 변경한 뒤 실행합니다.

```powershell
docker compose up -d mysql
```

현재 MySQL 컨테이너는 데이터베이스 실행 환경만 제공합니다. CSV 적재 기능은
후속 작업 범위입니다.

## 현재 상태와 제한

- 원천 데이터 13개 파일에서 전체 25개 조사일을 확인했습니다.
- 화면용 품목 전체 모델은 113행, 5개 시계열을 사용하며 4단계 실행 시
  `item_forecasts.json`에 20건을 생성합니다.
- 품목 고급 모델의 워크포워드 MAPE는 약 2주 `0.1796%`, 약 4주 `0.3267%`,
  약 6주 `0.4801%`, 약 8주 `0.6554%`입니다. 현재 데이터에서는 직접 ML이 직전가
  기준선을 이기지 못해 네 기간 모두 `validated_baseline_fallback`이 선택됐습니다. 새 데이터에서
  ML이 개선되면 `modelWeight`가 자동으로 0보다 커집니다.
- 세부유형 19개, 상품명 39개, 브랜드 222개, 상품·지점 9,615개 시계열을
  평가합니다.
- 지점 466개를 구분하며 최소 6회 이상 관측된 상품·지점 시계열 9,083개를
  직접예측합니다.
- 계란은 13개 조사일만 존재해 단기 예측 모델을 주장하기에는 부족합니다.
- 현재 시간순 검증에서는 외부시장 특성이 지점 모델에서만 sMAPE를
  `1.5455%`에서 `1.4694%`로 낮춰 채택됐습니다. 세부유형·상품·브랜드 모델은
  소매가격 이력만 사용하도록 자동 선택됐습니다.
- `evaluate_backtest_savings.py`는 상품·브랜드·지점 백테스트에서 MAPE와
  구매 시점 모의 절감률을 계산합니다. 상승 예측이면 현재 구매하고, 그 외에는 다음
  조사일까지 기다리는 전략을 `항상 현재 구매` 기준과 비교합니다.
- 현재 지점 모델은 MAPE `1.5469%`, 동일 기준선 MAPE `1.2835%`, 모의 절감률
  `0.2023%`입니다. 절감률은 각 행을 기준 단위 1개로 동일 가중한 실험값이며 실제
  주문 수량, 재고, 배송비와 폐기 비용은 포함하지 않습니다.
- FIS·KAMIS 검증 데이터의 마지막 날짜는 2026-07-31이므로 운영 전 최신 수집이
  필요합니다.
- 기준 모델 평가는 파이프라인 검증용이며 서비스용 최종 예측 모델이 아닙니다.
- 백엔드 애플리케이션은 아직 비어 있지만 전달용 JSON 계약과 내보내기는 준비했습니다.
- 3단계 지점 예측 JSON은 약 18MB이므로 실제 API는 상품·브랜드·단계별 필터와
  페이지네이션을 적용해야 합니다.
- MySQL 로컬 실행에는 별도로 Docker Desktop을 설치해야 합니다.

## 협업 규칙

- 브랜치 전략은 GitHub Flow를 따른다.
- 브랜치명은 `feature/`, `fix/`, `refactor/`, `docs/`, `test/`, `chore/` prefix를 사용한다.
- 커밋 메시지는 `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:` 형식을 사용한다.
