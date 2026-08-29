# CostRadar

개인 카페의 원재료 가격을 정제하고, 품목별 가격 추이와 구매 시점 판단에
사용할 모델링 데이터셋을 만드는 프로젝트입니다.

현재 데이터 파이프라인은 한국소비자원 생필품 가격 CSV에서 밀가루, 설탕,
버터, 계란, 우유를 선별합니다. 로컬 실행 파이프라인은 원천 데이터 품질 검사,
품목 매핑, 단위가격 표준화, 날짜별 대표가격 생성, 기준 모델 평가를 순서대로
수행합니다. 예측 결과는 세부유형, 개별 상품명, 브랜드, 지점 실제가격의 네 가지
단위로 생성합니다.

데이터 출처, 모델 입력, 성능 해석과 백엔드 JSON 계약은
[ML 가격예측 파이프라인 문서](docs/ml-price-forecasting.md)에 정리되어 있습니다.

## 이번에 구현한 내용

사용자가 먼저 마트 브랜드별 가격 추이를 비교하고, 브랜드를 선택하면 해당 브랜드의
지점별 가격을 확인할 수 있도록 예측 단위를 확장했습니다.

```text
상품 선택
  -> 브랜드별 미래 가격 비교
  -> 브랜드 선택
  -> 해당 브랜드의 지점별 미래 가격 목록
  -> 최저·평균·최고 지점을 차트나 표로 표시
```

구현된 핵심 기능은 다음과 같습니다.

- 판매업소 이름에서 이마트, 롯데마트·슈퍼, GS더프레시 등 10개 브랜드를 구분합니다.
- 466개 지점을 원본 판매업소 이름 그대로 구분합니다.
- 브랜드 가격은 `상품 × 브랜드 × 조사일`의 지점 가격 중앙값을 예측합니다.
- 지점 가격은 브랜드 평균에 보정계수를 곱하지 않습니다. 각 지점에서 실제로
  조사된 가격 이력을 학습하여 `상품 × 브랜드 × 지점` 가격을 직접 예측합니다.
- 지점마다 모델을 따로 만들지 않고 모든 지점 데이터를 하나의 글로벌 모델로
  학습하여 데이터가 적은 지점도 다른 지점의 공통 패턴을 활용합니다.
- `ForecastHorizon`을 사용하면 여러 미래 조사 시점을 연속으로 예측합니다.
  2단계부터는 직전 단계의 ML 예측값을 다음 단계 입력으로 사용합니다.
- 백엔드 전달용 JSON은 세부유형, 상품명, 브랜드, 지점의 네 종류로 생성합니다.

현재 데이터에서는 상품·지점 시계열 9,615개 중 실제가격이 최소 6회 이상 있는
9,083개를 지점 직접예측 대상으로 사용합니다. 3단계 예측을 실행하면 브랜드 예측
618건과 지점 예측 27,249건이 생성됩니다.

외부에 전달하는 미래 가격은 ML이 계산한 `modelPredictedUnitPrice`입니다. 마지막
실제가격은 `currentUnitPrice` 또는 `lastActualUnitPrice`로 구분해 기준 시점 정보로만
제공합니다. 직전 가격 유지값은 내부 백테스트 비교에만 사용하며 예측 CSV나 JSON에는
포함하지 않습니다.

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

최초 실행 또는 정기 재학습 시 세부유형, 상품명, 브랜드, 지점 가격예측 모델을
모두 학습합니다.
기본 학습기는 scikit-learn의 단일 프로세스 Gradient Boosting입니다.
시간순 백테스트에서 학습 모델을 직전 가격 기준선과 비교하지만, 기준선은 성능
평가에만 사용하고 예측 산출물에는 넣지 않습니다. 학습 후에는 전체 과거 데이터로
운영용 모델을 다시 적합하여 `price_model.joblib`에 저장합니다.

새 원천 CSV를 추가한 뒤에는 `-TrainModel`을 사용하지 않습니다. 전처리와 최신
특징값 계산만 다시 수행하고 저장된 모델을 불러와 예측합니다.

```powershell
.\run_local.ps1 -SkipProfiling
```

여러 조사 시점을 연속으로 예측하려면 재귀 예측 단계 수를 지정합니다. 2단계부터는
직전 모델 예측값을 임시 가격 이력에 추가해 lag와 이동통계를 다시 계산합니다.
예측값은 원천 데이터나 학습 데이터에 저장되지 않습니다.

```powershell
.\run_local.ps1 -SkipProfiling -ForecastHorizon 3
```

기본값은 `1`이며, 다단계 예측은 단계가 멀어질수록 오차가 누적될 수 있습니다.

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
      ├─ subtype_forecasts.json
      ├─ product_forecasts.json
      ├─ brand_forecasts.json
      ├─ store_forecasts.json
      └─ export_summary.json
```

루트 `ml/model_dataset.csv`와 `ml/model/`은 품목·세부유형 단위 결과입니다.
`ml/product/` 아래 결과는 동일 상품의 여러 판매점 가격을 조사일별 중앙값으로
합친 상품명 단위 결과입니다.
`ml/brand/`는 동일 상품과 브랜드의 지점 가격 중앙값이며, `ml/store/`는
보정계수가 아닌 개별 지점의 관측 단위가격을 직접 목표값으로 사용한 결과입니다.

### 개별 실행

```powershell
python data-pipeline\scripts\profile_kca.py data-pipeline\data\raw `
  --output artifacts\data-quality\profiling_summary.json

python data-pipeline\scripts\transform_kca.py data-pipeline\data\raw `
  --output-dir artifacts\processed `
  --report-dir artifacts\data-quality\transform

python ml\scripts\build_model_dataset.py `
  --input artifacts\processed\kca_prices_processed.csv `
  --output-dir artifacts\ml

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
  --subtype-input artifacts\ml\model\future_predictions.csv `
  --product-input artifacts\ml\product\model\future_predictions.csv `
  --brand-input artifacts\ml\brand\model\future_predictions.csv `
  --store-input artifacts\ml\store\model\future_predictions.csv `
  --output-dir artifacts\ml\backend
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
- 세부유형 19개, 상품명 39개, 브랜드 222개, 상품·지점 9,615개 시계열을
  평가합니다.
- 지점 466개를 구분하며 최소 6회 이상 관측된 상품·지점 시계열 9,083개를
  직접예측합니다.
- 계란은 13개 조사일만 존재해 단기 예측 모델을 주장하기에는 부족합니다.
- 기준 모델 평가는 파이프라인 검증용이며 서비스용 최종 예측 모델이 아닙니다.
- 백엔드 애플리케이션은 아직 비어 있지만 전달용 JSON 계약과 내보내기는 준비했습니다.
- 3단계 지점 예측 JSON은 약 18MB이므로 실제 API는 상품·브랜드·단계별 필터와
  페이지네이션을 적용해야 합니다.
- MySQL 로컬 실행에는 별도로 Docker Desktop을 설치해야 합니다.

## 협업 규칙

- 브랜치 전략은 GitHub Flow를 따른다.
- 브랜치명은 `feature/`, `fix/`, `refactor/`, `docs/`, `test/`, `chore/` prefix를 사용한다.
- 커밋 메시지는 `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:` 형식을 사용한다.
