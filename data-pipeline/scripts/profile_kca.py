from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any


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


def is_null(value: str | None, null_values: set[str]) -> bool:
    if value is None:
        return True
    return value.strip() in null_values


def empty_date_profile() -> dict[str, Any]:
    return {
        "non_null_count": 0,
        "parsed_count": 0,
        "unparsed_count": 0,
        "parse_rate": 0,
        "min": None,
        "max": None,
        "invalid_examples": [],
    }


def empty_numeric_profile() -> dict[str, Any]:
    return {
        "null_count": 0,
        "non_null_count": 0,
        "parsed_count": 0,
        "parse_fail_count": 0,
        "zero_count": 0,
        "negative_count": 0,
        "positive_count": 0,
        "parse_rate": 0,
        "min": None,
        "max": None,
        "invalid_examples": [],
    }


def parse_integer(value: str, allow_thousands_separator: bool) -> int:
    normalized = value.strip()
    if allow_thousands_separator:
        normalized = normalized.replace(",", "")
    return int(normalized)


def make_key(row: dict[str, str], columns: list[str]) -> tuple[str, ...]:
    return tuple((row.get(column) or "").strip() for column in columns)


def empty_item_accumulator() -> dict[str, Any]:
    return {
        "row_count": 0,
        "skus": set(),
        "stores": set(),
    }


