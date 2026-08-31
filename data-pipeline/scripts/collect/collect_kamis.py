from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BASE_URL = "https://www.kamis.or.kr/service/price/xml.do"
DEFAULT_RAW_DIR = ROOT / "data" / "raw" / "kamis"
DEFAULT_ENV_PATH = ROOT.parent / ".env"
REQUEST_TIMEOUT_SECONDS = 30
NORMAL_KAMIS_ERROR_CODES = {"0", "00", "000"}

PRODUCTS = {
    "egg_10": {
        "item_name": "계란",
        "kind_name": "특란10구",
        "item_category_code": "500",
        "item_code": "9903",
        "kind_code": "21",
        "product_rank_code": "71",
        "unit": "10구",
    },
    "egg_30": {
        "item_name": "계란",
        "kind_name": "특란30구",
        "item_category_code": "500",
        "item_code": "9903",
        "kind_code": "23",
        "product_rank_code": "71",
        "unit": "30구",
    },
    "milk_1l": {
        "item_name": "우유",
        "kind_name": "흰우유",
        "item_category_code": "500",
        "item_code": "9908",
        "kind_code": "01",
        "product_rank_code": "00",
        "unit": "1L",
    },
}


logger = logging.getLogger(__name__)


class KamisApiError(RuntimeError):
    def __init__(self, error_code: str | None, error_message: str | None) -> None:
        self.error_code = error_code
        self.error_message = error_message
        super().__init__(f"KAMIS API error_code={error_code}, message={error_message}")


class KamisRequestError(RuntimeError):
    pass


def valid_date(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid date '{value}'. Expected YYYY-MM-DD."
        ) from exc
    return value


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


def build_params(
    *,
    product: dict[str, str],
    start_date: str,
    end_date: str,
    api_key: str,
    api_id: str,
) -> dict[str, str]:
    item_category_code = product["item_category_code"]
    item_code = product["item_code"]
    kind_code = product["kind_code"]
    product_rank_code = product["product_rank_code"]

    return {
        # 조회 API 종류
        # 기간을 지정해서 품목별 소매가격을 조회
        "action": "periodRetailProductList",

        # 조회 시작일 / 종료일
        "p_startday": start_date,
        "p_endday": end_date,

        # 품목 부류 코드
        # 500 = 축산물
        "p_itemcategorycode": item_category_code,

        # 품목 코드
        # 계란 = 9903
        # 우유 = 9908
        "p_itemcode": item_code,

        # 품종/규격 코드
        # 계란 특란10구 = 21
        # 계란 특란30구 = 23
        # 우유 흰우유 = 01
        "p_kindcode": kind_code,

        # 등급 코드
        # 계란 일반란 = 71
        # 우유 흰우유 = 00
        "p_productrankcode": product_rank_code,

        # kg 환산 여부
        # N = KAMIS 원래 조사 단위 유지
        # ex. 계란 10구/30구, 우유 1L
        "p_convert_kg_yn": "N",

        # KAMIS 인증 정보
        "p_cert_key": api_key,
        "p_cert_id": api_id,

        # 응답 형식
        "p_returntype": "json",
    }


