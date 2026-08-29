from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = (
    REPO_ROOT
    / "data-pipeline"
    / "data"
    / "processed"
    / "kca_prices_processed.csv"
)
DEFAULT_OUTPUT_DIR = REPO_ROOT / "artifacts" / "ml"

NORMALIZED_FILENAME = "normalized_prices.csv"
MODEL_DATASET_FILENAME = "model_dataset.csv"
PRODUCT_OUTPUT_DIRNAME = "product"
BRAND_OUTPUT_DIRNAME = "brand"
STORE_OUTPUT_DIRNAME = "store"
REJECTED_FILENAME = "unit_normalization_rejected.csv"
SUMMARY_FILENAME = "dataset_summary.json"

BRAND_PREFIX_RULES = (
    ("홈플러스익스프레스", "홈플러스"),
    ("홈플러스", "홈플러스"),
    ("롯데마트", "롯데마트·슈퍼"),
    ("롯데슈퍼", "롯데마트·슈퍼"),
    ("GS더프레시", "GS더프레시"),
    ("이마트24", "이마트24"),
    ("이마트", "이마트"),
    ("(주)농협하나로유통", "농협하나로마트"),
    ("(주)농협유통", "농협하나로마트"),
    ("신세계백화점", "신세계백화점"),
    ("현대백화점", "현대백화점"),
    ("롯데백화점", "롯데백화점"),
    ("메가마트", "메가마트"),
    ("GS25", "GS25"),
    ("CU", "CU"),
    ("세븐일레븐", "세븐일레븐"),
)

SPEC_PATTERN = re.compile(
    r"^\s*(?P<quantity>\d+(?:\.\d+)?)\s*(?P<unit>kg|g|l|ml|개|개입)\s*$",
    re.IGNORECASE,
)

MASS_ITEMS = {"밀가루", "설탕", "버터"}
VOLUME_ITEMS = {"우유"}
COUNT_ITEMS = {"계란"}

NORMALIZED_COLUMNS = [
    "상품명",
    "조사일",
    "판매가격",
    "판매업소",
    "brand_name",
    "store_name",
    "제조사",
    "세일여부",
    "원플러스원",
    "canonical_item",
    "subtype",
    "spec",
    "package_quantity",
    "package_unit",
    "base_quantity",
    "unit_price",
    "unit_price_basis",
]

MODEL_COLUMNS = [
    "survey_date",
    "canonical_item",
    "subtype",
    "unit_price_basis",
    "observation_count",
    "store_count",
    "sku_count",
    "min_unit_price",
    "median_unit_price",
    "max_unit_price",
]

PRODUCT_MODEL_COLUMNS = [
    "survey_date",
    "product_name",
    "canonical_item",
    "subtype",
    "unit_price_basis",
    "observation_count",
    "store_count",
    "min_unit_price",
    "median_unit_price",
    "max_unit_price",
]

BRAND_MODEL_COLUMNS = [
    "survey_date",
    "product_name",
    "canonical_item",
    "subtype",
    "brand_name",
    "unit_price_basis",
    "observation_count",
    "store_count",
    "min_unit_price",
    "median_unit_price",
    "max_unit_price",
]

STORE_MODEL_COLUMNS = [
    "survey_date",
    "product_name",
    "canonical_item",
    "subtype",
    "brand_name",
    "store_name",
    "unit_price_basis",
    "observation_count",
    "actual_unit_price",
]

REJECTED_COLUMNS = [
    "상품명",
    "조사일",
    "판매가격",
    "canonical_item",
    "subtype",
    "spec",
    "reason",
]


@dataclass(frozen=True)
class UnitNormalization:
    package_quantity: float
    package_unit: str
    base_quantity: float
    unit_price: float
    unit_price_basis: str


def resolve_cli_path(path_text: str) -> Path:
    path = Path(path_text).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def format_number(value: float, digits: int = 4) -> str:
    return f"{value:.{digits}f}".rstrip("0").rstrip(".")


def parse_price(value: str) -> int:
    return int(value.strip().replace(",", ""))


def infer_brand_name(store_name: str) -> str:
    normalized_store_name = store_name.strip()
    for prefix, brand_name in BRAND_PREFIX_RULES:
        if normalized_store_name.startswith(prefix):
            return brand_name
    return "기타"


