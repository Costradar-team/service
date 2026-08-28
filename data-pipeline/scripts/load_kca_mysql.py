from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Sequence

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DECIMAL,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    select,
    tuple_,
)
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.engine import Connection, Engine, URL
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parent
DEFAULT_INPUT_PATH = ROOT / "data" / "processed" / "kca_prices_processed.csv"
DEFAULT_REPORT_DIR = ROOT / "reports" / "load"
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_BATCH_SIZE = 1000

REQUIRED_COLUMNS = [
    "상품명",
    "조사일",
    "판매가격",
    "판매업소",
    "제조사",
    "세일여부",
    "원플러스원",
    "canonical_item",
    "subtype",
    "spec",
]

PARENT_TABLE_ORDER = [
    "canonical_item",
    "item_subtype",
    "manufacturer",
    "product",
    "store",
]
LOAD_TABLE_ORDER = PARENT_TABLE_ORDER + ["price_observation"]

metadata = MetaData()

canonical_item = Table(
    "canonical_item",
    metadata,
    Column("canonical_item_id", BigInteger, primary_key=True, autoincrement=True),
    Column("name", String(50), nullable=False, unique=True),
)

item_subtype = Table(
    "item_subtype",
    metadata,
    Column("subtype_id", BigInteger, primary_key=True, autoincrement=True),
    Column(
        "canonical_item_id",
        BigInteger,
        ForeignKey("canonical_item.canonical_item_id"),
        nullable=False,
    ),
    Column("name", String(100), nullable=False),
    UniqueConstraint("canonical_item_id", "name", name="uq_item_subtype_item_name"),
)

manufacturer = Table(
    "manufacturer",
    metadata,
    Column("manufacturer_id", BigInteger, primary_key=True, autoincrement=True),
    Column("name", String(100), nullable=False, unique=True),
)

product = Table(
    "product",
    metadata,
    Column("product_id", BigInteger, primary_key=True, autoincrement=True),
    Column("source_product_name", String(255), nullable=False, unique=True),
    Column("manufacturer_id", BigInteger, ForeignKey("manufacturer.manufacturer_id")),
    Column("subtype_id", BigInteger, ForeignKey("item_subtype.subtype_id"), nullable=False),
    Column("quantity", DECIMAL(10, 2)),
    Column("unit", String(20)),
)

store = Table(
    "store",
    metadata,
    Column("store_id", BigInteger, primary_key=True, autoincrement=True),
    Column("name", String(255), nullable=False, unique=True),
)

price_observation = Table(
    "price_observation",
    metadata,
    Column("price_observation_id", BigInteger, primary_key=True, autoincrement=True),
    Column("product_id", BigInteger, ForeignKey("product.product_id"), nullable=False),
    Column("store_id", BigInteger, ForeignKey("store.store_id"), nullable=False),
    Column("survey_date", Date, nullable=False),
    Column("price", Integer, nullable=False),
    Column("unit_price", DECIMAL(10, 2)),
    Column("is_sale", Boolean),
    Column("is_one_plus_one", Boolean),
    UniqueConstraint("product_id", "store_id", "survey_date", name="uq_price_product_store_date"),
)


@dataclass(frozen=True)
class ProcessedRow:
    row_number: int
    source_product_name: str
    survey_date: date
    price: int
    store_name: str
    manufacturer_name: str
    is_sale: bool | None
    is_one_plus_one: bool | None
    canonical_item_name: str
    subtype_name: str
    quantity: Decimal | None
    unit: str | None


@dataclass
class TableReport:
    table_name: str
    input_rows: int = 0
    inserted: int = 0
    skipped: int = 0
    failed: int = 0
    failed_batches: int = 0


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


class LoadError(Exception):
    pass


class MissingRequiredValueError(LoadError):
    pass


class ForeignKeyMappingError(LoadError):
    pass


