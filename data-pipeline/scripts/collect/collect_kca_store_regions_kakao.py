from __future__ import annotations

import argparse
import csv
import difflib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = ROOT.parent
BASE_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_INPUT_PATH = ROOT / "data" / "processed" / "kca" / "kca_stores.csv"
DEFAULT_OUTPUT_PATH = ROOT / "data" / "processed" / "kca" / "kca_store_master.csv"
DEFAULT_DEBUG_REPORT_PATH = ROOT / "reports" / "kca_store_region_match_debug.csv"
DEFAULT_OVERRIDE_PATH = ROOT / "data" / "reference" / "kca" / "store_match_overrides.csv"
REQUEST_TIMEOUT_SECONDS = 15
INPUT_STORE_COLUMN = "source_store_name"
CSV_ENCODING = "utf-8-sig"

MART_CATEGORY_RETAILERS = {
    "이마트",
    "롯데슈퍼",
    "GS더프레시",
    "농협유통",
    "농협하나로유통",
}
CONVENIENCE_CATEGORY_RETAILERS = {
    "CU",
    "GS25",
    "세븐일레븐",
    "이마트24",
}
DEPARTMENT_STORE_RETAILERS = {
    "현대백화점",
    "신세계백화점",
}
RETAILER_ALIASES = {
    "농협유통": ["하나로마트", "농협 하나로마트"],
    "농협하나로유통": ["하나로마트", "농협 하나로마트"],
    "GS더프레시": ["GS더프레시", "GS THE FRESH", "GS수퍼마켓"],
    "롯데슈퍼": ["롯데슈퍼프레시", "롯데슈퍼", "롯데프레시", "LOTTE SUPER"],
    "세븐일레븐": ["세븐일레븐", "7ELEVEN"],
    "이마트24": ["이마트24", "emart24"],
    "현대백화점": ["현대백화점", "더현대"],
    "신세계백화점": ["신세계백화점", "신세계"],
}
OVERRIDE_COLUMNS = [
    "source_store_name",
    "decision",
    "canonical_place_name",
    "rejected_place_name",
    "address_name",
    "road_address_name",
    "region_1depth_name",
    "region_2depth_name",
    "region_3depth_name",
    "x",
    "y",
    "place_url",
    "store_status",
    "reason",
    "verified_source",
    "verified_at",
]
MATCH_OVERRIDE_DECISIONS = {"verified_match", "corrected_match"}
VALID_OVERRIDE_DECISIONS = {*MATCH_OVERRIDE_DECISIONS, "pending_review"}

OUTPUT_COLUMNS = [
    "source_store_name",
    "retailer_name",
    "store_branch_name",
    "row_count",
    "query",
    "category_group_code",
    "search_stage",
    "validation_status",
    "match_status",
    "store_status",
    "place_name",
    "category_name",
    "address_name",
    "road_address_name",
    "region_1depth_name",
    "region_2depth_name",
    "region_3depth_name",
    "x",
    "y",
    "place_url",
]
OUTPUT_COLUMN_SET = set(OUTPUT_COLUMNS)
VALID_CATEGORY_GROUP_CODES = {"", "MT1", "CS2"}
VALID_SEARCH_STAGES = {
    "fallback_alias",
    "fallback_no_category",
    "manual_override",
    "not_applicable",
    "primary_category",
}
VALID_VALIDATION_STATUSES = {
    "invalid",
    "not_applicable",
    "not_checked",
    "review",
    "valid",
}
VALID_MATCH_STATUSES = {
    "api_not_found",
    "matched",
    "review",
    "unmatched",
}
VALID_STORE_STATUSES = {"open", "closed", "historical", "unknown"}
ROAD_NAME_REGION_RE = re.compile(r"(대로|로|길)(\d+(번길)?|$)")
ADMIN_REGION_SUFFIX_RE = re.compile(r"(동|가|읍|면|리)$")
ADDRESS_REGION_TOKEN_RE = re.compile(
    r".*(특별자치시|특별자치도|광역시|특별시|자치구|시|군|구|읍|면|동|리|가)$"
)
PARCEL_TOKEN_RE = re.compile(r"^(산)?\d+(-\d+)?$")
DEBUG_COLUMNS = [
    "source_store_name",
    "retailer_name",
    "query",
    "search_step",
    "category_group_code",
    "raw_result_count",
    "candidate_place_name",
    "candidate_category_name",
    "candidate_address_name",
    "score",
    "reject_reason",
]


logger = logging.getLogger(__name__)


def blank_output_row(row: dict[str, str] | None = None) -> dict[str, str]:
    output_row = {column: "" for column in OUTPUT_COLUMNS}
    if row:
        for column in OUTPUT_COLUMNS:
            output_row[column] = row.get(column, "") or ""
    return output_row


