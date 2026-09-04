from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert

from load_kca_mysql import (
    DEFAULT_ENV_PATH, ROOT, canonical_item, item_subtype, manufacturer, metadata,
    product, retailer, retailer_price_observation, retailer_product_listing,
    load_env_file, make_engine, resolve_project_path,
)


DEFAULT_INPUT_DIR = ROOT / "data/processed/retailer"
DEFAULT_REPORT_DIR = ROOT / "reports/load"
LISTING_REQUIRED = ["retailer_name", "source_product_id", "source_product_name", "product_url", "canonical_item", "subtype", "manufacturer_name", "quantity", "unit"]
OBS_REQUIRED = ["retailer_name", "source_product_id", "collected_at", "price", "promotion_type"]


def resolve_path(value: str | Path) -> Path:
    path = Path(value); return path if path.is_absolute() else ROOT / path


def read_rows(path: Path, required: list[str]) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream); missing = [c for c in required if c not in (reader.fieldnames or [])]
        if missing: raise ValueError(f"{path} is missing columns: {missing}")
        return [{k: (v or "").strip() for k, v in row.items()} for row in reader]


def decimal_or_none(value: str) -> Decimal | None:
    if not value: return None
    try: return Decimal(value)
    except InvalidOperation as exc: raise ValueError(f"invalid quantity: {value}") from exc


def upsert(conn, table, rows: list[dict[str, Any]], update_columns: list[str] | None = None) -> None:
    if not rows: return
    statement = mysql_insert(table).values(rows)
    columns = update_columns or []
    if columns:
        updates = {c: getattr(statement.inserted, c) for c in columns}
    else:
        no_op_column = next(iter(rows[0]))
        updates = {no_op_column: table.c[no_op_column]}
    statement = statement.on_duplicate_key_update(**updates)
    conn.execute(statement)


def id_map(conn, table, id_column: str, key_column: str, values: set[str]) -> dict[str, int]:
    if not values: return {}
    rows = conn.execute(select(table.c[id_column], table.c[key_column]).where(table.c[key_column].in_(values)))
    return {key: identifier for identifier, key in rows}


def product_id_map(conn, keys: set[tuple[str, int | None, int]]) -> dict[tuple[str, int | None, int], int]:
    names = {key[0] for key in keys}
    rows = conn.execute(
        select(product.c.product_id, product.c.source_product_name, product.c.manufacturer_id, product.c.subtype_id)
        .where(product.c.source_product_name.in_(names))
    )
    return {(name, manufacturer_id, subtype_id): product_id for product_id, name, manufacturer_id, subtype_id in rows if (name, manufacturer_id, subtype_id) in keys}


def load(listing_rows: list[dict[str, str]], observation_rows: list[dict[str, str]], database_url: str | None, create_schema: bool) -> dict[str, int]:
    engine = make_engine(database_url)
    if create_schema: metadata.create_all(engine)
    with engine.begin() as conn:
        canonical_names = {r["canonical_item"] for r in listing_rows}
        upsert(conn, canonical_item, [{"name": n} for n in sorted(canonical_names)])
        canonical_ids = id_map(conn, canonical_item, "canonical_item_id", "name", canonical_names)
        subtype_keys = {(canonical_ids[r["canonical_item"]], r["subtype"]) for r in listing_rows}
        upsert(conn, item_subtype, [{"canonical_item_id": cid, "name": name} for cid, name in sorted(subtype_keys)])
        subtype_rows = conn.execute(select(item_subtype.c.subtype_id, item_subtype.c.canonical_item_id, item_subtype.c.name))
        subtype_ids = {(cid, name): sid for sid, cid, name in subtype_rows if (cid, name) in subtype_keys}
        manufacturer_names = {r["manufacturer_name"] for r in listing_rows if r["manufacturer_name"]}
        upsert(conn, manufacturer, [{"name": n} for n in sorted(manufacturer_names)])
        manufacturer_ids = id_map(conn, manufacturer, "manufacturer_id", "name", manufacturer_names)
        product_by_key: dict[tuple[str, int | None, int], dict[str, Any]] = {}
        for row in listing_rows:
            payload = {"source_product_name": row["source_product_name"], "manufacturer_id": manufacturer_ids.get(row["manufacturer_name"]),
                       "subtype_id": subtype_ids[(canonical_ids[row["canonical_item"]], row["subtype"])],
                       "quantity": decimal_or_none(row["quantity"]), "unit": row["unit"] or None}
            key = (payload["source_product_name"], payload["manufacturer_id"], payload["subtype_id"])
            previous = product_by_key.get(key)
            if previous and previous != payload: raise ValueError(f"conflicting product metadata: {key}")
            product_by_key[key] = payload
        upsert(conn, product, list(product_by_key.values()), ["quantity", "unit"])
        product_ids = product_id_map(conn, set(product_by_key))
        retailer_names = {r["retailer_name"] for r in listing_rows}
        upsert(conn, retailer, [{"name": n} for n in sorted(retailer_names)])
        retailer_ids = id_map(conn, retailer, "retailer_id", "name", retailer_names)
        listing_payloads = [{"product_id": product_ids[(r["source_product_name"], manufacturer_ids.get(r["manufacturer_name"]), subtype_ids[(canonical_ids[r["canonical_item"]], r["subtype"])])], "retailer_id": retailer_ids[r["retailer_name"]],
                             "source_product_id": r["source_product_id"], "source_product_name": r["source_product_name"], "product_url": r["product_url"]} for r in listing_rows]
        upsert(conn, retailer_product_listing, listing_payloads, ["product_id", "source_product_name", "product_url"])
        listing_db_rows = conn.execute(select(retailer_product_listing.c.listing_id, retailer_product_listing.c.retailer_id, retailer_product_listing.c.source_product_id))
        listing_ids = {(rid, source_id): lid for lid, rid, source_id in listing_db_rows}
        obs_payloads = []
        for row in observation_rows:
            rid = retailer_ids.get(row["retailer_name"]); lid = listing_ids.get((rid, row["source_product_id"]))
            if lid is None: raise ValueError(f"listing FK not found: {row['retailer_name']}/{row['source_product_id']}")
            price = int(row["price"])
            if price <= 0: raise ValueError(f"invalid price: {price}")
            obs_payloads.append({"listing_id": lid, "collected_at": datetime.fromisoformat(row["collected_at"]), "price": price, "promotion_type": row["promotion_type"] or None})
        upsert(conn, retailer_price_observation, obs_payloads, ["price", "promotion_type"])
    return {"listing_input": len(listing_rows), "observation_input": len(observation_rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Load retailer listings and price observations into MySQL.")
    parser.add_argument("--listing-input", default=str(DEFAULT_INPUT_DIR / "retailer_product_listing.csv")); parser.add_argument("--observation-input", default=str(DEFAULT_INPUT_DIR / "retailer_price_observation.csv"))
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR)); parser.add_argument("--database-url"); parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH)); parser.add_argument("--create-schema", action="store_true"); args = parser.parse_args()
    load_env_file(resolve_project_path(args.env_file)); listing_rows = read_rows(resolve_path(args.listing_input), LISTING_REQUIRED); observation_rows = read_rows(resolve_path(args.observation_input), OBS_REQUIRED)
    report = load(listing_rows, observation_rows, args.database_url, args.create_schema); report_dir = resolve_path(args.report_dir); report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "retailer_load_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); print(json.dumps(report)); return 0


if __name__ == "__main__": raise SystemExit(main())
