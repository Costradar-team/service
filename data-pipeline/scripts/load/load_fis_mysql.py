from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError

from load_kca_mysql import (
    DEFAULT_ENV_PATH,
    ROOT,
    ForeignKeyMappingError,
    LoadError,
    MissingRequiredValueError,
    TableReport,
    canonical_item,
    chunked,
    existing_composite_key_map,
    existing_single_key_map,
    fis_item,
    fis_price_observation,
    insert_ignore_existing,
    is_foreign_key_integrity_error,
    load_env_file,
    make_engine,
    metadata,
    resolve_project_path,
)


DEFAULT_INPUT_DIR = ROOT / "data" / "processed" / "fis"
DEFAULT_ITEM_PATH = DEFAULT_INPUT_DIR / "fis_item.csv"
DEFAULT_OBSERVATION_PATH = DEFAULT_INPUT_DIR / "fis_price_observation.csv"
DEFAULT_REPORT_DIR = ROOT / "reports" / "load"
DEFAULT_BATCH_SIZE = 1000

ITEM_REQUIRED_COLUMNS = [
    "item_key",
    "canonical_item",
    "cmdt_id",
    "cmdt_se_cd",
    "item_name",
    "price_unit",
    "relation_type",
]
OBSERVATION_REQUIRED_COLUMNS = [
    "item_key",
    "contract_month",
    "trade_date",
    "close_price",
    "unit_price",
]
LOAD_TABLE_ORDER = ["fis_item", "fis_price_observation"]


@dataclass(frozen=True)
class FisItemRow:
    row_number: int
    item_key: str
    canonical_item_name: str
    cmdt_id: str
    cmdt_se_cd: str
    item_name: str
    price_unit: str
    converted_unit: str | None
    relation_type: str


@dataclass(frozen=True)
class FisObservationRow:
    row_number: int
    item_key: str
    contract_month: str
    trade_date: date
    close_price: Decimal
    unit_price: Decimal | None
    change_amount: Decimal | None
    change_rate_pct: Decimal | None
    converted_price: Decimal | None


@dataclass
class LoadReport:
    tables: dict[str, TableReport] = field(
        default_factory=lambda: {
            table_name: TableReport(table_name=table_name)
            for table_name in LOAD_TABLE_ORDER
        }
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            table_name: {
                "input": table.input_rows,
                "inserted": table.inserted,
                "skipped": table.skipped,
                "failed": table.failed,
                "failed_batches": table.failed_batches,
            }
            for table_name, table in self.tables.items()
        }


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else ROOT / path


def setup_logging(report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    log_path = report_dir / "fis_load_failures.jsonl"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler(report_dir / "fis_load.log", encoding="utf-8"),
        ],
    )
    return log_path


def log_failure(log_path: Path, table_name: str, reason: str, payload: dict[str, Any]) -> None:
    with log_path.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {"table": table_name, "reason": reason, "payload": payload},
                ensure_ascii=False,
                default=str,
            )
            + "\n"
        )


def require_columns(reader: csv.DictReader, required_columns: Sequence[str], path: Path) -> None:
    missing_columns = [
        column for column in required_columns if column not in (reader.fieldnames or [])
    ]
    if missing_columns:
        raise MissingRequiredValueError(f"{path} is missing columns: {missing_columns}")


def optional_text(value: str | None) -> str | None:
    normalized = (value or "").strip()
    return normalized or None


def parse_decimal(value: str | None, *, required: bool) -> Decimal | None:
    normalized = (value or "").strip().replace(",", "")
    if not normalized:
        if required:
            raise ValueError("empty decimal")
        return None
    try:
        return Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError(f"invalid decimal: {value}") from exc


def read_item_rows(path: Path, log_path: Path) -> tuple[list[FisItemRow], int]:
    rows: list[FisItemRow] = []
    failed = 0
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        require_columns(reader, ITEM_REQUIRED_COLUMNS, path)
        for row_number, row in enumerate(reader, start=2):
            missing = [
                column
                for column in ITEM_REQUIRED_COLUMNS
                if not (row.get(column) or "").strip()
            ]
            if missing:
                failed += 1
                log_failure(
                    log_path,
                    "fis_item",
                    f"row {row_number}: missing required values {missing}",
                    {"row_number": row_number, "row": row},
                )
                continue
            rows.append(
                FisItemRow(
                    row_number=row_number,
                    item_key=row["item_key"].strip(),
                    canonical_item_name=row["canonical_item"].strip(),
                    cmdt_id=row["cmdt_id"].strip(),
                    cmdt_se_cd=row["cmdt_se_cd"].strip(),
                    item_name=row["item_name"].strip(),
                    price_unit=row["price_unit"].strip(),
                    converted_unit=optional_text(row.get("converted_unit")),
                    relation_type=row["relation_type"].strip(),
                )
            )
    return rows, failed


