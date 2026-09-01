"""Unit tests for UTS v2 Phase 3 setup confirmation."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

from setup_watch_confirmation import (
    SessionQuote,
    compute_rvol,
    confirm_lane_a,
    confirm_lane_b,
    confirm_setup,
)


def _lane_b_quote(**kwargs) -> SessionQuote:
    base = dict(
        price=7.50,
        low=7.35,
        high=7.55,
        session_open=7.20,
        vwap=7.32,
        orb_high=7.40,
        orb_low=7.22,
        rvol=1.2,
        was_below_vwap=True,
        minutes_since_open=20,
    )
    base.update(kwargs)
    return SessionQuote(**base)


class TestComputeRvol(unittest.TestCase):
    def test_above_threshold(self):
        r = compute_rvol(session_volume=500_000, avg_daily_volume=2_000_000, minutes_since_open=60)
        self.assertGreaterEqual(r, 0.8)


class TestLaneB(unittest.TestCase):
    def test_confirms_orb_break(self):
        q = _lane_b_quote(price=7.50, orb_high=7.40)
        ok, reason = confirm_lane_b(q, cross_level=7.31)
        self.assertTrue(ok)
        self.assertIn("orb_break", reason)

    def test_rejects_below_cross(self):
        q = _lane_b_quote(price=7.20)
        ok, reason = confirm_lane_b(q, cross_level=7.31)
        self.assertFalse(ok)
        self.assertEqual(reason, "below_cross_level")

    def test_rejects_low_rvol(self):
        q = _lane_b_quote(rvol=0.5)
        ok, reason = confirm_lane_b(q, cross_level=7.31)
        self.assertFalse(ok)
        self.assertTrue(reason.startswith("rvol_"))


class TestLaneA(unittest.TestCase):
    def test_confirms_gap_style(self):
        q = SessionQuote(
            price=10.50,
            low=10.20,
            high=10.55,
            session_open=10.00,
            vwap=10.20,
            orb_high=10.30,
            orb_low=9.90,
            rvol=1.0,
            up_vol=200_000,
            dn_vol=80_000,
            first_candle_low=9.95,
            first_candle_high=10.30,
            minutes_since_open=10,
            prev_close=10.00,
        )
        ok, reason = confirm_lane_a(q, cross_level=10.00)
        self.assertTrue(ok)
        self.assertEqual(reason, "lane_a_confirmed")


class TestConfirmSetup(unittest.TestCase):
    def test_lane_b_row(self):
        row = {"signal_lane": "B", "cross_level": 7.31}
        ok, _ = confirm_setup(row, _lane_b_quote())
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
