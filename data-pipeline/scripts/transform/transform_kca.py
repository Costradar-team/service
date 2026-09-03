from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RULES_PATH = ROOT / "config" / "profiling_rules.json"
DEFAULT_MAPPING_PATH = ROOT / "config" / "item_mapping.csv"
DEFAULT_RAW_DIR = ROOT / "data" / "raw"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "processed" / "kca"
DEFAULT_REPORT_DIR = ROOT / "reports" / "transform"
PROCESSED_FILENAME = "kca_prices_processed.csv"
UNMAPPED_FILENAME = "unmapped_products.csv"
REJECTED_FILENAME = "rejected_rows.csv"
CONFLICTING_ROWS_FILENAME = "conflicting_rows.csv"
SUMMARY_FILENAME = "transform_summary.json"
CANONICAL_ITEMS = {"밀가루", "설탕", "버터", "계란", "우유"}
MAPPING_COLUMNS = [
    "source_product",
    "canonical_item",
    "subtype",
    "spec",
    "mapping_include",
]
ADDED_COLUMNS = ["canonical_item", "subtype", "spec", "unit_price"]
DUPLICATE_GRAIN_COLUMNS = ["상품명", "판매업소", "조사일"]
REJECT_METADATA_COLUMNS = [
    "source_file",
    "source_row_number",
    "reject_reason",
    "original_조사일",
    "original_판매가격",
]
CONFLICTING_ROW_METADATA_COLUMNS = [
    "source_file",
    "source_row_number",
    "conflict_reason",
    "original_조사일",
    "original_판매가격",
]


@dataclass(frozen=True)
class SourceFile:
    path: Path
    encoding: str
    columns: list[str]


@dataclass(frozen=True)
class TransformRow:
    row: dict[str, str]
    source_file: str
    source_row_number: int
    original_survey_date: str
    original_price: str
    grain_key: tuple[str, ...]
    row_signature: tuple[str, ...]


