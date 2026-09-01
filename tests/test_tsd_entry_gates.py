"""Unit tests for UTS v2 Phase 1 entry gates."""
from __future__ import annotations

import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

import pytz
from tsd_scan_pipeline.tsd_entry_gates import (
    SCAN_SCORE_ENTRY_MIN,
    WT_GAP_MIN,
    evaluate_entry_gates,
    infer_signal_lane,
    is_entry_window,
    leg_eligible_for_day2_tighten,
)

ET = pytz.timezone("America/New_York")


class TestEntryWindow(unittest.TestCase):
    def test_inside_rth_window(self):
        dt = ET.localize(datetime(2026, 9, 1, 10, 0))
        self.assertTrue(is_entry_window(dt))

    def test_before_0935(self):
        dt = ET.localize(datetime(2026, 9, 1, 9, 20))
        self.assertFalse(is_entry_window(dt))

    def test_after_1400(self):
        dt = ET.localize(datetime(2026, 9, 1, 14, 30))
        self.assertFalse(is_entry_window(dt))


class TestEvaluateGates(unittest.TestCase):
    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value=set())
    def test_passes_all_core_gates(self, _occ):
        cand = {"symbol": "WEAV", "scan_score": 75, "wt_gap": 5.0, "close": 7.31}
        passed, gates, reasons = evaluate_entry_gates(cand, regime_bull=True)
        self.assertTrue(passed)
        self.assertTrue(gates["scan_score"])
        self.assertTrue(gates["wt_gap"])
        self.assertEqual(reasons, [])

    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value=set())
    def test_rejects_low_score(self, _occ):
        cand = {"symbol": "X", "scan_score": 65, "wt_gap": 5.0}
        passed, _, reasons = evaluate_entry_gates(cand, regime_bull=True)
        self.assertFalse(passed)
        self.assertTrue(any("scan_score" in r for r in reasons))

    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value={"WEAV"})
    def test_rejects_cross_book(self, _occ):
        cand = {"symbol": "WEAV", "scan_score": 80, "wt_gap": 5.0}
        passed, gates, reasons = evaluate_entry_gates(cand, regime_bull=True)
        self.assertFalse(passed)
        self.assertFalse(gates["dedup"])
        self.assertIn("cross_book_occupied", reasons)

    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value=set())
    def test_rejects_bear_regime(self, _occ):
        cand = {"symbol": "X", "scan_score": 80, "wt_gap": 5.0}
        passed, gates, _ = evaluate_entry_gates(cand, regime_bull=False)
        self.assertFalse(passed)
        self.assertFalse(gates["regime"])


class TestSignalLane(unittest.TestCase):
    def test_lane_a_fresh_cross(self):
        self.assertEqual(infer_signal_lane({"buy_signal": True, "wt_gap": 5.0}), "A")

    def test_lane_b_swing(self):
        self.assertEqual(infer_signal_lane({"buy_signal": False, "wt_gap": 20.0}), "B")


class TestDay2Eligibility(unittest.TestCase):
    def test_skip_first_session_after_entry(self):
        leg = {"time": "2026-08-31T15:26:05-04:00", "price": 12.56}
        trail = {"trading_day": 2, "opened_at": "2026-08-31T15:26:05-04:00"}
        now = ET.localize(datetime(2026, 9, 1, 10, 0))
        self.assertFalse(leg_eligible_for_day2_tighten(leg, trail, now=now))

    def test_allow_after_two_sessions(self):
        leg = {"time": "2026-08-29T10:00:00-04:00", "price": 12.56}
        trail = {"trading_day": 2, "opened_at": "2026-08-29T10:00:00-04:00"}
        now = ET.localize(datetime(2026, 9, 1, 10, 0))
        self.assertTrue(leg_eligible_for_day2_tighten(leg, trail, now=now))


if __name__ == "__main__":
    unittest.main()