def default_store_status(match_status: str) -> str:
    if match_status == "matched":
        return "open"
    if match_status in {"review", "api_not_found"}:
        return "unknown"
    return ""


def validate_output_row_schema(row: dict[str, str], row_number: int | None = None) -> None:
    keys = set(row)
    missing_columns = [column for column in OUTPUT_COLUMNS if column not in keys]
    extra_columns = sorted(keys - OUTPUT_COLUMN_SET)
    if not missing_columns and not extra_columns:
        return

    location = f" row {row_number}" if row_number is not None else ""
    details = []
    if missing_columns:
        details.append(f"missing columns: {', '.join(missing_columns)}")
    if extra_columns:
        details.append(f"unexpected columns: {', '.join(extra_columns)}")
    raise ValueError(f"Invalid output schema{location}: {'; '.join(details)}")


def validate_output_row_values(row: dict[str, str], row_number: int | None = None) -> None:
    validations = {
        "category_group_code": VALID_CATEGORY_GROUP_CODES,
        "search_stage": VALID_SEARCH_STAGES,
        "validation_status": VALID_VALIDATION_STATUSES,
        "match_status": VALID_MATCH_STATUSES,
    }
    invalid_values = [
        f"{column}={row.get(column, '')!r}"
        for column, allowed_values in validations.items()
        if row.get(column, "") not in allowed_values
    ]
    for column in ["region_1depth_name", "region_2depth_name", "region_3depth_name"]:
        region_name = row.get(column, "")
        if looks_like_invalid_region_name(region_name):
            invalid_values.append(f"{column}={region_name!r}")
    if row.get("match_status") == "matched" and row.get("validation_status") != "valid":
        invalid_values.append(
            f"matched row must have validation_status='valid', got {row.get('validation_status', '')!r}"
        )
    if row.get("match_status") in {"matched", "review", "api_not_found"}:
        store_status = row.get("store_status", "")
        if store_status not in VALID_STORE_STATUSES:
            invalid_values.append(f"store_status={store_status!r}")
    if row.get("match_status") != "matched":
        location_columns = [
            "place_name",
            "category_name",
            "address_name",
            "road_address_name",
            "region_1depth_name",
            "region_2depth_name",
            "region_3depth_name",
            "x",
            "y",
            "place_url",
        ]
        populated_location_columns = [
            column for column in location_columns if row.get(column, "")
        ]
        if populated_location_columns:
            invalid_values.append(
                "unmatched/review/api_not_found row must not persist candidate fields: "
                + ", ".join(populated_location_columns)
            )
    if not invalid_values:
        return

    location = f" row {row_number}" if row_number is not None else ""
    raise ValueError(
        f"Invalid output values{location}: {', '.join(invalid_values)}. "
        "This may indicate a shifted CSV row."
    )


def looks_like_road_name_region(region_name: str) -> bool:
    return bool(
        region_name
        and ROAD_NAME_REGION_RE.search(region_name)
        and not ADMIN_REGION_SUFFIX_RE.search(region_name)
    )


def looks_like_invalid_region_name(region_name: str) -> bool:
    return bool(region_name and (PARCEL_TOKEN_RE.match(region_name) or looks_like_road_name_region(region_name)))


def validate_output_header(fieldnames: list[str] | None, path: Path) -> None:
    actual_columns = list(fieldnames or [])
    if actual_columns == OUTPUT_COLUMNS:
        return
    missing_columns = [column for column in OUTPUT_COLUMNS if column not in actual_columns]
    extra_columns = [column for column in actual_columns if column not in OUTPUT_COLUMN_SET]
    raise ValueError(
        "Existing master schema does not match expected output columns "
        f"at {path}: missing={missing_columns}, extra={extra_columns}"
    )


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == ROOT.name:
        return PROJECT_ROOT / path
    return ROOT / path


def resolve_project_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_dotenv(path: Path = DEFAULT_ENV_PATH) -> None:
    if not path.exists():
        return

    with path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def split_region(address_name: str) -> tuple[str, str, str]:
    parts = address_name.split()
    if not parts:
        return "", "", ""

    region_1depth = parts[0] if is_first_depth_region_token(parts[0]) else ""
    if not region_1depth:
        return "", "", ""

    region_tokens = [
        token
        for token in parts[1:]
        if is_address_region_token(token) and not looks_like_road_name_region(token)
    ]
    if not region_tokens:
        return region_1depth, "", ""

    if len(region_tokens) == 1 and is_town_level_region(region_tokens[0]):
        return region_1depth, "", region_tokens[0]

    region_2depth = region_tokens[0] if len(region_tokens) >= 1 else ""
    region_3depth = region_tokens[1] if len(region_tokens) >= 2 else ""
    return region_1depth, region_2depth, region_3depth


