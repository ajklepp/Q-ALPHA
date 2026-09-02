"""Unit tests for UTS v2 Phase 2.5 strict HTF entry gates."""
from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

import pytz
from tsd_scan_pipeline.tsd_entry_gates import (
    evaluate_entry_gates,
    infer_signal_lane,
    is_entry_window,
    leg_eligible_for_day2_tighten,
)

ET = pytz.timezone("America/New_York")
ENTRY_NOW = ET.localize(datetime(2026, 9, 1, 10, 0))

ZIP_LAUNCH = {
    "symbol": "ZIP",
    "scan_score": 34.0,
    "trend_strength": 0.16,
    "buy_signal": True,
    "early_bull": False,
    "close": 4.245,
    "open": 4.30,
    "wt_gap": 5.0,
    "htf_range_20d_pct": 0.35,
    "htf_close_above_sma50": True,
    "htf_sma20_rising": True,
    "htf_1h_buy_signal": True,
}

WEAV_EXT = {
    "symbol": "WEAV",
    "scan_score": 77.99,
    "trend_strength": 0.67,
    "buy_signal": True,
    "early_bull": False,
    "close": 7.31,
    "open": 7.35,
    "wt_gap": 5.0,
}


class TestEntryWindow(unittest.TestCase):
    def test_inside_rth_window(self):
        dt = ET.localize(datetime(2026, 9, 1, 10, 0))
        self.assertTrue(is_entry_window(dt))

    def test_before_0935(self):
        dt = ET.localize(datetime(2026, 9, 1, 9, 20))
        self.assertFalse(is_entry_window(dt))

    def test_at_1500_blocked(self):
        dt = ET.localize(datetime(2026, 9, 1, 15, 0))
        self.assertFalse(is_entry_window(dt))

    def test_1459_allowed(self):
        dt = ET.localize(datetime(2026, 9, 1, 14, 59))
        self.assertTrue(is_entry_window(dt))


class TestEvaluateGates(unittest.TestCase):
    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value=set())
    def test_launch_passes(self, _occ):
        passed, gates, reasons = evaluate_entry_gates(
            ZIP_LAUNCH, regime_bull=False, now=ENTRY_NOW,
        )
        self.assertTrue(passed, msg=reasons)
        self.assertTrue(gates["buy_signal"])
        self.assertTrue(gates["launch_candidate"])
        self.assertTrue(gates["signal_bar_red"])
        self.assertTrue(gates["htf_daily"])
        self.assertEqual(reasons, [])

    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value=set())
    def test_rejects_extension_weav(self, _occ):
        passed, gates, reasons = evaluate_entry_gates(WEAV_EXT, regime_bull=True)
        self.assertFalse(passed)
        self.assertFalse(gates["not_extension"])
        self.assertIn("extension_phase", reasons)

    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value=set())
    def test_bear_regime_does_not_veto(self, _occ):
        passed, gates, _ = evaluate_entry_gates(
            ZIP_LAUNCH, regime_bull=False, now=ENTRY_NOW,
        )
        self.assertTrue(passed)
        self.assertTrue(gates["regime_context"])

    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value=set())
    def test_rejects_green_signal_bar(self, _occ):
        cand = {**ZIP_LAUNCH, "open": 4.0, "close": 4.5}
        passed, gates, reasons = evaluate_entry_gates(cand, regime_bull=True)
        self.assertFalse(passed)
        self.assertFalse(gates["signal_bar_red"])
        self.assertIn("signal_bar_not_red", reasons)

    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value=set())
    def test_rejects_htf_fail(self, _occ):
        cand = {**ZIP_LAUNCH, "htf_close_above_sma50": False}
        passed, gates, reasons = evaluate_entry_gates(cand, regime_bull=True)
        self.assertFalse(passed)
        self.assertFalse(gates["htf_daily"])
        self.assertTrue(any("sma50" in r for r in reasons))

    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value={"WEAV"})
    def test_rejects_cross_book(self, _occ):
        passed, gates, reasons = evaluate_entry_gates(WEAV_EXT, regime_bull=True)
        self.assertFalse(passed)
        self.assertFalse(gates["dedup"])
        self.assertIn("cross_book_occupied", reasons)


class TestSignalLane(unittest.TestCase):
    def test_lane_b_only(self):
        self.assertEqual(infer_signal_lane(ZIP_LAUNCH), "B")


class TestDay2Eligibility(unittest.TestCase):
    def test_day2_tighten_disabled(self):
        leg = {"time": "2026-08-29T10:00:00-04:00", "price": 12.56}
        trail = {"trading_day": 2, "opened_at": "2026-08-29T10:00:00-04:00"}
        now = ET.localize(datetime(2026, 9, 1, 10, 0))
        self.assertFalse(leg_eligible_for_day2_tighten(leg, trail, now=now))


if __name__ == "__main__":
    unittest.main()
