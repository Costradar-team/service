from __future__ import annotations

import argparse
import csv
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import func, select
from sqlalchemy import inspect, text
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError

from load_kca_mysql import (
    DEFAULT_ENV_PATH,
    ROOT,
    LoadError,
    MissingRequiredValueError,
    PROJECT_ROOT,
    TableReport,
    existing_region_key_map,
    existing_single_key_map,
    insert_ignore_existing,
    load_env_file,
    make_engine,
    metadata,
    region,
    resolve_project_path,
    retailer,
    store,
    unique_dicts,
)


DEFAULT_INPUT_PATH = ROOT / "data" / "processed" / "kca" / "kca_store_master.csv"
DEFAULT_REPORT_DIR = ROOT / "reports" / "load"
DEFAULT_BATCH_SIZE = 1000
CSV_ENCODING = "utf-8-sig"
LOAD_TABLE_ORDER = ["retailer", "region", "store"]
REQUIRED_COLUMNS = [
    "source_store_name",
    "retailer_name",
    "store_branch_name",
    "match_status",
    "validation_status",
    "store_status",
    "region_1depth_name",
    "region_2depth_name",
    "region_3depth_name",
]


def default_store_status(match_status: str) -> str:
    if match_status == "matched":
        return "open"
    if match_status in {"review", "api_not_found"}:
        return "unknown"
    return ""


def is_chain_level_store(source_store_name: str, store_branch_name: str) -> bool:
    return "본사" in source_store_name or "본사" in store_branch_name


def normalized_store_status(store_status: str, store_type: str, match_status: str) -> str:
    if store_status:
        return store_status
    if store_type == "CHAIN_LEVEL":
        return "unknown"
    return default_store_status(match_status) or "unknown"


def normalized_match_status(match_status: str, store_type: str) -> str:
    if store_type == "CHAIN_LEVEL":
        return "not_applicable"
    return match_status


def normalized_validation_status(validation_status: str, store_type: str) -> str:
    if store_type == "CHAIN_LEVEL":
        return "not_applicable"
    return validation_status


def split_region_tokens(*region_names: str) -> list[str]:
    tokens: list[str] = []
    for region_name in region_names:
        for token in clean_text(region_name).split():
            if token and token not in tokens:
                tokens.append(token)
    return tokens


def region_type_from_name(region_name: str, is_root: bool = False) -> str:
    if is_root:
        return "SIDO"
    suffix_types = [
        ("특별자치시", "SIDO"),
        ("특별자치도", "SIDO"),
        ("광역시", "SIDO"),
        ("특별시", "SIDO"),
        ("자치구", "GU"),
        ("시", "SI"),
        ("군", "GUN"),
        ("구", "GU"),
        ("읍", "EUP"),
        ("면", "MYEON"),
        ("동", "DONG"),
        ("리", "RI"),
        ("가", "DONG"),
    ]
    for suffix, region_type in suffix_types:
        if region_name.endswith(suffix):
            return region_type
    return "UNKNOWN"


@dataclass(frozen=True)
class StoreMasterRow:
    row_number: int
    source_store_name: str
    retailer_name: str
    store_branch_name: str
    region_1depth_name: str
    region_2depth_name: str
    region_3depth_name: str
    match_status: str = "matched"
    validation_status: str = "valid"
    store_status: str = "open"

    @property
    def store_type(self) -> str:
        return (
            "CHAIN_LEVEL"
            if is_chain_level_store(self.source_store_name, self.store_branch_name)
            else "BRANCH"
        )

    @property
    def normalized_match_status(self) -> str:
        return normalized_match_status(self.match_status, self.store_type)

    @property
    def normalized_validation_status(self) -> str:
        return normalized_validation_status(self.validation_status, self.store_type)

    @property
    def normalized_store_status(self) -> str:
        return normalized_store_status(
            self.store_status,
            self.store_type,
            self.normalized_match_status,
        )

    @property
    def region_names(self) -> list[tuple[str, str]]:
        if self.store_type == "CHAIN_LEVEL":
            return []
        tokens = split_region_tokens(
            self.region_1depth_name,
            self.region_2depth_name,
            self.region_3depth_name,
        )
        return [
            (region_type_from_name(name, is_root=index == 0), name)
            for index, name in enumerate(tokens)
        ]

    @property
    def region_path_key(self) -> tuple[str, ...]:
        return tuple(name for _, name in self.region_names)


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
    log_path = report_dir / "kca_store_load_failures.jsonl"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(report_dir / "kca_store_load.log", encoding="utf-8"),
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


def require_columns(reader: csv.DictReader, path: Path) -> None:
    missing_columns = [
        column for column in REQUIRED_COLUMNS if column not in (reader.fieldnames or [])
    ]
    if missing_columns:
        raise MissingRequiredValueError(f"{path} is missing columns: {missing_columns}")


def clean_text(value: str | None) -> str:
    return " ".join((value or "").split())


