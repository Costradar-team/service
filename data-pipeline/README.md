# Data Pipeline

## KCA MySQL Load

루트 `.env`의 MySQL 접속 정보를 사용해 정제 CSV를 MySQL에 적재한다.

```powershell
python data-pipeline\scripts\load\load_kca_mysql.py
```

기본 입력 파일:

```text
data-pipeline\data\processed\kca\kca_prices_processed.csv
```

다른 입력 파일을 사용할 때:

```powershell
python data-pipeline\scripts\load\load_kca_mysql.py --input data-pipeline\data\processed\kca\kca_prices_processed.csv
```

적재 리포트는 아래 경로에 생성된다.

```text
data-pipeline\reports\load\kca_load_report.json
data-pipeline\reports\load\kca_load.log
data-pipeline\reports\load\kca_load_failures.jsonl
```

KCA 정제 CSV에서 distinct 판매업소 목록을 별도 CSV로 생성한다.

```powershell
python data-pipeline\scripts\transform\export_kca_stores.py
```

기본 출력 파일:

```text
data-pipeline\data\processed\kca\kca_stores.csv
```

Kakao Local keyword API로 판매업소 주소/지역을 보강한다.
루트 `.env`에 `KAKAO_REST_API_KEY` 또는 `KAKAO_API_KEY`를 설정해야 한다.
검증된 예외 매칭은 `data-pipeline\data\reference\kca\store_match_overrides.csv`에서 관리한다.

```powershell
python data-pipeline\scripts\collect\collect_kca_store_regions_kakao.py
```

기존 master를 모두 무시하고 새 매칭/override 로직으로 재생성할 때:

```powershell
python data-pipeline\scripts\collect\collect_kca_store_regions_kakao.py --refresh-all
```

기본 출력 파일:

```text
data-pipeline\data\processed\kca\kca_store_master.csv
```

지역 보강된 판매업소 master를 MySQL에 먼저 적재한다.

```powershell
python data-pipeline\scripts\load\load_kca_store_mysql.py
```

그 다음 KCA 가격 데이터를 적재한다. 기본 동작은 `store`를 새로 만들지 않고
이미 적재된 `store.source_store_name`을 참조한다.

```powershell
python data-pipeline\scripts\load\load_kca_mysql.py
```

임시로 예전처럼 가격 CSV에서 `store`를 생성해야 할 때만 아래 옵션을 사용한다.

```powershell
python data-pipeline\scripts\load\load_kca_mysql.py --load-stores-from-price
```

## FIS MySQL Load

FIS 원자재 가격 변환 결과를 현재 ERD의 `fis_item`, `fis_price_observation` 테이블에 적재한다.
`fis_item.canonical_item_id`는 `config/profiling_rules_fis.json`의 `canonical_item` 값을 기존
`canonical_item.name`과 매핑한다.

```powershell
python data-pipeline\scripts\load\load_fis_mysql.py
```

기존 DB에 `fis_price_observation.unit_price`를 추가하고 `converted_price` 값으로 백필할 때:

```powershell
python data-pipeline\scripts\load\migrate_fis_unit_price.py
```

기존 DB에 KCA/KAMIS observation `unit_price`를 추가/백필할 때:

```powershell
python data-pipeline\scripts\load\migrate_observation_unit_price.py
```

기본 입력 파일:

```text
data-pipeline\data\processed\fis\fis_item.csv
data-pipeline\data\processed\fis\fis_price_observation.csv
```

적재 리포트는 아래 경로에 생성된다.

```text
data-pipeline\reports\load\fis_load_report.json
data-pipeline\reports\load\fis_load.log
data-pipeline\reports\load\fis_load_failures.jsonl
```

## Emart Mall Online Price Extract

이마트몰 검색 HTML에 포함된 상품 카드 JSON에서 온라인 상품 가격 원천 CSV를 수집한다.
기본 품목은 계란, 우유, 설탕, 밀가루, 버터다.

```powershell
python data-pipeline\scripts\collect\collect_emartmall.py
```

품목을 명시할 때:

```powershell
python data-pipeline\scripts\collect\collect_emartmall.py --product egg milk sugar flour butter
```

요청 간격과 429 backoff 설정을 조정할 때:

```powershell
python data-pipeline\scripts\collect\collect_emartmall.py --request-interval-seconds 2 --max-retries 3 --backoff-seconds 10
```

페이지 수와 Chrome 실행 모드 지정:

```powershell
python data-pipeline\scripts\collect\collect_emartmall.py --browser chrome --max-pages 3
```

`--max-pages` 기본값은 1이며, `0`이면 상품이 없는 마지막 페이지까지 시도한다. 품목별 결과는
`product_key`로 구분되며, 최종 통합 CSV에서 `item_id` 기준으로 품목 간 중복을 제거한다.

기본 출력 파일:

```text
data-pipeline\data\raw\emart\emart_all_{collect_date}.csv
```

## Airflow

`docker-compose.yml`의 Airflow 서비스는 `data-pipeline/dags`의 DAG를 읽어 KAMIS/FIS, 롯데마트,
농협몰 일 단위 ETL과 KCA 월 단위 ETL을 실행한다. Airflow 컨테이너는 시작 시 루트
`requirements.txt`를 설치한다.

롯데마트와 농협몰 수집기는 Playwright 및 Chromium을 사용한다. Compose는 `Dockerfile.airflow`로
Chromium, Playwright, Xvfb를 포함한 Airflow 커스텀 이미지를 빌드한다. 농협몰은 headless Chromium으로,
롯데마트는 `xvfb-run`을 통한 headful Chromium으로 실행한다. DAG는 소스별 raw/processed/report 경로를
분리해 서로의 실행 결과를 덮어쓰지 않는다.

### 0건 수집 정책

현재 KCA, FIS, 농협몰 수집기는 요청 자체가 성공했고 수집 결과만 0건인 경우 성공으로 처리한다.
빈 raw 파일과 그 Run 전용 processed 결과가 남으며, load는 빈 입력에 대해 기존 데이터를 삭제하지 않는
upsert 방식으로 동작한다. 이 정책은 수집 장애를 자동으로 판정하지 않으므로 DAG 실행 로그와 수집 건수를
모니터링해야 한다. 롯데마트는 0건이면 raw를 쓰지 않고 task를 실패시키며, KAMIS는 API 또는 상품별 수집
오류가 하나라도 있으면 task를 실패시킨다.

루트 `.env`에 아래 값이 필요하다.

```text
KAMIS_API_KEY=
KAMIS_API_ID=
KAKAO_REST_API_KEY=
ODCLOUD_SERVICE_KEY=
```

`KAMIS_ID`는 `KAMIS_API_ID`의 별칭으로, `KCA_API_KEY`는 `ODCLOUD_SERVICE_KEY`의 별칭으로 사용할 수 있다.

실행:

```powershell
docker compose up -d mysql airflow
```

웹 UI:

```text
http://localhost:8081
username: airflow
password: airflow
```