def read_observation_rows(path: Path, log_path: Path) -> tuple[list[FisObservationRow], int]:
    rows: list[FisObservationRow] = []
    failed = 0
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        require_columns(reader, OBSERVATION_REQUIRED_COLUMNS, path)
        for row_number, row in enumerate(reader, start=2):
            missing = [
                column
                for column in OBSERVATION_REQUIRED_COLUMNS
                if not (row.get(column) or "").strip()
            ]
            if missing:
                failed += 1
                log_failure(
                    log_path,
                    "fis_price_observation",
                    f"row {row_number}: missing required values {missing}",
                    {"row_number": row_number, "row": row},
                )
                continue
            try:
                rows.append(
                    FisObservationRow(
                        row_number=row_number,
                        item_key=row["item_key"].strip(),
                        contract_month=row["contract_month"].strip(),
                        trade_date=date.fromisoformat(row["trade_date"].strip()),
                        close_price=parse_decimal(row.get("close_price"), required=True),
                        unit_price=parse_decimal(row.get("unit_price"), required=False),
                        change_amount=parse_decimal(row.get("change_amount"), required=False),
                        change_rate_pct=parse_decimal(row.get("change_rate_pct"), required=False),
                        converted_price=parse_decimal(row.get("converted_price"), required=False),
                    )
                )
            except (ValueError, InvalidOperation) as exc:
                failed += 1
                log_failure(
                    log_path,
                    "fis_price_observation",
                    f"row {row_number}: invalid required value: {exc}",
                    {"row_number": row_number, "row": row},
                )
    return rows, failed


def load_fis_items(
    engine,
    table_report: TableReport,
    rows: Sequence[FisItemRow],
    batch_size: int,
) -> dict[str, int]:
    table_report.input_rows = len({row.item_key for row in rows})
    canonical_names = sorted({row.canonical_item_name for row in rows})

    with engine.begin() as conn:
        canonical_ids = existing_single_key_map(
            conn,
            canonical_item,
            "canonical_item_id",
            "name",
            canonical_names,
        )
        missing = sorted(set(canonical_names) - set(canonical_ids))
        if missing:
            raise ForeignKeyMappingError(f"Missing canonical_item rows for FIS: {missing}")

        item_rows_by_key: dict[str, dict[str, Any]] = {}
        for row in rows:
            item_rows_by_key.setdefault(
                row.item_key,
                {
                    "canonical_item_id": canonical_ids[row.canonical_item_name],
                    "item_key": row.item_key,
                    "cmdt_id": row.cmdt_id,
                    "cmdt_se_cd": row.cmdt_se_cd,
                    "item_name": row.item_name,
                    "price_unit": row.price_unit,
                    "converted_unit": row.converted_unit,
                    "relation_type": row.relation_type,
                },
            )
        item_rows = list(item_rows_by_key.values())
        existing_before = existing_single_key_map(
            conn,
            fis_item,
            "fis_item_id",
            "item_key",
            [row["item_key"] for row in item_rows],
        )
        to_insert = [
            row for row in item_rows if row["item_key"] not in existing_before
        ]
        insert_ignore_existing(conn, fis_item, to_insert, "item_key", batch_size)
        existing_after = existing_single_key_map(
            conn,
            fis_item,
            "fis_item_id",
            "item_key",
            [row["item_key"] for row in item_rows],
        )

    table_report.inserted = len(set(existing_after) - set(existing_before))
    table_report.skipped = table_report.input_rows - table_report.inserted
    return existing_after


