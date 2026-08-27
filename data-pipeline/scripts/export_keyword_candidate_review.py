from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUMMARY_PATH = ROOT / "reports" / "profiling" / "profiling_summary.json"
DEFAULT_OUTPUT_PATH = (
    ROOT / "reports" / "item_mapping" / "keyword_candidate_review.csv"
)


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = ROOT / path
    return path


def load_summary(path: Path) -> dict[str, Any]:
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


def parse_date(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def load_candidates(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    profile = (
        summary["merged_profile"]["checks"]["keyword_candidate_profile"]["profile"][
            "core_ingredient_product_candidates"
        ]
    )
    return profile["candidates"]


def collect_source_files(summary: dict[str, Any]) -> list[Path]:
    files = summary.get("merge_result", {}).get("normal_files", [])
    if not files and summary.get("merged_profile"):
        files = summary["merged_profile"].get("source_files", [])
    return [resolve_path(file) for file in files]


def build_review_rows(
    summary: dict[str, Any],
    encodings: list[str],
) -> list[dict[str, Any]]:
    candidates = load_candidates(summary)
    candidate_names = set(candidates)
    aggregates: dict[str, dict[str, Any]] = {
        product_name: {
            "상품명": product_name,
            "제조사": set(),
            "matching_keyword": set(candidates[product_name]["keywords"]),
            "row_count": 0,
            "판매업소": set(),
            "조사일": set(),
            "first_survey_date": None,
            "last_survey_date": None,
        }
        for product_name in candidate_names
    }

    for source_file in collect_source_files(summary):
        encoding, _ = read_header(source_file, encodings)
        with source_file.open("r", encoding=encoding, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                product_name = (row.get("상품명") or "").strip()
                if product_name not in candidate_names:
                    continue

                aggregate = aggregates[product_name]
                aggregate["row_count"] += 1

                manufacturer = (row.get("제조사") or "").strip()
                if manufacturer:
                    aggregate["제조사"].add(manufacturer)

                store = (row.get("판매업소") or "").strip()
                if store:
                    aggregate["판매업소"].add(store)

                survey_date = parse_date(row.get("조사일") or "")
                if survey_date is None:
                    continue

                aggregate["조사일"].add(survey_date)
                if (
                    aggregate["first_survey_date"] is None
                    or survey_date < aggregate["first_survey_date"]
                ):
                    aggregate["first_survey_date"] = survey_date
                if (
                    aggregate["last_survey_date"] is None
                    or survey_date > aggregate["last_survey_date"]
                ):
                    aggregate["last_survey_date"] = survey_date

    rows = []
    for product_name in sorted(aggregates):
        aggregate = aggregates[product_name]
        rows.append(
            {
                "상품명": product_name,
                "제조사": "|".join(sorted(aggregate["제조사"])),
                "matching_keyword": "|".join(sorted(aggregate["matching_keyword"])),
                "row_count": aggregate["row_count"],
                "store_count": len(aggregate["판매업소"]),
                "distinct_survey_date_count": len(aggregate["조사일"]),
                "first_survey_date": aggregate["first_survey_date"],
                "last_survey_date": aggregate["last_survey_date"],
            }
        )
    return rows


def write_csv(rows: list[dict[str, Any]], output_path: Path) -> None:
    fieldnames = [
        "상품명",
        "제조사",
        "matching_keyword",
        "row_count",
        "store_count",
        "distinct_survey_date_count",
        "first_survey_date",
        "last_survey_date",
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Export keyword-matched source product names for manual canonical "
            "item mapping review."
        )
    )
    parser.add_argument(
        "--summary",
        default=str(DEFAULT_SUMMARY_PATH),
        help="Path to profiling_summary.json.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Path to write the review CSV.",
    )
    parser.add_argument(
        "--encoding",
        action="append",
        dest="encodings",
        default=["utf-8-sig", "cp949"],
        help="CSV encoding candidate. Can be specified multiple times.",
    )
    args = parser.parse_args()

    summary_path = resolve_path(args.summary)
    output_path = resolve_path(args.output)
    summary = load_summary(summary_path)
    rows = build_review_rows(summary, args.encodings)
    write_csv(rows, output_path)

    print(
        json.dumps(
            {
                "summary": path_for_report(summary_path),
                "output": path_for_report(output_path),
                "candidate_count": len(rows),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