def read_store_master_rows(path: Path, log_path: Path) -> tuple[list[StoreMasterRow], int]:
    rows: list[StoreMasterRow] = []
    failed = 0
    with path.open("r", encoding=CSV_ENCODING, newline="") as f:
        reader = csv.DictReader(f)
        require_columns(reader, path)
        for row_number, row in enumerate(reader, start=2):
            source_store_name = clean_text(row.get("source_store_name"))
            retailer_name = clean_text(row.get("retailer_name"))
            store_branch_name = clean_text(row.get("store_branch_name"))
            missing = []
            if not source_store_name:
                missing.append("source_store_name")
            if not retailer_name:
                missing.append("retailer_name")
            if not store_branch_name:
                missing.append("store_branch_name")
            if missing:
                failed += 1
                log_failure(
                    log_path,
                    "store",
                    f"row {row_number}: missing required values {missing}",
                    {"row_number": row_number, "row": row},
                )
                continue
            match_status = clean_text(row.get("match_status"))
            rows.append(
                StoreMasterRow(
                    row_number=row_number,
                    source_store_name=source_store_name,
                    retailer_name=retailer_name,
                    store_branch_name=store_branch_name,
                    match_status=match_status,
                    validation_status=clean_text(row.get("validation_status")),
                    region_1depth_name=clean_text(row.get("region_1depth_name")),
                    region_2depth_name=clean_text(row.get("region_2depth_name")),
                    region_3depth_name=clean_text(row.get("region_3depth_name")),
                    store_status=clean_text(row.get("store_status")) or default_store_status(match_status),
                )
            )
    return rows, failed


def ensure_store_match_columns(engine: Engine) -> None:
    metadata.create_all(engine, tables=[region])
    inspector = inspect(engine)
    if "store" not in inspector.get_table_names():
        return
    column_names = {column["name"] for column in inspector.get_columns("store")}
    with engine.begin() as conn:
        if "store_type" not in column_names:
            conn.execute(text("ALTER TABLE store ADD COLUMN store_type VARCHAR(20) NOT NULL DEFAULT 'BRANCH' AFTER source_store_name"))
        if "store_status" not in column_names:
            conn.execute(text("ALTER TABLE store ADD COLUMN store_status VARCHAR(20) NOT NULL DEFAULT 'open' AFTER store_type"))
        if "match_status" not in column_names:
            conn.execute(text("ALTER TABLE store ADD COLUMN match_status VARCHAR(20) NOT NULL DEFAULT 'matched' AFTER store_status"))
        if "validation_status" not in column_names:
            conn.execute(text("ALTER TABLE store ADD COLUMN validation_status VARCHAR(20) NOT NULL DEFAULT 'valid' AFTER match_status"))
        if "region_id" not in column_names:
            conn.execute(text("ALTER TABLE store ADD COLUMN region_id BIGINT NULL AFTER validation_status"))


def load_retailers(
    engine: Engine,
    table_report: TableReport,
    rows: Sequence[StoreMasterRow],
    batch_size: int,
) -> dict[str, int]:
    retailer_rows = unique_dicts(
        [{"name": row.retailer_name} for row in rows],
        ["name"],
    )
    table_report.input_rows = len(retailer_rows)
    with engine.begin() as conn:
        existing_before = existing_single_key_map(
            conn,
            retailer,
            "retailer_id",
            "name",
            [row["name"] for row in retailer_rows],
        )
        to_insert = [row for row in retailer_rows if row["name"] not in existing_before]
        insert_ignore_existing(conn, retailer, to_insert, "name", batch_size)
        existing_after = existing_single_key_map(
            conn,
            retailer,
            "retailer_id",
            "name",
            [row["name"] for row in retailer_rows],
        )
    table_report.inserted = len(set(existing_after) - set(existing_before))
    table_report.skipped = table_report.input_rows - table_report.inserted
    return existing_after


def load_region_level(
    conn: Connection,
    rows: Sequence[dict[str, Any]],
) -> dict[tuple[int | None, str], int]:
    if not rows:
        return {}
    keys = [(row["parent_region_id"], row["name"]) for row in rows]
    existing_before = existing_region_key_map(conn, keys)
    to_insert = [
        row
        for row in unique_dicts(rows, ["parent_region_id", "name"])
        if (row["parent_region_id"], row["name"]) not in existing_before
    ]
    if to_insert:
        conn.execute(mysql_insert(region).values(to_insert))
    return existing_region_key_map(conn, keys)


