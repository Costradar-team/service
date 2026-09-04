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
    dag_id="cost_radar_fis_daily_etl",
    description="Run the existing FIS extract, transform, and load scripts daily.",
    default_args=DEFAULT_ARGS,
    start_date=datetime(2026, 9, 1, tzinfo=LOCAL_TZ),
    schedule="0 3 * * *",
    catchup=False,
    dagrun_timeout=timedelta(hours=2),
    tags=["cost_radar", "fis", "etl"],
    user_defined_filters={"safe_run_id": safe_run_id},
) as fis_daily_etl:
    extract = PythonOperator(
        task_id="extract",
        python_callable=run_python_script,
        op_args=[
            "scripts/collect/collect_fis.py",
            "--begin-date",
            "{{ (data_interval_end - macros.timedelta(days=1)).strftime('%Y-%m-%d') }}",
            "--end-date",
            "{{ (data_interval_end - macros.timedelta(days=1)).strftime('%Y-%m-%d') }}",
            "--output-dir",
            "data/raw/fis/dag_runs/{{ run_id | safe_run_id }}",
        ],
    )

    transform = PythonOperator(
        task_id="transform",
        python_callable=run_python_script,
        op_args=[
            "scripts/transform/transform_fis.py",
            "data/raw/fis/dag_runs/{{ run_id | safe_run_id }}",
            "--output-dir",
            "data/processed/fis/dag_runs/{{ run_id | safe_run_id }}",
            "--report-dir",
            "reports/transform/fis/dag_runs/{{ run_id | safe_run_id }}",
        ],
    )

    load = PythonOperator(
        task_id="load",
        python_callable=run_python_script,
        op_args=[
            "scripts/load/load_fis_mysql.py",
            "--item-input",
            "data/processed/fis/dag_runs/{{ run_id | safe_run_id }}/fis_item.csv",
            "--observation-input",
            "data/processed/fis/dag_runs/{{ run_id | safe_run_id }}/fis_price_observation.csv",
            "--report-dir",
            "reports/load/fis/dag_runs/{{ run_id | safe_run_id }}",
        ],
    )

    extract >> transform >> load