def chunked(values: Sequence[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for index in range(0, len(values), size):
        yield list(values[index : index + size])


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else ROOT / path


def resolve_project_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else PROJECT_ROOT / path


def setup_logging(report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    log_path = report_dir / "kca_load_failures.jsonl"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler(report_dir / "kca_load.log", encoding="utf-8"),
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


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            os.environ[key.strip()] = value.strip().strip("'\"")


def make_engine(database_url: str | None) -> Engine:
    url = database_url or os.getenv("DATABASE_URL")
    if not url:
        user = os.getenv("MYSQL_USER")
        password = os.getenv("MYSQL_PASSWORD")
        database = os.getenv("MYSQL_DATABASE")
        host = os.getenv("MYSQL_HOST", "127.0.0.1")
        port = os.getenv("MYSQL_PORT", "3306")
        if not all([user, password, database]):
            raise LoadError(
                "DB connection config is missing. Set DATABASE_URL or MYSQL_USER, "
                "MYSQL_PASSWORD, MYSQL_DATABASE, MYSQL_HOST, MYSQL_PORT."
            )
        url = URL.create(
            "mysql+pymysql",
            username=user,
            password=password,
            host=host,
            port=int(port),
            database=database,
            query={"charset": "utf8mb4"},
        )
    try:
        return create_engine(url, future=True, pool_pre_ping=True)
    except SQLAlchemyError as exc:
        raise LoadError(f"Failed to create DB engine: {exc}") from exc


def parse_bool(value: str) -> bool | None:
    normalized = value.strip().upper()
    if normalized == "":
        return None
    if normalized in {"Y", "YES", "TRUE", "1"}:
        return True
    if normalized in {"N", "NO", "FALSE", "0"}:
        return False
    raise ValueError(f"Unsupported boolean value: {value}")


def parse_spec(spec: str) -> tuple[Decimal | None, str | None]:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([A-Za-z가-힣]+)\s*", spec)
    if not match:
        return None, spec.strip() or None
    return Decimal(match.group(1)), match.group(2)


def normalize_csv_row(row: dict[str, str], row_number: int) -> ProcessedRow:
    missing = [column for column in REQUIRED_COLUMNS if not (row.get(column) or "").strip()]
    required_nullable_in_source = {"세일여부", "원플러스원"}
    missing = [column for column in missing if column not in required_nullable_in_source]
    if missing:
        raise MissingRequiredValueError(f"row {row_number}: missing required values {missing}")

    try:
        quantity, unit = parse_spec(row["spec"])
        return ProcessedRow(
            row_number=row_number,
            source_product_name=row["상품명"].strip(),
            survey_date=date.fromisoformat(row["조사일"].strip()),
            price=int(row["판매가격"].strip().replace(",", "")),
            store_name=row["판매업소"].strip(),
            manufacturer_name=row["제조사"].strip(),
            is_sale=parse_bool(row.get("세일여부", "")),
            is_one_plus_one=parse_bool(row.get("원플러스원", "")),
            canonical_item_name=row["canonical_item"].strip(),
            subtype_name=row["subtype"].strip(),
            quantity=quantity,
            unit=unit,
        )
    except (ValueError, InvalidOperation) as exc:
        raise MissingRequiredValueError(f"row {row_number}: invalid required value: {exc}") from exc


def read_processed_rows(path: Path, log_path: Path) -> tuple[list[ProcessedRow], int]:
    rows: list[ProcessedRow] = []
    failed = 0
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing_columns = [column for column in REQUIRED_COLUMNS if column not in (reader.fieldnames or [])]
        if missing_columns:
            raise MissingRequiredValueError(f"Processed CSV is missing columns: {missing_columns}")
        for row_number, row in enumerate(reader, start=2):
            try:
                rows.append(normalize_csv_row(row, row_number))
            except MissingRequiredValueError as exc:
                failed += 1
                log_failure(log_path, "input", str(exc), {"row_number": row_number, "row": row})
    return rows, failed


def unique_dicts(rows: Iterable[dict[str, Any]], key_columns: Sequence[str]) -> list[dict[str, Any]]:
    by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        by_key.setdefault(tuple(row[column] for column in key_columns), row)
    return list(by_key.values())


def existing_single_key_map(
    conn: Connection,
    table: Table,
    id_column: str,
    key_column: str,
    keys: Sequence[Any],
) -> dict[Any, int]:
    if not keys:
        return {}
    result = conn.execute(
        select(table.c[key_column], table.c[id_column]).where(table.c[key_column].in_(keys))
    )
    return {row[0]: int(row[1]) for row in result}


def existing_composite_key_map(
    conn: Connection,
    table: Table,
    id_column: str,
    key_columns: Sequence[str],
    keys: Sequence[tuple[Any, ...]],
) -> dict[tuple[Any, ...], int]:
    if not keys:
        return {}
    result = conn.execute(
        select(*(table.c[column] for column in key_columns), table.c[id_column]).where(
            tuple_(*(table.c[column] for column in key_columns)).in_(keys)
        )
    )
    return {tuple(row[index] for index in range(len(key_columns))): int(row[-1]) for row in result}


def insert_ignore_existing(
    conn: Connection,
    table: Table,
    rows: Sequence[dict[str, Any]],
    duplicate_update_column: str,
    batch_size: int,
) -> None:
    if not rows:
        return
    for batch in chunked(rows, batch_size):
        statement = mysql_insert(table).values(batch)
        statement = statement.on_duplicate_key_update(**{duplicate_update_column: table.c[duplicate_update_column]})
        conn.execute(statement)


def load_single_key_parent(
    engine: Engine,
    table: Table,
    table_report: TableReport,
    rows: Sequence[dict[str, Any]],
    key_column: str,
    id_column: str,
    batch_size: int,
) -> dict[Any, int]:
    rows = unique_dicts(rows, [key_column])
    table_report.input_rows = len(rows)
    try:
        # Parent dimensions are committed table by table. If any parent commit fails,
        # loading stops before children can be written with incomplete FK references.
        with engine.begin() as conn:
            existing_before = existing_single_key_map(
                conn, table, id_column, key_column, [row[key_column] for row in rows]
            )
            to_insert = [row for row in rows if row[key_column] not in existing_before]
            insert_ignore_existing(conn, table, to_insert, key_column, batch_size)
            existing_after = existing_single_key_map(
                conn, table, id_column, key_column, [row[key_column] for row in rows]
            )
    except (IntegrityError, DBAPIError) as exc:
        logging.exception("Parent load failed for %s. Transaction rolled back.", table.name)
        raise
    except SQLAlchemyError:
        logging.exception("Unexpected insert failure for parent table %s.", table.name)
        raise
    table_report.inserted = len(set(existing_after) - set(existing_before))
    table_report.skipped = table_report.input_rows - table_report.inserted
    return existing_after


def load_item_subtype_parent(
    engine: Engine,
    table_report: TableReport,
    rows: Sequence[ProcessedRow],
    canonical_item_ids: dict[str, int],
    batch_size: int,
) -> dict[tuple[int, str], int]:
    subtype_rows = []
    for row in rows:
        canonical_id = canonical_item_ids.get(row.canonical_item_name)
        if canonical_id is None:
            raise ForeignKeyMappingError(f"Missing canonical_item FK for {row.canonical_item_name}")
        subtype_rows.append({"canonical_item_id": canonical_id, "name": row.subtype_name})
    subtype_rows = unique_dicts(subtype_rows, ["canonical_item_id", "name"])
    table_report.input_rows = len(subtype_rows)
    keys = [(row["canonical_item_id"], row["name"]) for row in subtype_rows]

    try:
        with engine.begin() as conn:
            existing_before = existing_composite_key_map(
                conn,
                item_subtype,
                "subtype_id",
                ["canonical_item_id", "name"],
                keys,
            )
            to_insert = [
                row
                for row in subtype_rows
                if (row["canonical_item_id"], row["name"]) not in existing_before
            ]
            insert_ignore_existing(conn, item_subtype, to_insert, "name", batch_size)
            existing_after = existing_composite_key_map(
                conn,
                item_subtype,
                "subtype_id",
                ["canonical_item_id", "name"],
                keys,
            )
    except (IntegrityError, DBAPIError):
        logging.exception("Parent load failed for item_subtype. Transaction rolled back.")
        raise
    table_report.inserted = len(set(existing_after) - set(existing_before))
    table_report.skipped = table_report.input_rows - table_report.inserted
    return existing_after


def load_products(
    engine: Engine,
    table_report: TableReport,
    rows: Sequence[ProcessedRow],
    manufacturer_ids: dict[str, int],
    subtype_ids: dict[tuple[int, str], int],
    canonical_item_ids: dict[str, int],
    batch_size: int,
) -> dict[str, int]:
    product_rows = []
    for row in rows:
        manufacturer_id = manufacturer_ids.get(row.manufacturer_name)
        canonical_id = canonical_item_ids.get(row.canonical_item_name)
        subtype_id = subtype_ids.get((canonical_id, row.subtype_name))
        if canonical_id is None or subtype_id is None:
            raise ForeignKeyMappingError(
                f"Missing product FK for row {row.row_number}: "
                f"{row.source_product_name}/{row.canonical_item_name}/{row.subtype_name}"
            )
        if manufacturer_id is None:
            raise ForeignKeyMappingError(
                f"Missing manufacturer FK for row {row.row_number}: {row.manufacturer_name}"
            )
        product_rows.append(
            {
                "source_product_name": row.source_product_name,
                "manufacturer_id": manufacturer_id,
                "subtype_id": subtype_id,
                "quantity": row.quantity,
                "unit": row.unit,
            }
        )
    product_rows = unique_dicts(product_rows, ["source_product_name"])
    return load_single_key_parent(
        engine,
        product,
        table_report,
        product_rows,
        "source_product_name",
        "product_id",
        batch_size,
    )


def load_price_observations(
    engine: Engine,
    table_report: TableReport,
    rows: Sequence[ProcessedRow],
    product_ids: dict[str, int],
    store_ids: dict[str, int],
    batch_size: int,
    log_path: Path,
) -> None:
    observation_rows = []
    for row in rows:
        product_id = product_ids.get(row.source_product_name)
        store_id = store_ids.get(row.store_name)
        if product_id is None or store_id is None:
            table_report.failed += 1
            log_failure(
                log_path,
                "price_observation",
                "fk_mapping_failed",
                {
                    "row_number": row.row_number,
                    "source_product_name": row.source_product_name,
                    "store_name": row.store_name,
                },
            )
            continue
        observation_rows.append(
            {
                "product_id": product_id,
                "store_id": store_id,
                "survey_date": row.survey_date,
                "price": row.price,
                "unit_price": None,
                "is_sale": row.is_sale,
                "is_one_plus_one": row.is_one_plus_one,
            }
        )

    table_report.input_rows = len(rows)
    key_columns = ["product_id", "store_id", "survey_date"]
    inserted_total = 0
    skipped_total = 0

    # Fact rows are committed per batch. A failed batch is rolled back and logged,
    # while later batches can still load because all parent FKs have already been secured.
    for batch in chunked(observation_rows, batch_size):
        keys = [(row["product_id"], row["store_id"], row["survey_date"]) for row in batch]
        try:
            with engine.begin() as conn:
                existing_before = set(
                    existing_composite_key_map(
                        conn,
                        price_observation,
                        "price_observation_id",
                        key_columns,
                        keys,
                    )
                )
                to_insert = [
                    row
                    for row in batch
                    if (row["product_id"], row["store_id"], row["survey_date"]) not in existing_before
                ]
                insert_ignore_existing(conn, price_observation, to_insert, "price", batch_size)
                existing_after = set(
                    existing_composite_key_map(
                        conn,
                        price_observation,
                        "price_observation_id",
                        key_columns,
                        keys,
                    )
                )
        except IntegrityError as exc:
            table_report.failed += len(batch)
            table_report.failed_batches += 1
            log_failure(log_path, "price_observation", f"integrity_error: {exc}", {"rows": batch})
            logging.exception("price_observation batch failed and was rolled back.")
            continue
        except DBAPIError as exc:
            table_report.failed += len(batch)
            table_report.failed_batches += 1
            log_failure(log_path, "price_observation", f"db_error: {exc}", {"rows": batch})
            logging.exception("price_observation batch failed and was rolled back.")
            continue
        inserted = len(existing_after - existing_before)
        inserted_total += inserted
        skipped_total += len(batch) - inserted

    table_report.inserted = inserted_total
    table_report.skipped = skipped_total


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

    rows, input_failed = read_processed_rows(input_path, log_path)
    for table_name in LOAD_TABLE_ORDER:
        report.tables[table_name].failed += input_failed if table_name == "price_observation" else 0

    canonical_ids = load_single_key_parent(
        engine,
        canonical_item,
        report.tables["canonical_item"],
        [{"name": row.canonical_item_name} for row in rows],
        "name",
        "canonical_item_id",
        batch_size,
    )
    subtype_ids = load_item_subtype_parent(
        engine,
        report.tables["item_subtype"],
        rows,
        canonical_ids,
        batch_size,
    )
    manufacturer_ids = load_single_key_parent(
        engine,
        manufacturer,
        report.tables["manufacturer"],
        [{"name": row.manufacturer_name} for row in rows],
        "name",
        "manufacturer_id",
        batch_size,
    )
    product_ids = load_products(
        engine,
        report.tables["product"],
        rows,
        manufacturer_ids,
        subtype_ids,
        canonical_ids,
        batch_size,
    )
    store_ids = load_single_key_parent(
        engine,
        store,
        report.tables["store"],
        [{"name": row.store_name} for row in rows],
        "name",
        "store_id",
        batch_size,
    )
    load_price_observations(
        engine,
        report.tables["price_observation"],
        rows,
        product_ids,
        store_ids,
        batch_size,
        log_path,
    )
    (report_dir / "kca_load_report.json").write_text(
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
    totals = {
        "input": sum(table.input_rows for table in report.tables.values()),
        "inserted": sum(table.inserted for table in report.tables.values()),
        "skipped": sum(table.skipped for table in report.tables.values()),
        "failed": sum(table.failed for table in report.tables.values()),
    }
    print()
    print("[SUMMARY]")
    for key, value in totals.items():
        print(f"{key}: {value:,}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Load processed KCA price data into MySQL.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT_PATH), help="Processed KCA CSV path.")
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
            resolve_path(args.input),
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
