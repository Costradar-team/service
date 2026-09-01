from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, URL

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV = ROOT / ".env"

_engine: Engine | None = None


def load_env(path: Path = DEFAULT_ENV) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def get_engine() -> Engine:
    global _engine
    if _engine is not None:
        return _engine
    load_env()
    user = os.getenv("MYSQL_USER")
    password = os.getenv("MYSQL_PASSWORD")
    database = os.getenv("MYSQL_DATABASE")
    host = os.getenv("MYSQL_HOST", "127.0.0.1")
    port = int(os.getenv("MYSQL_PORT", "3307"))
    if not all([user, password, database]):
        raise RuntimeError("MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE 가 .env 에 필요합니다.")
    url = URL.create(
        "mysql+pymysql",
        username=user,
        password=password,
        host=host,
        port=port,
        database=database,
        query={"charset": "utf8mb4"},
    )
    _engine = create_engine(url, future=True, pool_pre_ping=True)
    return _engine


def fetch_all(sql: str, params: dict | None = None) -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(text(sql), params or {})
        return [dict(row) for row in rows.mappings().all()]


def fetch_one(sql: str, params: dict | None = None) -> dict | None:
    rows = fetch_all(sql, params)
    return rows[0] if rows else None


def ping() -> bool:
    try:
        fetch_one("SELECT 1 AS ok")
        return True
    except Exception:
        return False
