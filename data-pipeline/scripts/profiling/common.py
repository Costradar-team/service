from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd


def normalized_text(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


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


def is_null(value: str, null_values: set[str]) -> bool:
    return value.strip() in null_values


def numeric_values(
    series: pd.Series,
    allow_thousands_separator: bool,
) -> pd.Series:
    values = normalized_text(series)
    if allow_thousands_separator:
        values = values.str.replace(",", "", regex=False)
    return pd.to_numeric(values, errors="coerce")


def profile_dataframe(
    df: pd.DataFrame,
    rules: dict[str, Any],
    *,
    columns: list[str] | None = None,
    missing_columns_by_file: dict[str, list[str]] | None = None,
    unexpected_columns_by_file: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    columns = columns or list(df.columns)
    row_count = len(df)
    null_values = set(rules.get("null_values", [""]))
    missing_columns_by_file = missing_columns_by_file or {}
    unexpected_columns_by_file = unexpected_columns_by_file or {}

    null_profile = {}
    for column in columns:
        values = normalized_text(df[column])
        null_count = int(values.isin(null_values).sum())
        null_profile[column] = {
            "null_count": null_count,
            "null_rate": round(null_count / row_count, 6) if row_count else 0,
        }

    date_profiles = {}
    for column, date_rule in rules.get("date_columns", {}).items():
        if column not in df.columns:
            continue

        profile = empty_date_profile()
        date_format = date_rule["format"]
        for value in normalized_text(df[column]):
            if is_null(value, null_values):
                continue

            profile["non_null_count"] += 1
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
        date_profiles[column] = profile

    numeric_profiles = {}
    for column, numeric_rule in rules.get("numeric_columns", {}).items():
        if column not in df.columns:
            continue

        profile = empty_numeric_profile()
        values = normalized_text(df[column])
        parsed_values = numeric_values(
            values,
            numeric_rule.get("allow_thousands_separator", False),
        )
        for value, parsed_number in zip(values, parsed_values):
            if is_null(value, null_values):
                profile["null_count"] += 1
                continue

            profile["non_null_count"] += 1
            if pd.isna(parsed_number):
                profile["parse_fail_count"] += 1
                if len(profile["invalid_examples"]) < 5:
                    profile["invalid_examples"].append(value)
                continue

            parsed_int = int(parsed_number)
            profile["parsed_count"] += 1
            if parsed_int == 0:
                profile["zero_count"] += 1
            elif parsed_int < 0:
                profile["negative_count"] += 1
            else:
                profile["positive_count"] += 1

            if profile["min"] is None or parsed_int < profile["min"]:
                profile["min"] = parsed_int
            if profile["max"] is None or parsed_int > profile["max"]:
                profile["max"] = parsed_int

        non_null_count = profile["non_null_count"]
        profile["parse_rate"] = (
            round(profile["parsed_count"] / non_null_count, 6)
            if non_null_count
            else 0
        )
        numeric_profiles[column] = profile

    duplicate_profiles = {}
    for key_rule in rules.get("unique_keys", []):
        key_columns = key_rule["columns"]
        missing_columns = [column for column in key_columns if column not in df.columns]
        if missing_columns:
            duplicate_profiles[key_rule["name"]] = {
                "columns": key_columns,
                "skipped": True,
                "reason": "One or more key columns are missing.",
                "missing_columns": missing_columns,
            }
            continue

        key_frame = pd.DataFrame(
            {
                column: normalized_text(df[column])
                for column in key_columns
            }
        )
        key_counts = key_frame.value_counts()
        duplicate_items = key_counts[key_counts > 1]
        duplicate_profiles[key_rule["name"]] = {
            "columns": key_columns,
            "skipped": False,
            "duplicate_key_count": int(len(duplicate_items)),
            "duplicate_row_count": int((duplicate_items - 1).sum()),
            "duplicate_examples": [
                {
                    "key": dict(zip(key_columns, key if isinstance(key, tuple) else (key,))),
                    "count": int(count),
                }
                for key, count in duplicate_items.head(5).items()
            ],
        }

    return {
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
        },
    }
