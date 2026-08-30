from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

from profiling.common import normalized_text, profile_dataframe


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULES_PATH = ROOT / "config" / "profiling_rules_fis.json"
DEFAULT_INPUT_PATH = ROOT / "data" / "raw" / "fis"
DEFAULT_OUTPUT_PATH = ROOT / "reports" / "profiling" / "profiling_summary_fis.json"
CLOSE_PRICE_COLUMN_RE = re.compile(r"^종가\(")


def load_rules(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def path_for_report(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def resolve_input_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else ROOT / path


def read_header(path: Path, encodings: list[str]) -> tuple[str, list[str]]:
    last_error: UnicodeDecodeError | None = None
    for encoding in encodings:
        try:
            with path.open("r", encoding=encoding, newline="") as f:
                reader = csv.reader(f)
                header = next(reader, [])
            return encoding, header
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
    files_by_path: dict[Path, Path] = {}
    for path_text in paths:
        for file in collect_csv_files_from_path(resolve_input_path(path_text)):
            files_by_path[file.resolve()] = file
    return sorted(files_by_path.values())


def close_price_column(columns: list[str]) -> str | None:
    matches = [column for column in columns if CLOSE_PRICE_COLUMN_RE.match(column)]
    return matches[0] if len(matches) == 1 else None


def read_fis_dataframe(path: Path, encoding: str) -> tuple[pd.DataFrame, str | None]:
    df = pd.read_csv(path, encoding=encoding, dtype=str, keep_default_na=False)
    price_column = close_price_column(list(df.columns))
    df["close_price"] = df[price_column] if price_column else ""
    return df, price_column


def normalize_decimal_series(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        normalized_text(series).str.replace(",", "", regex=False),
        errors="coerce",
    )


def add_close_price_column_profile(
    result: dict[str, Any],
    price_columns_by_file: dict[str, str | None],
) -> None:
    missing = sorted(
        file for file, column in price_columns_by_file.items() if column is None
    )
    result["checks"]["close_price_column_profile"] = {
        "mode": "profile_only",
        "passed": not missing,
        "columns_by_file": price_columns_by_file,
        "missing_close_price_column_files": missing,
    }


def add_product_metadata_profile(
    result: dict[str, Any],
    df: pd.DataFrame,
    rules: dict[str, Any],
) -> None:
    expected_products = rules.get("expected_products", {})
    profiles = {}
    for product_key, expected in expected_products.items():
        product_df = df[normalized_text(df["product_key"]) == product_key]
        actual = {
            column: sorted(normalized_text(product_df[column]).unique().tolist())
            for column in ["fis_item", "cmdt_id", "cmdt_se_cd", "unit"]
            if column in product_df.columns
        }
        mismatches = {
            column: {
                "expected": expected[column],
                "actual": values,
            }
            for column, values in actual.items()
            if values != [expected[column]]
        }
        profiles[product_key] = {
            "row_count": int(len(product_df)),
            "expected": expected,
            "actual": actual,
            "passed": not mismatches,
            "mismatches": mismatches,
        }

    unexpected_products = sorted(
        set(normalized_text(df["product_key"]).unique()) - set(expected_products)
    )
    result["checks"]["product_metadata_profile"] = {
        "mode": "profile_only",
        "passed": not unexpected_products
        and all(profile["passed"] for profile in profiles.values()),
        "unexpected_products": unexpected_products,
        "profile": profiles,
    }


def add_trade_date_range_profile(
    result: dict[str, Any],
    df: pd.DataFrame,
    rules: dict[str, Any],
) -> None:
    parsed_dates = pd.to_datetime(
        normalized_text(df["거래일자"]),
        format="%Y-%m-%d",
        errors="coerce",
    )
    valid_dates = parsed_dates.dropna()
    min_date = valid_dates.min().date().isoformat() if not valid_dates.empty else None
    max_date = valid_dates.max().date().isoformat() if not valid_dates.empty else None
    expected_begin = rules.get("expected_begin_date")
    expected_end = rules.get("expected_end_date")

    by_product = {}
    frame = pd.DataFrame(
        {
            "product_key": normalized_text(df["product_key"]),
            "trade_date": parsed_dates,
        }
    ).dropna(subset=["trade_date"])
    for product_key, product_frame in frame.groupby("product_key", sort=True):
        dates = product_frame["trade_date"].drop_duplicates().sort_values()
        by_product[product_key] = {
            "min": dates.min().date().isoformat(),
            "max": dates.max().date().isoformat(),
            "distinct_trade_date_count": int(len(dates)),
            "weekday_trade_date_count": int(dates.dt.weekday.lt(5).sum()),
        }

    result["checks"]["trade_date_range_profile"] = {
        "mode": "profile_only",
        "expected_begin_date": expected_begin,
        "expected_end_date": expected_end,
        "min": min_date,
        "max": max_date,
        "covers_expected_range": min_date == expected_begin and max_date == expected_end,
        "by_product": by_product,
    }


def add_price_consistency_profile(result: dict[str, Any], df: pd.DataFrame) -> None:
    close_price = normalize_decimal_series(df["close_price"])
    converted_price = normalize_decimal_series(df["환산가($/ton)"])
    product_keys = normalized_text(df["product_key"])

    profiles = {}
    for product_key in sorted(product_keys.unique()):
        mask = product_keys == product_key
        product_close = close_price[mask]
        product_converted = converted_price[mask]
        profiles[product_key] = {
            "row_count": int(mask.sum()),
            "close_price_parse_fail_count": int(product_close.isna().sum()),
            "converted_price_parse_fail_count": int(product_converted.isna().sum()),
            "close_price_min": (
                float(product_close.min()) if not product_close.dropna().empty else None
            ),
            "close_price_max": (
                float(product_close.max()) if not product_close.dropna().empty else None
            ),
            "converted_price_min": (
                float(product_converted.min())
                if not product_converted.dropna().empty
                else None
            ),
            "converted_price_max": (
                float(product_converted.max())
                if not product_converted.dropna().empty
                else None
            ),
        }

    result["checks"]["price_consistency_profile"] = {
        "mode": "profile_only",
        "profile": profiles,
    }


def profile_fis_files(paths: list[Path], rules: dict[str, Any]) -> dict[str, Any]:
    dataframes = []
    sources = []
    missing_columns_by_file = {}
    unexpected_columns_by_file = {}
    price_columns_by_file: dict[str, str | None] = {}

    for path in paths:
        encoding, columns = read_header(path, rules["encoding_candidates"])
        file_name = path_for_report(path)
        df, price_column = read_fis_dataframe(path, encoding)
        profile_columns = list(df.columns)
        expected_columns = [*rules["required_columns"], "close_price"]
        missing_columns = [
            column for column in expected_columns if column not in profile_columns
        ]
        unexpected_columns = [
            column
            for column in profile_columns
            if column not in expected_columns and not CLOSE_PRICE_COLUMN_RE.match(column)
        ]
        if missing_columns:
            missing_columns_by_file[file_name] = missing_columns
        if unexpected_columns:
            unexpected_columns_by_file[file_name] = unexpected_columns

        sources.append(
            {
                "file": file_name,
                "encoding": encoding,
                "columns": columns,
                "close_price_column": price_column,
                "row_count": int(len(df)),
            }
        )
        price_columns_by_file[file_name] = price_column
        dataframes.append(df)

    df = pd.concat(dataframes, ignore_index=True) if dataframes else pd.DataFrame()
    columns = list(df.columns)
    result = profile_dataframe(
        df,
        rules,
        columns,
        missing_columns_by_file,
        unexpected_columns_by_file,
    )
    result["profile_name"] = "fis_raw_profile"
    result["source_file_count"] = len(sources)
    result["source_files"] = sources
    add_close_price_column_profile(result, price_columns_by_file)
    if not df.empty:
        add_product_metadata_profile(result, df, rules)
        add_trade_date_range_profile(result, df, rules)
        add_price_consistency_profile(result, df)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Profile raw FIS commodity CSV files."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=[str(DEFAULT_INPUT_PATH)],
        help="FIS CSV files or directories to profile. Defaults to data/raw/fis.",
    )
    parser.add_argument(
        "--rules",
        default=str(DEFAULT_RULES_PATH),
        help="Path to FIS profiling rules JSON.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Path to write the JSON report.",
    )
    args = parser.parse_args()

    rules = load_rules(resolve_input_path(args.rules))
    files = collect_csv_files(args.paths)
    if not files:
        raise FileNotFoundError("No FIS CSV input files found.")

    report = {
        "rules": path_for_report(resolve_input_path(args.rules)),
        "stage": "fis_raw_profile",
        "purpose": "Validate raw FIS commodity CSV files before transform/load.",
        "file_count": len(files),
        "profile": profile_fis_files(files, rules),
    }
    output = json.dumps(report, ensure_ascii=False, indent=2)
    output_path = resolve_input_path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output + "\n", encoding="utf-8")
    print(f"Wrote FIS profiling report: {path_for_report(output_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
