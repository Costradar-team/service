from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from airflow import DAG
from airflow.operators.python import PythonOperator


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_PIPELINE_ROOT = PROJECT_ROOT / "data-pipeline"
LOCAL_TZ = ZoneInfo("Asia/Seoul")

DEFAULT_ARGS = {
    "owner": "cost_radar",
    "depends_on_past": False,
    "retries": 0,
}


def run_python_script(script_path: str, *args: str) -> None:
    command = [sys.executable, str(DATA_PIPELINE_ROOT / script_path), *args]
    print("Running command:", " ".join(command))
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


with DAG(
    dag_id="cost_radar_kca_monthly_etl",
    description="Run the existing KCA transform, store enrichment, and load scripts monthly.",
    default_args=DEFAULT_ARGS,
    start_date=datetime(2026, 9, 1, tzinfo=LOCAL_TZ),
    schedule="0 4 1 * *",
    catchup=False,
    dagrun_timeout=timedelta(hours=4),
    tags=["cost_radar", "kca", "etl"],
) as kca_monthly_etl:
    extract = PythonOperator(
        task_id="extract",
        python_callable=run_python_script,
        op_args=["scripts/collect/collect_kca.py"],
    )

    transform = PythonOperator(
        task_id="transform",
        python_callable=run_python_script,
        op_args=["scripts/transform/transform_kca.py", "data/raw/kca"],
    )

    export_stores = PythonOperator(
        task_id="export_stores",
        python_callable=run_python_script,
        op_args=["scripts/transform/export_kca_stores.py"],
    )

    enrich_new_stores = PythonOperator(
        task_id="enrich_new_stores",
        python_callable=run_python_script,
        op_args=["scripts/collect/collect_kca_store_regions_kakao.py"],
    )

    load_stores = PythonOperator(
        task_id="load_stores",
        python_callable=run_python_script,
        op_args=["scripts/load/load_kca_store_mysql.py"],
    )

    load_prices = PythonOperator(
        task_id="load_prices",
        python_callable=run_python_script,
        op_args=["scripts/load/load_kca_mysql.py"],
    )

    extract >> transform >> export_stores >> enrich_new_stores >> load_stores >> load_prices
