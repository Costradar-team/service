# Data Pipeline

## KCA MySQL Load

루트 `.env`의 MySQL 접속 정보를 사용해 정제 CSV를 MySQL에 적재한다.

```powershell
python data-pipeline\scripts\load_kca_mysql.py
```

기본 입력 파일:

```text
data-pipeline\data\processed\kca_prices_processed.csv
```

다른 입력 파일을 사용할 때:

```powershell
python data-pipeline\scripts\load_kca_mysql.py --input data-pipeline\data\processed\kca_prices_processed.csv
```

적재 리포트는 아래 경로에 생성된다.

```text
data-pipeline\reports\load\kca_load_report.json
data-pipeline\reports\load\kca_load.log
data-pipeline\reports\load\kca_load_failures.jsonl
```