def normalize_unit_price(
    canonical_item: str,
    spec: str,
    price: int,
) -> UnitNormalization:
    match = SPEC_PATTERN.fullmatch(spec)
    if match is None:
        raise ValueError("unsupported_spec")

    quantity = float(match.group("quantity"))
    if quantity <= 0:
        raise ValueError("non_positive_quantity")

    raw_unit = match.group("unit").lower()
    if canonical_item in MASS_ITEMS:
        if raw_unit == "kg":
            base_quantity = quantity
        elif raw_unit == "g":
            base_quantity = quantity / 1000
        else:
            raise ValueError("unit_item_mismatch")
        normalized_unit = "kg"
        unit_price = price / base_quantity
        basis = "KRW/kg"
    elif canonical_item in VOLUME_ITEMS:
        if raw_unit == "l":
            base_quantity = quantity
        elif raw_unit == "ml":
            base_quantity = quantity / 1000
        else:
            raise ValueError("unit_item_mismatch")
        normalized_unit = "L"
        unit_price = price / base_quantity
        basis = "KRW/L"
    elif canonical_item in COUNT_ITEMS:
        if raw_unit not in {"개", "개입"}:
            raise ValueError("unit_item_mismatch")
        base_quantity = quantity
        normalized_unit = "ea"
        unit_price = price / base_quantity * 10
        basis = "KRW/10ea"
    else:
        raise ValueError("unsupported_item")

    return UnitNormalization(
        package_quantity=quantity,
        package_unit=normalized_unit,
        base_quantity=base_quantity,
        unit_price=unit_price,
        unit_price_basis=basis,
    )


