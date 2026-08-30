from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULES_PATH = ROOT / "config" / "profiling_rules_fis.json"
DEFAULT_RAW_DIR = ROOT / "data" / "raw" / "fis"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "processed" / "fis"
DEFAULT_REPORT_DIR = ROOT / "reports" / "transform"
ITEM_FILENAME = "fis_item.csv"
PRICE_OBSERVATION_FILENAME = "fis_price_observation.csv"
REJECTED_FILENAME = "fis_rejected_rows.csv"
CONFLICTING_ROWS_FILENAME = "fis_conflicting_rows.csv"
SUMMARY_FILENAME = "fis_transform_summary.json"
CLOSE_PRICE_COLUMN_RE = re.compile(r"^종가\(")
SIGNED_TEXT_RE = re.compile(
    r"^(?:(?P<direction>상승|하락|보합)\s*)?(?P<number>[+-]?\d+(?:\.\d+)?)%?$"
)

ITEM_COLUMNS = [
    "item_key",
    "canonical_item",
    "cmdt_id",
    "cmdt_se_cd",
    "item_name",
    "price_unit",
    "converted_unit",
    "relation_type",
]
PRICE_OBSERVATION_COLUMNS = [
    "item_key",
    "contract_month",
    "trade_date",
    "close_price",
    "change_amount",
    "change_rate_pct",
    "converted_price",
]
OBSERVATION_GRAIN_COLUMNS = ["item_key", "contract_month", "trade_date"]
REJECT_METADATA_COLUMNS = [
    "source_file",
    "source_row_number",
    "reject_reason",
]
CONFLICTING_ROW_METADATA_COLUMNS = [
    "source_file",
    "source_row_number",
    "conflict_reason",
]


@dataclass(frozen=True)
class SourceFile:
    path: Path
    encoding: str
    columns: list[str]
    close_price_column: str


@dataclass(frozen=True)
class TransformRow:
    row: dict[str, str]
    source_file: str
    source_row_number: int
    grain_key: tuple[str, ...]
    row_signature: tuple[str, ...]


