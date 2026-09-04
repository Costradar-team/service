from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import inspect, text

from load_kca_mysql import DEFAULT_ENV_PATH, PROJECT_ROOT, load_env_file, make_engine, split_store_name


def resolve_project_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else PROJECT_ROOT / path


def column_exists(inspector, table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def index_exists(inspector, table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def foreign_key_exists(inspector, table_name: str, fk_name: str) -> bool:
    return any(fk["name"] == fk_name for fk in inspector.get_foreign_keys(table_name))


def drop_index_if_exists(conn, table_name: str, index_name: str) -> None:
    exists = conn.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.statistics
            WHERE table_schema = DATABASE()
              AND table_name = :table_name
              AND index_name = :index_name
            """
        ),
        {"table_name": table_name, "index_name": index_name},
    ).scalar_one()
    if exists:
        conn.execute(text(f"ALTER TABLE {table_name} DROP INDEX {index_name}"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate legacy store rows into retailer/source_store_name structure."
    )
    parser.add_argument("--database-url", help="SQLAlchemy URL. Defaults to DATABASE_URL or MYSQL_* env.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH), help="Env file with MYSQL_* values.")
    args = parser.parse_args()

    load_env_file(resolve_project_path(args.env_file))
    engine = make_engine(args.database_url)

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS retailer (
                  retailer_id BIGINT NOT NULL AUTO_INCREMENT,
                  name VARCHAR(255) NOT NULL,
                  PRIMARY KEY (retailer_id),
                  UNIQUE KEY uq_retailer_name (name)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
                """
            )
        )

        inspector = inspect(conn)
        if not column_exists(inspector, "store", "retailer_id"):
            conn.execute(text("ALTER TABLE store ADD COLUMN retailer_id BIGINT NULL AFTER store_id"))
        if not column_exists(inspector, "store", "source_store_name"):
            conn.execute(
                text("ALTER TABLE store ADD COLUMN source_store_name VARCHAR(255) NULL AFTER name")
            )

        drop_index_if_exists(conn, "store", "uq_store_name")
        drop_index_if_exists(conn, "store", "name")

        stores = conn.execute(text("SELECT store_id, name, source_store_name FROM store")).mappings().all()
        for row in stores:
            source_store_name = row["source_store_name"] or row["name"]
            retailer_name, branch_name = split_store_name(source_store_name)
            conn.execute(
                text("INSERT IGNORE INTO retailer (name) VALUES (:name)"),
                {"name": retailer_name},
            )
            retailer_id = conn.execute(
                text("SELECT retailer_id FROM retailer WHERE name = :name"),
                {"name": retailer_name},
            ).scalar_one()
            conn.execute(
                text(
                    """
                    UPDATE store
                    SET retailer_id = :retailer_id,
                        name = :branch_name,
                        source_store_name = :source_store_name
                    WHERE store_id = :store_id
                    """
                ),
                {
                    "retailer_id": retailer_id,
                    "branch_name": branch_name,
                    "source_store_name": source_store_name,
                    "store_id": row["store_id"],
                },
            )

        conn.execute(text("ALTER TABLE store MODIFY retailer_id BIGINT NOT NULL"))
        conn.execute(text("ALTER TABLE store MODIFY source_store_name VARCHAR(255) NOT NULL"))

        inspector = inspect(conn)
        if not index_exists(inspector, "store", "uq_store_retailer_source_store_name"):
            conn.execute(text(
                "ALTER TABLE store ADD UNIQUE KEY "
                "uq_store_retailer_source_store_name (retailer_id, source_store_name)"
            ))
        drop_index_if_exists(conn, "store", "uq_store_source_store_name")
        if not index_exists(inspector, "store", "uq_store_retailer_name"):
            conn.execute(text("ALTER TABLE store ADD UNIQUE KEY uq_store_retailer_name (retailer_id, name)"))
        if not foreign_key_exists(inspector, "store", "fk_store_retailer"):
            conn.execute(
                text(
                    """
                    ALTER TABLE store
                    ADD CONSTRAINT fk_store_retailer
                    FOREIGN KEY (retailer_id) REFERENCES retailer (retailer_id)
                    """
                )
            )

    print("Store retailer migration is complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
