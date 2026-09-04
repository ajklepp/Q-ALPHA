"""Unit tests for UTS v2.6 1H LAUNCH entry gates."""
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
ENTRY_NOW = ET.localize(datetime(2026, 9, 1, 7, 5))

ZIP_LAUNCH = {
    "symbol": "ZIP",
    "scan_score": 34.0,
    "trend_strength": 0.16,
    "buy_signal": True,
    "early_bull": False,
    "close": 5.25,
    "open": 5.30,
    "wt_gap": 5.0,
    "htf_range_20d_pct": 0.35,
    "htf_close_above_sma50": True,
    "htf_sma20_rising": True,
    "htf_1h_buy_signal": True,
    "htf_1h_bar_hour": 7,
    "htf_1h_close": 5.25,
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
    "htf_range_20d_pct": 0.35,
    "htf_close_above_sma50": True,
    "htf_sma20_rising": True,
    "htf_1h_buy_signal": True,
    "htf_1h_bar_hour": 7,
}


class TestEntryWindow(unittest.TestCase):
    def test_0700_allowed(self):
        dt = ET.localize(datetime(2026, 9, 1, 7, 0))
        self.assertTrue(is_entry_window(dt))

    def test_0800_allowed(self):
        dt = ET.localize(datetime(2026, 9, 1, 8, 15))
        self.assertTrue(is_entry_window(dt))

    def test_0500_allowed(self):
        dt = ET.localize(datetime(2026, 9, 1, 5, 15))
        self.assertTrue(is_entry_window(dt))

    def test_at_1500_allowed(self):
        dt = ET.localize(datetime(2026, 9, 1, 15, 0))
        self.assertTrue(is_entry_window(dt))

    def test_1400_allowed(self):
        dt = ET.localize(datetime(2026, 9, 1, 14, 0))
        self.assertTrue(is_entry_window(dt))

    def test_1600_blocked(self):
        dt = ET.localize(datetime(2026, 9, 1, 16, 0))
        self.assertFalse(is_entry_window(dt))

    def test_0400_blocked(self):
        dt = ET.localize(datetime(2026, 9, 1, 4, 0))
        self.assertFalse(is_entry_window(dt))


class TestEvaluateGates(unittest.TestCase):
    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value=set())
    def test_launch_passes(self, _occ):
        passed, gates, reasons = evaluate_entry_gates(
            ZIP_LAUNCH, regime_bull=False, now=ENTRY_NOW,
        )
        self.assertTrue(passed, msg=reasons)
        self.assertTrue(gates["htf_1h_buy"])
        self.assertTrue(gates["launch_candidate"])
        self.assertTrue(gates["trigger"])
        self.assertTrue(gates["htf_daily"])
        self.assertEqual(reasons, [])

    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value=set())
    def test_rejects_extension_weav(self, _occ):
        passed, gates, reasons = evaluate_entry_gates(
            WEAV_EXT, regime_bull=True, now=ENTRY_NOW,
        )
        self.assertFalse(passed)
        self.assertFalse(gates["not_extension"])
        self.assertIn("extension_hard", reasons)

    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value=set())
    def test_bear_regime_does_not_veto(self, _occ):
        passed, gates, _ = evaluate_entry_gates(
            ZIP_LAUNCH, regime_bull=False, now=ENTRY_NOW,
        )
        self.assertTrue(passed)
        self.assertTrue(gates["regime_context"])

    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value=set())
    def test_green_signal_bar_still_passes(self, _occ):
        """Color is soft — yellow/green no longer hard-veto."""
        cand = {**ZIP_LAUNCH, "open": 5.0, "close": 5.5, "high": 5.6, "low": 4.9, "htf_1h_close": 5.5}
        passed, gates, reasons = evaluate_entry_gates(cand, regime_bull=True, now=ENTRY_NOW)
        self.assertTrue(passed, msg=reasons)
        self.assertFalse(gates["signal_bar_red"])
        self.assertNotIn("signal_bar_not_red", reasons)

    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value=set())
    def test_rejects_htf_fail(self, _occ):
        cand = {**ZIP_LAUNCH, "htf_close_above_sma50": False}
        passed, gates, reasons = evaluate_entry_gates(cand, regime_bull=True, now=ENTRY_NOW)
        self.assertFalse(passed)
        self.assertFalse(gates["htf_daily"])
        self.assertTrue(any("sma50" in r for r in reasons))

    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value={"WEAV"})
    def test_rejects_cross_book(self, _occ):
        passed, gates, reasons = evaluate_entry_gates(
            WEAV_EXT, regime_bull=True, now=ENTRY_NOW,
        )
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
