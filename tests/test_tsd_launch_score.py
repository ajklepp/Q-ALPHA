"""Unit tests for UTS v2 LAUNCH phase scoring."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline.tsd_launch_score import (
    LAUNCH_SCAN_MAX,
    LAUNCH_SCORE_MIN,
    compute_launch_phase,
    compute_launch_score,
    enrich_launch_fields,
    is_launch_candidate,
    signal_bar_red,
)


WEAV_831 = {
    "symbol": "WEAV",
    "scan_score": 77.99,
    "trend_strength": 0.6697525755038307,
    "buy_signal": True,
    "early_bull": False,
    "close": 7.31,
    "open": 7.35,
    "wt_gap": 1.45,
}

ZIP_LAUNCH = {
    "symbol": "ZIP",
    "scan_score": 34.0,
    "trend_strength": 0.15592699830719084,
    "buy_signal": True,
    "early_bull": False,
    "close": 4.245,
    "open": 4.30,
    "wt_gap": 1.03,
}


class TestLaunchPhase(unittest.TestCase):
    def test_weav_831_is_extension(self):
        row = enrich_launch_fields(WEAV_831)
        self.assertEqual(row["phase"], "EXTENSION")
        self.assertFalse(is_launch_candidate(row))

    def test_zip_low_score_is_launch(self):
        row = enrich_launch_fields(ZIP_LAUNCH)
        self.assertEqual(row["phase"], "LAUNCH")
        self.assertGreaterEqual(row["launch_score"], LAUNCH_SCORE_MIN)
        self.assertLessEqual(row["scan_score"], LAUNCH_SCAN_MAX)
        self.assertTrue(is_launch_candidate(row))

    def test_extension_by_score_and_trend(self):
        row = enrich_launch_fields({
            "scan_score": 68,
            "trend_strength": 0.75,
            "buy_signal": True,
        })
        self.assertEqual(row["phase"], "EXTENSION")

    def test_signal_bar_red(self):
        self.assertTrue(signal_bar_red({"open": 10.0, "close": 9.5}))
        self.assertFalse(signal_bar_red({"open": 9.0, "close": 10.0}))

    def test_launch_score_components(self):
        base = {"scan_score": 35, "buy_signal": True, "early_bull": True,
                "open": 10.0, "close": 9.5}
        score = compute_launch_score(base)
        self.assertGreaterEqual(score, 70)


if __name__ == "__main__":
    unittest.main()
