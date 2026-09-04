from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = ROOT.parent
BASE_URL = "https://api.odcloud.kr/api/15083256/v1"
SWAGGER_URL = "https://infuser.odcloud.kr/oas/docs"
DATASET_ID = "15083256"
SWAGGER_NAMESPACE = "15083256/v1"
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "raw" / "kca"
DEFAULT_PER_PAGE = 1000
REQUEST_TIMEOUT_SECONDS = 30
CSV_ENCODING = "utf-8-sig"
TARGET_ITEM_KEYWORDS = {"계란", "우유", "설탕", "버터", "밀가루"}
OUTPUT_COLUMNS = [
    "상품명",
    "조사일",
    "판매가격",
    "판매업소",
    "제조사",
    "세일여부",
    "원플러스원",
]


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KcaDataset:
    uuid: str
    dataset_date: str | None = None


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


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else ROOT / path


def normalize_uuid(value: str) -> str:
    normalized = value.strip()
    if normalized.startswith("uddi:"):
        normalized = normalized.removeprefix("uddi:")
    if not re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        normalized,
    ):
        raise argparse.ArgumentTypeError("UUID must be a valid UUID string.")
    return normalized.lower()


def service_key_from_env() -> str:
    service_key = os.getenv("ODCLOUD_SERVICE_KEY") or os.getenv("KCA_API_KEY")
    if not service_key:
        raise RuntimeError("ODCLOUD_SERVICE_KEY or KCA_API_KEY is required.")
    return service_key


def uuid_from_env() -> str:
    uuid = os.getenv("KCA_ODCLOUD_UUID") or os.getenv("KCA_UUID")
    if not uuid:
        return ""
    return normalize_uuid(uuid)


def output_path(output_dir: Path, dataset: KcaDataset) -> Path:
    suffix = dataset.dataset_date or dataset.uuid
    return output_dir / f"kca_{suffix}.csv"