def extract_kamis_error_code(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    header = payload.get("response", {}).get("header", {})
    data = payload.get("data", {})
    candidates = [
        (header.get("resultCode"), header.get("resultMsg")),
        (header.get("error_code"), header.get("error_msg")),
        (header.get("errorCode"), header.get("errorMsg")),
        (data.get("error_code"), data.get("error_msg")),
        (data.get("errorCode"), data.get("errorMsg")),
        (payload.get("error_code"), payload.get("error_msg")),
        (payload.get("errorCode"), payload.get("errorMsg")),
    ]
    for error_code, error_message in candidates:
        if error_code is not None:
            return str(error_code), None if error_message is None else str(error_message)
    return None, None


def validate_kamis_response(payload: dict[str, Any]) -> None:
    error_code, error_message = extract_kamis_error_code(payload)
    if error_code is None or error_code not in NORMAL_KAMIS_ERROR_CODES:
        raise KamisApiError(error_code, error_message)


def fetch_kamis(params: dict[str, str]) -> tuple[dict[str, Any], str]:
    try:
        import requests
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "The 'requests' package is required. Install dependencies with "
            "'pip install -r requirements.txt'."
        ) from exc

    try:
        response = requests.get(
            BASE_URL,
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        raise KamisRequestError(f"HTTP status error: {exc}") from exc
    except requests.exceptions.ConnectionError as exc:
        raise KamisRequestError(f"Connection error: {exc}") from exc
    except requests.exceptions.Timeout as exc:
        raise KamisRequestError(f"Timeout after {REQUEST_TIMEOUT_SECONDS}s: {exc}") from exc

    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise ValueError("KAMIS response is not valid JSON.") from exc
    validate_kamis_response(payload)
    return payload, response.text


def save_raw(
    *,
    product_key: str,
    start_date: str,
    end_date: str,
    raw_text: str,
    output_dir: Path,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"kamis_{product_key}_{start_date}_{end_date}.json"
    output_path.write_text(raw_text, encoding="utf-8")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect raw KAMIS daily retail price JSON files."
    )
    parser.add_argument(
        "--product",
        nargs="+",
        choices=sorted(PRODUCTS),
        default=sorted(PRODUCTS),
        help="Product keys to collect. Defaults to all registered products.",
    )
    parser.add_argument(
        "--start-date",
        required=True,
        type=valid_date,
        help="Collection start date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--end-date",
        required=True,
        type=valid_date,
        help="Collection end date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_RAW_DIR),
        help="Directory for raw KAMIS JSON files. Defaults to data/raw/kamis.",
    )
    args = parser.parse_args()

    start_dt = datetime.strptime(args.start_date, "%Y-%m-%d").date()
    end_dt = datetime.strptime(args.end_date, "%Y-%m-%d").date()
    if start_dt > end_dt:
        parser.error("--start-date must be earlier than or equal to --end-date.")

    return args


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else ROOT / path


def log_collect_error(
    *,
    product_key: str,
    product: dict[str, str],
    start_date: str,
    end_date: str,
    error_code: str | None,
    exc: Exception,
) -> None:
    logger.error(
        "KAMIS collection failed: product=%s item_code=%s kind_code=%s "
        "start_date=%s end_date=%s error_code=%s error=%s",
        product_key,
        product["item_code"],
        product["kind_code"],
        start_date,
        end_date,
        error_code,
        exc,
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_dotenv()
    args = parse_args()

    api_key = os.getenv("KAMIS_API_KEY", "")
    api_id = os.getenv("KAMIS_API_ID") or os.getenv("KAMIS_ID", "")
    output_dir = resolve_path(args.output_dir)
    failed_count = 0

    for product_key in args.product:
        product = PRODUCTS[product_key]
        params = build_params(
            product=product,
            start_date=args.start_date,
            end_date=args.end_date,
            api_key=api_key,
            api_id=api_id,
        )
        try:
            _payload, raw_text = fetch_kamis(params)
            output_path = save_raw(
                product_key=product_key,
                start_date=args.start_date,
                end_date=args.end_date,
                raw_text=raw_text,
                output_dir=output_dir,
            )
        except KamisRequestError as exc:
            failed_count += 1
            log_collect_error(
                product_key=product_key,
                product=product,
                start_date=args.start_date,
                end_date=args.end_date,
                error_code=None,
                exc=exc,
            )
            continue
        except KamisApiError as exc:
            failed_count += 1
            log_collect_error(
                product_key=product_key,
                product=product,
                start_date=args.start_date,
                end_date=args.end_date,
                error_code=exc.error_code,
                exc=exc,
            )
            continue
        except ValueError as exc:
            failed_count += 1
            log_collect_error(
                product_key=product_key,
                product=product,
                start_date=args.start_date,
                end_date=args.end_date,
                error_code=None,
                exc=exc,
            )
            continue
        except RuntimeError as exc:
            failed_count += 1
            log_collect_error(
                product_key=product_key,
                product=product,
                start_date=args.start_date,
                end_date=args.end_date,
                error_code=None,
                exc=exc,
            )
            continue

        logger.info(
            "KAMIS collection saved: product=%s item_code=%s kind_code=%s "
            "start_date=%s end_date=%s path=%s",
            product_key,
            product["item_code"],
            product["kind_code"],
            args.start_date,
            args.end_date,
            output_path,
        )

    return 1 if failed_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
