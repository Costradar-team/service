from __future__ import annotations

import csv
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from sqlalchemy.dialects import mysql


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/load"))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path); module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader; sys.modules[name] = module; spec.loader.exec_module(module); return module


profile = load_module("profile_retailer", ROOT / "scripts/profile/profile_retailer.py")
transformer = load_module("transform_retailer", ROOT / "scripts/transform/transform_retailer.py")
loader = load_module("load_retailer_mysql", ROOT / "scripts/load/load_retailer_mysql.py")


class RetailerEtlTests(unittest.TestCase):
    def test_quantity_normalization(self) -> None:
        self.assertEqual(transformer.parse_quantity("우유 1L"), ("1000", "ml"))
        self.assertEqual(transformer.parse_quantity("계란 30입"), ("30", "입"))
        self.assertEqual(transformer.parse_quantity("설탕"), ("", ""))

    def test_promotion_is_normalized_to_erd_code(self) -> None:
        self.assertEqual(transformer.normalize_promotion("2개 구매 시 50% 할인"), "MULTIBUY_50_PERCENT")
        self.assertEqual(transformer.normalize_promotion("하나 증정"), "FREE_ITEM")
        self.assertEqual(transformer.normalize_promotion("행사 A / 행사 B"), "MULTI")

    def test_profile_and_transform_preserve_erd_grain(self) -> None:
        columns = ["collected_at", "source", "product_key", "item_id", "item_name", "brand_name", "sale_price", "promotion_type", "product_url"]
        row = {"collected_at": "2026-09-03T10:00:00", "source": "LOTTEMART_ZETTA", "product_key": "milk", "item_id": "P1", "item_name": "서울우유 1L", "brand_name": "서울우유", "sale_price": "3000", "promotion_type": "", "product_url": "https://example/P1"}
        rules = {"dataset": "test", "required_columns": columns, "product_mapping": {"milk": "우유"}, "retailer_mapping": {"LOTTEMART_ZETTA": "롯데마트"}}
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp); source = temp_path / "raw.csv"
            with source.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=columns); writer.writeheader(); writer.writerow(row)
            result = profile.profile_file(source, rules); self.assertTrue(result["passed"])
            report = transformer.transform([source], rules, temp_path / "out", temp_path / "report.json")
            self.assertEqual(report["listing_rows"], 1); self.assertEqual(report["observation_rows"], 1)
            with (temp_path / "out/retailer_product_listing.csv").open(encoding="utf-8-sig") as stream: listing = next(csv.DictReader(stream))
            self.assertEqual((listing["retailer_name"], listing["source_product_id"], listing["canonical_item"]), ("롯데마트", "P1", "우유"))

    def test_loader_tables_follow_erd_foreign_keys(self) -> None:
        listing_fks = {fk.target_fullname for fk in loader.retailer_product_listing.foreign_keys}
        observation_fks = {fk.target_fullname for fk in loader.retailer_price_observation.foreign_keys}
        self.assertEqual(listing_fks, {"product.product_id", "retailer.retailer_id"})
        self.assertEqual(observation_fks, {"retailer_product_listing.listing_id"})

    def test_skip_policy_is_explicit_no_op_upsert(self) -> None:
        connection = Mock()
        loader.upsert(connection, loader.retailer, [{"name": "롯데마트"}])
        statement = connection.execute.call_args.args[0]
        sql = str(statement.compile(dialect=mysql.dialect()))
        self.assertIn("ON DUPLICATE KEY UPDATE", sql)
        self.assertNotIn("IGNORE", sql)

    def test_product_grain_matches_composite_unique_key(self) -> None:
        unique = next(c for c in loader.product.constraints if getattr(c, "name", "") == "uq_product_source_manufacturer_subtype")
        self.assertEqual([column.name for column in unique.columns], ["source_product_name", "manufacturer_id_for_unique", "subtype_id"])


if __name__ == "__main__": unittest.main()
