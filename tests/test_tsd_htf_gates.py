"""Unit tests for UTS v2 Phase 2.5 HTF daily gates + continuous rank."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline.tsd_htf_gates import (
    HTF_RANGE_20D_MIN,
    compute_combined_rank_score,
    compute_htf_metrics,
    compute_htf_rank_score,
    evaluate_htf_daily_gates,
)


def _daily_series(n: int = 60, *, base: float = 80.0, drift: float = 0.01):
    closes, highs, lows = [], [], []
    price = base
    for i in range(n):
        swing = 0.03 if i % 5 == 0 else 0.01
        highs.append(price * (1 + swing))
        lows.append(price * (1 - swing))
        closes.append(price)
        price *= 1 + drift
    return closes, highs, lows


class TestHtfGates(unittest.TestCase):
    def test_metrics_pass_typical_uptrend(self):
        closes, highs, lows = _daily_series()
        m = compute_htf_metrics(closes, highs, lows)
        self.assertFalse(m["insufficient_bars"])
        self.assertGreaterEqual(m["range_20d_pct"], HTF_RANGE_20D_MIN)
        self.assertTrue(m["close_above_sma50"])
        self.assertTrue(m["sma20_rising"])
        self.assertIsNotNone(m.get("dist_sma50_pct"))

    def test_evaluate_from_enriched_row(self):
        row = {
            "symbol": "ZIP",
            "htf_range_20d_pct": 0.35,
            "htf_close_above_sma50": True,
            "htf_sma20_rising": True,
            "htf_dist_sma50_pct": 0.05,
            "launch_score": 70,
        }
        passed, gates, reasons, score = evaluate_htf_daily_gates(row)
        self.assertTrue(passed)
        self.assertEqual(reasons, [])
        self.assertGreater(score, 0)
        self.assertLess(score, 99.9)  # continuous, not flat 99.9
        row["htf_score"] = score
        self.assertAlmostEqual(compute_combined_rank_score(row), 70 + score, places=1)

    def test_continuous_scores_spread_by_range(self):
        low = {
            "symbol": "A",
            "htf_range_20d_pct": 0.30,
            "htf_close_above_sma50": True,
            "htf_sma20_rising": True,
            "htf_dist_sma50_pct": 0.02,
        }
        high = {
            "symbol": "B",
            "htf_range_20d_pct": 0.70,
            "htf_close_above_sma50": True,
            "htf_sma20_rising": True,
            "htf_dist_sma50_pct": 0.02,
        }
        _, _, _, s_low = evaluate_htf_daily_gates(low)
        _, _, _, s_high = evaluate_htf_daily_gates(high)
        self.assertNotAlmostEqual(s_low, s_high)
        self.assertGreater(s_high, s_low)
        self.assertGreater(compute_htf_rank_score(high), compute_htf_rank_score(low))


if __name__ == "__main__":
    unittest.main()
