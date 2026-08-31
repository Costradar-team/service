from __future__ import annotations

import argparse
import logging
from pathlib import Path

from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from load_kca_mysql import DEFAULT_ENV_PATH, PROJECT_ROOT, load_env_file, make_engine


OLD_UNIQUE_NAME = "uq_product_source_mfr_subtype_qty_unit"
NEW_UNIQUE_NAME = "source_product_name"
NEW_UNIQUE_COLUMNS = "source_product_name"


def resolve_project_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else PROJECT_ROOT / path


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate product unique key to source_product_name.")
    parser.add_argument("--database-url", help="SQLAlchemy URL. Defaults to DATABASE_URL or MYSQL_* env.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH), help="Env file with MYSQL_* values.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_env_file(resolve_project_path(args.env_file))
    engine = make_engine(args.database_url)

    duplicate_check_sql = f"""
        SELECT {NEW_UNIQUE_COLUMNS}, COUNT(*) AS row_count
        FROM product
        GROUP BY {NEW_UNIQUE_COLUMNS}
        HAVING row_count > 1
    """
    unique_index_sql = """
        SELECT INDEX_NAME
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'product'
          AND NON_UNIQUE = 0
          AND INDEX_NAME <> 'PRIMARY'
        GROUP BY INDEX_NAME
    """

    try:
        with engine.begin() as conn:
            duplicates = list(conn.exec_driver_sql(duplicate_check_sql))
            if duplicates:
                for row in duplicates[:20]:
                    logging.error("Duplicate product composite key: %s", tuple(row))
                raise RuntimeError(
                    f"Cannot add {NEW_UNIQUE_NAME}; duplicate composite product keys exist: "
                    f"{len(duplicates)}"
                )

            unique_indexes = {row[0] for row in conn.exec_driver_sql(unique_index_sql)}
            if OLD_UNIQUE_NAME in unique_indexes:
                conn.exec_driver_sql(f"ALTER TABLE product DROP INDEX {OLD_UNIQUE_NAME}")
            if NEW_UNIQUE_NAME not in unique_indexes:
                conn.exec_driver_sql(
                    f"ALTER TABLE product ADD CONSTRAINT {NEW_UNIQUE_NAME} "
                    f"UNIQUE ({NEW_UNIQUE_COLUMNS})"
                )
    except DBAPIError as exc:
        logging.error("DB error while migrating product unique key: %s", exc)
        raise
    except SQLAlchemyError as exc:
        logging.error("SQLAlchemy error while migrating product unique key: %s", exc)
        raise

    print("product unique key migrated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
