from __future__ import annotations

import unittest

import pandas as pd

from ml.scripts.train_price_model import build_supervised_dataset, split_by_date


class TrainPriceModelTest(unittest.TestCase):
    def setUp(self) -> None:
        rows = []
        for item, subtype, start_price in [
            ("밀가루", "박력분", 2000),
            ("설탕", "백설탕", 2500),
        ]:
            for index, survey_date in enumerate(
                pd.date_range("2026-01-01", periods=10, freq="14D")
            ):
                rows.append(
                    {
                        "survey_date": survey_date.date().isoformat(),
                        "canonical_item": item,
                        "subtype": subtype,
                        "product_name": f"{item} 테스트 상품",
                        "unit_price_basis": "KRW/kg",
                        "median_unit_price": start_price + index * 10,
                    }
                )
        self.frame = pd.DataFrame(rows)

    def test_features_use_only_prior_prices(self) -> None:
        supervised = build_supervised_dataset(
            self.frame,
            minimum_series_points=6,
        )
        first = supervised.loc[
            (supervised["canonical_item"] == "밀가루")
            & (supervised["survey_date"] == pd.Timestamp("2026-02-26"))
        ].iloc[0]
        self.assertEqual(first["lag_1"], 2030)
        self.assertEqual(first["lag_4"], 2000)
        self.assertEqual(first["rolling_mean_4"], 2015)
        self.assertEqual(first["external_market_available"], 0)
        self.assertEqual(first["external_market_age_days"], 0)

    def test_external_features_are_frozen_at_the_previous_survey_date(self) -> None:
        frame = self.frame.copy()
        frame["external_market_price"] = (
            frame.groupby(["canonical_item", "subtype"]).cumcount() + 100
        )
        frame["external_market_available"] = 1
        frame["external_market_age_days"] = 2

        supervised = build_supervised_dataset(frame, minimum_series_points=6)
        row = supervised.loc[
            (supervised["canonical_item"] == "밀가루")
            & (supervised["survey_date"] == pd.Timestamp("2026-02-26"))
        ].iloc[0]

        self.assertEqual(row["external_market_price"], 103)
        self.assertEqual(row["external_market_age_days"], 16)

    def test_chronological_split_keeps_latest_dates_for_test(self) -> None:
        supervised = build_supervised_dataset(
            self.frame,
            minimum_series_points=6,
        )
        train, test, test_dates = split_by_date(supervised, test_fraction=0.25)
        self.assertLess(train["survey_date"].max(), test["survey_date"].min())
        self.assertEqual(len(test_dates), 2)

    def test_product_features_do_not_mix_product_histories(self) -> None:
        rows = []
        for product_name, start_price in [("상품 A", 1000), ("상품 B", 5000)]:
            for index, survey_date in enumerate(
                pd.date_range("2026-01-01", periods=8, freq="14D")
            ):
                rows.append(
                    {
                        "survey_date": survey_date.date().isoformat(),
                        "canonical_item": "밀가루",
                        "subtype": "박력분",
                        "product_name": product_name,
                        "unit_price_basis": "KRW/kg",
                        "median_unit_price": start_price + index * 10,
                    }
                )

        supervised = build_supervised_dataset(
            pd.DataFrame(rows),
            minimum_series_points=6,
            series_level="product",
        )
        first_b = supervised.loc[
            (supervised["product_name"] == "상품 B")
            & (supervised["survey_date"] == pd.Timestamp("2026-02-26"))
        ].iloc[0]
        self.assertEqual(first_b["lag_1"], 5030)
        self.assertEqual(first_b["lag_4"], 5000)

    def test_store_features_use_each_stores_direct_actual_prices(self) -> None:
        rows = []
        for store_name, start_price in [
            ("이마트A점", 1000),
            ("이마트B점", 5000),
        ]:
            for index, survey_date in enumerate(
                pd.date_range("2026-01-01", periods=8, freq="14D")
            ):
                rows.append(
                    {
                        "survey_date": survey_date.date().isoformat(),
                        "canonical_item": "밀가루",
                        "subtype": "박력분",
                        "product_name": "상품 A",
                        "brand_name": "이마트",
                        "store_name": store_name,
                        "unit_price_basis": "KRW/kg",
                        "actual_unit_price": start_price + index * 10,
                    }
                )

        supervised = build_supervised_dataset(
            pd.DataFrame(rows),
            minimum_series_points=6,
            series_level="store",
        )
        first_b = supervised.loc[
            (supervised["store_name"] == "이마트B점")
            & (supervised["survey_date"] == pd.Timestamp("2026-02-26"))
        ].iloc[0]
        self.assertEqual(first_b["lag_1"], 5030)
        self.assertEqual(first_b["lag_4"], 5000)
        self.assertEqual(first_b["actual_unit_price"], 5040)


if __name__ == "__main__":
    unittest.main()