def load_observations(
    engine,
    table_report: TableReport,
    rows: Sequence[FisObservationRow],
    fis_item_ids: dict[str, int],
    batch_size: int,
    log_path: Path,
) -> None:
    observation_rows = []
    for row in rows:
        item_id = fis_item_ids.get(row.item_key)
        if item_id is None:
            table_report.failed += 1
            log_failure(
                log_path,
                "fis_price_observation",
                "fk_mapping_failed",
                {"row_number": row.row_number, "item_key": row.item_key},
            )
            continue
        observation_rows.append(
            {
                "fis_item_id": item_id,
                "contract_month": row.contract_month,
                "trade_date": row.trade_date,
                "close_price": row.close_price,
                "unit_price": row.unit_price,
                "change_amount": row.change_amount,
                "change_rate_pct": row.change_rate_pct,
                "converted_price": row.converted_price,
            }
        )

    table_report.input_rows = len(rows)
    key_columns = ["fis_item_id", "contract_month", "trade_date"]
    inserted_total = 0
    skipped_total = 0

    for batch in chunked(observation_rows, batch_size):
        keys = [
            (row["fis_item_id"], row["contract_month"], row["trade_date"])
            for row in batch
        ]
        try:
            with engine.begin() as conn:
                existing_before = set(
                    existing_composite_key_map(
                        conn,
                        fis_price_observation,
                        "fis_price_observation_id",
                        key_columns,
                        keys,
                    )
                )
                to_insert = [
                    row
                    for row in batch
                    if (
                        row["fis_item_id"],
                        row["contract_month"],
                        row["trade_date"],
                    )
                    not in existing_before
                ]
                insert_ignore_existing(
                    conn,
                    fis_price_observation,
                    to_insert,
                    "close_price",
                    batch_size,
                )
                existing_after = set(
                    existing_composite_key_map(
                        conn,
                        fis_price_observation,
                        "fis_price_observation_id",
                        key_columns,
                        keys,
                    )
                )
        except IntegrityError as exc:
            table_report.failed += len(batch)
            table_report.failed_batches += 1
            log_failure(
                log_path,
                "fis_price_observation",
                f"integrity_error: {exc}",
                {"rows": batch},
            )
            if is_foreign_key_integrity_error(exc):
                logging.exception("fis_price_observation FK batch failed. Stopping load.")
                raise
            logging.exception("fis_price_observation batch failed and was rolled back.")
            continue
        except DBAPIError as exc:
            table_report.failed += len(batch)
            table_report.failed_batches += 1
            log_failure(
                log_path,
                "fis_price_observation",
                f"db_error: {exc}",
                {"rows": batch},
            )
            logging.exception("fis_price_observation batch failed and was rolled back.")
            continue

        inserted = len(existing_after - existing_before)
        inserted_total += inserted
        skipped_total += len(batch) - inserted

    table_report.inserted = inserted_total
    table_report.skipped = skipped_total


def run_load(
    item_path: Path,
    observation_path: Path,
    report_dir: Path,
    batch_size: int,
    database_url: str | None,
    create_schema: bool,
    env_file: Path,
) -> LoadReport:
    log_path = setup_logging(report_dir)
    report = LoadReport()
    load_env_file(env_file)
    engine = make_engine(database_url)

    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
    except DBAPIError as exc:
        raise LoadError(f"DB connection failed: {exc}") from exc

    if create_schema:
        metadata.create_all(engine)

    item_rows, item_failed = read_item_rows(item_path, log_path)
    observation_rows, observation_failed = read_observation_rows(observation_path, log_path)
    report.tables["fis_item"].failed += item_failed
    report.tables["fis_price_observation"].failed += observation_failed

    fis_item_ids = load_fis_items(
        engine,
        report.tables["fis_item"],
        item_rows,
        batch_size,
    )
    load_observations(
        engine,
        report.tables["fis_price_observation"],
        observation_rows,
        fis_item_ids,
        batch_size,
        log_path,
    )

    (report_dir / "fis_load_report.json").write_text(
        json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def print_report(report: LoadReport) -> None:
    print("[LOAD REPORT]")
    for table_name in LOAD_TABLE_ORDER:
        table = report.tables[table_name]
        print()
        print(table.table_name)
        print(f"input: {table.input_rows:,}")
        print(f"inserted: {table.inserted:,}")
        print(f"skipped: {table.skipped:,}")
        print(f"failed: {table.failed:,}")
        if table.failed_batches:
            print(f"failed_batches: {table.failed_batches:,}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Load processed FIS commodity data into MySQL.")
    parser.add_argument("--item-input", default=str(DEFAULT_ITEM_PATH), help="Processed fis_item CSV path.")
    parser.add_argument(
        "--observation-input",
        default=str(DEFAULT_OBSERVATION_PATH),
        help="Processed fis_price_observation CSV path.",
    )
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR), help="Load report directory.")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Bulk insert chunk size.")
    parser.add_argument("--database-url", help="SQLAlchemy URL. Defaults to DATABASE_URL or MYSQL_* env.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH), help="Env file with MYSQL_* values.")
    parser.add_argument(
        "--create-schema",
        action="store_true",
        help="Create missing tables using the current SQLAlchemy Core metadata.",
    )
    args = parser.parse_args()
    if args.batch_size <= 0:
        raise ValueError("--batch-size must be greater than 0.")

    try:
        report = run_load(
            resolve_path(args.item_input),
            resolve_path(args.observation_input),
            resolve_path(args.report_dir),
            args.batch_size,
            args.database_url,
            args.create_schema,
            resolve_project_path(args.env_file),
        )
    except MissingRequiredValueError as exc:
        logging.error("Required value validation failed: %s", exc)
        raise
    except ForeignKeyMappingError as exc:
        logging.error("FK mapping failed: %s", exc)
        raise
    except IntegrityError as exc:
        logging.error("Duplicate/FK constraint error during parent load: %s", exc)
        raise
    except DBAPIError as exc:
        logging.error("DB connection or insert error: %s", exc)
        raise
    except SQLAlchemyError as exc:
        logging.error("SQLAlchemy load error: %s", exc)
        raise

    print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