def load_rules(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else ROOT / path


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


def close_price_column(columns: list[str]) -> str | None:
    matches = [column for column in columns if CLOSE_PRICE_COLUMN_RE.match(column)]
    return matches[0] if len(matches) == 1 else None


def inspect_sources(files: list[Path], rules: dict[str, Any]) -> list[SourceFile]:
    required_columns = rules["required_columns"]
    sources: list[SourceFile] = []
    for file in files:
        encoding, columns = read_header(file, rules["encoding_candidates"])
        missing_columns = [
            column for column in required_columns if column not in columns
        ]
        price_column = close_price_column(columns)
        if price_column is None:
            missing_columns.append("종가(...)")
        if missing_columns:
            source_name = path_for_report(file)
            raise ValueError(f"{source_name} is missing required columns: {missing_columns}")
        sources.append(
            SourceFile(
                path=file,
                encoding=encoding,
                columns=columns,
                close_price_column=price_column,
            )
        )
    return sources


def parse_date(value: str, date_format: str) -> str:
    return datetime.strptime(value.strip(), date_format).date().isoformat()


def parse_contract_month(value: str) -> str:
    normalized = value.strip().replace("/", ".").replace("-", ".")
    parsed = datetime.strptime(normalized, "%Y.%m")
    return parsed.strftime("%Y-%m")


def parse_decimal(value: str) -> Decimal:
    normalized = value.strip().replace(",", "")
    if not normalized:
        raise ValueError("empty decimal")
    try:
        return Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError(f"invalid decimal: {value}") from exc


def decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def parse_signed_korean_value(value: str) -> str:
    normalized = value.strip().replace(",", "")
    if not normalized:
        return ""
    match = SIGNED_TEXT_RE.match(normalized)
    if not match:
        raise ValueError(f"invalid signed value: {value}")

    number = parse_decimal(match.group("number"))
    direction = match.group("direction")
    if direction == "하락":
        number = -number
    elif direction == "보합" or direction is None and number == 0:
        number = Decimal("0")
    return decimal_text(number)


def make_key(row: dict[str, str], columns: list[str]) -> tuple[str, ...]:
    return tuple((row.get(column) or "").strip() for column in columns)


def make_row_signature(row: dict[str, str], columns: list[str]) -> tuple[str, ...]:
    return tuple((row.get(column) or "").strip() for column in columns)


def expected_product(row: dict[str, str], rules: dict[str, Any]) -> dict[str, str] | None:
    return rules.get("expected_products", {}).get(row.get("product_key", ""))


def metadata_reject_reasons(row: dict[str, str], rules: dict[str, Any]) -> list[str]:
    product_key = row.get("product_key", "")
    expected = expected_product(row, rules)
    if expected is None:
        return ["unexpected_product_key"]

    checks = {
        "fis_item": "fis_item",
        "cmdt_id": "cmdt_id",
        "cmdt_se_cd": "cmdt_se_cd",
        "unit": "unit",
    }
    return [
        f"metadata_mismatch_{column}"
        for column, expected_column in checks.items()
        if row.get(column, "") != expected.get(expected_column, "")
    ]


def transform_row(
    row: dict[str, str],
    *,
    close_column: str,
    rules: dict[str, Any],
) -> tuple[dict[str, str] | None, list[str]]:
    reasons = metadata_reject_reasons(row, rules)
    try:
        trade_date = parse_date(row.get("거래일자", ""), rules["date_columns"]["거래일자"]["format"])
    except ValueError:
        reasons.append("invalid_trade_date")
        trade_date = ""

    try:
        contract_month = parse_contract_month(row.get("인도월", ""))
    except ValueError:
        reasons.append("invalid_contract_month")
        contract_month = ""

    try:
        close_price = decimal_text(parse_decimal(row.get(close_column, "")))
    except ValueError:
        reasons.append("invalid_close_price")
        close_price = ""

    try:
        converted_price = decimal_text(parse_decimal(row.get("환산가($/ton)", "")))
    except ValueError:
        reasons.append("invalid_converted_price")
        converted_price = ""

    try:
        change_amount = parse_signed_korean_value(row.get("전일대비", ""))
    except ValueError:
        reasons.append("invalid_change_amount")
        change_amount = ""

    try:
        change_rate_pct = parse_signed_korean_value(row.get("등락률(%)", ""))
    except ValueError:
        reasons.append("invalid_change_rate_pct")
        change_rate_pct = ""

    if reasons:
        return None, reasons

    return {
        "item_key": row["product_key"],
        "contract_month": contract_month,
        "trade_date": trade_date,
        "close_price": close_price,
        "change_amount": change_amount,
        "change_rate_pct": change_rate_pct,
        "converted_price": converted_price,
    }, []


def item_row_from_source(row: dict[str, str], rules: dict[str, Any]) -> dict[str, str]:
    expected = expected_product(row, rules)
    converted_unit = "USD/ton" if row.get("환산가($/ton)", "") else ""
    return {
        "item_key": row["product_key"],
        "canonical_item": expected.get("canonical_item", "") if expected else "",
        "cmdt_id": row["cmdt_id"],
        "cmdt_se_cd": row["cmdt_se_cd"],
        "item_name": expected["fis_item"] if expected else row["fis_item"],
        "price_unit": row["unit"],
        "converted_unit": converted_unit,
        "relation_type": expected.get("relation_type", "") if expected else "",
    }


def write_rows(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def transform(
    sources: list[SourceFile],
    rules: dict[str, Any],
    output_dir: Path,
    report_dir: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    item_path = output_dir / ITEM_FILENAME
    observation_path = output_dir / PRICE_OBSERVATION_FILENAME
    rejected_path = report_dir / REJECTED_FILENAME
    conflicting_rows_path = report_dir / CONFLICTING_ROWS_FILENAME
    summary_path = report_dir / SUMMARY_FILENAME

    input_row_count = 0
    rejected_rows: list[dict[str, str]] = []
    conflicting_rows: list[dict[str, str]] = []
    item_rows_by_key: dict[str, dict[str, str]] = {}
    rows_by_grain: dict[tuple[str, ...], list[TransformRow]] = {}
    product_key_counts: Counter[str] = Counter()
    duplicate_key_counts: Counter[tuple[str, ...]] = Counter()

    for source in sources:
        with source.path.open("r", encoding=source.encoding, newline="") as input_file:
            reader = csv.DictReader(input_file)
            for source_row_number, raw_row in enumerate(reader, start=2):
                input_row_count += 1
                row = strip_string_values(raw_row)
                transformed, reasons = transform_row(
                    row,
                    close_column=source.close_price_column,
                    rules=rules,
                )
                if reasons or transformed is None:
                    rejected_rows.append(
                        {
                            "source_file": path_for_report(source.path),
                            "source_row_number": str(source_row_number),
                            "reject_reason": ";".join(reasons),
                            **row,
                        }
                    )
                    continue

                item_row = item_row_from_source(row, rules)
                item_rows_by_key[item_row["item_key"]] = item_row
                grain_key = make_key(transformed, OBSERVATION_GRAIN_COLUMNS)
                duplicate_key_counts[grain_key] += 1
                rows_by_grain.setdefault(grain_key, []).append(
                    TransformRow(
                        row=transformed,
                        source_file=path_for_report(source.path),
                        source_row_number=source_row_number,
                        grain_key=grain_key,
                        row_signature=make_row_signature(
                            transformed,
                            PRICE_OBSERVATION_COLUMNS,
                        ),
                    )
                )

    observation_rows: list[dict[str, str]] = []
    identical_duplicate_key_count = 0
    identical_duplicate_row_count = 0
    conflict_key_count = 0
    conflict_row_count = 0
    for grain_rows in rows_by_grain.values():
        row_signatures = {row.row_signature for row in grain_rows}
        if len(row_signatures) > 1:
            conflict_key_count += 1
            conflict_row_count += len(grain_rows)
            for grain_row in grain_rows:
                conflicting_rows.append(
                    {
                        "source_file": grain_row.source_file,
                        "source_row_number": str(grain_row.source_row_number),
                        "conflict_reason": "duplicate_grain_conflict",
                        **grain_row.row,
                    }
                )
            continue

        selected_row = grain_rows[0].row
        observation_rows.append(selected_row)
        product_key_counts[selected_row["item_key"]] += 1
        if len(grain_rows) > 1:
            identical_duplicate_key_count += 1
            identical_duplicate_row_count += len(grain_rows) - 1
            for duplicate_row in grain_rows[1:]:
                rejected_rows.append(
                    {
                        "source_file": duplicate_row.source_file,
                        "source_row_number": str(duplicate_row.source_row_number),
                        "reject_reason": "duplicate_identical",
                        **duplicate_row.row,
                    }
                )

    item_rows = sorted(item_rows_by_key.values(), key=lambda row: row["item_key"])
    observation_rows = sorted(
        observation_rows,
        key=lambda row: (row["item_key"], row["trade_date"]),
    )
    rejected_columns = sorted(
        {column for row in rejected_rows for column in row},
        key=lambda column: (
            REJECT_METADATA_COLUMNS.index(column)
            if column in REJECT_METADATA_COLUMNS
            else len(REJECT_METADATA_COLUMNS),
            column,
        ),
    )
    conflicting_columns = CONFLICTING_ROW_METADATA_COLUMNS + PRICE_OBSERVATION_COLUMNS

    write_rows(item_path, ITEM_COLUMNS, item_rows)
    write_rows(observation_path, PRICE_OBSERVATION_COLUMNS, observation_rows)
    write_rows(rejected_path, rejected_columns or REJECT_METADATA_COLUMNS, rejected_rows)
    write_rows(conflicting_rows_path, conflicting_columns, conflicting_rows)

    duplicate_keys = {
        "|".join(key): count
        for key, count in duplicate_key_counts.items()
        if count > 1
    }
    summary = {
        "input_file_count": len(sources),
        "input_row_count": input_row_count,
        "item_row_count": len(item_rows),
        "processed_row_count": len(observation_rows),
        "rejected_row_count": len(rejected_rows),
        "conflicting_row_count": len(conflicting_rows),
        "product_key_row_counts": dict(sorted(product_key_counts.items())),
        "duplicate_check": {
            "grain_columns": OBSERVATION_GRAIN_COLUMNS,
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
                "close_price_column": source.close_price_column,
            }
            for source in sources
        ],
        "outputs": {
            "fis_item": path_for_report(item_path),
            "fis_price_observation": path_for_report(observation_path),
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
        description="Transform raw FIS commodity CSV files into ERD-shaped CSV files."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=[str(DEFAULT_RAW_DIR)],
        help="FIS CSV files or directories to transform. Defaults to data/raw/fis.",
    )
    parser.add_argument(
        "--rules",
        default=str(DEFAULT_RULES_PATH),
        help="Path to FIS profiling rules JSON.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for processed FIS CSV files.",
    )
    parser.add_argument(
        "--report-dir",
        default=str(DEFAULT_REPORT_DIR),
        help="Directory for FIS transform report outputs.",
    )
    args = parser.parse_args()

    rules = load_rules(resolve_path(args.rules))
    files = collect_csv_files(args.paths)
    if not files:
        raise FileNotFoundError("No FIS CSV input files found.")

    sources = inspect_sources(files, rules)
    summary = transform(
        sources=sources,
        rules=rules,
        output_dir=resolve_path(args.output_dir),
        report_dir=resolve_path(args.report_dir),
    )
    print(f"Wrote FIS transform summary: {summary['outputs']['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
