from __future__ import annotations

import argparse
import logging
from pathlib import Path

from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from load_kca_mysql import DEFAULT_ENV_PATH, PROJECT_ROOT, load_env_file, make_engine


TABLE_NAME = "fis_price_observation"
COLUMN_NAME = "unit_price"


def resolve_project_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else PROJECT_ROOT / path


def table_exists(conn) -> bool:
    result = conn.exec_driver_sql(
        """
        SELECT COUNT(*)
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
        """,
        (TABLE_NAME,),
    )
    return int(result.scalar_one()) > 0


def column_exists(conn) -> bool:
    result = conn.exec_driver_sql(
        """
        SELECT COUNT(*)
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND COLUMN_NAME = %s
        """,
        (TABLE_NAME, COLUMN_NAME),
    )
    return int(result.scalar_one()) > 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add fis_price_observation.unit_price and backfill from converted_price."
    )
    parser.add_argument("--database-url", help="SQLAlchemy URL. Defaults to DATABASE_URL or MYSQL_* env.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH), help="Env file with MYSQL_* values.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_env_file(resolve_project_path(args.env_file))
    engine = make_engine(args.database_url)

    try:
        with engine.begin() as conn:
            if not table_exists(conn):
                print(f"{TABLE_NAME} does not exist; create schema before running this migration.")
                return 0

            if not column_exists(conn):
                conn.exec_driver_sql(
                    f"""
                    ALTER TABLE {TABLE_NAME}
                    ADD COLUMN {COLUMN_NAME} DECIMAL(10,2) NULL
                    AFTER close_price
                    """
                )

            conn.exec_driver_sql(
                f"""
                UPDATE {TABLE_NAME}
                SET {COLUMN_NAME} = converted_price
                WHERE {COLUMN_NAME} IS NULL
                  AND converted_price IS NOT NULL
                """
            )
    except DBAPIError as exc:
        logging.error("DB error while migrating FIS unit_price: %s", exc)
        raise
    except SQLAlchemyError as exc:
        logging.error("SQLAlchemy error while migrating FIS unit_price: %s", exc)
        raise

    print("fis_price_observation.unit_price migrated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
