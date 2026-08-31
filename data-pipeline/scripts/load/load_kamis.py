from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, SQLAlchemyError


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = ROOT.parent / ".env"
DEFAULT_ITEM_PATH = ROOT / "data" / "processed" / "kamis" / "kamis_item.csv"
DEFAULT_OBSERVATION_PATH = (
    ROOT / "data" / "processed" / "kamis" / "kamis_price_observation.csv"
)
DEFAULT_REPORT_DIR = ROOT / "reports" / "load"
REPORT_FILENAME = "kamis_load_report.json"
FAILED_OBSERVATION_FILENAME = "kamis_price_observation_failed_rows.csv"

ITEM_KEY_COLUMNS = ["item_category_code", "item_code", "kind_code", "rank_code"]
OBSERVATION_KEY_COLUMNS = [
    "item_category_code",
    "item_code",
    "kind_code",
    "rank_code",
    "observed_date",
    "scope_name",
]
MYSQL_FK_ERROR = 1452


def load_dotenv(path: Path = DEFAULT_ENV_PATH) -> None:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else ROOT / path


def path_for_report(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def mysql_url() -> str:
    host = os.getenv("MYSQL_HOST", "127.0.0.1")
    port = os.getenv("MYSQL_PORT", "3306")
    database = os.getenv("MYSQL_DATABASE")
    user = os.getenv("MYSQL_USER")
    password = os.getenv("MYSQL_PASSWORD")
    missing = [
        name
        for name, value in {
            "MYSQL_DATABASE": database,
            "MYSQL_USER": user,
            "MYSQL_PASSWORD": password,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
    return (
        f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{port}/{database}?charset=utf8mb4"
    )


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Input CSV does not exist: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return [dict(row) for row in csv.DictReader(f)]


def normalize_nullable(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def parse_decimal(value: str | None) -> Decimal | None:
    normalized = normalize_nullable(value)
    if normalized is None:
        return None
    try:
        return Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid decimal value: {value}") from exc


def mysql_error_code(error: IntegrityError) -> int | None:
    original = getattr(error, "orig", None)
    args = getattr(original, "args", ())
    if args and isinstance(args[0], int):
        return args[0]
    return None


def is_fk_error(error: IntegrityError) -> bool:
    return mysql_error_code(error) == MYSQL_FK_ERROR


def key_tuple(row: dict[str, Any], columns: list[str]) -> tuple[Any, ...]:
    return tuple(row[column] for column in columns)


def initial_table_report(input_count: int) -> dict[str, int]:
    return {
        "input": input_count,
        "inserted": 0,
        "updated": 0,
        "failed": 0,
        "failed_batches": 0,
    }


def scalar_in_clause(
    values: set[str],
    *,
    prefix: str,
) -> tuple[str, dict[str, str]]:
    placeholders = []
    params = {}
    for idx, value in enumerate(sorted(values)):
        name = f"{prefix}_{idx}"
        placeholders.append(f":{name}")
        params[name] = value
    return ", ".join(placeholders), params


def fetch_existing_canonical_names(conn: Connection, names: set[str]) -> set[str]:
    if not names:
        return set()
    placeholders, params = scalar_in_clause(names, prefix="name")
    rows = conn.execute(
        text(f"SELECT name FROM canonical_item WHERE name IN ({placeholders})"),
        params,
    ).mappings()
    return {row["name"] for row in rows}


def upsert_canonical_items(
    conn: Connection,
    item_rows: list[dict[str, str]],
) -> dict[str, int]:
    names = sorted({row["canonical_item"].strip() for row in item_rows})
    report = initial_table_report(len(names))
    existing = fetch_existing_canonical_names(conn, set(names))
    stmt = text(
        """
        INSERT INTO canonical_item (name)
        VALUES (:name)
        ON DUPLICATE KEY UPDATE name = VALUES(name)
        """
    )
    params = [{"name": name} for name in names]
    try:
        conn.execute(stmt, params)
    except SQLAlchemyError:
        report["failed"] = len(params)
        raise
    report["inserted"] = sum(1 for name in names if name not in existing)
    report["updated"] = sum(1 for name in names if name in existing)
    return report


def fetch_canonical_ids(conn: Connection, names: set[str]) -> dict[str, int]:
    if not names:
        return {}
    placeholders, params = scalar_in_clause(names, prefix="name")
    rows = conn.execute(
        text(
            f"""
            SELECT canonical_item_id, name
            FROM canonical_item
            WHERE name IN ({placeholders})
            """
        ),
        params,
    ).mappings()
    result = {row["name"]: row["canonical_item_id"] for row in rows}
    missing = sorted(names - set(result))
    if missing:
        raise RuntimeError(f"Missing parent canonical_item rows: {', '.join(missing)}")
    return result


def fetch_existing_kamis_item_keys(
    conn: Connection,
    rows: list[dict[str, Any]],
) -> set[tuple[str, str, str, str]]:
    if not rows:
        return set()
    values = ", ".join(
        f"(:item_category_code_{idx}, :item_code_{idx}, :kind_code_{idx}, :rank_code_{idx})"
        for idx, _row in enumerate(rows)
    )
    params: dict[str, Any] = {}
    for idx, row in enumerate(rows):
        params[f"item_category_code_{idx}"] = row["item_category_code"]
        params[f"item_code_{idx}"] = row["item_code"]
        params[f"kind_code_{idx}"] = row["kind_code"]
        params[f"rank_code_{idx}"] = row["rank_code"]
    result = conn.execute(
        text(
            f"""
            SELECT item_category_code, item_code, kind_code, rank_code
            FROM kamis_item
            WHERE (item_category_code, item_code, kind_code, rank_code) IN ({values})
            """
        ),
        params,
    ).mappings()
    return {
        (
            row["item_category_code"],
            row["item_code"],
            row["kind_code"],
            row["rank_code"],
        )
        for row in result
    }


def upsert_kamis_items(
    conn: Connection,
    item_rows: list[dict[str, str]],
) -> dict[str, int]:
    report = initial_table_report(len(item_rows))
    canonical_ids = fetch_canonical_ids(
        conn, {row["canonical_item"].strip() for row in item_rows}
    )
    params = [
        {
            "canonical_item_id": canonical_ids[row["canonical_item"].strip()],
            "item_category_code": row["item_category_code"].strip(),
            "item_code": row["item_code"].strip(),
            "item_name": row["item_name"].strip(),
            "kind_code": row["kind_code"].strip(),
            "kind_name": row["kind_name"].strip(),
            "rank_code": row["rank_code"].strip(),
            "rank_name": normalize_nullable(row.get("rank_name")),
            "quantity": parse_decimal(row.get("quantity")),
            "unit": normalize_nullable(row.get("unit")),
        }
        for row in item_rows
    ]
    existing = fetch_existing_kamis_item_keys(conn, params)
    stmt = text(
        """
        INSERT INTO kamis_item (
          canonical_item_id,
          item_category_code,
          item_code,
          item_name,
          kind_code,
          kind_name,
          rank_code,
          rank_name,
          quantity,
          unit
        )
        VALUES (
          :canonical_item_id,
          :item_category_code,
          :item_code,
          :item_name,
          :kind_code,
          :kind_name,
          :rank_code,
          :rank_name,
          :quantity,
          :unit
        )
        ON DUPLICATE KEY UPDATE
          canonical_item_id = VALUES(canonical_item_id),
          item_name = VALUES(item_name),
          kind_name = VALUES(kind_name),
          rank_name = VALUES(rank_name),
          quantity = VALUES(quantity),
          unit = VALUES(unit)
        """
    )
    try:
        conn.execute(stmt, params)
    except SQLAlchemyError:
        report["failed"] = len(params)
        raise
    report["inserted"] = sum(1 for row in params if key_tuple(row, ITEM_KEY_COLUMNS) not in existing)
    report["updated"] = sum(1 for row in params if key_tuple(row, ITEM_KEY_COLUMNS) in existing)
    return report


def fetch_kamis_item_ids(
    conn: Connection,
    item_rows: list[dict[str, str]],
) -> dict[tuple[str, str, str, str], int]:
    keys = [
        {
            "item_category_code": row["item_category_code"].strip(),
            "item_code": row["item_code"].strip(),
            "kind_code": row["kind_code"].strip(),
            "rank_code": row["rank_code"].strip(),
        }
        for row in item_rows
    ]
    if not keys:
        return {}
    values = ", ".join(
        f"(:item_category_code_{idx}, :item_code_{idx}, :kind_code_{idx}, :rank_code_{idx})"
        for idx, _row in enumerate(keys)
    )
    params: dict[str, Any] = {}
    for idx, row in enumerate(keys):
        params[f"item_category_code_{idx}"] = row["item_category_code"]
        params[f"item_code_{idx}"] = row["item_code"]
        params[f"kind_code_{idx}"] = row["kind_code"]
        params[f"rank_code_{idx}"] = row["rank_code"]
    rows = conn.execute(
        text(
            f"""
            SELECT
              kamis_item_id,
              item_category_code,
              item_code,
              kind_code,
              rank_code
            FROM kamis_item
            WHERE (item_category_code, item_code, kind_code, rank_code) IN ({values})
            """
        ),
        params,
    ).mappings()
    result = {
        (
            row["item_category_code"],
            row["item_code"],
            row["kind_code"],
            row["rank_code"],
        ): row["kamis_item_id"]
        for row in rows
    }
    missing = sorted(
        {
            key_tuple(row, ITEM_KEY_COLUMNS)
            for row in keys
            if key_tuple(row, ITEM_KEY_COLUMNS) not in result
        }
    )
    if missing:
        raise RuntimeError(f"Missing parent kamis_item rows: {missing}")
    return result


def observation_params(
    observation_rows: list[dict[str, str]],
    kamis_item_ids: dict[tuple[str, str, str, str], int],
) -> list[dict[str, Any]]:
    params = []
    for row_number, row in enumerate(observation_rows, start=2):
        item_key = key_tuple(row, ITEM_KEY_COLUMNS)
        if item_key not in kamis_item_ids:
            raise RuntimeError(
                f"Missing parent kamis_item for observation row {row_number}: {item_key}"
            )
        params.append(
            {
                "source_row_number": row_number,
                "kamis_item_id": kamis_item_ids[item_key],
                "observed_date": date.fromisoformat(row["observed_date"].strip()),
                "price": int(row["price"].strip()),
                "scope_name": row["scope_name"].strip(),
                **{column: row[column].strip() for column in OBSERVATION_KEY_COLUMNS},
            }
        )
    return params


def fetch_existing_observation_keys(
    conn: Connection,
    rows: list[dict[str, Any]],
) -> set[tuple[int, date, str]]:
    if not rows:
        return set()
    values = ", ".join(
        f"(:kamis_item_id_{idx}, :observed_date_{idx}, :scope_name_{idx})"
        for idx, _row in enumerate(rows)
    )
    params: dict[str, Any] = {}
    for idx, row in enumerate(rows):
        params[f"kamis_item_id_{idx}"] = row["kamis_item_id"]
        params[f"observed_date_{idx}"] = row["observed_date"]
        params[f"scope_name_{idx}"] = row["scope_name"]
    result = conn.execute(
        text(
            f"""
            SELECT kamis_item_id, observed_date, scope_name
            FROM kamis_price_observation
            WHERE (kamis_item_id, observed_date, scope_name) IN ({values})
            """
        ),
        params,
    ).mappings()
    return {
        (
            row["kamis_item_id"],
            row["observed_date"],
            row["scope_name"],
        )
        for row in result
    }


def observation_db_key(row: dict[str, Any]) -> tuple[int, date, str]:
    return (
        row["kamis_item_id"],
        row["observed_date"],
        row["scope_name"],
    )


def failed_row(
    row: dict[str, Any],
    *,
    batch_number: int,
    error: BaseException,
) -> dict[str, str]:
    return {
        "batch_number": str(batch_number),
        "source_row_number": str(row["source_row_number"]),
        "error_type": type(error).__name__,
        "error": str(error),
        **{column: str(row[column]) for column in OBSERVATION_KEY_COLUMNS},
        "price": str(row["price"]),
    }


def write_failed_rows(path: Path, rows: list[dict[str, str]]) -> None:
    columns = [
        "batch_number",
        "source_row_number",
        "error_type",
        "error",
        *OBSERVATION_KEY_COLUMNS,
        "price",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def upsert_observation_batch(conn: Connection, rows: list[dict[str, Any]]) -> None:
    stmt = text(
        """
        INSERT INTO kamis_price_observation (
          kamis_item_id,
          observed_date,
          price,
          scope_name
        )
        VALUES (
          :kamis_item_id,
          :observed_date,
          :price,
          :scope_name
        )
        ON DUPLICATE KEY UPDATE
          price = VALUES(price)
        """
    )
    conn.execute(stmt, rows)


def upsert_kamis_observations(
    conn: Connection,
    observation_rows: list[dict[str, str]],
    kamis_item_ids: dict[tuple[str, str, str, str], int],
    *,
    batch_size: int,
    failed_rows_path: Path,
) -> dict[str, int]:
    params = observation_params(observation_rows, kamis_item_ids)
    report = initial_table_report(len(params))
    existing = fetch_existing_observation_keys(conn, params)
    failed_rows: list[dict[str, str]] = []

    for batch_start in range(0, len(params), batch_size):
        batch_number = batch_start // batch_size + 1
        batch = params[batch_start : batch_start + batch_size]
        try:
            upsert_observation_batch(conn, batch)
        except IntegrityError as exc:
            if is_fk_error(exc):
                report["failed"] += len(batch)
                report["failed_batches"] += 1
                failed_rows.extend(
                    failed_row(row, batch_number=batch_number, error=exc)
                    for row in batch
                )
                write_failed_rows(failed_rows_path, failed_rows)
                raise
            report["failed_batches"] += 1
            for row in batch:
                try:
                    upsert_observation_batch(conn, [row])
                except IntegrityError as row_exc:
                    if is_fk_error(row_exc):
                        report["failed"] += 1
                        failed_rows.append(
                            failed_row(row, batch_number=batch_number, error=row_exc)
                        )
                        write_failed_rows(failed_rows_path, failed_rows)
                        raise
                    report["failed"] += 1
                    failed_rows.append(
                        failed_row(row, batch_number=batch_number, error=row_exc)
                    )
        except SQLAlchemyError as exc:
            report["failed_batches"] += 1
            for row in batch:
                try:
                    upsert_observation_batch(conn, [row])
                except SQLAlchemyError as row_exc:
                    report["failed"] += 1
                    failed_rows.append(
                        failed_row(row, batch_number=batch_number, error=row_exc)
                    )

    failed_source_rows = {int(failed["source_row_number"]) for failed in failed_rows}
    successful = [
        row for row in params if row["source_row_number"] not in failed_source_rows
    ]
    report["inserted"] = sum(
        1 for row in successful if observation_db_key(row) not in existing
    )
    report["updated"] = sum(
        1 for row in successful if observation_db_key(row) in existing
    )
    write_failed_rows(failed_rows_path, failed_rows)
    return report


def load_kamis(
    *,
    item_path: Path,
    observation_path: Path,
    report_dir: Path,
    batch_size: int,
) -> dict[str, Any]:
    item_rows = read_csv_rows(item_path)
    observation_rows = read_csv_rows(observation_path)
    report_dir.mkdir(parents=True, exist_ok=True)
    failed_rows_path = report_dir / FAILED_OBSERVATION_FILENAME
    report_path = report_dir / REPORT_FILENAME

    engine = create_engine(mysql_url(), future=True)
    report: dict[str, Any] = {}
    with engine.begin() as conn:
        report["canonical_item"] = upsert_canonical_items(conn, item_rows)
        report["kamis_item"] = upsert_kamis_items(conn, item_rows)
        kamis_item_ids = fetch_kamis_item_ids(conn, item_rows)
        report["kamis_price_observation"] = upsert_kamis_observations(
            conn,
            observation_rows,
            kamis_item_ids,
            batch_size=batch_size,
            failed_rows_path=failed_rows_path,
        )

    report["outputs"] = {
        "failed_rows": path_for_report(failed_rows_path),
        "summary": path_for_report(report_path),
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Load processed KAMIS CSV files into MySQL."
    )
    parser.add_argument(
        "--items",
        default=str(DEFAULT_ITEM_PATH),
        help="Path to kamis_item.csv.",
    )
    parser.add_argument(
        "--observations",
        default=str(DEFAULT_OBSERVATION_PATH),
        help="Path to kamis_price_observation.csv.",
    )
    parser.add_argument(
        "--report-dir",
        default=str(DEFAULT_REPORT_DIR),
        help="Directory for load report outputs.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Batch size for kamis_price_observation upserts.",
    )
    args = parser.parse_args()
    if args.batch_size <= 0:
        raise ValueError("--batch-size must be greater than zero")

    load_dotenv()
    report = load_kamis(
        item_path=resolve_path(args.items),
        observation_path=resolve_path(args.observations),
        report_dir=resolve_path(args.report_dir),
        batch_size=args.batch_size,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
