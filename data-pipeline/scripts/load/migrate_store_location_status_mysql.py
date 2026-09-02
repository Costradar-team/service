from __future__ import annotations

import argparse
import logging
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from load_kca_mysql import DEFAULT_ENV_PATH, PROJECT_ROOT, load_env_file, make_engine


def resolve_project_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else PROJECT_ROOT / path


def column_exists(inspector, table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def table_exists(inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def foreign_key_exists(inspector, table_name: str, constraint_name: str) -> bool:
    return any(fk.get("name") == constraint_name for fk in inspector.get_foreign_keys(table_name))


def migrate(conn: Connection) -> None:
    inspector = inspect(conn)
    if not table_exists(inspector, "region"):
        conn.execute(
            text(
                """
                CREATE TABLE region (
                  region_id BIGINT NOT NULL AUTO_INCREMENT,
                  parent_region_id BIGINT NULL,
                  name VARCHAR(50) NOT NULL,
                  region_type VARCHAR(20) NOT NULL,
                  PRIMARY KEY (region_id),
                  UNIQUE KEY uq_region_parent_name (parent_region_id, name),
                  CONSTRAINT fk_region_parent
                    FOREIGN KEY (parent_region_id) REFERENCES region (region_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
                """
            )
        )

    inspector = inspect(conn)
    if not column_exists(inspector, "store", "store_type"):
        conn.execute(
            text("ALTER TABLE store ADD COLUMN store_type VARCHAR(20) NOT NULL DEFAULT 'BRANCH' AFTER source_store_name")
        )
    if not column_exists(inspector, "store", "store_status"):
        conn.execute(
            text("ALTER TABLE store ADD COLUMN store_status VARCHAR(20) NOT NULL DEFAULT 'open' AFTER store_type")
        )
    if not column_exists(inspector, "store", "match_status"):
        conn.execute(
            text("ALTER TABLE store ADD COLUMN match_status VARCHAR(20) NOT NULL DEFAULT 'matched' AFTER store_status")
        )
    if not column_exists(inspector, "store", "validation_status"):
        conn.execute(
            text("ALTER TABLE store ADD COLUMN validation_status VARCHAR(20) NOT NULL DEFAULT 'valid' AFTER match_status")
        )
    if not column_exists(inspector, "store", "region_id"):
        conn.execute(text("ALTER TABLE store ADD COLUMN region_id BIGINT NULL AFTER validation_status"))

    inspector = inspect(conn)
    if not foreign_key_exists(inspector, "store", "fk_store_region"):
        conn.execute(
            text(
                """
                ALTER TABLE store
                ADD CONSTRAINT fk_store_region
                FOREIGN KEY (region_id) REFERENCES region (region_id)
                """
            )
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Add store location/status columns and region table.")
    parser.add_argument("--database-url", help="SQLAlchemy URL. Defaults to DATABASE_URL or MYSQL_* env.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH), help="Env file with MYSQL_* values.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_env_file(resolve_project_path(args.env_file))
    engine = make_engine(args.database_url)

    try:
        with engine.begin() as conn:
            migrate(conn)
    except DBAPIError as exc:
        logging.error("DB connection or migration failed: %s", exc)
        raise
    except SQLAlchemyError as exc:
        logging.error("SQLAlchemy migration failed: %s", exc)
        raise

    print("store location/status migration is complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
