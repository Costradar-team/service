# CostRadar Repository Record

이 파일의 적용 범위는 저장소 전체다. 이 저장소에서 작업하는 에이전트는 작업을
시작하기 전에 이 파일을 끝까지 읽고, 아래 사실과 규칙을 기준으로 현재 상태를
확인해야 한다.

이 파일을 읽은 직후 루트의 `PROJECT_STATUS.md`도 반드시 끝까지 읽는다.
`AGENTS.md`는 작업 규칙과 변하지 않는 구조를 기록하고, `PROJECT_STATUS.md`는
진행률, 최신 검증 결과, 미커밋 작업과 다음 할 일을 기록한다.

## 작업 시작 시 필수 확인

1. `PROJECT_STATUS.md`를 끝까지 읽는다.
2. `git status --short`, 현재 브랜치, 최근 커밋을 확인하고 기록과 실제 상태를
   대조한다.
3. 사용자의 기존 미커밋 변경을 보존하고 관련 없는 파일을 되돌리지 않는다.
4. 구현 상태를 설명할 때 코드, 생성된 산출물, 계획만 있는 기능을 구분한다.
5. 사용자가 명시적으로 요청하기 전에는 commit, push, force-push, PR 생성을 하지
   않는다.
6. 새 기능 브랜치는 최신 `origin/main`에서 분기한다. 다른 feature 브랜치를
   기반으로 삼지 않는다.

## 프로젝트 목적과 데이터

- 프로젝트명은 CostRadar다.
- 한국소비자원 생필품 가격 CSV에서 밀가루, 설탕, 버터, 계란, 우유 가격을
  정제하고 가격 예측 데이터를 만든다.
- 원본 CSV는 `data-pipeline/data/raw/`에 있다.
- `data-pipeline/config/item_mapping.csv`에 등록되고 `mapping_include=O`인 상품만
  분석 대상이다. 등록되지 않은 새 상품은 자동으로 학습에 포함되지 않는다.
- 데이터 처리 스크립트는 `data-pipeline/scripts/`에 있다.
- 전처리 연결 파일은 `artifacts/processed/kca_prices_processed.csv`다.
- `artifacts/`는 생성 결과 디렉터리이며 Git에서 제외된다.

## 현재 파이프라인

```text
원본 CSV
  -> 프로파일링과 상품 매핑
  -> 전처리 CSV
  -> 단위가격 표준화
  -> 세부유형별/상품명별/브랜드별/지점별 모델 데이터
  -> 기준선 평가와 선택적 모델 학습
  -> 저장 모델을 사용한 예측
  -> 백엔드 전달용 JSON
```

- 단위가격은 밀가루·설탕·버터 `KRW/kg`, 우유 `KRW/L`, 계란 `KRW/10ea`다.
- ML 예측 단위는 세부유형, 상품명, 브랜드, 지점 네 가지다.
- 상품명 예측은 여러 판매점 가격을 조사일별 중앙값으로 합친 값이다. 브랜드
  예측은 같은 상품과 브랜드의 지점 가격 중앙값을 사용한다.
- 지점 예측은 보정계수를 곱하지 않고 `상품명 × 브랜드 × 판매업소`의 실제
  단위가격 이력을 직접 목표값으로 사용한다. 지점 466개에 별도 모델을 만들지 않고
  모든 지점 시계열을 하나의 글로벌 모델로 함께 학습한다.
- 판매업소 접두어로 10개 브랜드를 분류한다. 현재 실데이터에는 지점 466개,
  상품-지점 시계열 9,615개가 있으며 최소 6회 기준 9,083개가 예측 대상이다.
- 학습과 예측은 분리한다. `-TrainModel`이 있을 때만 모델을 학습하고, 평상시에는
  저장된 `price_model.joblib`을 읽어 예측한다.
- 새 CSV 예측 시 lag와 이동통계를 다시 만드는 것은 재학습이 아니다.
- 여러 미래 조사 시점은 `-ForecastHorizon` 또는 `--forecast-horizon`으로 요청하며,
  2단계 이후는 이전 모델 예측을 다음 단계 입력으로 사용하는 재귀 예측이다.
  기간이 길수록 오차가 누적된다는 사실을 문서와 사용자 설명에서 숨기지 않는다.
- 원천 조사 간격이 대체로 약 2주이므로 기본 1단계 예측은 '다음 조사 시점,
  약 2주 후'로 설명한다. 1주 예측이라고 단정하지 않는다.
