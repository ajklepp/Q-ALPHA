"""UTS v2.6 — hours, 1H trigger, slot-then-size, idle_no_1r."""
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
from tsd_scan_pipeline.tsd_1h_signal import is_allowed_hour, is_launch_hour_window
from tsd_scan_pipeline.tsd_capacity import (
    MAX_FULL_SLOTS,
    can_enter,
    deploy_budget,
    slot_ladder,
)
from tsd_scan_pipeline.tsd_entry_gates import evaluate_entry_gates, is_entry_window
from tsd_scan_pipeline.tsd_structure import (
    apply_day_structure_rules,
    be_lock_price,
    maybe_arm_be_lock_on_1r,
    should_idle_no_1r,
)

ET = pytz.timezone("America/New_York")
LAUNCH_NOW = ET.localize(datetime(2026, 9, 1, 7, 5))

LAUNCH_ROW = {
    "symbol": "ZIP",
    "scan_score": 34.0,
    "trend_strength": 0.16,
    "buy_signal": False,
    "3h_buy_signal": False,
    "early_bull": False,
    "close": 5.25,
    "open": 5.30,
    "wt_gap": 5.0,
    "phase_3h": "NEUTRAL",
    "htf_range_20d_pct": 0.35,
    "htf_close_above_sma50": True,
    "htf_sma20_rising": True,
    "htf_1h_buy_signal": True,
    "htf_1h_bar_hour": 7,
    "htf_1h_close": 5.25,
}


class TestHoursAllowlist(unittest.TestCase):
    def test_0700_allowed(self):
        self.assertTrue(is_allowed_hour(7))
        self.assertTrue(is_launch_hour_window(LAUNCH_NOW))
        self.assertTrue(is_entry_window(LAUNCH_NOW))

    def test_0800_not_in_allowlist(self):
        self.assertFalse(is_allowed_hour(8))
        dt = ET.localize(datetime(2026, 9, 1, 8, 5))
        self.assertFalse(is_entry_window(dt))

    def test_1400_rejected(self):
        self.assertFalse(is_allowed_hour(14))
        dt = ET.localize(datetime(2026, 9, 1, 14, 0))
        self.assertFalse(is_entry_window(dt))

    def test_0400_rejected(self):
        self.assertFalse(is_allowed_hour(4))
        dt = ET.localize(datetime(2026, 9, 1, 4, 0))
        self.assertFalse(is_entry_window(dt))


class TestOneHTriggerNoThreeHBuy(unittest.TestCase):
    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value=set())
    def test_1h_buy_true_3h_buy_false(self, _occ):
        passed, gates, reasons = evaluate_entry_gates(
            LAUNCH_ROW, regime_bull=False, now=LAUNCH_NOW,
        )
        self.assertTrue(passed, msg=reasons)
        self.assertTrue(gates["htf_1h_buy"])
        self.assertTrue(gates["not_extension"])
        self.assertNotIn("buy_signal", gates)

    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value=set())
    def test_hour_08_rejected_at_gate(self, _occ):
        cand = {**LAUNCH_ROW, "htf_1h_bar_hour": 8}
        passed, gates, reasons = evaluate_entry_gates(
            cand, regime_bull=False, now=ET.localize(datetime(2026, 9, 1, 8, 5)),
        )
        self.assertFalse(passed)
        self.assertFalse(gates["hour_allowed"])
        self.assertTrue(any("hour_not_allowed" in r for r in reasons))


