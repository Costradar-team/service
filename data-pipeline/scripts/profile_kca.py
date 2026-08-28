from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import pandas as pd

from profiling.common import normalized_text, profile_dataframe


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULES_PATH = ROOT / "config" / "profiling_rules.json" #검사 규칙 지정
DEFAULT_DATA_DIR = ROOT / "data" / "raw"


def load_rules(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


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


def path_for_report(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def read_csv_dataframe(path: Path, encoding: str, columns: list[str]) -> pd.DataFrame:
    df = pd.read_csv(
        path,
        encoding=encoding,
        dtype=str,
        keep_default_na=False,
    )
    return df.reindex(columns=columns, fill_value="")


def empty_item_accumulator() -> dict[str, Any]:
    return {
        "row_count": 0,
        "skus": set(),
        "stores": set(),
    }


def add_item_coverage_profile(
    result: dict[str, Any],
    df: pd.DataFrame,
    rules: dict[str, Any],
    columns: list[str],
) -> None:
    item_coverage_profiles = {}
    for item_rule in rules.get("item_profiles", []):
        profile_name = item_rule["name"]
        required_columns = [
            item_rule["item_column"],
            item_rule["sku_column"],
            item_rule["store_column"],
        ]
        if not all(column in columns and column in df.columns for column in required_columns):
            item_coverage_profiles[profile_name] = {
                "item_column": item_rule["item_column"],
                "sku_column": item_rule["sku_column"],
                "store_column": item_rule["store_column"],
                "skipped": True,
                "reason": "One or more item profile columns are missing.",
            }
            continue

        item_series = normalized_text(df[item_rule["item_column"]])
        sku_series = normalized_text(df[item_rule["sku_column"]])
        store_series = normalized_text(df[item_rule["store_column"]])
        profile_frame = pd.DataFrame(
            {
                "item": item_series,
                "sku": sku_series,
                "store": store_series,
            }
        )
        row_counts = profile_frame.groupby("item", sort=True).size()
        sku_counts = (
            profile_frame[profile_frame["sku"] != ""]
            .groupby("item", sort=True)["sku"]
            .nunique()
        )
        store_counts = (
            profile_frame[profile_frame["store"] != ""]
            .groupby("item", sort=True)["store"]
            .nunique()
        )
        items = {
            str(item): {
                "row_count": int(row_count),
                "sku_count": int(sku_counts.get(item, 0)),
                "store_count": int(store_counts.get(item, 0)),
            }
            for item, row_count in row_counts.items()
        }
        item_coverage_profiles[profile_name] = {
            "item_column": item_rule["item_column"],
            "sku_column": item_rule["sku_column"],
            "store_column": item_rule["store_column"],
            "skipped": False,
            "item_count": len(items),
            "items": items,
        }

    result["checks"]["item_coverage_profile"] = {
        "mode": "profile_only",
        "profile": item_coverage_profiles,
    }


def add_keyword_candidate_profile(
    result: dict[str, Any],
    df: pd.DataFrame,
    rules: dict[str, Any],
    columns: list[str],
) -> None:
    keyword_candidate_output = {}
    for keyword_rule in rules.get("keyword_candidate_profiles", []):
        profile_name = keyword_rule["name"]
        column = keyword_rule["column"]
        if column not in columns or column not in df.columns:
            keyword_candidate_output[profile_name] = {
                "column": column,
                "keywords": keyword_rule["keywords"],
                "skipped": True,
                "reason": "Keyword candidate column is missing.",
            }
            continue

        values = normalized_text(df[column])
        matched_rows = []
        for product_name in values:
            matched_keywords = [
                keyword
                for keyword in keyword_rule["keywords"]
                if keyword in product_name
            ]
            if not matched_keywords:
                continue
            matched_rows.append((product_name, matched_keywords))

        candidates: dict[str, dict[str, Any]] = {}
        for product_name, matched_keywords in matched_rows:
            candidate = candidates.setdefault(product_name, {"row_count": 0, "keywords": set()})
            candidate["row_count"] += 1
            candidate["keywords"].update(matched_keywords)

        candidate_items = {
            product_name: {
                "row_count": candidate["row_count"],
                "keywords": sorted(candidate["keywords"]),
            }
            for product_name, candidate in sorted(candidates.items())
        }
        keyword_candidate_output[profile_name] = {
            "column": column,
            "keywords": keyword_rule["keywords"],
            "skipped": False,
            "candidate_count": len(candidate_items),
            "candidates": candidate_items,
        }

    result["checks"]["keyword_candidate_profile"] = {
        "mode": "profile_only",
        "profile": keyword_candidate_output,
    }


def profile_csv_files(
    paths: list[Path],
    rules: dict[str, Any],
    profile_name: str | None = None,
) -> dict[str, Any]:
    required_columns = rules["required_columns"]
    sources = []
    dataframes = []
    columns: list[str] = []
    missing_columns_by_file = {}
    unexpected_columns_by_file = {}

    for path in paths:
        encoding, file_columns = read_header(path, rules["encoding_candidates"])
        sources.append(
            {
                "path": path,
                "file": path_for_report(path),
                "encoding": encoding,
                "columns": file_columns,
            }
        )
        if not columns:
            columns = file_columns

        missing_columns = [
            column for column in required_columns if column not in file_columns
        ]
        unexpected_columns = [
            column for column in file_columns if column not in required_columns
        ]
        if missing_columns:
            missing_columns_by_file[path_for_report(path)] = missing_columns
        if unexpected_columns:
            unexpected_columns_by_file[path_for_report(path)] = unexpected_columns

        dataframes.append(read_csv_dataframe(path, encoding, columns))

    df = (
        pd.concat(dataframes, ignore_index=True)
        if dataframes
        else pd.DataFrame(columns=columns)
    )
    result = profile_dataframe(
        df,
        rules,
        columns=columns,
        missing_columns_by_file=missing_columns_by_file,
        unexpected_columns_by_file=unexpected_columns_by_file,
    )
    add_item_coverage_profile(result, df, rules, columns)
    add_keyword_candidate_profile(result, df, rules, columns)

    if len(sources) == 1:
        result["file"] = sources[0]["file"]
        result["encoding"] = sources[0]["encoding"]
        result["checks"]["required_columns_present"]["missing_columns"] = (
            missing_columns_by_file.get(sources[0]["file"], [])
        )
        result["checks"]["required_columns_present"]["unexpected_columns"] = (
            unexpected_columns_by_file.get(sources[0]["file"], [])
        )
    else:
        result["profile_name"] = profile_name or "merged_profile"
        result["source_file_count"] = len(sources)
        result["source_files"] = [source["file"] for source in sources]
        result["encodings"] = sorted({source["encoding"] for source in sources})
    return result


def profile_file(path: Path, rules: dict[str, Any]) -> dict[str, Any]:
    return profile_csv_files([path], rules)


def resolve_input_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = ROOT / path
    return path


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


def write_merged_csv(files: list[Path], rules: dict[str, Any], output_path: Path) -> dict[str, Any] | None:
    if not files:
        return None

    first_encoding, columns = read_header(files[0], rules["encoding_candidates"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    row_count = 0
    with output_path.open("w", encoding="utf-8-sig", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=columns)
        writer.writeheader()
        for file in files:
            encoding, _ = read_header(file, rules["encoding_candidates"])
            with file.open("r", encoding=encoding, newline="") as input_file:
                reader = csv.DictReader(input_file)
                for row in reader:
                    writer.writerow({column: row.get(column, "") for column in columns})
                    row_count += 1

    return {
        "file": path_for_report(output_path),
        "encoding": "utf-8-sig",
        "source_file_count": len(files),
        "row_count": row_count,
        "column_count": len(columns),
        "columns": columns,
        "source_first_file_encoding": first_encoding,
    }


def assess_file_quality(profile: dict[str, Any]) -> dict[str, Any]:
    failed_checks = []
    required_columns = profile["checks"]["required_columns_present"]
    if not required_columns["passed"]:
        failed_checks.append("required_columns_present")

    for date_profile in profile["checks"]["date_parse_profile"]["profile"].values():
        if date_profile["unparsed_count"] > 0:
            failed_checks.append("date_parse_profile")
            break

    for numeric_profile in profile["checks"]["numeric_value_profile"][
        "profile"
    ].values():
        if (
            numeric_profile["null_count"] > 0
            or numeric_profile["parse_fail_count"] > 0
            or numeric_profile["zero_count"] > 0
            or numeric_profile["negative_count"] > 0
        ):
            failed_checks.append("numeric_value_profile")
            break

    for duplicate_profile in profile["checks"]["duplicate_key_profile"][
        "profile"
    ].values():
        if duplicate_profile.get("skipped") or duplicate_profile.get(
            "duplicate_key_count",
            0,
        ) > 0:
            failed_checks.append("duplicate_key_profile")
            break

    return {
        "passed": not failed_checks,
        "failed_checks": failed_checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Profile raw CSV files using reusable data-quality rules."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=[str(DEFAULT_DATA_DIR)],
        help=(
            "CSV files or directories to profile. Accepts multiple inputs. "
            "Defaults to data/raw."
        ),
    )
    parser.add_argument(
        "--rules",
        default=str(DEFAULT_RULES_PATH),
        help="Path to profiling rules JSON.",
    )
    parser.add_argument(
        "--output",
        help="Optional path to write the JSON report.",
    )
    parser.add_argument(
        "--merged-csv-output",
        help="Optional path to write a merged CSV containing only normal files.",
    )
    args = parser.parse_args()

    rules_path = Path(args.rules)
    if not rules_path.is_absolute():
        rules_path = ROOT / rules_path

    rules = load_rules(rules_path)
    files = collect_csv_files(args.paths)
    profiles = [profile_file(file, rules) for file in files]
    for profile in profiles:
        profile["file_quality"] = assess_file_quality(profile)

    normal_files = [
        file
        for file, profile in zip(files, profiles)
        if profile["file_quality"]["passed"]
    ]
    excluded_files = [
        profile["file"]
        for profile in profiles
        if not profile["file_quality"]["passed"]
    ]
    merged_profile = (
        profile_csv_files(normal_files, rules, "normal_files_merged_profile")
        if normal_files
        else None
    )
    merged_csv = None
    if args.merged_csv_output:
        merged_output_path = Path(args.merged_csv_output)
        if not merged_output_path.is_absolute():
            merged_output_path = ROOT / merged_output_path
        merged_csv = write_merged_csv(normal_files, rules, merged_output_path)
    report = {
        "rules": str(rules_path.relative_to(ROOT)),
        "stage": "pre_analysis",
        "purpose": (
            "Analyze one year of raw CSV files before deciding ERD, grain, "
            "item mapping, and preprocessing rules."
        ),
        "non_goals": [
            "modify_source_data",
            "finalize_item_mapper",
            "load_mysql",
            "implement_airflow_dag",
        ],
        "workflow": [
            "file_quality_profiles",
            "merge_result",
            "merged_profile",
        ],
        "file_count": len(profiles),
        "file_quality_profiles": profiles,
        "merge_result": {
            "normal_file_count": len(normal_files),
            "excluded_file_count": len(excluded_files),
            "normal_files": [
                profile["file"]
                for profile in profiles
                if profile["file_quality"]["passed"]
            ],
            "excluded_files": excluded_files,
            "merged_csv": merged_csv,
        },
        "merged_profile": merged_profile,
    }

    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = ROOT / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)

    return 1 if excluded_files else 0


if __name__ == "__main__":
    raise SystemExit(main())
