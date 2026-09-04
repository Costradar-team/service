from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUTS = [ROOT / "data/raw/lottemart", ROOT / "data/raw/nonghyup"]
DEFAULT_RULES = ROOT / "config/profiling_rules_retailer.json"
DEFAULT_OUTPUT = ROOT / "data/processed/retailer"
DEFAULT_REPORT = ROOT / "reports/transform/retailer_transform_summary.json"
LISTING_COLUMNS = ["retailer_name", "source_product_id", "source_product_name", "product_url", "canonical_item", "subtype", "manufacturer_name", "quantity", "unit"]
OBSERVATION_COLUMNS = ["retailer_name", "source_product_id", "collected_at", "price", "promotion_type"]
QUANTITY_RE = re.compile(r"(?i)(\d+(?:\.\d+)?)\s*(kg|g|ml|l|구|개입|개|입|팩|봉)")
UNIT_SCALE = {"kg": (Decimal("1000"), "g"), "l": (Decimal("1000"), "ml")}


def resolve_path(value: str | Path) -> Path:
    path = Path(value); return path if path.is_absolute() else ROOT / path


def csv_files(paths: list[str | Path]) -> list[Path]:
    result: set[Path] = set()
    for value in paths:
        path = resolve_path(value)
        if path.is_file() and path.suffix.lower() == ".csv": result.add(path)
        elif path.is_dir(): result.update(p for p in path.iterdir() if p.is_file() and p.suffix.lower() == ".csv")
        elif not path.exists(): raise FileNotFoundError(path)
    return sorted(result)


def parse_quantity(name: str) -> tuple[str, str]:
    matches = list(QUANTITY_RE.finditer(name))
    if not matches: return "", ""
    match = matches[-1]; quantity = Decimal(match.group(1)); unit = match.group(2).lower()
    if unit in UNIT_SCALE: quantity, unit = quantity * UNIT_SCALE[unit][0], UNIT_SCALE[unit][1]
    return format(quantity, "f"), unit


def normalize_promotion(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        return ""
    if "/" in normalized:
        return "MULTI"
    if "50%" in normalized:
        return "MULTIBUY_50_PERCENT"
    if "증정" in normalized:
        return "FREE_ITEM"
    return "OTHER"


def write_csv(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns); writer.writeheader(); writer.writerows(rows)


def transform(files: list[Path], rules: dict[str, Any], output_dir: Path, report_path: Path) -> dict[str, Any]:
    listings: dict[tuple[str, str], dict[str, str]] = {}; observations: dict[tuple[str, str, str], dict[str, str]] = {}
    rejected: list[dict[str, Any]] = []; input_count = 0; duplicate_count = 0
    for path in files:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            missing = [c for c in rules["required_columns"] if c not in (reader.fieldnames or [])]
            if missing: raise ValueError(f"{path} is missing columns: {missing}")
            for row_number, raw in enumerate(reader, start=2):
                input_count += 1; row = {k: (v or "").strip() for k, v in raw.items()}
                try:
                    retailer_name = rules["retailer_mapping"][row["source"]]
                    canonical_item = rules["product_mapping"][row["product_key"]]
                    collected_at = datetime.fromisoformat(row["collected_at"]).isoformat(timespec="seconds")
                    price = int(row["sale_price"].replace(",", ""))
                    if not row["item_id"] or not row["item_name"] or not row["product_url"] or price <= 0: raise ValueError("missing/invalid required value")
                except (KeyError, ValueError) as exc:
                    rejected.append({"file": str(path.relative_to(ROOT)), "row_number": row_number, "reason": str(exc)}); continue
                quantity, unit = parse_quantity(row["item_name"])
                listing_key = (retailer_name, row["item_id"])
                listing = {"retailer_name": retailer_name, "source_product_id": row["item_id"], "source_product_name": row["item_name"],
                           "product_url": row["product_url"], "canonical_item": canonical_item, "subtype": canonical_item,
                           "manufacturer_name": row["brand_name"], "quantity": quantity, "unit": unit}
                if listing_key in listings and listings[listing_key] != listing: raise ValueError(f"conflicting listing: {listing_key}")
                listings[listing_key] = listing
                grain = (retailer_name, row["item_id"], collected_at)
                observation = {"retailer_name": retailer_name, "source_product_id": row["item_id"], "collected_at": collected_at,
                               "price": str(price), "promotion_type": normalize_promotion(row["promotion_type"])}
                if grain in observations:
                    if observations[grain] != observation: raise ValueError(f"conflicting observation: {grain}")
                    duplicate_count += 1
                observations[grain] = observation
    output_dir.mkdir(parents=True, exist_ok=True); report_path.parent.mkdir(parents=True, exist_ok=True)
    listing_rows = sorted(listings.values(), key=lambda r: (r["retailer_name"], r["source_product_id"]))
    observation_rows = sorted(observations.values(), key=lambda r: (r["collected_at"], r["retailer_name"], r["source_product_id"]))
    write_csv(output_dir / "retailer_product_listing.csv", LISTING_COLUMNS, listing_rows)
    write_csv(output_dir / "retailer_price_observation.csv", OBSERVATION_COLUMNS, observation_rows)
    report = {"input_rows": input_count, "listing_rows": len(listing_rows), "observation_rows": len(observation_rows),
              "identical_duplicate_rows": duplicate_count, "rejected_rows": len(rejected), "rejections": rejected}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Transform retailer online-price CSV files.")
    parser.add_argument("paths", nargs="*", default=[str(p) for p in DEFAULT_INPUTS]); parser.add_argument("--rules", default=str(DEFAULT_RULES))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT)); parser.add_argument("--report", default=str(DEFAULT_REPORT)); args = parser.parse_args()
    rules = json.loads(resolve_path(args.rules).read_text(encoding="utf-8")); files = csv_files(args.paths)
    if not files: raise FileNotFoundError("No retailer CSV input files found.")
    report = transform(files, rules, resolve_path(args.output_dir), resolve_path(args.report)); print(json.dumps(report, ensure_ascii=False)); return 0


if __name__ == "__main__": raise SystemExit(main())