def path_for_report(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def profile_csv_files(
    paths: list[Path],
    rules: dict[str, Any],
    profile_name: str | None = None,
) -> dict[str, Any]:
    required_columns = rules["required_columns"]
    null_values = set(rules["null_values"])
    date_columns = rules.get("date_columns", {})
    numeric_columns = rules.get("numeric_columns", {})
    unique_keys = rules.get("unique_keys", [])
    item_profiles = rules.get("item_profiles", [])
    keyword_candidate_profiles = rules.get("keyword_candidate_profiles", [])
    sources = []
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

    row_count = 0
    null_counts = dict.fromkeys(columns, 0)
    date_profiles = {
        column: empty_date_profile()
        for column in date_columns
        if column in columns
    }
    numeric_profiles = {
        column: empty_numeric_profile()
        for column in numeric_columns
        if column in columns
    }
    unique_key_counts: dict[str, dict[tuple[str, ...], int]] = {
        key_rule["name"]: {}
        for key_rule in unique_keys
        if all(column in columns for column in key_rule["columns"])
    }
    item_profile_accumulators: dict[str, dict[str, dict[str, Any]]] = {
        item_rule["name"]: {}
        for item_rule in item_profiles
        if all(
            item_rule[column_name] in columns
            for column_name in ("item_column", "sku_column", "store_column")
        )
    }
    keyword_candidate_accumulators: dict[str, dict[str, dict[str, Any]]] = {
        keyword_rule["name"]: {}
        for keyword_rule in keyword_candidate_profiles
        if keyword_rule["column"] in columns
    }

    for source in sources:
        with source["path"].open("r", encoding=source["encoding"], newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                row_count += 1
                for column in columns:
                    if is_null(row.get(column), null_values):
                        null_counts[column] += 1
                for column, date_rule in date_columns.items():
                    if column not in date_profiles:
                        continue
                    value = row.get(column)
                    if is_null(value, null_values):
                        continue

                    profile = date_profiles[column]
                    date_format = date_rule["format"]
                    profile["non_null_count"] += 1
                    try:
                        parsed_date = datetime.strptime(
                            value.strip(),
                            date_format,
                        ).date()
                    except ValueError:
                        profile["unparsed_count"] += 1
                        if len(profile["invalid_examples"]) < 5:
                            profile["invalid_examples"].append(value)
                        continue

                    parsed_date_text = parsed_date.isoformat()
                    profile["parsed_count"] += 1
                    if profile["min"] is None or parsed_date_text < profile["min"]:
                        profile["min"] = parsed_date_text
                    if profile["max"] is None or parsed_date_text > profile["max"]:
                        profile["max"] = parsed_date_text
                for column, numeric_rule in numeric_columns.items():
                    if column not in numeric_profiles:
                        continue

                    value = row.get(column)
                    profile = numeric_profiles[column]
                    if is_null(value, null_values):
                        profile["null_count"] += 1
                        continue

                    profile["non_null_count"] += 1
                    try:
                        parsed_number = parse_integer(
                            value,
                            numeric_rule.get("allow_thousands_separator", False),
                        )
                    except ValueError:
                        profile["parse_fail_count"] += 1
                        if len(profile["invalid_examples"]) < 5:
                            profile["invalid_examples"].append(value)
                        continue

                    profile["parsed_count"] += 1
                    if parsed_number == 0:
                        profile["zero_count"] += 1
                    elif parsed_number < 0:
                        profile["negative_count"] += 1
                    else:
                        profile["positive_count"] += 1

                    if profile["min"] is None or parsed_number < profile["min"]:
                        profile["min"] = parsed_number
                    if profile["max"] is None or parsed_number > profile["max"]:
                        profile["max"] = parsed_number
                for key_rule in unique_keys:
                    key_name = key_rule["name"]
                    if key_name not in unique_key_counts:
                        continue
                    key = make_key(row, key_rule["columns"])
                    unique_key_counts[key_name][key] = (
                        unique_key_counts[key_name].get(key, 0) + 1
                    )
                for item_rule in item_profiles:
                    item_profile_name = item_rule["name"]
                    if item_profile_name not in item_profile_accumulators:
                        continue

                    item_value = (row.get(item_rule["item_column"]) or "").strip()
                    sku_value = (row.get(item_rule["sku_column"]) or "").strip()
                    store_value = (row.get(item_rule["store_column"]) or "").strip()
                    accumulator = item_profile_accumulators[
                        item_profile_name
                    ].setdefault(
                        item_value,
                        empty_item_accumulator(),
                    )
                    accumulator["row_count"] += 1
                    if sku_value:
                        accumulator["skus"].add(sku_value)
                    if store_value:
                        accumulator["stores"].add(store_value)
                for keyword_rule in keyword_candidate_profiles:
                    keyword_profile_name = keyword_rule["name"]
                    if keyword_profile_name not in keyword_candidate_accumulators:
                        continue

                    value = (row.get(keyword_rule["column"]) or "").strip()
                    matched_keywords = [
                        keyword
                        for keyword in keyword_rule["keywords"]
                        if keyword in value
                    ]
                    if not matched_keywords:
                        continue

                    candidates = keyword_candidate_accumulators[keyword_profile_name]
                    candidate = candidates.setdefault(
                        value,
                        {
                            "row_count": 0,
                            "keywords": set(),
                        },
                    )
                    candidate["row_count"] += 1
                    candidate["keywords"].update(matched_keywords)

    null_profile = {
        column: {
            "null_count": null_count,
            "null_rate": round(null_count / row_count, 6) if row_count else 0,
        }
        for column, null_count in null_counts.items()
    }
    for profile in date_profiles.values():
        non_null_count = profile["non_null_count"]
        profile["parse_rate"] = (
            round(profile["parsed_count"] / non_null_count, 6)
            if non_null_count
            else 0
        )
    for profile in numeric_profiles.values():
        non_null_count = profile["non_null_count"]
        profile["parse_rate"] = (
            round(profile["parsed_count"] / non_null_count, 6)
            if non_null_count
            else 0
        )
    duplicate_profiles = {}
    for key_rule in unique_keys:
        key_name = key_rule["name"]
        key_counts = unique_key_counts.get(key_name)
        if key_counts is None:
            duplicate_profiles[key_name] = {
                "columns": key_rule["columns"],
                "skipped": True,
                "reason": "One or more key columns are missing.",
            }
            continue

        duplicate_items = [
            (key, count)
            for key, count in key_counts.items()
            if count > 1
        ]
        duplicate_profiles[key_name] = {
            "columns": key_rule["columns"],
            "skipped": False,
            "duplicate_key_count": len(duplicate_items),
            "duplicate_row_count": sum(count - 1 for _, count in duplicate_items),
            "duplicate_examples": [
                {
                    "key": dict(zip(key_rule["columns"], key)),
                    "count": count,
                }
                for key, count in duplicate_items[:5]
            ],
        }
    item_coverage_profiles = {}
    for item_rule in item_profiles:
        profile_name = item_rule["name"]
        accumulators = item_profile_accumulators.get(profile_name)
        if accumulators is None:
            item_coverage_profiles[profile_name] = {
                "item_column": item_rule["item_column"],
                "sku_column": item_rule["sku_column"],
                "store_column": item_rule["store_column"],
                "skipped": True,
                "reason": "One or more item profile columns are missing.",
            }
            continue

        items = {
            item: {
                "row_count": accumulator["row_count"],
                "sku_count": len(accumulator["skus"]),
                "store_count": len(accumulator["stores"]),
            }
            for item, accumulator in sorted(accumulators.items())
        }
        item_coverage_profiles[profile_name] = {
            "item_column": item_rule["item_column"],
            "sku_column": item_rule["sku_column"],
            "store_column": item_rule["store_column"],
            "skipped": False,
            "item_count": len(items),
            "items": items,
        }
    keyword_candidate_output = {}
    for keyword_rule in keyword_candidate_profiles:
        profile_name = keyword_rule["name"]
        candidates = keyword_candidate_accumulators.get(profile_name)
        if candidates is None:
            keyword_candidate_output[profile_name] = {
                "column": keyword_rule["column"],
                "keywords": keyword_rule["keywords"],
                "skipped": True,
                "reason": "Keyword candidate column is missing.",
            }
            continue

        candidate_items = {
            product_name: {
                "row_count": candidate["row_count"],
                "keywords": sorted(candidate["keywords"]),
            }
            for product_name, candidate in sorted(candidates.items())
        }
        keyword_candidate_output[profile_name] = {
            "column": keyword_rule["column"],
            "keywords": keyword_rule["keywords"],
            "skipped": False,
            "candidate_count": len(candidate_items),
            "candidates": candidate_items,
        }

    result = {
        "row_count": row_count,
        "column_count": len(columns),
        "columns": columns,
        "checks": {
            "required_columns_present": {
                "passed": not missing_columns_by_file,
                "missing_columns_by_file": missing_columns_by_file,
                "unexpected_columns_by_file": unexpected_columns_by_file,
            },
            "column_null_profile": {
                "mode": "profile_only",
                "profile": null_profile,
            },
            "date_parse_profile": {
                "mode": "profile_only",
                "profile": date_profiles,
            },
            "numeric_value_profile": {
                "mode": "profile_only",
                "profile": numeric_profiles,
            },
            "duplicate_key_profile": {
                "mode": "profile_only",
                "profile": duplicate_profiles,
            },
            "item_coverage_profile": {
                "mode": "profile_only",
                "profile": item_coverage_profiles,
            },
            "keyword_candidate_profile": {
                "mode": "profile_only",
                "profile": keyword_candidate_output,
            }
        },
    }
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
    if path.is_absolute():
        return path

    cwd_candidate = Path.cwd() / path
    root_candidate = ROOT / path
    if cwd_candidate.exists():
        return cwd_candidate
    if root_candidate.exists():
        return root_candidate
    return cwd_candidate


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
