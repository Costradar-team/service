from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = ROOT.parent
LOAD_SCRIPT_DIR = ROOT / "scripts" / "load"
if str(LOAD_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(LOAD_SCRIPT_DIR))

from load_kca_mysql import DEFAULT_INPUT_PATH, split_store_name  # noqa: E402


DEFAULT_OUTPUT_PATH = ROOT / "data" / "processed" / "kca" / "kca_stores.csv"
STORE_COLUMN = "판매업소"
CSV_ENCODING = "utf-8-sig"
OUTPUT_COLUMNS = [
    "source_store_name",
    "retailer_name",
    "store_branch_name",
    "row_count",
]


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == ROOT.name:
        return PROJECT_ROOT / path
    return ROOT / path


def normalize_store_name(value: str) -> str:
    return " ".join(value.split())


def collect_store_counts(input_path: Path) -> Counter[str]:
    store_counts: Counter[str] = Counter()
    with input_path.open("r", encoding=CSV_ENCODING, newline="") as f:
        reader = csv.DictReader(f)
        if STORE_COLUMN not in (reader.fieldnames or []):
            raise ValueError(f"Input CSV is missing required column: {STORE_COLUMN}")
        for row in reader:
            store_name = normalize_store_name(row.get(STORE_COLUMN, ""))
            if store_name:
                store_counts[store_name] += 1
    return store_counts


def write_stores(output_path: Path, store_counts: Counter[str]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding=CSV_ENCODING, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        for source_store_name, row_count in sorted(store_counts.items()):
            retailer_name, store_branch_name = split_store_name(source_store_name)
            writer.writerow(
                {
                    "source_store_name": source_store_name,
                    "retailer_name": retailer_name,
                    "store_branch_name": store_branch_name,
                    "row_count": row_count,
                }
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export distinct KCA stores from the processed KCA price CSV."
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT_PATH),
        help="Processed KCA CSV path.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Output CSV path for distinct stores.",
    )
    args = parser.parse_args()

    input_path = resolve_path(args.input)
    output_path = resolve_path(args.output)
    store_counts = collect_store_counts(input_path)
    write_stores(output_path, store_counts)
    print(f"Exported {len(store_counts)} stores to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