def is_address_region_token(token: str) -> bool:
    return bool(
        token
        and not PARCEL_TOKEN_RE.match(token)
        and ADDRESS_REGION_TOKEN_RE.match(token)
    )


def is_first_depth_region_token(token: str) -> bool:
    return bool(
        token
        and not PARCEL_TOKEN_RE.match(token)
        and not looks_like_road_name_region(token)
    )


def is_town_level_region(token: str) -> bool:
    return bool(re.search(r"(읍|면|동|리|가)$", token))


def document_region(document: dict[str, Any], address_name: str) -> tuple[str, str, str]:
    address = document.get("address")
    if isinstance(address, dict):
        region = (
            str(address.get("region_1depth_name") or ""),
            str(address.get("region_2depth_name") or ""),
            str(address.get("region_3depth_name") or ""),
        )
        if any(region):
            return region

    region = (
        str(document.get("region_1depth_name") or ""),
        str(document.get("region_2depth_name") or ""),
        str(document.get("region_3depth_name") or ""),
    )
    if any(region):
        return region

    return split_region(address_name)


def normalized_retailer_name(retailer_name: str) -> str:
    return retailer_name.replace("(주)", "").strip()


def normalized_text(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def normalized_match_text(value: str) -> str:
    normalized = normalized_text(value)
    normalized = normalized.replace("ㆍ", "").replace(".", "").replace("-", "")
    normalized = normalized.replace("프레시", "fresh")
    return normalized


def normalized_branch_name(branch_name: str) -> str:
    branch = branch_name.strip()
    branch = branch.replace("(본사)", "").replace("본사", "")
    return branch


def branch_name_variants(row: dict[str, str]) -> list[str]:
    branch_name = normalized_branch_name(row.get("store_branch_name", ""))
    return [branch_name] if branch_name else []


def is_headquarters(row: dict[str, str]) -> bool:
    source_store_name = row.get(INPUT_STORE_COLUMN, "")
    branch_name = row.get("store_branch_name", "")
    return "본사" in source_store_name or "본사" in branch_name


def category_group_code(retailer_name: str) -> str:
    normalized = normalized_retailer_name(retailer_name)
    if normalized in MART_CATEGORY_RETAILERS:
        return "MT1"
    if normalized in CONVENIENCE_CATEGORY_RETAILERS:
        return "CS2"
    return ""


def base_search_query(row: dict[str, str]) -> str:
    retailer_name = normalized_retailer_name(row.get("retailer_name", ""))
    branch_name = normalized_branch_name(row.get("store_branch_name", ""))
    if retailer_name and branch_name and branch_name != "본점":
        return f"{retailer_name} {branch_name}"
    return row.get(INPUT_STORE_COLUMN, "").strip()


def alias_queries(row: dict[str, str]) -> list[str]:
    retailer_name = normalized_retailer_name(row.get("retailer_name", ""))
    branch_name = normalized_branch_name(row.get("store_branch_name", ""))
    aliases = RETAILER_ALIASES.get(retailer_name, [])
    queries = []
    for alias in aliases:
        if branch_name and branch_name != "본점":
            queries.append(f"{alias} {branch_name}")
        else:
            queries.append(alias)

    source_store_name = row.get(INPUT_STORE_COLUMN, "").strip()
    if retailer_name == "롯데슈퍼":
        branch_without_g = branch_name[1:] if branch_name.startswith("G") else branch_name
        if branch_without_g:
            queries.extend(
                [
                    f"롯데슈퍼프레시 {branch_without_g}",
                    f"롯데슈퍼프레시 {branch_name}",
                    f"롯데슈퍼 {branch_without_g}",
                    f"롯데슈퍼 {branch_name}",
                ]
            )
            if branch_without_g.endswith("점"):
                branch_without_suffix = branch_without_g[:-1]
                queries.append(f"롯데슈퍼프레시 {branch_without_suffix}")
    elif retailer_name == "GS더프레시" and branch_name:
        queries.extend(
            [
                f"GS더프레시 {branch_name}",
                f"GS THE FRESH {branch_name}",
                f"GS수퍼마켓 {branch_name}",
            ]
        )

    compact_query = re.sub(r"(?<!\s)(점)$", r" \1", source_store_name)
    queries.extend([source_store_name.replace("(주)", "").strip(), compact_query])
    return unique_texts(query for query in queries if query)


def unique_texts(values: Any) -> list[str]:
    seen = set()
    unique = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def validation_status(row: dict[str, str], document: dict[str, Any] | None) -> str:
    if is_headquarters(row) or document is None:
        return "not_applicable"

    decision = evaluate_document_match(row, document)
    if decision["match_status"] == "matched":
        return "valid"
    if any(
        reason in decision["reject_reasons"]
        for reason in [
            "retailer_mismatch",
            "branch_mismatch",
            "category_mismatch",
            "closed_place_name",
            "brand_variant_mismatch",
        ]
    ):
        return "invalid"
    return "not_checked"


def read_store_rows(input_path: Path) -> list[dict[str, str]]:
    with input_path.open("r", encoding=CSV_ENCODING, newline="") as f:
        reader = csv.DictReader(f)
        if INPUT_STORE_COLUMN not in (reader.fieldnames or []):
            raise ValueError(f"Input CSV is missing required column: {INPUT_STORE_COLUMN}")
        return [{key: value or "" for key, value in row.items()} for row in reader]


def read_existing_master(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open("r", encoding=CSV_ENCODING, newline="") as f:
        reader = csv.DictReader(f)
        validate_output_header(reader.fieldnames, path)
        if INPUT_STORE_COLUMN not in (reader.fieldnames or []):
            raise ValueError(f"Existing master is missing required column: {INPUT_STORE_COLUMN}")
        return {
            row[INPUT_STORE_COLUMN]: blank_output_row(row)
            for row in reader
            if row.get(INPUT_STORE_COLUMN)
        }


def read_store_overrides(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}

    overrides: dict[str, dict[str, str]] = {}
    with path.open("r", encoding=CSV_ENCODING, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        missing_columns = [column for column in OVERRIDE_COLUMNS if column not in fieldnames]
        if missing_columns:
            raise ValueError(f"Override CSV is missing columns: {missing_columns}")

        for row_number, row in enumerate(reader, start=2):
            override = {column: (row.get(column) or "").strip() for column in OVERRIDE_COLUMNS}
            source_store_name = override["source_store_name"]
            if not source_store_name:
                raise ValueError(f"Override CSV row {row_number}: source_store_name is required")
            if source_store_name in overrides:
                raise ValueError(f"Duplicate override source_store_name: {source_store_name}")
            validate_store_override_row(override, row_number)
            overrides[source_store_name] = override
    return overrides


def validate_store_override_row(row: dict[str, str], row_number: int | None = None) -> None:
    decision = row.get("decision", "")
    location = f" row {row_number}" if row_number is not None else ""
    if decision not in VALID_OVERRIDE_DECISIONS:
        raise ValueError(f"Invalid override decision{location}: {decision!r}")

    if decision in MATCH_OVERRIDE_DECISIONS:
        required_columns = [
            "canonical_place_name",
            "address_name",
            "region_1depth_name",
            "x",
            "y",
        ]
        missing = [column for column in required_columns if not row.get(column, "")]
        if missing:
            raise ValueError(
                f"Override CSV{location} with decision={decision} is missing required values: {missing}"
            )


def store_override_row(row: dict[str, str], override: dict[str, str]) -> dict[str, str]:
    decision = override["decision"]
    output_row = blank_output_row(row)
    output_row["query"] = override.get("canonical_place_name") or base_search_query(row)
    output_row["search_stage"] = "manual_override"
    output_row["category_group_code"] = ""
    output_row["store_status"] = override.get("store_status", "")

    if decision in MATCH_OVERRIDE_DECISIONS:
        output_row.update(
            {
                "validation_status": "valid",
                "match_status": "matched",
                "place_name": override.get("canonical_place_name", ""),
                "category_name": "",
                "address_name": override.get("address_name", ""),
                "road_address_name": override.get("road_address_name", ""),
                "region_1depth_name": override.get("region_1depth_name", ""),
                "region_2depth_name": override.get("region_2depth_name", ""),
                "region_3depth_name": override.get("region_3depth_name", ""),
                "x": override.get("x", ""),
                "y": override.get("y", ""),
                "place_url": override.get("place_url", ""),
            }
        )
        output_row["store_status"] = output_row["store_status"] or default_store_status("matched")
        return output_row

    output_row.update(
        {
            "validation_status": "review",
            "match_status": "review",
            "store_status": output_row["store_status"] or default_store_status("review"),
        }
    )
    return output_row


def document_from_master_row(row: dict[str, str]) -> dict[str, Any] | None:
    if not row.get("place_name"):
        return None
    return {
        "place_name": row.get("place_name", ""),
        "category_name": row.get("category_name", ""),
        "address_name": row.get("address_name", ""),
        "road_address_name": row.get("road_address_name", ""),
        "region_1depth_name": row.get("region_1depth_name", ""),
        "region_2depth_name": row.get("region_2depth_name", ""),
        "region_3depth_name": row.get("region_3depth_name", ""),
        "x": row.get("x", ""),
        "y": row.get("y", ""),
        "place_url": row.get("place_url", ""),
    }


def existing_master_row_needs_refresh(row: dict[str, str]) -> bool:
    if row.get("match_status") != "matched":
        return False
    if row.get("search_stage") == "manual_override":
        return row.get("validation_status") != "valid"
    document = document_from_master_row(row)
    if document is None:
        return True
    decision = evaluate_document_match(row, document)
    return decision["match_status"] != "matched" or row.get("validation_status") != "valid"


def build_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"KakaoAK {api_key}"}


def fetch_keyword(
    session: requests.Session,
    api_key: str,
    query: str,
    requested_category_group_code: str,
) -> list[dict[str, Any]]:
    params = {"query": query}
    if requested_category_group_code:
        params["category_group_code"] = requested_category_group_code
    response = session.get(
        BASE_URL,
        params=params,
        headers=build_headers(api_key),
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    documents = payload.get("documents", [])
    return documents if isinstance(documents, list) else []


def retailer_matches(retailer_name: str, text: str) -> bool:
    normalized_retailer = normalized_text(normalized_retailer_name(retailer_name))
    normalized_candidate = normalized_text(text)
    aliases = [normalized_retailer, *RETAILER_ALIASES.get(normalized_retailer_name(retailer_name), [])]
    return any(normalized_text(alias) in normalized_candidate for alias in aliases if alias)


def retailer_brand_matches(retailer_name: str, text: str) -> bool:
    retailer = normalized_retailer_name(retailer_name)
    normalized_candidate = normalized_text(text)
    if retailer == "이마트":
        disallowed = ["이마트24", "emart24", "트레이더스", "traders", "노브랜드"]
        return not any(token in normalized_candidate or token in text for token in disallowed) and "이마트" in text
    if retailer == "이마트24":
        return "이마트24" in text or "emart24" in normalized_candidate
    if retailer == "GS더프레시":
        return any(token in normalized_candidate for token in ["gs더프레시", "gsthefresh", "gs수퍼마켓"])
    if retailer == "GS25":
        return "gs25" in normalized_candidate
    if retailer == "CU":
        return bool(re.search(r"(^|[^a-zA-Z가-힣])CU([^a-zA-Z가-힣]|$)", text, re.IGNORECASE))
    if retailer == "세븐일레븐":
        return "세븐일레븐" in text or "7eleven" in normalized_candidate
    if retailer in {"농협유통", "농협하나로유통"}:
        return any(token in text for token in ["농협유통", "농협하나로유통", "하나로마트"])
    if retailer == "롯데슈퍼":
        return any(token in text for token in ["롯데슈퍼", "롯데슈퍼프레시", "롯데프레시"])
    if retailer in DEPARTMENT_STORE_RETAILERS:
        return retailer in text or any(alias in text for alias in RETAILER_ALIASES.get(retailer, []))
    return retailer_matches(retailer_name, text)


def candidate_branch_name(row: dict[str, str], place_name: str) -> str:
    candidate = place_name
    retailer_name = normalized_retailer_name(row.get("retailer_name", ""))
    aliases = [retailer_name, *RETAILER_ALIASES.get(retailer_name, [])]
    aliases = sorted(unique_texts(alias for alias in aliases if alias), key=len, reverse=True)
    for alias in aliases:
        candidate = re.sub(re.escape(alias), "", candidate, flags=re.IGNORECASE)
    candidate = candidate.replace("(주)", "")
    return candidate.strip()


def branch_name_matches(row: dict[str, str], place_name: str) -> bool:
    branch_variants = branch_name_variants(row)
    if not branch_variants:
        return False

    candidate_branch = candidate_branch_name(row, place_name)
    normalized_candidate_branch = normalized_match_text(candidate_branch)
    normalized_place_name = normalized_match_text(place_name)
    for branch_variant in branch_variants:
        normalized_branch = normalized_match_text(branch_variant)
        if not normalized_branch:
            continue
        if normalized_candidate_branch == normalized_branch:
            return True
        if normalized_place_name.endswith(normalized_branch):
            return True
    return False


def evaluate_document_match(row: dict[str, str], document: dict[str, Any]) -> dict[str, Any]:
    score, reject_reasons = score_document(row, document)
    required_rejections = {
        "retailer_mismatch",
        "branch_mismatch",
        "category_mismatch",
        "closed_place_name",
        "brand_variant_mismatch",
    }
    match_status = "matched" if score >= 95 and not any(reason in reject_reasons for reason in required_rejections) else "review"
    return {
        "match_status": match_status,
        "validation_status": "valid" if match_status == "matched" else "review",
        "score": score,
        "reject_reasons": reject_reasons,
    }


def score_document(row: dict[str, str], document: dict[str, Any]) -> tuple[int, list[str]]:
    retailer_name = row.get("retailer_name", "")
    branch_variants = branch_name_variants(row)
    place_name = str(document.get("place_name") or "")
    category_name = str(document.get("category_name") or "")
    text = f"{place_name} {category_name}"
    expected_place_name = normalized_text(base_search_query(row))
    normalized_place_name = normalized_text(place_name)
    place_similarity = difflib.SequenceMatcher(
        None,
        expected_place_name,
        normalized_place_name,
    ).ratio()
    score = 0
    reject_reasons = []

    if retailer_brand_matches(retailer_name, text):
        score += 50
    else:
        reject_reasons.append("retailer_mismatch")
    if branch_name_matches(row, place_name):
        score += 40
    elif branch_variants:
        reject_reasons.append("branch_mismatch")
    if place_name and "폐점" not in place_name:
        score += 5
    elif "폐점" in place_name:
        reject_reasons.append("closed_place_name")
    if place_similarity >= 0.75:
        score += 20
    elif place_similarity >= 0.55:
        score += 10
    else:
        reject_reasons.append("low_place_name_similarity")

    retailer = normalized_retailer_name(retailer_name)
    if retailer in DEPARTMENT_STORE_RETAILERS:
        if "백화점" in category_name:
            score += 80
        else:
            score -= 80
            reject_reasons.append("category_mismatch")
    elif retailer in MART_CATEGORY_RETAILERS:
        if any(token in category_name for token in ["대형마트", "대형슈퍼", "슈퍼마켓", "마트"]):
            score += 25
        else:
            reject_reasons.append("category_mismatch")
    elif retailer in CONVENIENCE_CATEGORY_RETAILERS:
        if "편의점" in category_name:
            score += 25
        else:
            score -= 20
            reject_reasons.append("category_mismatch")

    return score, reject_reasons


def candidate_debug_row(
    row: dict[str, str],
    query: str,
    search_stage: str,
    requested_category_group_code: str,
    raw_result_count: int,
    document: dict[str, Any] | None,
    score: int | str,
    reject_reason: str,
) -> dict[str, str]:
    return {
        "source_store_name": row.get(INPUT_STORE_COLUMN, ""),
        "retailer_name": row.get("retailer_name", ""),
        "query": query,
        "search_step": search_stage,
        "category_group_code": requested_category_group_code,
        "raw_result_count": str(raw_result_count),
        "candidate_place_name": str((document or {}).get("place_name") or ""),
        "candidate_category_name": str((document or {}).get("category_name") or ""),
        "candidate_address_name": str((document or {}).get("address_name") or ""),
        "score": str(score),
        "reject_reason": reject_reason,
    }


def select_best_document(
    row: dict[str, str],
    documents: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[tuple[dict[str, Any], int, str]]]:
    if not documents:
        return None, []
    scored = []
    for document in documents:
        decision = evaluate_document_match(row, document)
        score = int(decision["score"])
        reject_reasons = list(decision["reject_reasons"])
        scored.append((document, score, ";".join(reject_reasons) if reject_reasons else ""))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[0][0], scored


def find_best_match(
    session: requests.Session,
    api_key: str,
    row: dict[str, str],
) -> tuple[dict[str, Any] | None, str, str, str, str, list[dict[str, str]]]:
    query = base_search_query(row)
    requested_category_group_code = category_group_code(row.get("retailer_name", ""))

    search_attempts = [(query, requested_category_group_code, "primary_category")]
    if requested_category_group_code:
        search_attempts.append((query, "", "fallback_no_category"))
    for alias_query in alias_queries(row):
        search_attempts.append((alias_query, "", "fallback_alias"))

    debug_rows = []
    fallback_query = query
    fallback_category_group_code = requested_category_group_code
    fallback_search_stage = "primary_category"
    saw_any_raw_result = False
    saw_fallback_candidate = False
    for attempt_query, attempt_category_group_code, search_stage in search_attempts:
        documents = fetch_keyword(session, api_key, attempt_query, attempt_category_group_code)
        raw_result_count = len(documents)
        saw_any_raw_result = saw_any_raw_result or raw_result_count > 0
        document, scored_documents = select_best_document(row, documents)
        if not documents:
            debug_rows.append(
                candidate_debug_row(
                    row,
                    attempt_query,
                    search_stage,
                    attempt_category_group_code,
                    raw_result_count,
                    None,
                    "",
                    "api_returned_no_documents",
                )
            )
        else:
            selected_place_url = str((document or {}).get("place_url") or "")
            for candidate, score, reject_reason in scored_documents:
                candidate_place_url = str(candidate.get("place_url") or "")
                if selected_place_url and candidate_place_url == selected_place_url:
                    reason = f"selected;{reject_reason}" if reject_reason else "selected"
                else:
                    reason = reject_reason
                debug_rows.append(
                    candidate_debug_row(
                        row,
                        attempt_query,
                        search_stage,
                        attempt_category_group_code,
                        raw_result_count,
                        candidate,
                        score,
                        reason or "lower_score_candidate",
                    )
                )
        if document is not None:
            if not saw_fallback_candidate:
                fallback_query = attempt_query
                fallback_category_group_code = attempt_category_group_code
                fallback_search_stage = search_stage
                saw_fallback_candidate = True
            if evaluate_document_match(row, document)["match_status"] == "matched":
                return document, attempt_query, attempt_category_group_code, search_stage, "matched", debug_rows

    if saw_any_raw_result:
        return (
            None,
            fallback_query,
            fallback_category_group_code,
            fallback_search_stage,
            "review",
            debug_rows,
        )

    return None, query, requested_category_group_code, "primary_category", "api_not_found", debug_rows


def enriched_row(
    row: dict[str, str],
    document: dict[str, Any] | None,
    query: str,
    requested_category_group_code: str,
    search_stage: str,
    match_status: str,
) -> dict[str, str]:
    validation = validation_status(row, document)
    output_row = blank_output_row(row)
    if document is not None and match_status == "matched" and validation != "valid":
        match_status = "review"
        validation = "review"
    if document is None or match_status != "matched" or validation != "valid":
        if match_status == "review":
            validation = "review"
        output_row.update(
            {
                "query": query,
                "category_group_code": requested_category_group_code,
                "search_stage": search_stage,
                "validation_status": validation,
                "match_status": match_status,
                "store_status": default_store_status(match_status),
            }
        )
        return output_row

    address_name = str(document.get("address_name") or "")
    road_address_name = str(document.get("road_address_name") or "")
    region_1depth, region_2depth, region_3depth = document_region(document, address_name)
    output_row.update(
        {
            "query": query,
            "category_group_code": requested_category_group_code,
            "search_stage": search_stage,
            "validation_status": validation,
            "match_status": match_status,
            "store_status": default_store_status(match_status),
            "place_name": str(document.get("place_name") or ""),
            "category_name": str(document.get("category_name") or ""),
            "address_name": address_name,
            "road_address_name": road_address_name,
            "region_1depth_name": region_1depth,
            "region_2depth_name": region_2depth,
            "region_3depth_name": region_3depth,
            "x": str(document.get("x") or ""),
            "y": str(document.get("y") or ""),
            "place_url": str(document.get("place_url") or ""),
        }
    )
    return output_row


def write_rows(output_path: Path, rows: list[dict[str, str]]) -> None:
    for row_number, row in enumerate(rows, start=1):
        validate_output_row_schema(row, row_number)
        validate_output_row_values(row, row_number)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding=CSV_ENCODING, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def write_debug_rows(output_path: Path, rows: list[dict[str, str]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding=CSV_ENCODING, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DEBUG_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Enrich distinct KCA stores with Kakao Local keyword search region data."
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT_PATH),
        help="Distinct KCA stores CSV path.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Output store master CSV path.",
    )
    parser.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV_PATH),
        help="Env file path containing KAKAO_REST_API_KEY or KAKAO_API_KEY.",
    )
    parser.add_argument(
        "--debug-report",
        default=str(DEFAULT_DEBUG_REPORT_PATH),
        help="CSV path for raw Kakao candidate match debug rows.",
    )
    parser.add_argument(
        "--overrides",
        default=str(DEFAULT_OVERRIDE_PATH),
        help="CSV path for data-driven store match overrides.",
    )
    parser.add_argument(
        "--refresh-status",
        action="append",
        default=[],
        help=(
            "Re-query existing master rows with this match_status. "
            "Can be passed multiple times, e.g. --refresh-status api_not_found."
        ),
    )
    parser.add_argument(
        "--refresh-all",
        action="store_true",
        help="Re-query every input store, ignoring existing master rows.",
    )
    parser.add_argument(
        "--refresh-source-store",
        action="append",
        default=[],
        help=(
            "Re-query this source_store_name even if it already exists in the master. "
            "Can be passed multiple times."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Optional maximum number of new stores to request. Useful for smoke tests.",
    )
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        raise ValueError("--limit must be greater than zero")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_dotenv(resolve_project_path(args.env_file))
    api_key = os.getenv("KAKAO_REST_API_KEY") or os.getenv("KAKAO_API_KEY")
    if not api_key:
        raise RuntimeError("KAKAO_REST_API_KEY or KAKAO_API_KEY is required.")

    input_rows = read_store_rows(resolve_path(args.input))
    output_path = resolve_path(args.output)
    overrides = read_store_overrides(resolve_project_path(args.overrides))
    existing_by_source_store_name = {} if args.refresh_all else read_existing_master(output_path)
    if overrides:
        existing_by_source_store_name = {
            source_store_name: row
            for source_store_name, row in existing_by_source_store_name.items()
            if source_store_name not in overrides
        }
    refresh_statuses = set(args.refresh_status)
    refresh_source_store_names = set(args.refresh_source_store)
    revalidated_refresh_store_names = {
        source_store_name
        for source_store_name, row in existing_by_source_store_name.items()
        if existing_master_row_needs_refresh(row)
    }
    if revalidated_refresh_store_names:
        logger.info(
            "Re-querying %s existing matched rows that failed local revalidation.",
            len(revalidated_refresh_store_names),
        )
        existing_by_source_store_name = {
            source_store_name: row
            for source_store_name, row in existing_by_source_store_name.items()
            if source_store_name not in revalidated_refresh_store_names
        }
    if refresh_statuses:
        existing_by_source_store_name = {
            source_store_name: row
            for source_store_name, row in existing_by_source_store_name.items()
            if row.get("match_status") not in refresh_statuses
        }
    if refresh_source_store_names:
        existing_by_source_store_name = {
            source_store_name: row
            for source_store_name, row in existing_by_source_store_name.items()
            if source_store_name not in refresh_source_store_names
        }
    new_rows = [
        row for row in input_rows if row[INPUT_STORE_COLUMN] not in existing_by_source_store_name
    ]
    if args.limit is not None:
        new_rows = new_rows[: args.limit]

    new_output_rows: list[dict[str, str]] = []
    debug_rows: list[dict[str, str]] = []
    with requests.Session() as session:
        for index, row in enumerate(new_rows, start=1):
            override = overrides.get(row[INPUT_STORE_COLUMN])
            if override:
                output_row = store_override_row(row, override)
                new_output_rows.append(output_row)
                debug_rows.append(
                    candidate_debug_row(
                        row,
                        output_row["query"],
                        "manual_override",
                        output_row["category_group_code"],
                        0,
                        None,
                        "",
                        f"override_{override['decision']}",
                    )
                )
                logger.info(
                    "Kakao store lookup skipped: %s/%s query=%s reason=manual_override status=%s",
                    index,
                    len(new_rows),
                    output_row["query"],
                    output_row["match_status"],
                )
                continue

            if is_headquarters(row):
                output_row = enriched_row(
                    row,
                    None,
                    base_search_query(row),
                    "",
                    "not_applicable",
                    "unmatched",
                )
                new_output_rows.append(output_row)
                debug_rows.append(
                    candidate_debug_row(
                        row,
                        output_row["query"],
                        "not_applicable",
                        "",
                        0,
                        None,
                        "",
                        "headquarters_store",
                    )
                )
                logger.info(
                    "Kakao store lookup skipped: %s/%s query=%s reason=headquarters",
                    index,
                    len(new_rows),
                    output_row["query"],
                )
                continue

            (
                document,
                query,
                requested_category_group_code,
                search_stage,
                match_status,
                match_debug_rows,
            ) = find_best_match(
                session,
                api_key,
                row,
            )
            debug_rows.extend(match_debug_rows)
            new_output_rows.append(
                enriched_row(
                    row,
                    document,
                    query,
                    requested_category_group_code,
                    search_stage,
                    match_status,
                )
            )
            logger.info(
                "Kakao store lookup: %s/%s query=%s category_group_code=%s stage=%s status=%s",
                index,
                len(new_rows),
                query,
                requested_category_group_code or "-",
                search_stage,
                match_status,
            )

    merged_by_source_store_name = {
        **existing_by_source_store_name,
        **{row[INPUT_STORE_COLUMN]: row for row in new_output_rows},
    }
    output_rows = [
        merged_by_source_store_name[row[INPUT_STORE_COLUMN]]
        for row in input_rows
        if row[INPUT_STORE_COLUMN] in merged_by_source_store_name
    ]
    write_rows(output_path, output_rows)
    write_debug_rows(resolve_path(args.debug_report), debug_rows)
    print(
        json.dumps(
            {
                "existing_row_count": len(existing_by_source_store_name),
                "new_row_count": len(new_output_rows),
                "output_row_count": len(output_rows),
                "output": str(output_path),
                "debug_report": str(resolve_path(args.debug_report)),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
