from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUTS = [ROOT / "data/raw/lottemart", ROOT / "data/raw/nonghyup"]
DEFAULT_RULES = ROOT / "config/profiling_rules_retailer.json"
DEFAULT_OUTPUT = ROOT / "reports/profiling/profiling_summary_retailer.json"


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def report_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def csv_files(paths: list[str | Path]) -> list[Path]:
    files: set[Path] = set()
    for value in paths:
        path = resolve_path(value)
        if path.is_file() and path.suffix.lower() == ".csv":
            files.add(path)
        elif path.is_dir():
            files.update(p for p in path.iterdir() if p.is_file() and p.suffix.lower() == ".csv")
        elif not path.exists():
            raise FileNotFoundError(path)
    return sorted(files)


def profile_file(path: Path, rules: dict[str, Any]) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames or []
        rows = [{key: (value or "").strip() for key, value in row.items()} for row in reader]
    required = rules["required_columns"]
    missing_columns = [column for column in required if column not in columns]
    null_counts = {column: sum(not row.get(column, "") for row in rows) for column in columns}
    distinct_counts = {column: len({row[column] for row in rows if row[column]}) for column in columns}
    invalid_dates = invalid_prices = 0
    for row in rows:
        try:
            datetime.fromisoformat(row.get("collected_at", ""))
        except ValueError:
            invalid_dates += 1
        try:
            if int(row.get("sale_price", "").replace(",", "")) <= 0:
                invalid_prices += 1
        except ValueError:
            invalid_prices += 1
    grain = [(r.get("source", ""), r.get("item_id", ""), r.get("collected_at", "")) for r in rows]
    sources = Counter(r.get("source", "") for r in rows)
    product_keys = Counter(r.get("product_key", "") for r in rows)
    unknown_sources = sorted(set(sources) - set(rules["retailer_mapping"]))
    unknown_products = sorted(set(product_keys) - set(rules["product_mapping"]))
    return {
        "file": report_path(path), "encoding": "utf-8-sig", "row_count": len(rows),
        "columns": columns, "missing_columns": missing_columns, "null_counts": null_counts,
        "distinct_counts": distinct_counts, "source_counts": dict(sources),
        "product_key_counts": dict(product_keys), "invalid_collected_at_count": invalid_dates,
        "invalid_sale_price_count": invalid_prices,
        "duplicate_observation_grain_count": len(grain) - len(set(grain)),
        "unknown_sources": unknown_sources, "unknown_product_keys": unknown_products,
        "passed": not (missing_columns or invalid_dates or invalid_prices or unknown_sources or unknown_products),
    }


def build_report(files: list[Path], rules: dict[str, Any]) -> dict[str, Any]:
    profiles = [profile_file(path, rules) for path in files]
    return {"dataset": rules["dataset"], "stage": "pre_transform", "file_count": len(files),
            "row_count": sum(p["row_count"] for p in profiles),
            "passed": bool(profiles) and all(p["passed"] for p in profiles), "profiles": profiles}


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile retailer online-price CSV files.")
    parser.add_argument("paths", nargs="*", default=[str(path) for path in DEFAULT_INPUTS])
    parser.add_argument("--rules", default=str(DEFAULT_RULES)); parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    rules = json.loads(resolve_path(args.rules).read_text(encoding="utf-8"))
    files = csv_files(args.paths)
    if not files: raise FileNotFoundError("No retailer CSV input files found.")
    report = build_report(files, rules)
    output = resolve_path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote retailer profiling report: {output.relative_to(ROOT)}")
    return 0 if report["passed"] else 1


if __name__ == "__main__": raise SystemExit(main())
