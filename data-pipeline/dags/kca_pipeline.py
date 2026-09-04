from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote
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


def safe_run_id(value: object) -> str:
    """Return a single, cross-platform-safe path component for an Airflow run ID."""
    return "run_" + quote(str(value), safe="-_.")


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
    user_defined_filters={"safe_run_id": safe_run_id},
) as kca_monthly_etl:
    extract = PythonOperator(
        task_id="extract",
        python_callable=run_python_script,
        op_args=[
            "scripts/collect/collect_kca.py",
            "--output-dir",
            "data/raw/kca/dag_runs/{{ run_id | safe_run_id }}",
        ],
    )

    transform = PythonOperator(
        task_id="transform",
        python_callable=run_python_script,
        op_args=[
            "scripts/transform/transform_kca.py",
            "data/raw/kca/dag_runs/{{ run_id | safe_run_id }}",
            "--output-dir",
            "data/processed/kca/dag_runs/{{ run_id | safe_run_id }}",
            "--report-dir",
            "reports/transform/kca/dag_runs/{{ run_id | safe_run_id }}",
        ],
    )

    export_stores = PythonOperator(
        task_id="export_stores",
        python_callable=run_python_script,
        op_args=[
            "scripts/transform/export_kca_stores.py",
            "--input",
            "data/processed/kca/dag_runs/{{ run_id | safe_run_id }}/kca_prices_processed.csv",
            "--output",
            "data/processed/kca/dag_runs/{{ run_id | safe_run_id }}/kca_stores.csv",
        ],
    )

    enrich_new_stores = PythonOperator(
        task_id="enrich_new_stores",
        python_callable=run_python_script,
        op_args=[
            "scripts/collect/collect_kca_store_regions_kakao.py",
            "--input",
            "data/processed/kca/dag_runs/{{ run_id | safe_run_id }}/kca_stores.csv",
            "--output",
            "data/processed/kca/dag_runs/{{ run_id | safe_run_id }}/kca_store_master.csv",
            "--debug-report",
            "reports/collect/kca/dag_runs/{{ run_id | safe_run_id }}/kca_store_kakao_candidates.csv",
            "--rejected-output",
            "reports/collect/kca/dag_runs/{{ run_id | safe_run_id }}/kca_store_rejected_rows.csv",
        ],
    )

    load_stores = PythonOperator(
        task_id="load_stores",
        python_callable=run_python_script,
        op_args=[
            "scripts/load/load_kca_store_mysql.py",
            "--input",
            "data/processed/kca/dag_runs/{{ run_id | safe_run_id }}/kca_store_master.csv",
            "--report-dir",
            "reports/load/kca/dag_runs/{{ run_id | safe_run_id }}/stores",
        ],
    )

    load_prices = PythonOperator(
        task_id="load_prices",
        python_callable=run_python_script,
        op_args=[
            "scripts/load/load_kca_mysql.py",
            "--input",
            "data/processed/kca/dag_runs/{{ run_id | safe_run_id }}/kca_prices_processed.csv",
            "--report-dir",
            "reports/load/kca/dag_runs/{{ run_id | safe_run_id }}/prices",
        ],
    )

    extract >> transform >> export_stores >> enrich_new_stores >> load_stores >> load_prices
