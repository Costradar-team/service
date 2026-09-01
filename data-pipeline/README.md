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