def load_rules(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path

    cwd_candidate = Path.cwd() / path
    root_candidate = ROOT / path
    if cwd_candidate.exists():
        return cwd_candidate
    if root_candidate.exists():
        return root_candidate
    return cwd_candidate


def path_for_report(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def read_header(path: Path, encodings: list[str]) -> tuple[str, list[str]]:
    last_error: UnicodeDecodeError | None = None
    for encoding in encodings:
        try:
            with path.open("r", encoding=encoding, newline="") as f:
                reader = csv.reader(f)
                return encoding, next(reader, [])
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise ValueError("At least one encoding must be provided.")


def collect_csv_files_from_path(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() == ".csv" else []
    if not path.exists():
        raise FileNotFoundError(f"Input path does not exist: {path}")
    return sorted(
        file
        for file in path.iterdir()
        if file.is_file() and file.suffix.lower() == ".csv"
    )


def collect_csv_files(paths: list[str]) -> list[Path]:
    files_by_resolved_path: dict[Path, Path] = {}
    for path_text in paths:
        for file in collect_csv_files_from_path(resolve_path(path_text)):
            files_by_resolved_path[file.resolve()] = file
    return sorted(files_by_resolved_path.values())


def strip_string_values(row: dict[str, str]) -> dict[str, str]:
    return {
        column: (value.strip() if value is not None else "")
        for column, value in row.items()
    }


def parse_date(value: str, date_format: str) -> str:
    return datetime.strptime(value.strip(), date_format).date().isoformat()


def parse_integer(value: str, allow_thousands_separator: bool) -> int:
    normalized = value.strip()
    if allow_thousands_separator:
        normalized = normalized.replace(",", "")
    return int(normalized)


def parse_spec_quantity(spec: str) -> Decimal | None:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([A-Za-z가-힣]+)\s*", spec)
    if not match:
        return None
    try:
        quantity = Decimal(match.group(1))
    except InvalidOperation:
        return None
    return quantity if quantity > 0 else None


def unit_price_text(price: int, spec: str) -> str:
    quantity = parse_spec_quantity(spec)
    if quantity is None:
        return ""
    unit_price = (Decimal(price) / quantity).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    return format(unit_price, "f")


def make_key(row: dict[str, str], columns: list[str]) -> tuple[str, ...]:
    return tuple((row.get(column) or "").strip() for column in columns)


def make_row_signature(row: dict[str, str], columns: list[str]) -> tuple[str, ...]:
    return tuple((row.get(column) or "").strip() for column in columns)


def load_item_mapping(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing_columns = [
            column
            for column in MAPPING_COLUMNS
            if column not in (reader.fieldnames or [])
        ]
        if missing_columns:
            raise ValueError(f"Mapping file is missing columns: {missing_columns}")

        mapping: dict[str, dict[str, str]] = {}
        duplicate_products: list[str] = []
        invalid_rows: list[str] = []
        for row_number, row in enumerate(reader, start=2):
            stripped = strip_string_values(row)
            product = stripped["source_product"]
            include = stripped["mapping_include"]
            canonical_item = stripped["canonical_item"]
            if not product:
                invalid_rows.append(f"row {row_number}: source_product is empty")
                continue
            if include not in {"O", "X"}:
                invalid_rows.append(f"row {row_number}: mapping_include must be O or X")
            if include == "O" and canonical_item not in CANONICAL_ITEMS:
                invalid_rows.append(f"row {row_number}: invalid canonical_item")
            if product in mapping:
                duplicate_products.append(product)
            mapping[product] = stripped

    if duplicate_products:
        duplicates = sorted(set(duplicate_products))
        raise ValueError(f"Mapping file has duplicate source_product values: {duplicates}")
    if invalid_rows:
        raise ValueError("Invalid mapping rows: " + "; ".join(invalid_rows))
    return mapping


def inspect_sources(files: list[Path], rules: dict[str, Any]) -> list[SourceFile]:
    required_columns = rules["required_columns"]
    sources: list[SourceFile] = []
    for file in files:
        encoding, columns = read_header(file, rules["encoding_candidates"])
        missing_columns = [
            column for column in required_columns if column not in columns
        ]
        if missing_columns:
            source_name = path_for_report(file)
            raise ValueError(f"{source_name} is missing required columns: {missing_columns}")
        sources.append(SourceFile(path=file, encoding=encoding, columns=columns))
    return sources


def build_output_columns(sources: list[SourceFile]) -> list[str]:
    columns: list[str] = []
    for source in sources:
        for column in source.columns:
            if column not in columns:
                columns.append(column)
    return columns + [column for column in ADDED_COLUMNS if column not in columns]


def reject_reason(
    row: dict[str, str],
    date_format: str,
    allow_thousands_separator: bool,
) -> str | None:
    reasons = []
    try:
        parse_date(row.get("조사일", ""), date_format)
    except ValueError:
        reasons.append("invalid_survey_date")
    try:
        parse_integer(row.get("판매가격", ""), allow_thousands_separator)
    except ValueError:
        reasons.append("invalid_price")
    return ";".join(reasons) if reasons else None


def write_count_csv(
    path: Path,
    rows: list[dict[str, Any]],
    fieldnames: list[str],
) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def transform(
    sources: list[SourceFile],
    item_mapping: dict[str, dict[str, str]],
    rules: dict[str, Any],
    output_dir: Path,
    report_dir: Path,
) -> dict[str, Any]:
    date_format = rules["date_columns"]["조사일"]["format"]
    allow_thousands_separator = rules["numeric_columns"]["판매가격"].get(
        "allow_thousands_separator",
        False,
    )
    output_columns = build_output_columns(sources)
    rejected_columns = REJECT_METADATA_COLUMNS + output_columns
    conflicting_row_columns = CONFLICTING_ROW_METADATA_COLUMNS + output_columns

    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    processed_path = output_dir / PROCESSED_FILENAME
    rejected_path = report_dir / REJECTED_FILENAME
    unmapped_path = report_dir / UNMAPPED_FILENAME
    conflicting_rows_path = report_dir / CONFLICTING_ROWS_FILENAME
    summary_path = report_dir / SUMMARY_FILENAME

    input_row_count = 0
    processed_row_count = 0
    excluded_by_mapping_count = 0
    rejected_row_count = 0
    identical_duplicate_key_count = 0
    identical_duplicate_row_count = 0
    conflict_key_count = 0
    conflict_row_count = 0
    unmapped_product_counts: Counter[str] = Counter()
    canonical_item_counts: Counter[str] = Counter()
    duplicate_key_counts: Counter[tuple[str, ...]] = Counter()
    rows_by_grain: dict[tuple[str, ...], list[TransformRow]] = {}

    with processed_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as processed_file, rejected_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as rejected_file, conflicting_rows_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as conflicting_rows_file:
        processed_writer = csv.DictWriter(
            processed_file,
            fieldnames=output_columns,
            extrasaction="ignore",
        )
        rejected_writer = csv.DictWriter(
            rejected_file,
            fieldnames=rejected_columns,
            extrasaction="ignore",
        )
        conflicting_rows_writer = csv.DictWriter(
            conflicting_rows_file,
            fieldnames=conflicting_row_columns,
            extrasaction="ignore",
        )
        processed_writer.writeheader()
        rejected_writer.writeheader()
        conflicting_rows_writer.writeheader()

        for source in sources:
            with source.path.open("r", encoding=source.encoding, newline="") as input_file:
                reader = csv.DictReader(input_file)
                for source_row_number, raw_row in enumerate(reader, start=2):
                    input_row_count += 1
                    original_survey_date = raw_row.get("조사일", "")
                    original_price = raw_row.get("판매가격", "")
                    row = strip_string_values(raw_row)
                    reason = reject_reason(row, date_format, allow_thousands_separator)
                    if reason:
                        rejected_row_count += 1
                        rejected_writer.writerow(
                            {
                                **{column: row.get(column, "") for column in output_columns},
                                "source_file": path_for_report(source.path),
                                "source_row_number": source_row_number,
                                "reject_reason": reason,
                                "original_조사일": original_survey_date,
                                "original_판매가격": original_price,
                            }
                        )
                        continue

                    row["조사일"] = parse_date(row["조사일"], date_format)
                    price = parse_integer(row["판매가격"], allow_thousands_separator)
                    row["판매가격"] = str(price)

                    product = row.get("상품명", "")
                    mapping_row = item_mapping.get(product)
                    if mapping_row is None:
                        unmapped_product_counts[product] += 1
                        continue
                    if mapping_row["mapping_include"] == "X":
                        excluded_by_mapping_count += 1
                        continue

                    row["canonical_item"] = mapping_row["canonical_item"]
                    row["subtype"] = mapping_row["subtype"]
                    row["spec"] = mapping_row["spec"]
                    row["unit_price"] = unit_price_text(price, row["spec"])

                    grain_key = make_key(row, DUPLICATE_GRAIN_COLUMNS)
                    duplicate_key_counts[grain_key] += 1
                    rows_by_grain.setdefault(grain_key, []).append(
                        TransformRow(
                            row=row,
                            source_file=path_for_report(source.path),
                            source_row_number=source_row_number,
                            original_survey_date=original_survey_date,
                            original_price=original_price,
                            grain_key=grain_key,
                            row_signature=make_row_signature(row, output_columns),
                        )
                    )

        for grain_rows in rows_by_grain.values():
            row_signatures = {row.row_signature for row in grain_rows}
            if len(row_signatures) > 1:
                conflict_key_count += 1
                conflict_row_count += len(grain_rows)
                for grain_row in grain_rows:
                    conflicting_rows_writer.writerow(
                        {
                            **{
                                column: grain_row.row.get(column, "")
                                for column in output_columns
                            },
                            "source_file": grain_row.source_file,
                            "source_row_number": grain_row.source_row_number,
                            "conflict_reason": "duplicate_grain_conflict",
                            "original_조사일": grain_row.original_survey_date,
                            "original_판매가격": grain_row.original_price,
                        }
                    )
                continue

            selected_row = grain_rows[0]
            processed_writer.writerow(
                {column: selected_row.row.get(column, "") for column in output_columns}
            )
            processed_row_count += 1
            canonical_item_counts[selected_row.row["canonical_item"]] += 1

            if len(grain_rows) > 1:
                identical_duplicate_key_count += 1
                identical_duplicate_row_count += len(grain_rows) - 1
                for duplicate_row in grain_rows[1:]:
                    rejected_row_count += 1
                    rejected_writer.writerow(
                        {
                            **{
                                column: duplicate_row.row.get(column, "")
                                for column in output_columns
                            },
                            "source_file": duplicate_row.source_file,
                            "source_row_number": duplicate_row.source_row_number,
                            "reject_reason": "duplicate_identical",
                            "original_조사일": duplicate_row.original_survey_date,
                            "original_판매가격": duplicate_row.original_price,
                        }
                    )

    unmapped_rows = [
        {"상품명": product, "row_count": count}
        for product, count in sorted(
            unmapped_product_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]
    write_count_csv(unmapped_path, unmapped_rows, ["상품명", "row_count"])

    duplicate_keys = {
        "|".join(key): count
        for key, count in duplicate_key_counts.items()
        if count > 1
    }
    summary = {
        "input_row_count": input_row_count,
        "processed_row_count": processed_row_count,
        "excluded_mapping_include_x_row_count": excluded_by_mapping_count,
        "unmapped_row_count": sum(unmapped_product_counts.values()),
        "rejected_row_count": rejected_row_count,
        "canonical_item_row_counts": {
            item: canonical_item_counts.get(item, 0)
            for item in sorted(CANONICAL_ITEMS)
        },
        "duplicate_check": {
            "key_columns": DUPLICATE_GRAIN_COLUMNS,
            "duplicate_key_count": len(duplicate_keys),
            "duplicate_row_count": sum(count - 1 for count in duplicate_keys.values()),
            "identical_duplicate_key_count": identical_duplicate_key_count,
            "identical_duplicate_removed_row_count": identical_duplicate_row_count,
            "conflict_key_count": conflict_key_count,
            "conflict_row_count": conflict_row_count,
            "examples": dict(list(duplicate_keys.items())[:10]),
        },
        "source_files": [
            {
                "file": path_for_report(source.path),
                "encoding": source.encoding,
                "columns": source.columns,
            }
            for source in sources
        ],
        "outputs": {
            "processed": path_for_report(processed_path),
            "unmapped_products": path_for_report(unmapped_path),
            "rejected_rows": path_for_report(rejected_path),
            "conflicting_rows": path_for_report(conflicting_rows_path),
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
        description="Transform KCA raw price CSV files using the confirmed item mapping."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=[str(DEFAULT_RAW_DIR)],
        help="CSV files or directories to transform. Defaults to data/raw.",
    )
    parser.add_argument(
        "--rules",
        default=str(DEFAULT_RULES_PATH),
        help="Path to profiling rules JSON.",
    )
    parser.add_argument(
        "--mapping",
        default=str(DEFAULT_MAPPING_PATH),
        help="Path to item mapping CSV.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for the processed CSV.",
    )
    parser.add_argument(
        "--report-dir",
        default=str(DEFAULT_REPORT_DIR),
        help="Directory for transform report outputs.",
    )
    args = parser.parse_args()

    rules = load_rules(resolve_path(args.rules))
    files = collect_csv_files(args.paths)
    if not files:
        raise FileNotFoundError("No CSV input files found.")

    sources = inspect_sources(files, rules)
    item_mapping = load_item_mapping(resolve_path(args.mapping))
    summary = transform(
        sources,
        item_mapping,
        rules,
        resolve_path(args.output_dir),
        resolve_path(args.report_dir),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