def fetch_swagger() -> dict[str, Any]:
    response = requests.get(
        SWAGGER_URL,
        params={"namespace": SWAGGER_NAMESPACE},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def date_from_summary(summary: str) -> str:
    normalized = summary.strip()
    ymd_match = re.search(r"(\d{8})$", normalized)
    if ymd_match:
        date_text = ymd_match.group(1)
        datetime.strptime(date_text, "%Y%m%d")
        return date_text

    slash_match = re.search(r"(\d{2}/\d{2}/\d{4})$", normalized)
    if slash_match:
        parsed = datetime.strptime(slash_match.group(1), "%m/%d/%Y")
        return parsed.strftime("%Y%m%d")

    raise ValueError(f"KCA Swagger summary does not contain a supported date: {summary}")


def uuid_from_path(path: str) -> str:
    prefix = f"/{DATASET_ID}/v1/uddi:"
    if not path.startswith(prefix):
        raise ValueError(f"KCA Swagger path does not start with {prefix}: {path}")
    return normalize_uuid(path.removeprefix(prefix))


def resolve_latest_dataset() -> KcaDataset:
    payload = fetch_swagger()
    paths = payload.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("KCA Swagger response does not contain a paths object.")

    datasets: list[KcaDataset] = []
    prefix = f"/{DATASET_ID}/v1/uddi:"
    for path, path_config in paths.items():
        if not isinstance(path, str) or not path.startswith(prefix):
            continue
        if not isinstance(path_config, dict):
            continue
        get_config = path_config.get("get")
        if not isinstance(get_config, dict):
            continue
        summary = get_config.get("summary")
        if not isinstance(summary, str):
            raise ValueError(f"KCA Swagger path is missing get.summary: {path}")
        datasets.append(
            KcaDataset(
                uuid=uuid_from_path(path),
                dataset_date=date_from_summary(summary),
            )
        )

    if not datasets:
        raise RuntimeError("No KCA uddi paths were found in Swagger response.")

    latest = max(datasets, key=lambda dataset: dataset.dataset_date or "")
    logger.info("Resolved KCA dataset: %s", latest.dataset_date)
    logger.info("Resolved KCA UUID: %s", latest.uuid)
    return latest


def resolve_dataset(uuid_override: str | None) -> KcaDataset:
    uuid = uuid_override or uuid_from_env()
    if uuid:
        logger.info("Using KCA UUID override: %s", uuid)
        return KcaDataset(uuid=uuid)
    return resolve_latest_dataset()


def fetch_page(
    session: requests.Session,
    *,
    uuid: str,
    page: int,
    per_page: int,
    service_key: str,
) -> dict[str, Any]:
    url = f"{BASE_URL}/uddi:{uuid}"
    params: dict[str, Any] = {"page": page, "perPage": per_page}
    if "%" in service_key:
        url = f"{url}?page={page}&perPage={per_page}&serviceKey={service_key}"
        params = {}
    else:
        params["serviceKey"] = service_key

    response = session.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def payload_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("data", [])
    if not isinstance(rows, list):
        raise ValueError("KCA OpenAPI response data must be a list.")
    return [row for row in rows if isinstance(row, dict)]


def payload_total_count(payload: dict[str, Any]) -> int | None:
    total_count = payload.get("totalCount")
    if total_count is None:
        return None
    try:
        return int(total_count)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid totalCount: {total_count}") from exc


def collect_rows(
    *,
    uuid: str,
    per_page: int,
    service_key: str,
) -> list[dict[str, Any]]:
    all_rows: list[dict[str, Any]] = []
    page = 1
    total_count: int | None = None

    with requests.Session() as session:
        while True:
            payload = fetch_page(
                session,
                uuid=uuid,
                page=page,
                per_page=per_page,
                service_key=service_key,
            )
            rows = payload_rows(payload)
            total_count = payload_total_count(payload)
            all_rows.extend(rows)
            logger.info(
                "KCA page collected: uuid=%s page=%s rows=%s total=%s",
                uuid,
                page,
                len(rows),
                total_count,
            )

            if not rows:
                break
            if total_count is not None and len(all_rows) >= total_count:
                break
            if len(rows) < per_page:
                break
            page += 1

    return all_rows


def normalize_row(row: dict[str, Any]) -> dict[str, str]:
    return {column: "" if row.get(column) is None else str(row.get(column)) for column in OUTPUT_COLUMNS}


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=CSV_ENCODING, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(normalize_row(row))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect one monthly KCA Champrice OpenAPI dataset as a raw CSV."
    )
    parser.add_argument(
        "--uuid",
        type=normalize_uuid,
        help=(
            "Monthly OpenAPI UDDI UUID override. Defaults to KCA_ODCLOUD_UUID "
            "or KCA_UUID, then latest Swagger dataset."
        ),
    )
    parser.add_argument(
        "--output",
        help="Output raw CSV path. Defaults to data/raw/kca/kca_{dataset_date_or_uuid}.csv.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Output directory when --output is omitted. Defaults to data/raw/kca.",
    )
    parser.add_argument(
        "--per-page",
        type=int,
        default=DEFAULT_PER_PAGE,
        help="OpenAPI perPage value. Defaults to 1000.",
    )
    parser.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV_PATH),
        help="Env file path containing ODCLOUD_SERVICE_KEY or KCA_API_KEY.",
    )
    args = parser.parse_args()
    if args.per_page <= 0:
        raise ValueError("--per-page must be greater than zero.")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_dotenv(resolve_path(args.env_file))
    dataset = resolve_dataset(args.uuid)
    service_key = service_key_from_env()
    rows = collect_rows(uuid=dataset.uuid, per_page=args.per_page, service_key=service_key)
    path = resolve_path(args.output) if args.output else output_path(resolve_path(args.output_dir), dataset)
    write_rows(path, rows)
    print(
        json.dumps(
            {
                "dataset_id": DATASET_ID,
                "dataset_date": dataset.dataset_date,
                "uuid": dataset.uuid,
                "row_count": len(rows),
                "target_item_keywords": sorted(TARGET_ITEM_KEYWORDS),
                "output": str(path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
