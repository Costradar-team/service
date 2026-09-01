from __future__ import annotations

import argparse
import logging
from pathlib import Path

from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from load_kca_mysql import DEFAULT_ENV_PATH, PROJECT_ROOT, load_env_file, make_engine


def resolve_project_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else PROJECT_ROOT / path


def table_exists(conn, table_name: str) -> bool:
    result = conn.exec_driver_sql(
        """
        SELECT COUNT(*)
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
        """,
        (table_name,),
    )
    return int(result.scalar_one()) > 0


def column_exists(conn, table_name: str, column_name: str) -> bool:
    result = conn.exec_driver_sql(
        """
        SELECT COUNT(*)
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND COLUMN_NAME = %s
        """,
        (table_name, column_name),
    )
    return int(result.scalar_one()) > 0


def ensure_column(conn, table_name: str, after_column: str) -> None:
    if column_exists(conn, table_name, "unit_price"):
        return
    conn.exec_driver_sql(
        f"""
        ALTER TABLE {table_name}
        ADD COLUMN unit_price DECIMAL(10,2) NULL
        AFTER {after_column}
        """
    )


def migrate_kca(conn) -> None:
    if not table_exists(conn, "price_observation"):
        logging.info("price_observation does not exist; skipping KCA unit_price migration.")
        return
    ensure_column(conn, "price_observation", "price")
    conn.exec_driver_sql(
        """
        UPDATE price_observation po
        JOIN product p ON p.product_id = po.product_id
        SET po.unit_price = ROUND(po.price / p.quantity, 2)
        WHERE po.unit_price IS NULL
          AND p.quantity IS NOT NULL
          AND p.quantity > 0
        """
    )


def migrate_kamis(conn) -> None:
    if not table_exists(conn, "kamis_price_observation"):
        logging.info("kamis_price_observation does not exist; skipping KAMIS unit_price migration.")
        return
    ensure_column(conn, "kamis_price_observation", "price")
    conn.exec_driver_sql(
        """
        UPDATE kamis_price_observation kpo
        JOIN kamis_item ki ON ki.kamis_item_id = kpo.kamis_item_id
        SET kpo.unit_price = ROUND(kpo.price / ki.quantity, 2)
        WHERE kpo.unit_price IS NULL
          AND ki.quantity IS NOT NULL
          AND ki.quantity > 0
        """
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add and backfill unit_price for KCA and KAMIS observation tables."
    )
    parser.add_argument("--database-url", help="SQLAlchemy URL. Defaults to DATABASE_URL or MYSQL_* env.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH), help="Env file with MYSQL_* values.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_env_file(resolve_project_path(args.env_file))
    engine = make_engine(args.database_url)

    try:
        with engine.begin() as conn:
            migrate_kca(conn)
            migrate_kamis(conn)
    except DBAPIError as exc:
        logging.error("DB error while migrating observation unit_price: %s", exc)
        raise
    except SQLAlchemyError as exc:
        logging.error("SQLAlchemy error while migrating observation unit_price: %s", exc)
        raise

    print("KCA/KAMIS observation unit_price migrated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
