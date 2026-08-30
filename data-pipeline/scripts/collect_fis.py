from __future__ import annotations

import argparse
import csv
import logging
import re
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://www.atfis.or.kr/home/commodity.do"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "raw" / "fis"
DEFAULT_BEGIN_DATE = "2025-08-01"
DEFAULT_END_DATE = "2026-07-31"
DEFAULT_PAGE_UNIT = 15
REQUEST_TIMEOUT_SECONDS = 30

PRODUCTS = {
    "wheat_srw": {
        "fis_item": "소맥(SRW)",
        "cmdt_id": "0601000002001103",
        "cmdt_se_cd": "CORN",
        "unit": "US¢/bu",
    },
    "white_sugar": {
        "fis_item": "White Sugar",
        "cmdt_id": "0701000002011303",
        "cmdt_se_cd": "FOOD",
        "unit": "USD/ton",
    },
}


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProductRequest:
    product_key: str
    fis_item: str
    cmdt_id: str
    cmdt_se_cd: str
    unit: str


def valid_date(value: str) -> str:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid date '{value}'. Expected YYYY-MM-DD."
        ) from exc
    return value


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else ROOT / path


def normalize_cell(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def product_request(product_key: str) -> ProductRequest:
    product = PRODUCTS[product_key]
    return ProductRequest(
        product_key=product_key,
        fis_item=product["fis_item"],
        cmdt_id=product["cmdt_id"],
        cmdt_se_cd=product["cmdt_se_cd"],
        unit=product["unit"],
    )


def build_params(
    *,
    product: ProductRequest,
    begin_date: str,
    end_date: str,
    page_index: int,
    page_unit: int,
) -> dict[str, Any]:
    return {
        "act": "detail",
        "cmdtId": product.cmdt_id,
        "cmdtSeCd": product.cmdt_se_cd,
        "beginYmd": begin_date,
        "endYmd": end_date,
        "periodGubun": "DAY",
        "pageIndex": page_index,
        "pageUnit": page_unit,
    }


def fetch_page(session: requests.Session, params: dict[str, Any]) -> str:
    response = session.get(
        BASE_URL,
        params=params,
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={
            "User-Agent": "cost-radar-data-pipeline/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    return response.text


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table_depth = 0
        self._current_rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._current_rows = []
            return
        if self._table_depth != 1:
            return
        if tag == "tr":
            self._current_row = []
        elif tag in {"td", "th"} and self._current_row is not None:
            self._current_cell = []

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._table_depth != 1:
            if tag == "table" and self._table_depth:
                self._table_depth -= 1
            return
        if tag in {"td", "th"} and self._current_cell is not None:
            cell = normalize_cell(" ".join(self._current_cell))
            if self._current_row is not None:
                self._current_row.append(cell)
            self._current_cell = None
        elif tag == "tr" and self._current_row is not None:
            if any(cell for cell in self._current_row):
                self._current_rows.append(self._current_row)
            self._current_row = None
        elif tag == "table":
            self.tables.append(self._current_rows)
            self._current_rows = []
            self._table_depth -= 1


def table_score(rows: list[list[str]]) -> int:
    text = normalize_cell(" ".join(" ".join(row) for row in rows))
    return sum(
        1
        for keyword in ["날짜", "일자", "가격", "등락", "전일", "종가", "평균"]
        if keyword in text
    )


def parse_table(rows: list[list[str]]) -> list[dict[str, str]]:
    if not rows:
        return []

    headers = rows[0]
    body_rows = rows[1:]
    headers = [
        header if header else f"column_{idx + 1}"
        for idx, header in enumerate(headers)
    ]
    parsed_rows: list[dict[str, str]] = []
    for cells in body_rows:
        if not cells or all(not cell for cell in cells):
            continue
        if len(cells) == 1 and any(text in cells[0] for text in ["조회", "없습니다", "데이터"]):
            continue
        if len(cells) < len(headers):
            cells.extend([""] * (len(headers) - len(cells)))
        parsed_rows.append(dict(zip(headers, cells[: len(headers)])))
    return parsed_rows


def parse_rows(html: str) -> list[dict[str, str]]:
    parser = TableParser()
    parser.feed(html)
    if not parser.tables:
        return []
    table = max(parser.tables, key=table_score)
    return parse_table(table)


def output_path(output_dir: Path, product_key: str, begin_date: str, end_date: str) -> Path:
    return output_dir / f"fis_{product_key}_{begin_date}_{end_date}.csv"


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata_columns = ["product_key", "fis_item", "cmdt_id", "cmdt_se_cd", "unit"]
    data_columns: list[str] = []
    for row in rows:
        for column in row:
            if column not in metadata_columns and column not in data_columns:
                data_columns.append(column)

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[*metadata_columns, *data_columns])
        writer.writeheader()
        writer.writerows(rows)


def collect_product(
    *,
    session: requests.Session,
    product: ProductRequest,
    begin_date: str,
    end_date: str,
    output_dir: Path,
    page_unit: int,
) -> dict[str, Any]:
    all_rows: list[dict[str, str]] = []
    page_index = 1
    collected_page_count = 0

    while True:
        params = build_params(
            product=product,
            begin_date=begin_date,
            end_date=end_date,
            page_index=page_index,
            page_unit=page_unit,
        )
        html = fetch_page(session, params)
        rows = parse_rows(html)
        if not rows:
            break

        for row in rows:
            row.update(
                {
                    "product_key": product.product_key,
                    "fis_item": product.fis_item,
                    "cmdt_id": product.cmdt_id,
                    "cmdt_se_cd": product.cmdt_se_cd,
                    "unit": product.unit,
                }
            )
        all_rows.extend(rows)
        collected_page_count += 1
        logger.info(
            "FIS page collected: product=%s page=%s rows=%s",
            product.product_key,
            page_index,
            len(rows),
        )

        if len(rows) < page_unit:
            break
        page_index += 1

    path = output_path(output_dir, product.product_key, begin_date, end_date)
    write_rows(path, all_rows)
    return {
        "product_key": product.product_key,
        "fis_item": product.fis_item,
        "cmdt_id": product.cmdt_id,
        "cmdt_se_cd": product.cmdt_se_cd,
        "unit": product.unit,
        "begin_date": begin_date,
        "end_date": end_date,
        "row_count": len(all_rows),
        "page_count": collected_page_count,
        "output": str(path.relative_to(ROOT)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect FIS commodity daily price tables as CSV files."
    )
    parser.add_argument(
        "--product",
        nargs="+",
        choices=sorted(PRODUCTS),
        default=sorted(PRODUCTS),
        help="Product keys to collect. Defaults to all registered products.",
    )
    parser.add_argument(
        "--begin-date",
        type=valid_date,
        default=DEFAULT_BEGIN_DATE,
        help="Collection begin date in YYYY-MM-DD format. Defaults to 2025-08-01.",
    )
    parser.add_argument(
        "--end-date",
        type=valid_date,
        default=DEFAULT_END_DATE,
        help="Collection end date in YYYY-MM-DD format. Defaults to 2026-07-31.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for raw FIS CSV files. Defaults to data/raw/fis.",
    )
    parser.add_argument(
        "--page-unit",
        type=int,
        default=DEFAULT_PAGE_UNIT,
        help="Rows per page requested from FIS. Defaults to 15.",
    )
    args = parser.parse_args()
    if args.page_unit <= 0:
        raise ValueError("--page-unit must be greater than zero")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    output_dir = resolve_path(args.output_dir)
    with requests.Session() as session:
        summaries = [
            collect_product(
                session=session,
                product=product_request(product_key),
                begin_date=args.begin_date,
                end_date=args.end_date,
                output_dir=output_dir,
                page_unit=args.page_unit,
            )
            for product_key in args.product
        ]

    for summary in summaries:
        logger.info(
            "FIS collection saved: product=%s rows=%s output=%s",
            summary["product_key"],
            summary["row_count"],
            summary["output"],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