def load_regions(
    engine: Engine,
    table_report: TableReport,
    rows: Sequence[StoreMasterRow],
) -> dict[tuple[str, ...], int]:
    region_paths = sorted(
        {
            tuple(row.region_names)
            for row in rows
            if row.region_names
        }
    )
    table_report.input_rows = len(
        {
            path[: index + 1]
            for path in region_paths
            for index in range(len(path))
        }
    )

    with engine.begin() as conn:
        before_count = int(conn.execute(select(func.count()).select_from(region)).scalar_one())
        path_ids: dict[tuple[str, ...], int] = {}
        max_depth = max((len(path) for path in region_paths), default=0)
        for depth in range(max_depth):
            level_rows = []
            for path in region_paths:
                if len(path) <= depth:
                    continue
                region_type_name, name = path[depth]
                parent_path = tuple(region_name for _, region_name in path[:depth])
                level_rows.append(
                    {
                        "parent_region_id": path_ids.get(parent_path),
                        "name": name,
                        "region_type": region_type_name,
                    }
                )
            level_ids = load_region_level(conn, level_rows)
            for path in region_paths:
                if len(path) <= depth:
                    continue
                parent_path = tuple(region_name for _, region_name in path[:depth])
                path_key = tuple(region_name for _, region_name in path[: depth + 1])
                name = path[depth][1]
                parent_id = path_ids.get(parent_path)
                path_ids[path_key] = level_ids[(parent_id, name)]

        after_count = int(conn.execute(select(func.count()).select_from(region)).scalar_one())

    table_report.inserted = max(0, after_count - before_count)
    table_report.skipped = table_report.input_rows - table_report.inserted

    return path_ids


def store_row_payload(
    row: StoreMasterRow,
    retailer_id: int,
    region_ids: dict[tuple[str, ...], int],
) -> dict[str, Any]:
    region_id = None if row.store_type == "CHAIN_LEVEL" else region_ids.get(row.region_path_key)
    return {
        "retailer_id": retailer_id,
        "name": row.store_branch_name,
        "source_store_name": row.source_store_name,
        "store_type": row.store_type,
        "store_status": row.normalized_store_status,
        "match_status": row.normalized_match_status,
        "validation_status": row.normalized_validation_status,
        "region_id": region_id,
    }


def upsert_stores(
    engine: Engine,
    table_report: TableReport,
    rows: Sequence[StoreMasterRow],
    retailer_ids: dict[str, int],
    region_ids: dict[tuple[str, ...], int],
    batch_size: int,
    log_path: Path,
) -> None:
    store_rows = []
    for row in rows:
        retailer_id = retailer_ids.get(row.retailer_name)
        if retailer_id is None:
            table_report.failed += 1
            log_failure(
                log_path,
                "store",
                "retailer_fk_mapping_failed",
                {"row_number": row.row_number, "retailer_name": row.retailer_name},
            )
            continue
        store_rows.append(store_row_payload(row, retailer_id, region_ids))

    store_rows = unique_dicts(store_rows, ["source_store_name"])
    table_report.input_rows = len(store_rows)
    with engine.begin() as conn:
        existing_before = existing_single_key_map(
            conn,
            store,
            "store_id",
            "source_store_name",
            [row["source_store_name"] for row in store_rows],
        )
        for batch_start in range(0, len(store_rows), batch_size):
            batch = store_rows[batch_start : batch_start + batch_size]
            statement = mysql_insert(store).values(batch)
            statement = statement.on_duplicate_key_update(
                retailer_id=statement.inserted.retailer_id,
                name=statement.inserted.name,
                store_type=statement.inserted.store_type,
                store_status=statement.inserted.store_status,
                match_status=statement.inserted.match_status,
                validation_status=statement.inserted.validation_status,
                region_id=statement.inserted.region_id,
            )
            conn.execute(statement)
        existing_after = existing_single_key_map(
            conn,
            store,
            "store_id",
            "source_store_name",
            [row["source_store_name"] for row in store_rows],
        )
    table_report.inserted = len(set(existing_after) - set(existing_before))
    table_report.skipped = table_report.input_rows - table_report.inserted


def run_load(
    input_path: Path,
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
    ensure_store_match_columns(engine)

    rows, input_failed = read_store_master_rows(input_path, log_path)
    report.tables["store"].failed += input_failed
    retailer_ids = load_retailers(engine, report.tables["retailer"], rows, batch_size)
    region_ids = load_regions(engine, report.tables["region"], rows)
    upsert_stores(
        engine,
        report.tables["store"],
        rows,
        retailer_ids,
        region_ids,
        batch_size,
        log_path,
    )
    (report_dir / "kca_store_load_report.json").write_text(
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
        print(f"skipped_or_updated: {table.skipped:,}")
        print(f"failed: {table.failed:,}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Load enriched KCA store master rows into MySQL.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT_PATH), help="KCA store master CSV path.")
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR), help="Load report directory.")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Bulk upsert chunk size.")
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
            resolve_path(args.input),
            resolve_path(args.report_dir),
            args.batch_size,
            args.database_url,
            args.create_schema,
            resolve_project_path(args.env_file),
        )
    except (MissingRequiredValueError, IntegrityError, DBAPIError, SQLAlchemyError) as exc:
        logging.error("KCA store load failed: %s", exc)
        raise
    print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
