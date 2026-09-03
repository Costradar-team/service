from __future__ import annotations

import unittest
import pandas as pd
import numpy as np

from ml.scripts.price_signals import (
    calculate_drop_probability,
    classify_price_signal,
    estimate_item_volatilities,
    enrich_predictions_with_signals,
)


class PriceSignalsTest(unittest.TestCase):
    def test_drop_probability_symmetric_when_prices_equal(self):
        prob = calculate_drop_probability(2000.0, 2000.0, volatility=0.03)
        self.assertAlmostEqual(prob, 0.50, places=2)

    def test_drop_probability_increases_when_price_projected_to_fall(self):
        # Current = 2000, Predicted = 1900 (price falls -> drop probability should be high)
        prob = calculate_drop_probability(2000.0, 1900.0, volatility=0.03)
        self.assertGreater(prob, 0.70)
        self.assertLessEqual(prob, 0.99)

    def test_drop_probability_decreases_when_price_projected_to_rise(self):
        # Current = 2000, Predicted = 2100 (price rises -> drop probability should be low)
        prob = calculate_drop_probability(2000.0, 2100.0, volatility=0.03)
        self.assertLess(prob, 0.30)
        self.assertGreaterEqual(prob, 0.01)

    def test_signal_classification_buy(self):
        # Rising price: drop_prob is low (e.g. 0.20), change is positive (+5%)
        signal, msg = classify_price_signal(
            current_price=2000.0,
            predicted_price=2100.0,
            drop_probability=0.20,
            change_percent=5.0,
        )
        self.assertEqual(signal, "BUY")
        self.assertIn("구매", msg)

    def test_signal_classification_wait(self):
        # Falling price: drop_prob is high (e.g. 0.80), change is negative (-5%)
        signal, msg = classify_price_signal(
            current_price=2000.0,
            predicted_price=1900.0,
            drop_probability=0.80,
            change_percent=-5.0,
        )
        self.assertEqual(signal, "WAIT")
        self.assertIn("미루고", msg)

    def test_signal_classification_hold(self):
        # Flat price: drop_prob ~ 0.50, change ~ 0.2%
        signal, msg = classify_price_signal(
            current_price=2000.0,
            predicted_price=2004.0,
            drop_probability=0.48,
            change_percent=0.2,
        )
        self.assertEqual(signal, "HOLD")

    def test_estimate_item_volatilities(self):
        history = pd.DataFrame({
            "survey_date": ["2026-01-01", "2026-01-15", "2026-01-30", "2026-02-15"],
            "canonical_item": ["밀가루", "밀가루", "밀가루", "밀가루"],
            "median_unit_price": [2000.0, 2050.0, 2020.0, 2080.0],
        })
        vols = estimate_item_volatilities(history)
        self.assertIn("밀가루", vols)
        self.assertGreater(vols["밀가루"], 0.01)

    def test_enrich_predictions_with_signals(self):
        pred_df = pd.DataFrame([{
            "forecast_date": "2026-08-07",
            "as_of_date": "2026-07-24",
            "canonical_item": "밀가루",
            "subtype": "중력분",
            "current_median_unit_price": 2000.0,
            "model_predicted_unit_price": 2100.0,
            "model_predicted_change_percent": 5.0,
            "forecast_horizon_step": 4,
        }])
        enriched = enrich_predictions_with_signals(pred_df)
        self.assertNotIn("pred_low", enriched.columns)
        self.assertNotIn("pred_high", enriched.columns)
        self.assertIn("drop_probability", enriched.columns)
        self.assertIn("signal", enriched.columns)
        self.assertIn("signal_message", enriched.columns)
        self.assertEqual(enriched.iloc[0]["signal"], "BUY")
        self.assertLess(enriched.iloc[0]["drop_probability"], 0.40)
        self.assertIn("8주 뒤", enriched.iloc[0]["signal_message"])


if __name__ == "__main__":
    unittest.main()
