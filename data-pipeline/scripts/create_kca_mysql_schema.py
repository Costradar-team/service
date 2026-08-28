from __future__ import annotations

import argparse
import logging
from pathlib import Path

from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from load_kca_mysql import DEFAULT_ENV_PATH, PROJECT_ROOT, load_env_file, make_engine, metadata


def resolve_project_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else PROJECT_ROOT / path


def main() -> int:
    parser = argparse.ArgumentParser(description="Create KCA MySQL tables from SQLAlchemy Core metadata.")
    parser.add_argument("--database-url", help="SQLAlchemy URL. Defaults to DATABASE_URL or MYSQL_* env.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH), help="Env file with MYSQL_* values.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_env_file(resolve_project_path(args.env_file))
    engine = make_engine(args.database_url)

    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        metadata.create_all(engine)
    except DBAPIError as exc:
        logging.error("DB connection or schema creation failed: %s", exc)
        raise
    except SQLAlchemyError as exc:
        logging.error("SQLAlchemy schema creation failed: %s", exc)
        raise

    print("KCA MySQL schema is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