- 현재 데이터는 조사일이 최대 25개, 계란은 13개라 장기 예측 신뢰도가 낮다.
- `naive_last_value`는 백테스트 비교 기준으로만 사용한다. 예측 CSV와 백엔드 JSON은
  직전 가격을 예측값이나 권장값으로 내보내지 않고 ML의 `modelPredictedUnitPrice`만
  예측값으로 제공한다. 현재 백테스트에서 ML이 기준선보다 낮다는 사실은 성능
  제한으로 명시한다.

## 실행 명령

최초 학습 또는 의도한 재학습:

```powershell
.\run_local.ps1 -SkipProfiling -TrainModel
```

새 CSV 추가 후 저장 모델로 예측만 갱신:

```powershell
.\run_local.ps1 -SkipProfiling
```

여러 조사 시점 예측 예시:

```powershell
.\run_local.ps1 -SkipProfiling -ForecastHorizon 3
```

테스트:

```powershell
python -m unittest discover -s ml\tests -v
```

자동 재학습 스케줄은 아직 없다. 새 조사일 약 6개, 현재 수집 간격 기준 약
3개월치가 누적됐을 때 재학습을 검토한다. 실제 오차가 악화되면 더 일찍
재학습할 수 있다.

## DB와 백엔드 상태

- `docs/erd/kca_erd.dbml`은 ERD 설계 문서다. 실제 DB가 생성 또는 적재됐다는
  증거가 아니다.
- `docker-compose.yml`은 MySQL 실행 환경만 정의한다.
- 현재 저장소에는 CSV를 MySQL에 적재하는 마이그레이션, seed, loader가 없다.
- ML은 DB에서 읽지 않고 전처리 CSV를 읽는다.
- `ml/scripts/export_backend_forecasts.py`는 백엔드용 JSON 파일만 생성한다.
  HTTP 전송, DB upsert, 백엔드 조회 API는 아직 구현되지 않았다.
- 3단계 지점 예측 JSON은 현재 약 18MB이므로 실제 조회 API는 전체 파일을 한 번에
  반환하지 않고 상품, 브랜드, 예측 단계로 필터링하고 페이지네이션해야 한다.
- 현재 ERD에는 과거 가격용 `price_observation`은 있지만 예측값을 저장할
  `price_forecast` 테이블은 없다.

## 주요 문서와 코드

- 프로젝트 실행 안내: `README.md`
- ML 상세 설명과 JSON 계약: `docs/ml-price-forecasting.md`
- ERD: `docs/erd/kca_erd.dbml`
- 전체 로컬 실행: `run_local.ps1`
- 모델 데이터 생성: `ml/scripts/build_model_dataset.py`
- 모델 학습: `ml/scripts/train_price_model.py`
- 저장 모델 예측: `ml/scripts/predict_prices.py`
- 백엔드 JSON 변환: `ml/scripts/export_backend_forecasts.py`

## Git 기록 메모

- 2026-08-28 기준 `feature/ml-pipeline` 로컬 브랜치는 `origin/main`의
  `69730c4`에서 직접 분기하도록 재배치했다.
- 재배치된 ML 커밋은 로컬 `1fec6d8`이다.
- 원격 `feature/ml-pipeline`에는 재배치 전 기록이 남아 있을 수 있으므로, 향후
  push 전에 원격 상태를 다시 확인한다. 강제 push는 사용자 승인 없이 하지 않는다.
- 이 절의 해시나 브랜치 상태가 바뀌면 같은 작업에서 이 기록도 갱신한다.

## 문서 유지 규칙

- 파이프라인, 실행 명령, DB 연동 상태 또는 Git 기준 브랜치가 바뀌면
  `AGENTS.md`, `PROJECT_STATUS.md`, `README.md`,
  `docs/ml-price-forecasting.md`의 설명을 함께 확인한다.
- 기능 구현, 테스트, commit, push, PR, merge 또는 주요 차단 상태가 바뀌면 같은
  작업에서 `PROJECT_STATUS.md`의 날짜와 해당 상태를 갱신한다.
- 테스트를 통과하지 않은 기능을 완료된 기능으로 기록하지 않는다.
- 사용자에게는 기본적으로 한국어로 설명하고, 현재 가능한 범위와 후속 구현 범위를
  명확히 나눈다.
