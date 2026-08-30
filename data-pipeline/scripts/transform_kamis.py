from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from collect_kamis import PRODUCTS


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW_DIR = ROOT / "data" / "raw" / "kamis"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "processed" / "kamis"
DEFAULT_REPORT_DIR = ROOT / "reports" / "transform"
ITEM_FILENAME = "kamis_item.csv"
PRICE_OBSERVATION_FILENAME = "kamis_price_observation.csv"
REJECTED_FILENAME = "kamis_rejected_rows.csv"
SUMMARY_FILENAME = "kamis_transform_summary.json"
RAW_FILENAME_RE = re.compile(
    r"^kamis_(?P<product>.+)_(?P<start>\d{4}-\d{2}-\d{2})_"
    r"(?P<end>\d{4}-\d{2}-\d{2})\.json$"
)
SCOPE_TYPES = {
    "평균": "AVERAGE",
    "전국": "NATIONAL",
    "평년": "NORMAL_YEAR",
}
PRODUCT_MASTER_OVERRIDES = {
    "egg_10": {
        "canonical_item": "계란",
        "kind_name": "특란10구",
        "rank_code": "71",
        "rank_name": "일반란",
        "quantity": "10",
        "unit": "구",
    },
    "egg_30": {
        "canonical_item": "계란",
        "kind_name": "특란30구",
        "rank_code": "71",
        "rank_name": "일반란",
        "quantity": "30",
        "unit": "구",
    },
    "milk_1l": {
        "canonical_item": "우유",
        "kind_name": "흰우유",
        "rank_code": "00",
        "rank_name": "",
        "quantity": "1",
        "unit": "L",
    },
}
ITEM_COLUMNS = [
    "canonical_item",
    "item_category_code",
    "item_code",
    "item_name",
    "kind_code",
    "kind_name",
    "rank_code",
    "rank_name",
    "quantity",
    "unit",
]
PRICE_OBSERVATION_COLUMNS = [
    "item_category_code",
    "item_code",
    "kind_code",
    "rank_code",
    "observed_date",
    "price_scope_type",
    "scope_name",
    "price",
]
ITEM_KEY_COLUMNS = [
    "item_category_code",
    "item_code",
    "kind_code",
    "rank_code",
]
PRICE_OBSERVATION_GRAIN_COLUMNS = [
    "item_category_code",
    "item_code",
    "kind_code",
    "rank_code",
    "observed_date",
    "price_scope_type",
    "scope_name",
]
REJECTED_COLUMNS = [
    "source_file",
    "source_row_number",
    "reject_reason",
    "product_key",
    "original_itemname",
    "original_kindname",
    "original_countyname",
    "original_marketname",
    "original_yyyy",
    "original_regday",
    "original_price",
]


@dataclass(frozen=True)
class SourceFile:
    path: Path
    product_key: str


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else ROOT / path


