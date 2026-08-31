from __future__ import annotations

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


def is_null(value: Any, null_values: set[str]) -> bool:
    if value is None or pd.isna(value):
        return True
    return str(value).strip() in null_values


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
    date_columns = rules.get("date_columns", {})
    numeric_columns = rules.get("numeric_columns", {})
    unique_keys = rules.get("unique_keys", [])
    missing_columns_by_file = missing_columns_by_file or {}
    unexpected_columns_by_file = unexpected_columns_by_file or {}

    null_profile = {}
    for column in columns:
        if column not in df.columns:
            null_count = row_count
        else:
            null_count = int(df[column].map(lambda value: is_null(value, null_values)).sum())
        null_profile[column] = {
            "null_count": null_count,
            "null_rate": round(null_count / row_count, 6) if row_count else 0,
        }

    date_profiles = {
        column: empty_date_profile()
        for column in date_columns
        if column in columns
    }
    for column, date_rule in date_columns.items():
        if column not in date_profiles or column not in df.columns:
            continue

        profile = date_profiles[column]
        values = normalized_text(df[column])
        non_null_values = values[~values.isin(null_values)]
        parsed_dates = pd.to_datetime(
            non_null_values,
            format=date_rule["format"],
            errors="coerce",
        )
        valid_dates = parsed_dates.dropna()
        invalid_values = non_null_values[parsed_dates.isna()]

        profile["non_null_count"] = int(len(non_null_values))
        profile["parsed_count"] = int(len(valid_dates))
        profile["unparsed_count"] = int(len(invalid_values))
        profile["invalid_examples"] = invalid_values.head(5).tolist()
        if not valid_dates.empty:
            profile["min"] = valid_dates.min().date().isoformat()
            profile["max"] = valid_dates.max().date().isoformat()
        profile["parse_rate"] = (
            round(profile["parsed_count"] / profile["non_null_count"], 6)
            if profile["non_null_count"]
            else 0
        )

    numeric_profiles = {
        column: empty_numeric_profile()
        for column in numeric_columns
        if column in columns
    }
    for column, numeric_rule in numeric_columns.items():
        if column not in numeric_profiles or column not in df.columns:
            continue

        profile = numeric_profiles[column]
        values = normalized_text(df[column])
        null_mask = values.isin(null_values)
        non_null_values = values[~null_mask]
        normalized_numbers = non_null_values
        if numeric_rule.get("allow_thousands_separator", False):
            normalized_numbers = normalized_numbers.str.replace(",", "", regex=False)

        valid_mask = normalized_numbers.str.fullmatch(r"[+-]?\d+")
        parsed_numbers = normalized_numbers[valid_mask].astype("int64")
        invalid_values = non_null_values[~valid_mask]

        profile["null_count"] = int(null_mask.sum())
        profile["non_null_count"] = int(len(non_null_values))
        profile["parsed_count"] = int(len(parsed_numbers))
        profile["parse_fail_count"] = int(len(invalid_values))
        profile["zero_count"] = int((parsed_numbers == 0).sum())
        profile["negative_count"] = int((parsed_numbers < 0).sum())
        profile["positive_count"] = int((parsed_numbers > 0).sum())
        profile["invalid_examples"] = invalid_values.head(5).tolist()
        if not parsed_numbers.empty:
            profile["min"] = int(parsed_numbers.min())
            profile["max"] = int(parsed_numbers.max())
        profile["parse_rate"] = (
            round(profile["parsed_count"] / profile["non_null_count"], 6)
            if profile["non_null_count"]
            else 0
        )

    duplicate_profiles = {}
    for key_rule in unique_keys:
        key_name = key_rule["name"]
        key_columns = key_rule["columns"]
        missing_columns = [
            column
            for column in key_columns
            if column not in columns or column not in df.columns
        ]
        if missing_columns:
            duplicate_profiles[key_name] = {
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
        key_counts = key_frame.value_counts(sort=False)
        duplicate_items = [
            (tuple(key) if isinstance(key, tuple) else (key,), int(count))
            for key, count in key_counts.items()
            if int(count) > 1
        ]
        duplicate_profiles[key_name] = {
            "columns": key_columns,
            "skipped": False,
            "duplicate_key_count": len(duplicate_items),
            "duplicate_row_count": sum(count - 1 for _, count in duplicate_items),
            "duplicate_examples": [
                {
                    "key": dict(zip(key_columns, key)),
                    "count": count,
                }
                for key, count in duplicate_items[:5]
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
