from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path

from ml.scripts.external_market_features import (
    MarketPoint,
    features_as_of,
    load_fis_series,
    load_kamis_series,
)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class ExternalMarketFeaturesTest(unittest.TestCase):
    def test_as_of_features_never_read_a_future_observation(self) -> None:
        points = [
            MarketPoint(date(2026, 1, 1), 100.0),
            MarketPoint(date(2026, 1, 10), 200.0),
        ]

        features = features_as_of(points, date(2026, 1, 5))

        self.assertEqual(features["external_market_available"], 1.0)
        self.assertEqual(features["external_market_price"], 100.0)
        self.assertEqual(features["external_market_age_days"], 4.0)

    def test_loads_fis_converted_price_by_canonical_item(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_csv(
                root / "fis_item.csv",
                ["item_key", "canonical_item"],
                [{"item_key": "wheat", "canonical_item": "밀가루"}],
            )
            write_csv(
                root / "fis_price_observation.csv",
                ["item_key", "trade_date", "close_price", "converted_price"],
                [
                    {
                        "item_key": "wheat",
                        "trade_date": "2026-01-02",
                        "close_price": "500",
                        "converted_price": "123.4",
                    }
                ],
            )

            series = load_fis_series(root)

            self.assertEqual(series["밀가루"][0].price, 123.4)

    def test_kamis_egg_pack_prices_are_normalized_to_ten_eggs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            keys = ["item_category_code", "item_code", "kind_code", "rank_code"]
            write_csv(
                root / "kamis_item.csv",
                ["canonical_item", *keys, "quantity", "unit"],
                [
                    {
                        "canonical_item": "계란",
                        "item_category_code": "100",
                        "item_code": "1",
                        "kind_code": "10",
                        "rank_code": "71",
                        "quantity": "10",
                        "unit": "구",
                    },
                    {
                        "canonical_item": "계란",
                        "item_category_code": "100",
                        "item_code": "1",
                        "kind_code": "30",
                        "rank_code": "71",
                        "quantity": "30",
                        "unit": "구",
                    },
                ],
            )
            write_csv(
                root / "kamis_price_observation.csv",
                [*keys, "observed_date", "price_scope_type", "scope_name", "price"],
                [
                    {
                        "item_category_code": "100",
                        "item_code": "1",
                        "kind_code": "10",
                        "rank_code": "71",
                        "observed_date": "2026-01-02",
                        "price_scope_type": "AVERAGE",
                        "scope_name": "평균",
                        "price": "3000",
                    },
                    {
                        "item_category_code": "100",
                        "item_code": "1",
                        "kind_code": "30",
                        "rank_code": "71",
                        "observed_date": "2026-01-02",
                        "price_scope_type": "AVERAGE",
                        "scope_name": "평균",
                        "price": "9000",
                    },
                ],
            )

            series = load_kamis_series(root)

            self.assertEqual(series["계란"][0].price, 3000.0)


if __name__ == "__main__":
    unittest.main()