def build_model_dataset(
    input_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    if not input_path.is_file():
        raise FileNotFoundError(f"Processed input CSV not found: {input_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    normalized_path = output_dir / NORMALIZED_FILENAME
    model_path = output_dir / MODEL_DATASET_FILENAME
    product_output_dir = output_dir / PRODUCT_OUTPUT_DIRNAME
    product_output_dir.mkdir(parents=True, exist_ok=True)
    product_model_path = product_output_dir / MODEL_DATASET_FILENAME
    brand_output_dir = output_dir / BRAND_OUTPUT_DIRNAME
    brand_output_dir.mkdir(parents=True, exist_ok=True)
    brand_model_path = brand_output_dir / MODEL_DATASET_FILENAME
    store_output_dir = output_dir / STORE_OUTPUT_DIRNAME
    store_output_dir.mkdir(parents=True, exist_ok=True)
    store_model_path = store_output_dir / MODEL_DATASET_FILENAME
    rejected_path = output_dir / REJECTED_FILENAME
    summary_path = output_dir / SUMMARY_FILENAME

    aggregates: dict[
        tuple[str, str, str, str],
        dict[str, Any],
    ] = {}
    product_aggregates: dict[
        tuple[str, str, str, str, str],
        dict[str, Any],
    ] = {}
    brand_aggregates: dict[
        tuple[str, str, str, str, str, str],
        dict[str, Any],
    ] = {}
    store_aggregates: dict[
        tuple[str, str, str, str, str, str, str],
        dict[str, Any],
    ] = {}
    input_count = 0
    normalized_count = 0
    rejected_count = 0
    item_row_counts: Counter[str] = Counter()
    item_dates: dict[str, set[str]] = {}

    with input_path.open("r", encoding="utf-8-sig", newline="") as source_file, (
        normalized_path.open("w", encoding="utf-8-sig", newline="")
    ) as normalized_file, rejected_path.open(
        "w", encoding="utf-8-sig", newline=""
    ) as rejected_file:
        reader = csv.DictReader(source_file)
        normalized_writer = csv.DictWriter(
            normalized_file,
            fieldnames=NORMALIZED_COLUMNS,
        )
        rejected_writer = csv.DictWriter(
            rejected_file,
            fieldnames=REJECTED_COLUMNS,
        )
        normalized_writer.writeheader()
        rejected_writer.writeheader()

        for row in reader:
            input_count += 1
            try:
                price = parse_price(row.get("판매가격", ""))
                if price <= 0:
                    raise ValueError("non_positive_price")
                normalized = normalize_unit_price(
                    (row.get("canonical_item") or "").strip(),
                    (row.get("spec") or "").strip(),
                    price,
                )
            except (TypeError, ValueError) as exc:
                rejected_count += 1
                rejected_writer.writerow(
                    {
                        column: row.get(column, "")
                        for column in REJECTED_COLUMNS
                        if column != "reason"
                    }
                    | {"reason": str(exc)}
                )
                continue

            output_row = {
                column: row.get(column, "")
                for column in NORMALIZED_COLUMNS
                if column
                not in {
                    "brand_name",
                    "store_name",
                    "package_quantity",
                    "package_unit",
                    "base_quantity",
                    "unit_price",
                    "unit_price_basis",
                }
            }
            store_name = (row.get("판매업소") or "").strip()
            if not store_name:
                store_name = "판매업소 미확인"
            brand_name = infer_brand_name(store_name)
            output_row.update(
                {
                    "brand_name": brand_name,
                    "store_name": store_name,
                    "package_quantity": format_number(
                        normalized.package_quantity,
                        3,
                    ),
                    "package_unit": normalized.package_unit,
                    "base_quantity": format_number(
                        normalized.base_quantity,
                        4,
                    ),
                    "unit_price": format_number(normalized.unit_price, 4),
                    "unit_price_basis": normalized.unit_price_basis,
                }
            )
            normalized_writer.writerow(output_row)
            normalized_count += 1

            survey_date = (row.get("조사일") or "").strip()
            product_name = (row.get("상품명") or "").strip()
            canonical_item = (row.get("canonical_item") or "").strip()
            subtype = (row.get("subtype") or "").strip()
            key = (
                survey_date,
                canonical_item,
                subtype,
                normalized.unit_price_basis,
            )
            accumulator = aggregates.setdefault(
                key,
                {"prices": [], "stores": set(), "skus": set()},
            )
            accumulator["prices"].append(normalized.unit_price)
            accumulator["stores"].add((row.get("판매업소") or "").strip())
            accumulator["skus"].add(product_name)

            product_key = (
                survey_date,
                product_name,
                canonical_item,
                subtype,
                normalized.unit_price_basis,
            )
            product_accumulator = product_aggregates.setdefault(
                product_key,
                {"prices": [], "stores": set()},
            )
            product_accumulator["prices"].append(normalized.unit_price)
            product_accumulator["stores"].add(
                store_name
            )

            brand_key = (
                survey_date,
                product_name,
                canonical_item,
                subtype,
                brand_name,
                normalized.unit_price_basis,
            )
            brand_accumulator = brand_aggregates.setdefault(
                brand_key,
                {"prices": [], "stores": set()},
            )
            brand_accumulator["prices"].append(normalized.unit_price)
            brand_accumulator["stores"].add(store_name)

            store_key = (
                survey_date,
                product_name,
                canonical_item,
                subtype,
                brand_name,
                store_name,
                normalized.unit_price_basis,
            )
            store_accumulator = store_aggregates.setdefault(
                store_key,
                {"prices": []},
            )
            store_accumulator["prices"].append(normalized.unit_price)
            item_row_counts[canonical_item] += 1
            item_dates.setdefault(canonical_item, set()).add(survey_date)

    model_rows = []
    for (
        survey_date,
        canonical_item,
        subtype,
        basis,
    ), accumulator in sorted(aggregates.items()):
        prices = accumulator["prices"]
        model_rows.append(
            {
                "survey_date": survey_date,
                "canonical_item": canonical_item,
                "subtype": subtype,
                "unit_price_basis": basis,
                "observation_count": len(prices),
                "store_count": len(accumulator["stores"]),
                "sku_count": len(accumulator["skus"]),
                "min_unit_price": format_number(min(prices), 4),
                "median_unit_price": format_number(median(prices), 4),
                "max_unit_price": format_number(max(prices), 4),
            }
        )

    with model_path.open("w", encoding="utf-8-sig", newline="") as model_file:
        writer = csv.DictWriter(model_file, fieldnames=MODEL_COLUMNS)
        writer.writeheader()
        writer.writerows(model_rows)

    product_model_rows = []
    for (
        survey_date,
        product_name,
        canonical_item,
        subtype,
        basis,
    ), accumulator in sorted(product_aggregates.items()):
        prices = accumulator["prices"]
        product_model_rows.append(
            {
                "survey_date": survey_date,
                "product_name": product_name,
                "canonical_item": canonical_item,
                "subtype": subtype,
                "unit_price_basis": basis,
                "observation_count": len(prices),
                "store_count": len(accumulator["stores"]),
                "min_unit_price": format_number(min(prices), 4),
                "median_unit_price": format_number(median(prices), 4),
                "max_unit_price": format_number(max(prices), 4),
            }
        )

    with product_model_path.open(
        "w", encoding="utf-8-sig", newline=""
    ) as product_model_file:
        writer = csv.DictWriter(
            product_model_file,
            fieldnames=PRODUCT_MODEL_COLUMNS,
        )
        writer.writeheader()
        writer.writerows(product_model_rows)

    brand_model_rows = []
    for (
        survey_date,
        product_name,
        canonical_item,
        subtype,
        brand_name,
        basis,
    ), accumulator in sorted(brand_aggregates.items()):
        prices = accumulator["prices"]
        brand_model_rows.append(
            {
                "survey_date": survey_date,
                "product_name": product_name,
                "canonical_item": canonical_item,
                "subtype": subtype,
                "brand_name": brand_name,
                "unit_price_basis": basis,
                "observation_count": len(prices),
                "store_count": len(accumulator["stores"]),
                "min_unit_price": format_number(min(prices), 4),
                "median_unit_price": format_number(median(prices), 4),
                "max_unit_price": format_number(max(prices), 4),
            }
        )

    with brand_model_path.open(
        "w", encoding="utf-8-sig", newline=""
    ) as brand_model_file:
        writer = csv.DictWriter(
            brand_model_file,
            fieldnames=BRAND_MODEL_COLUMNS,
        )
        writer.writeheader()
        writer.writerows(brand_model_rows)

    store_model_rows = []
    for (
        survey_date,
        product_name,
        canonical_item,
        subtype,
        brand_name,
        store_name,
        basis,
    ), accumulator in sorted(store_aggregates.items()):
        prices = accumulator["prices"]
        store_model_rows.append(
            {
                "survey_date": survey_date,
                "product_name": product_name,
                "canonical_item": canonical_item,
                "subtype": subtype,
                "brand_name": brand_name,
                "store_name": store_name,
                "unit_price_basis": basis,
                "observation_count": len(prices),
                # Duplicate source observations are not expected. If they appear,
                # their median is the single direct store price used for modeling.
                "actual_unit_price": format_number(median(prices), 4),
            }
        )

    with store_model_path.open(
        "w", encoding="utf-8-sig", newline=""
    ) as store_model_file:
        writer = csv.DictWriter(
            store_model_file,
            fieldnames=STORE_MODEL_COLUMNS,
        )
        writer.writeheader()
        writer.writerows(store_model_rows)

    all_dates = sorted({row["survey_date"] for row in model_rows})
    summary = {
        "input_row_count": input_count,
        "normalized_row_count": normalized_count,
        "rejected_row_count": rejected_count,
        "model_row_count": len(model_rows),
        "product_model_row_count": len(product_model_rows),
        "product_series_count": len(
            {
                (
                    row["product_name"],
                    row["canonical_item"],
                    row["subtype"],
                    row["unit_price_basis"],
                )
                for row in product_model_rows
            }
        ),
        "brand_model_row_count": len(brand_model_rows),
        "brand_series_count": len(
            {
                (
                    row["product_name"],
                    row["canonical_item"],
                    row["subtype"],
                    row["brand_name"],
                    row["unit_price_basis"],
                )
                for row in brand_model_rows
            }
        ),
        "store_model_row_count": len(store_model_rows),
        "store_series_count": len(
            {
                (
                    row["product_name"],
                    row["canonical_item"],
                    row["subtype"],
                    row["brand_name"],
                    row["store_name"],
                    row["unit_price_basis"],
                )
                for row in store_model_rows
            }
        ),
        "brand_count": len({row["brand_name"] for row in store_model_rows}),
        "store_count": len({row["store_name"] for row in store_model_rows}),
        "date_min": all_dates[0] if all_dates else None,
        "date_max": all_dates[-1] if all_dates else None,
        "unique_survey_date_count": len(all_dates),
        "canonical_item_row_counts": dict(sorted(item_row_counts.items())),
        "canonical_item_unique_date_counts": {
            item: len(dates) for item, dates in sorted(item_dates.items())
        },
        "outputs": {
            "normalized_prices": str(normalized_path),
            "model_dataset": str(model_path),
            "product_model_dataset": str(product_model_path),
            "brand_model_dataset": str(brand_model_path),
            "store_model_dataset": str(store_model_path),
            "rejected_rows": str(rejected_path),
            "summary": str(summary_path),
        },
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize KCA package prices and build a date-level CostRadar "
            "modeling dataset."
        )
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT),
        help="Path to kca_prices_processed.csv.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for normalized and model-ready outputs.",
    )
    args = parser.parse_args()

    summary = build_model_dataset(
        resolve_cli_path(args.input),
        resolve_cli_path(args.output_dir),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