class TestKillUntil1R(unittest.TestCase):
    def _leg_and_trail(self, *, entry=10.0, kill_pct=0.1):
        trail = {
            "entry_price": entry,
            "kill_price": entry * (1 - kill_pct),
            "kill_pct": kill_pct,
            "trail_pct": 0.04,
            "trading_day": 1,
            "tranches": [
                {
                    "id": "T1",
                    "shares": 4,
                    "weight": 0.4,
                    "trigger_pct": 0.03,
                    "trigger_price": entry * 1.03,
                    "trail_pct": 0.04,
                    "trailing": False,
                    "run_high": 0.0,
                    "closed": False,
                },
            ],
        }
        leg = {
            "price": entry,
            "structure_stop": None,
            "one_r_locked": False,
            "trail": trail,
        }
        return leg, trail

    def test_pre_1r_structure_none(self):
        leg, trail = self._leg_and_trail()
        maybe_arm_be_lock_on_1r(leg, trail, quote_high=10.99)
        self.assertIsNone(leg.get("structure_stop"))

    def test_post_1r_be_lock(self):
        leg, trail = self._leg_and_trail()
        maybe_arm_be_lock_on_1r(leg, trail, quote_high=11.0)
        self.assertEqual(leg["structure_stop"], be_lock_price(10.0))
        self.assertTrue(leg["one_r_locked"])

    def test_day2_tighten_noop(self):
        leg, trail = self._leg_and_trail()
        trail["trading_day"] = 2
        apply_day_structure_rules(leg, trail)
        self.assertIsNone(leg.get("structure_stop"))


class TestIdleNo1R(unittest.TestCase):
    def _trail(self, day: int, *, trailing: bool = False):
        return {
            "trading_day": day,
            "one_r_locked": False,
            "entry_price": 10.0,
            "kill_price": 9.0,
            "kill_pct": 0.1,
            "trail_pct": 0.04,
            "tranches": [
                {
                    "id": "T1",
                    "shares": 4,
                    "weight": 0.4,
                    "trigger_pct": 0.03,
                    "trigger_price": 10.3,
                    "trail_pct": 0.04,
                    "trailing": trailing,
                    "run_high": 0.0,
                    "closed": False,
                },
            ],
        }

    def test_day6_never_1r_not_trailing(self):
        trail = self._trail(6)
        leg = {"one_r_locked": False}
        self.assertTrue(should_idle_no_1r(trail, leg))

    def test_day6_one_r_locked_no_idle(self):
        trail = self._trail(6)
        leg = {"one_r_locked": True}
        self.assertFalse(should_idle_no_1r(trail, leg))

    def test_day5_no_idle(self):
        self.assertFalse(should_idle_no_1r(self._trail(5), {"one_r_locked": False}))


class TestSlotThenSize(unittest.TestCase):
    def test_equity_1500(self):
        n, s = slot_ladder(1500)
        self.assertEqual(n, 5)
        self.assertEqual(s, 300.0)

    def test_equity_4000(self):
        n, s = slot_ladder(4000)
        self.assertEqual(n, 10)
        self.assertEqual(s, 400.0)

    def test_never_raise_s_before_10(self):
        n, s = slot_ladder(2700)
        self.assertEqual(n, 9)
        self.assertEqual(s, 300.0)

    def test_deploy_not_full_pool_over_2(self):
        budget = deploy_budget(equity=3000, cash=3000, open_count=0)
        self.assertLessEqual(budget, 300.0)
        self.assertNotAlmostEqual(budget, 1500.0)


class TestCapacityCaps(unittest.TestCase):
    def test_third_entry_same_hour_rejected(self):
        state = {"positions": [], "entries_this_scan": 2}
        ok, reason = can_enter(state, "AAA", is_addon=False, slot_cap=10)
        self.assertFalse(ok)
        self.assertEqual(reason, "scan_cap_2")

    def test_eleventh_concurrent_rejected(self):
        state = {
            "entries_this_scan": 0,
            "positions": [
                {"symbol": f"S{i}", "status": "OPEN", "t4_only": False}
                for i in range(10)
            ],
        }
        ok, reason = can_enter(state, "NEW", is_addon=False, slot_cap=10)
        self.assertFalse(ok)
        self.assertIn("slots_full", reason)

    def test_no_daily_cap(self):
        state = {
            "entries_this_scan": 0,
            "positions": [
                {
                    "symbol": "OLD",
                    "status": "CLOSED",
                    "t4_only": False,
                    "legs": [{"time": "2026-09-01T07:10:00-04:00"}],
                },
            ],
        }
        ok, reason = can_enter(state, "NEW", is_addon=False, slot_cap=10)
        self.assertTrue(ok)
        self.assertEqual(reason, "new")

    def test_slot_ceiling(self):
        self.assertEqual(MAX_FULL_SLOTS, 10)


if __name__ == "__main__":
    unittest.main()