def path_for_report(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def collect_json_files_from_path(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() == ".json" else []
    if not path.exists():
        raise FileNotFoundError(f"Input path does not exist: {path}")
    return sorted(
        file
        for file in path.iterdir()
        if file.is_file() and file.suffix.lower() == ".json"
    )


def collect_json_files(paths: list[str]) -> list[Path]:
    files_by_resolved_path: dict[Path, Path] = {}
    for path_text in paths:
        for file in collect_json_files_from_path(resolve_path(path_text)):
            files_by_resolved_path[file.resolve()] = file
    return sorted(files_by_resolved_path.values())


def product_key_from_filename(path: Path) -> str:
    match = RAW_FILENAME_RE.match(path.name)
    if not match:
        raise ValueError(f"Cannot infer KAMIS product key from filename: {path.name}")
    product_key = match.group("product")
    if product_key not in PRODUCTS:
        raise ValueError(f"Unknown KAMIS product key in filename: {product_key}")
    if product_key not in PRODUCT_MASTER_OVERRIDES:
        raise ValueError(f"Missing transform master config for product: {product_key}")
    return product_key


def inspect_sources(files: list[Path]) -> list[SourceFile]:
    return [
        SourceFile(path=file, product_key=product_key_from_filename(file))
        for file in files
    ]


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def extract_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    item = payload.get("data", {}).get("item")
    if isinstance(item, dict):
        return [item]
    if isinstance(item, list):
        return item
    raise ValueError("KAMIS JSON item payload must be an object or a list at data.item.")


def normalized_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def item_master(product_key: str) -> dict[str, str]:
    product = PRODUCTS[product_key]
    overrides = PRODUCT_MASTER_OVERRIDES[product_key]
    return {
        "canonical_item": overrides["canonical_item"],
        "item_category_code": product["item_category_code"],
        "item_code": product["item_code"],
        "item_name": product["item_name"],
        "kind_code": product["kind_code"],
        "kind_name": overrides["kind_name"],
        "rank_code": overrides["rank_code"],
        "rank_name": overrides["rank_name"],
        "quantity": overrides["quantity"],
        "unit": overrides["unit"],
    }


def parse_observed_date(row: dict[str, Any]) -> str:
    date_text = f"{normalized_text(row.get('yyyy'))}/{normalized_text(row.get('regday'))}"
    return datetime.strptime(date_text, "%Y/%m/%d").date().isoformat()


def parse_price(value: Any) -> int:
    normalized = normalized_text(value).replace(",", "")
    price = int(normalized)
    if price <= 0:
        raise ValueError("price must be greater than zero")
    return price


def reject_row(
    *,
    source: SourceFile,
    source_row_number: int,
    reason: str,
    row: dict[str, Any],
) -> dict[str, str]:
    return {
        "source_file": path_for_report(source.path),
        "source_row_number": str(source_row_number),
        "reject_reason": reason,
        "product_key": source.product_key,
        "original_itemname": normalized_text(row.get("itemname")),
        "original_kindname": normalized_text(row.get("kindname")),
        "original_countyname": normalized_text(row.get("countyname")),
        "original_marketname": normalized_text(row.get("marketname")),
        "original_yyyy": normalized_text(row.get("yyyy")),
        "original_regday": normalized_text(row.get("regday")),
        "original_price": normalized_text(row.get("price")),
    }


def make_key(row: dict[str, str], columns: list[str]) -> tuple[str, ...]:
    return tuple(row[column] for column in columns)


def transform(
    sources: list[SourceFile],
    output_dir: Path,
    report_dir: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    item_path = output_dir / ITEM_FILENAME
    observation_path = output_dir / PRICE_OBSERVATION_FILENAME
    rejected_path = report_dir / REJECTED_FILENAME
    summary_path = report_dir / SUMMARY_FILENAME

    input_row_count = 0
    rejected_rows: list[dict[str, str]] = []
    item_rows_by_key: dict[tuple[str, ...], dict[str, str]] = {}
    observation_rows_by_grain: dict[tuple[str, ...], dict[str, str]] = {}
    observation_product_keys_by_grain: dict[tuple[str, ...], str] = {}
    duplicate_removed_count = 0
    conflicting_duplicate_grain_count = 0
    conflicting_duplicate_row_count = 0
    product_key_row_counts: Counter[str] = Counter()
    item_key_row_counts: Counter[str] = Counter()
    scope_type_counts: Counter[str] = Counter()

    for source in sources:
        payload = load_json(source.path)
        rows = extract_items(payload)
        master = item_master(source.product_key)
        item_key = make_key(master, ITEM_KEY_COLUMNS)
        item_rows_by_key[item_key] = {column: master[column] for column in ITEM_COLUMNS}

        for source_row_number, raw_row in enumerate(rows, start=1):
            input_row_count += 1
            countyname = normalized_text(raw_row.get("countyname"))
            price_scope_type = SCOPE_TYPES.get(countyname)
            if price_scope_type is None:
                rejected_rows.append(
                    reject_row(
                        source=source,
                        source_row_number=source_row_number,
                        reason="invalid_scope_name",
                        row=raw_row,
                    )
                )
                continue

            try:
                observed_date = parse_observed_date(raw_row)
            except ValueError:
                rejected_rows.append(
                    reject_row(
                        source=source,
                        source_row_number=source_row_number,
                        reason="invalid_observed_date",
                        row=raw_row,
                    )
                )
                continue

            try:
                price = parse_price(raw_row.get("price"))
            except ValueError:
                rejected_rows.append(
                    reject_row(
                        source=source,
                        source_row_number=source_row_number,
                        reason="invalid_price",
                        row=raw_row,
                    )
                )
                continue

            observation_row = {
                "item_category_code": master["item_category_code"],
                "item_code": master["item_code"],
                "kind_code": master["kind_code"],
                "rank_code": master["rank_code"],
                "observed_date": observed_date,
                "price_scope_type": price_scope_type,
                "scope_name": countyname,
                "price": str(price),
            }
            grain = make_key(observation_row, PRICE_OBSERVATION_GRAIN_COLUMNS)
            existing_row = observation_rows_by_grain.get(grain)
            if existing_row is None:
                observation_rows_by_grain[grain] = observation_row
                observation_product_keys_by_grain[grain] = source.product_key
                continue
            if existing_row == observation_row:
                duplicate_removed_count += 1
                continue

            conflicting_duplicate_grain_count += 1
            conflicting_duplicate_row_count += 1
            rejected_rows.append(
                reject_row(
                    source=source,
                    source_row_number=source_row_number,
                    reason="duplicate_grain_conflict",
                    row=raw_row,
                )
            )

    item_rows = sorted(item_rows_by_key.values(), key=lambda row: make_key(row, ITEM_KEY_COLUMNS))
    observation_rows = sorted(
        observation_rows_by_grain.values(),
        key=lambda row: make_key(row, PRICE_OBSERVATION_GRAIN_COLUMNS),
    )

    for row in observation_rows:
        grain = make_key(row, PRICE_OBSERVATION_GRAIN_COLUMNS)
        item_key = "|".join(row[column] for column in ITEM_KEY_COLUMNS)
        item_key_row_counts[item_key] += 1
        product_key_row_counts[observation_product_keys_by_grain[grain]] += 1
        scope_type_counts[row["price_scope_type"]] += 1

    with item_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ITEM_COLUMNS)
        writer.writeheader()
        writer.writerows(item_rows)

    with observation_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PRICE_OBSERVATION_COLUMNS)
        writer.writeheader()
        writer.writerows(observation_rows)

    with rejected_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REJECTED_COLUMNS)
        writer.writeheader()
        writer.writerows(rejected_rows)

    summary = {
        "input_file_count": len(sources),
        "input_row_count": input_row_count,
        "processed_row_count": len(observation_rows),
        "rejected_row_count": len(rejected_rows),
        "item_row_count": len(item_rows),
        "product_key_row_counts": dict(sorted(product_key_row_counts.items())),
        "item_key_row_counts": dict(sorted(item_key_row_counts.items())),
        "price_scope_type_row_counts": dict(sorted(scope_type_counts.items())),
        "duplicate_removed_count": duplicate_removed_count,
        "duplicate_check": {
            "grain_columns": PRICE_OBSERVATION_GRAIN_COLUMNS,
            "identical_duplicate_removed_row_count": duplicate_removed_count,
            "conflicting_duplicate_grain_count": conflicting_duplicate_grain_count,
            "conflicting_duplicate_row_count": conflicting_duplicate_row_count,
        },
        "source_files": [
            {
                "file": path_for_report(source.path),
                "product_key": source.product_key,
            }
            for source in sources
        ],
        "outputs": {
            "kamis_item": path_for_report(item_path),
            "kamis_price_observation": path_for_report(observation_path),
            "rejected_rows": path_for_report(rejected_path),
            "summary": path_for_report(summary_path),
        },
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Transform raw KAMIS JSON files into processed CSV files."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=[str(DEFAULT_RAW_DIR)],
        help="KAMIS JSON files or directories to transform. Defaults to data/raw/kamis.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for processed KAMIS CSV files.",
    )
    parser.add_argument(
        "--report-dir",
        default=str(DEFAULT_REPORT_DIR),
        help="Directory for KAMIS transform report outputs.",
    )
    args = parser.parse_args()

    files = collect_json_files(args.paths)
    if not files:
        raise FileNotFoundError("No KAMIS JSON input files found.")

    sources = inspect_sources(files)
    summary = transform(
        sources=sources,
        output_dir=resolve_path(args.output_dir),
        report_dir=resolve_path(args.report_dir),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
