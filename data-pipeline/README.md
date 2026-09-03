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
