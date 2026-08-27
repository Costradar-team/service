# CostRadar

개인 카페의 원재료 가격을 정제하고, 품목별 가격 추이와 구매 시점 판단에
사용할 모델링 데이터셋을 만드는 프로젝트입니다.

현재 데이터 파이프라인은 한국소비자원 생필품 가격 CSV에서 밀가루, 설탕,
버터, 계란, 우유를 선별합니다. 로컬 실행 파이프라인은 원천 데이터 품질 검사,
품목 매핑, 단위가격 표준화, 날짜별 대표가격 생성, 기준 모델 평가를 순서대로
수행합니다. 예측 결과는 세부유형 대표가격과 개별 상품명 가격의 두 가지 단위로
생성합니다.

데이터 출처, 모델 입력, 성능 해석과 백엔드 JSON 계약은
[ML 가격예측 파이프라인 문서](docs/ml-price-forecasting.md)에 정리되어 있습니다.

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

저장소 루트에서 실행합니다.

```powershell
.\run_local.ps1
```

이미 데이터 품질 검사를 완료했다면 빠르게 다시 실행할 수 있습니다.

```powershell
.\run_local.ps1 -SkipProfiling
```

데이터 생성 후 세부유형 및 상품명 가격예측 모델을 모두 학습합니다.
기본 학습기는 scikit-learn의 단일 프로세스 Gradient Boosting입니다.
시간순 백테스트에서 학습 모델과 직전 가격 기준 모델을 비교하고, 더 나은 쪽을
`future_predictions.csv`의 권장 예측으로 자동 선택합니다.

```powershell
.\run_local.ps1 -SkipProfiling -TrainModel
```

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
   │  └─ future_predictions.csv
   ├─ product/                       # 상품명별 가격 모델
   │  ├─ model_dataset.csv
   │  ├─ baseline_metrics.json
   │  ├─ baseline_predictions.csv
   │  └─ model/
   │     ├─ price_model.joblib
   │     ├─ training_report.json
   │     ├─ backtest_predictions.csv
   │     └─ future_predictions.csv
   └─ backend/                       # 백엔드 API 요청 본문
      ├─ subtype_forecasts.json
      ├─ product_forecasts.json
      └─ export_summary.json
```

루트 `ml/model_dataset.csv`와 `ml/model/`은 품목·세부유형 단위 결과입니다.
`ml/product/` 아래 결과는 동일 상품의 여러 판매점 가격을 조사일별 중앙값으로
합친 상품명 단위 결과입니다.

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

python ml\scripts\evaluate_baselines.py `
  --input artifacts\ml\product\model_dataset.csv `
  --output-dir artifacts\ml\product `
  --series-level product

python ml\scripts\train_price_model.py `
  --input artifacts\ml\product\model_dataset.csv `
  --output-dir artifacts\ml\product\model `
  --series-level product

python ml\scripts\export_backend_forecasts.py `
  --subtype-input artifacts\ml\model\future_predictions.csv `
  --product-input artifacts\ml\product\model\future_predictions.csv `
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
- 세부유형 19개와 상품명 39개의 가격 시계열을 각각 평가합니다.
- 계란은 13개 조사일만 존재해 단기 예측 모델을 주장하기에는 부족합니다.
- 기준 모델 평가는 파이프라인 검증용이며 서비스용 최종 예측 모델이 아닙니다.
- 백엔드 애플리케이션은 아직 비어 있지만 전달용 JSON 계약과 내보내기는 준비했습니다.
- MySQL 로컬 실행에는 별도로 Docker Desktop을 설치해야 합니다.

## 협업 규칙

- 브랜치 전략은 GitHub Flow를 따른다.
- 브랜치명은 `feature/`, `fix/`, `refactor/`, `docs/`, `test/`, `chore/` prefix를 사용한다.
- 커밋 메시지는 `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:` 형식을 사용한다.
