from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from collect_kamis import PRODUCTS
from profiling.common import normalized_text, profile_dataframe


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULES_PATH = ROOT / "config" / "profiling_rules_kamis.json"
DEFAULT_INPUT_PATH = ROOT / "data" / "raw" / "kamis"
DEFAULT_OUTPUT_PATH = ROOT / "reports" / "profiling" / "profiling_summary_kamis.json"
RAW_FILENAME_RE = re.compile(
    r"^kamis_(?P<product>.+)_(?P<start>\d{4}-\d{2}-\d{2})_"
    r"(?P<end>\d{4}-\d{2}-\d{2})\.json$"
)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_rules(path: Path) -> dict[str, Any]:
    return load_json(path)


def path_for_report(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def resolve_input_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = ROOT / path
    return path


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
        for file in collect_json_files_from_path(resolve_input_path(path_text)):
            files_by_resolved_path[file.resolve()] = file
    return sorted(files_by_resolved_path.values())


def extract_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    item = payload.get("data", {}).get("item")
    if item is None:
        item = (
            payload.get("response", {})
            .get("body", {})
            .get("items", {})
            .get("item", [])
        )
    if isinstance(item, dict):
        return [item]
    if isinstance(item, list):
        return item
    raise ValueError(
        "KAMIS JSON item payload must be an object or a list at data.item "
        "or response.body.items.item."
    )


def filename_metadata(path: Path) -> dict[str, str] | None:
    match = RAW_FILENAME_RE.match(path.name)
    if not match:
        return None
    return match.groupdict()


def parse_survey_dates(df: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(
        normalized_text(df["yyyy"]) + "/" + normalized_text(df["regday"]),
        format="%Y/%m/%d",
        errors="coerce",
    )


def add_response_validation_profile(
    result: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    data = payload.get("data")
    item = data.get("item") if isinstance(data, dict) else None
    error_code = data.get("error_code") if isinstance(data, dict) else None
    response_header = payload.get("response", {}).get("header", {})
    if error_code is None:
        error_code = response_header.get("resultCode")

    result["checks"]["kamis_response_validation"] = {
        "status": "PASS" if error_code == "000" and item is not None else "FAIL",
        "error_code": error_code,
        "data_item_exists": item is not None,
        "data_item_type": type(item).__name__ if item is not None else None,
    }


def add_request_condition_profile(
    result: dict[str, Any],
    payload: dict[str, Any],
    path: Path,
) -> None:
    metadata = filename_metadata(path)
    condition = payload.get("condition", {}).get("item", {})
    if metadata is None:
        result["checks"]["kamis_request_condition_profile"] = {
            "status": "REVIEW",
            "skipped": True,
            "reason": "Cannot infer expected request condition from filename.",
        }
        return

    product_key = metadata["product"]
    product = PRODUCTS.get(product_key)
    expected = {
        "p_startday": metadata["start"],
        "p_endday": metadata["end"],
    }
    if product is not None:
        expected.update(
            {
                "p_itemcode": product["item_code"],
                "p_kindcode": product["kind_code"],
                "p_productrankcode": product["product_rank_code"],
            }
        )

    comparisons = {
        key: {
            "expected": expected_value,
            "actual": None if condition.get(key) is None else str(condition.get(key)),
            "matched": (
                None if condition.get(key) is None else str(condition.get(key))
            ) == expected_value,
        }
        for key, expected_value in expected.items()
    }
    hard_check_keys = ["p_startday", "p_endday", "p_itemcode", "p_kindcode"]
    hard_checks_passed = all(
        comparisons[key]["matched"]
        for key in hard_check_keys
        if key in comparisons
    )
    all_checks_passed = all(row["matched"] for row in comparisons.values())
    status = "PASS" if all_checks_passed else "REVIEW" if hard_checks_passed else "FAIL"
    result["checks"]["kamis_request_condition_profile"] = {
        "status": status,
        "product": product_key,
        "skipped": False,
        "review_note": (
            "KAMIS response may normalize p_productrankcode in condition echo."
            if status == "REVIEW"
            else None
        ),
        "comparisons": comparisons,
    }


def read_kamis_dataframe(path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    payload = load_json(path)
    items = extract_items(payload)
    return pd.DataFrame(items, dtype=str).fillna(""), payload


def required_column_profile(
    file_name: str,
    columns: list[str],
    required_columns: list[str],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    missing_columns = [column for column in required_columns if column not in columns]
    unexpected_columns = [column for column in columns if column not in required_columns]
    missing_columns_by_file = {file_name: missing_columns} if missing_columns else {}
    unexpected_columns_by_file = {file_name: unexpected_columns} if unexpected_columns else {}
    return missing_columns_by_file, unexpected_columns_by_file


def add_value_distribution_profiles(
    result: dict[str, Any],
    df: pd.DataFrame,
    rules: dict[str, Any],
) -> None:
    output = {}
    for rule in rules.get("value_distribution_profiles", []):
        profile_name = rule["name"]
        columns = rule["columns"]
        top_n = rule.get("top_n", 20)
        column_profiles = {}

        for column in columns:
            if column not in df.columns:
                column_profiles[column] = {
                    "skipped": True,
                    "reason": "Column is missing.",
                }
                continue

            values = normalized_text(df[column])
            non_null_values = values[values != ""]
            top_values = [
                {
                    "value": str(value),
                    "count": int(count),
                    "rate": round(int(count) / len(df), 6) if len(df) else 0,
                }
                for value, count in non_null_values.value_counts().head(top_n).items()
            ]
            column_profiles[column] = {
                "skipped": False,
                "null_count": int((values == "").sum()),
                "distinct_count": int(non_null_values.nunique()),
                "top_values": top_values,
            }

        output[profile_name] = {
            "columns": columns,
            "top_n": top_n,
            "profile": column_profiles,
        }

    result["checks"]["kamis_value_distribution_profile"] = {
        "mode": "profile_only",
        "profile": output,
    }


def add_combination_profiles(
    result: dict[str, Any],
    df: pd.DataFrame,
    rules: dict[str, Any],
) -> None:
    output = {}
    for rule in rules.get("combination_profiles", []):
        profile_name = rule["name"]
        columns = rule["columns"]
        top_n = rule.get("top_n", 20)
        missing_columns = [column for column in columns if column not in df.columns]

        if missing_columns:
            output[profile_name] = {
                "columns": columns,
                "skipped": True,
                "reason": "One or more combination columns are missing.",
                "missing_columns": missing_columns,
            }
            continue

        combination_frame = pd.DataFrame(
            {
                column: normalized_text(df[column])
                for column in columns
            }
        )
        counts = combination_frame.value_counts().head(top_n)
        combinations = [
            {
                "values": dict(zip(columns, tuple(key))),
                "count": int(count),
                "rate": round(int(count) / len(df), 6) if len(df) else 0,
            }
            for key, count in counts.items()
        ]
        output[profile_name] = {
            "columns": columns,
            "skipped": False,
            "distinct_combination_count": int(len(combination_frame.drop_duplicates())),
            "top_n": top_n,
            "top_combinations": combinations,
        }

    result["checks"]["kamis_combination_profile"] = {
        "mode": "profile_only",
        "profile": output,
    }


def numeric_series(series: pd.Series, allow_thousands_separator: bool = True) -> pd.Series:
    values = normalized_text(series)
    if allow_thousands_separator:
        values = values.str.replace(",", "", regex=False)
    return pd.to_numeric(values, errors="coerce")


def add_combined_date_profiles(
    result: dict[str, Any],
    df: pd.DataFrame,
    rules: dict[str, Any],
) -> None:
    output = {}
    for rule in rules.get("combined_date_columns", []):
        profile_name = rule["name"]
        year_column = rule["year_column"]
        month_day_column = rule["month_day_column"]
        date_format = rule["format"]
        missing_columns = [
            column
            for column in (year_column, month_day_column)
            if column not in df.columns
        ]

        if missing_columns:
            output[profile_name] = {
                "year_column": year_column,
                "month_day_column": month_day_column,
                "skipped": True,
                "reason": "One or more combined date columns are missing.",
                "missing_columns": missing_columns,
            }
            continue

        profile = {
            "year_column": year_column,
            "month_day_column": month_day_column,
            "format": date_format,
            "skipped": False,
            "non_null_count": 0,
            "parsed_count": 0,
            "unparsed_count": 0,
            "parse_rate": 0,
            "min": None,
            "max": None,
            "invalid_examples": [],
        }
        for year, month_day in zip(
            normalized_text(df[year_column]),
            normalized_text(df[month_day_column]),
        ):
            if not year or not month_day:
                continue

            profile["non_null_count"] += 1
            value = f"{year}/{month_day}"
            try:
                parsed_date = datetime.strptime(value, date_format).date()
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

        non_null_count = profile["non_null_count"]
        profile["parse_rate"] = (
            round(profile["parsed_count"] / non_null_count, 6)
            if non_null_count
            else 0
        )
        output[profile_name] = profile

    result["checks"]["kamis_combined_date_profile"] = {
        "mode": "profile_only",
        "profile": output,
    }


def requested_range(path: Path) -> tuple[datetime.date, datetime.date] | None:
    metadata = filename_metadata(path)
    if metadata is None:
        return None
    return (
        datetime.strptime(metadata["start"], "%Y-%m-%d").date(),
        datetime.strptime(metadata["end"], "%Y-%m-%d").date(),
    )


def add_requested_range_profile(result: dict[str, Any], df: pd.DataFrame, path: Path) -> None:
    date_range = requested_range(path)
    if date_range is None:
        result["checks"]["kamis_requested_range_profile"] = {
            "mode": "profile_only",
            "skipped": True,
            "reason": "Cannot infer requested date range from filename.",
        }
        return

    requested_start, requested_end = date_range
    requested_start_text = requested_start.isoformat()
    requested_end_text = requested_end.isoformat()
    required_columns = {"yyyy", "regday"}
    missing_columns = sorted(required_columns - set(df.columns))
    if missing_columns:
        result["checks"]["kamis_requested_range_profile"] = {
            "mode": "profile_only",
            "skipped": True,
            "requested_start_date": requested_start_text,
            "requested_end_date": requested_end_text,
            "reason": "One or more date columns are missing.",
            "missing_columns": missing_columns,
        }
        return

    before_count = 0
    after_count = 0
    invalid_count = 0
    examples = []
    for row_number, (year, month_day) in enumerate(
        zip(normalized_text(df["yyyy"]), normalized_text(df["regday"])),
        start=1,
    ):
        value = f"{year}/{month_day}"
        try:
            observed_date = datetime.strptime(value, "%Y/%m/%d").date()
        except ValueError:
            invalid_count += 1
            if len(examples) < 5:
                examples.append({"row_number": row_number, "date": value})
            continue

        outside_direction = None
        if observed_date < requested_start:
            before_count += 1
            outside_direction = "before"
        elif observed_date > requested_end:
            after_count += 1
            outside_direction = "after"

        if outside_direction and len(examples) < 5:
            examples.append(
                {
                    "row_number": row_number,
                    "date": observed_date.isoformat(),
                    "direction": outside_direction,
                }
            )

    result["checks"]["kamis_requested_range_profile"] = {
        "mode": "profile_only",
        "skipped": False,
        "requested_start_date": requested_start_text,
        "requested_end_date": requested_end_text,
        "before_requested_start_count": before_count,
        "after_requested_end_count": after_count,
        "outside_requested_range_count": before_count + after_count,
        "invalid_combined_date_count": invalid_count,
        "outside_or_invalid_examples": examples,
    }


def add_series_profiles(result: dict[str, Any], df: pd.DataFrame, path: Path) -> None:
    required_columns = {"countyname", "yyyy", "regday", "price"}
    missing_columns = sorted(required_columns - set(df.columns))
    if missing_columns:
        result["checks"]["kamis_series_profile"] = {
            "mode": "profile_only",
            "skipped": True,
            "missing_columns": missing_columns,
        }
        return

    frame = df.copy()
    frame["_survey_date"] = parse_survey_dates(frame)
    frame["_price"] = numeric_series(frame["price"])
    date_range = requested_range(path)
    series_output = {}

    for series_name, group in frame.groupby("countyname", dropna=False, sort=True):
        series_key = "" if pd.isna(series_name) else str(series_name)
        parsed_dates = group["_survey_date"].dropna().dt.date
        in_requested_group = group
        missing_dates = []
        coverage_rate = None
        expected_day_count = None
        if date_range is not None and series_key in {"평균", "전국"}:
            requested_start, requested_end = date_range
            expected_dates = set(
                pd.date_range(requested_start, requested_end, freq="D").date
            )
            observed_dates = set(
                group.loc[
                    (group["_survey_date"].dt.date >= requested_start)
                    & (group["_survey_date"].dt.date <= requested_end),
                    "_survey_date",
                ].dt.date
            )
            missing_dates = sorted(expected_dates - observed_dates)
            expected_day_count = len(expected_dates)
            coverage_rate = (
                round((expected_day_count - len(missing_dates)) / expected_day_count, 6)
                if expected_day_count
                else 0
            )
            in_requested_group = group[
                (group["_survey_date"].dt.date >= requested_start)
                & (group["_survey_date"].dt.date <= requested_end)
            ]

        outside_requested_count = None
        if date_range is not None:
            requested_start, requested_end = date_range
            outside_requested_count = int(
                (
                    (group["_survey_date"].dt.date < requested_start)
                    | (group["_survey_date"].dt.date > requested_end)
                ).sum()
            )

        parsed_prices = in_requested_group["_price"].dropna()
        series_output[series_key] = {
            "row_count": int(len(group)),
            "date_min": parsed_dates.min().isoformat() if not parsed_dates.empty else None,
            "date_max": parsed_dates.max().isoformat() if not parsed_dates.empty else None,
            "outside_requested_range_count": outside_requested_count,
            "coverage": (
                {
                    "status": "PASS" if not missing_dates else "FAIL",
                    "requested_day_count": expected_day_count,
                    "observed_day_count": (
                        expected_day_count - len(missing_dates)
                        if expected_day_count is not None
                        else None
                    ),
                    "missing_day_count": len(missing_dates),
                    "coverage_rate": coverage_rate,
                    "missing_dates": [date.isoformat() for date in missing_dates[:20]],
                }
                if series_key in {"평균", "전국"}
                else None
            ),
            "price_min": int(parsed_prices.min()) if not parsed_prices.empty else None,
            "price_max": int(parsed_prices.max()) if not parsed_prices.empty else None,
        }

    result["checks"]["kamis_series_profile"] = {
        "mode": "profile_only",
        "skipped": False,
        "series": series_output,
        "pyeongnyeon_outside_requested_range": {
            "status": "INFO",
            "message": "평년 series may include dates outside the requested API range.",
            "outside_requested_range_count": series_output.get("평년", {}).get(
                "outside_requested_range_count"
            ),
        },
    }


def add_county_date_duplicate_profile(result: dict[str, Any], df: pd.DataFrame) -> None:
    required_columns = {"countyname", "yyyy", "regday"}
    missing_columns = sorted(required_columns - set(df.columns))
    if missing_columns:
        result["checks"]["kamis_county_date_duplicate_profile"] = {
            "mode": "profile_only",
            "skipped": True,
            "missing_columns": missing_columns,
        }
        return

    key_frame = pd.DataFrame(
        {
            "countyname": normalized_text(df["countyname"]),
            "survey_date": parse_survey_dates(df).dt.date.astype(str),
        }
    )
    key_counts = key_frame.value_counts()
    duplicate_items = key_counts[key_counts > 1]
    result["checks"]["kamis_county_date_duplicate_profile"] = {
        "mode": "profile_only",
        "status": "PASS" if duplicate_items.empty else "FAIL",
        "columns": ["countyname", "survey_date"],
        "duplicate_key_count": int(len(duplicate_items)),
        "duplicate_row_count": int((duplicate_items - 1).sum()),
        "duplicate_examples": [
            {
                "key": {"countyname": key[0], "survey_date": key[1]},
                "count": int(count),
            }
            for key, count in duplicate_items.head(10).items()
        ],
    }


def add_allowed_null_profile(result: dict[str, Any], df: pd.DataFrame) -> None:
    allowed_null_columns = ["itemname", "kindname", "marketname"]
    output = {}
    for column in allowed_null_columns:
        if column not in df.columns:
            output[column] = {"skipped": True, "reason": "Column is missing."}
            continue
        null_count = int(normalized_text(df[column]).eq("").sum())
        output[column] = {
            "status": "INFO",
            "null_count": null_count,
            "null_rate": round(null_count / len(df), 6) if len(df) else 0,
            "message": "Null is allowed by KAMIS response structure and is not treated as FAIL.",
        }

    result["checks"]["kamis_allowed_null_profile"] = {
        "mode": "profile_only",
        "profile": output,
    }


def add_average_national_comparison_profile(result: dict[str, Any], df: pd.DataFrame) -> None:
    required_columns = {"countyname", "yyyy", "regday", "price"}
    missing_columns = sorted(required_columns - set(df.columns))
    if missing_columns:
        result["checks"]["kamis_average_national_comparison"] = {
            "status": "REVIEW",
            "skipped": True,
            "missing_columns": missing_columns,
        }
        return

    frame = df.copy()
    frame["_survey_date"] = parse_survey_dates(frame).dt.date.astype(str)
    frame["_price"] = normalized_text(frame["price"]).str.replace(",", "", regex=False)
    pivot = frame.pivot_table(
        index="_survey_date",
        columns="countyname",
        values="_price",
        aggfunc="first",
    )

    if "평균" not in pivot.columns or "전국" not in pivot.columns:
        result["checks"]["kamis_average_national_comparison"] = {
            "status": "REVIEW",
            "skipped": True,
            "reason": "Either 평균 or 전국 series is missing.",
        }
        return

    comparable = pivot[["평균", "전국"]].dropna()
    differences = comparable[comparable["평균"] != comparable["전국"]]
    result["checks"]["kamis_average_national_comparison"] = {
        "status": "REVIEW",
        "skipped": False,
        "comparable_day_count": int(len(comparable)),
        "is_completely_same": bool(differences.empty),
        "different_day_count": int(len(differences)),
        "different_examples": [
            {
                "survey_date": str(index),
                "average_price": row["평균"],
                "national_price": row["전국"],
            }
            for index, row in differences.head(10).iterrows()
        ],
    }


def add_price_stat_combination_profiles(
    result: dict[str, Any],
    df: pd.DataFrame,
    rules: dict[str, Any],
) -> None:
    output = {}
    for rule in rules.get("price_stat_combination_profiles", []):
        profile_name = rule["name"]
        columns = rule["columns"]
        date_column = rule["date_column"]
        price_column = rule["price_column"]
        required_columns = columns + [date_column, price_column]
        missing_columns = [column for column in required_columns if column not in df.columns]

        if missing_columns:
            output[profile_name] = {
                "columns": columns,
                "date_column": date_column,
                "price_column": price_column,
                "skipped": True,
                "reason": "One or more required columns are missing.",
                "missing_columns": missing_columns,
            }
            continue

        profile_frame = pd.DataFrame(
            {
                column: normalized_text(df[column])
                for column in columns
            }
        )
        profile_frame[date_column] = normalized_text(df[date_column])
        profile_frame[price_column] = numeric_series(df[price_column])
        grouped = profile_frame.groupby(columns, dropna=False, sort=True)

        combinations = []
        for key, group in grouped:
            key_values = key if isinstance(key, tuple) else (key,)
            parsed_prices = group[price_column].dropna()
            combinations.append(
                {
                    "values": dict(zip(columns, key_values)),
                    "row_count": int(len(group)),
                    "distinct_survey_date_count": int(group[date_column].nunique()),
                    "price_parse_fail_count": int(group[price_column].isna().sum()),
                    "price_min": (
                        int(parsed_prices.min())
                        if not parsed_prices.empty
                        else None
                    ),
                    "price_max": (
                        int(parsed_prices.max())
                        if not parsed_prices.empty
                        else None
                    ),
                    "price_mean": (
                        round(float(parsed_prices.mean()), 2)
                        if not parsed_prices.empty
                        else None
                    ),
                }
            )

        output[profile_name] = {
            "columns": columns,
            "date_column": date_column,
            "price_column": price_column,
            "skipped": False,
            "combination_count": len(combinations),
            "combinations": combinations,
        }

    result["checks"]["kamis_price_stat_combination_profile"] = {
        "mode": "profile_only",
        "profile": output,
    }


def profile_kamis_file(path: Path, rules: dict[str, Any]) -> dict[str, Any]:
    df, payload = read_kamis_dataframe(path)
    columns = list(df.columns)
    file_name = path_for_report(path)
    missing_columns_by_file, unexpected_columns_by_file = required_column_profile(
        file_name,
        columns,
        rules["required_columns"],
    )
    result = profile_dataframe(
        df,
        rules,
        columns=columns,
        missing_columns_by_file=missing_columns_by_file,
        unexpected_columns_by_file=unexpected_columns_by_file,
    )
    add_value_distribution_profiles(result, df, rules)
    add_combination_profiles(result, df, rules)
    add_combined_date_profiles(result, df, rules)
    add_requested_range_profile(result, df, path)
    add_series_profiles(result, df, path)
    add_county_date_duplicate_profile(result, df)
    add_allowed_null_profile(result, df)
    add_average_national_comparison_profile(result, df)
    add_price_stat_combination_profiles(result, df, rules)
    add_response_validation_profile(result, payload)
    add_request_condition_profile(result, payload, path)
    result["file"] = file_name
    response_body = payload.get("response", {}).get("body", {})
    response_header = payload.get("response", {}).get("header", {})
    data = payload.get("data", {})
    result["data_type"] = response_body.get("dataType")
    result["api_total_count"] = response_body.get("totalCount")
    result["api_result_code"] = response_header.get("resultCode") or data.get("error_code")
    result["api_result_msg"] = response_header.get("resultMsg") or data.get("error_msg")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Profile raw KAMIS JSON files using common DataFrame quality rules."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=[str(DEFAULT_INPUT_PATH)],
        help="KAMIS JSON files or directories to profile. Defaults to data/raw/kamis.",
    )
    parser.add_argument(
        "--rules",
        default=str(DEFAULT_RULES_PATH),
        help="Path to KAMIS profiling rules JSON.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Path to write the KAMIS JSON profiling report.",
    )
    args = parser.parse_args()

    rules_path = Path(args.rules)
    if not rules_path.is_absolute():
        rules_path = ROOT / rules_path
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = ROOT / output_path

    rules = load_rules(rules_path)
    files = collect_json_files(args.paths)
    if not files:
        raise FileNotFoundError("No KAMIS JSON input files found.")
    profiles = [profile_kamis_file(path, rules) for path in files]
    report = {
        "rules": str(rules_path.relative_to(ROOT)),
        "dataset": rules.get("dataset"),
        "stage": "pre_analysis",
        "source": "kamis",
        "workflow": [
            "json_to_dataframe",
            "common_quality_profile",
            "kamis_specific_profile",
        ],
        "file_count": len(files),
        "profiles": profiles,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
