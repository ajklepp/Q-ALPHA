"""Unit tests for TSD kill-until-1R structure stop (Phase 2.5) + live flag."""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))
sys.path.insert(0, str(ROOT / "strategy_lab"))

from tsd_scan_pipeline.tsd_keep_profit import init_php_trail_state
from tsd_scan_pipeline.tsd_structure import (
    BE_LOCK_PCT,
    apply_day_structure_rules,
    be_lock_price,
    clear_structure_stop_fields,
    maybe_arm_be_lock_on_1r,
    maybe_lock_profit_via_trail,
    maybe_ratchet_breakeven,
    one_r_price,
    should_exit_on_structure_stop,
    should_thesis_fail_exit,
    structure_stop_breached,
    structure_stop_exits_enabled,
)
from tsd_scan_pipeline.tsd_trail import evaluate_trail_tick, remaining_shares


def _flag(value: str):
    return patch.dict(os.environ, {"PHP_STRUCTURE_STOP_EXITS": value}, clear=False)


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
            "structure_stop_reason": None,
            "one_r_locked": False,
            "breakeven_locked": False,
            "trail": trail,
        }
        return leg, trail

    def test_pre_1r_structure_stop_none(self):
        leg, trail = self._leg_and_trail()
        target = one_r_price(10.0, 0.1)
        self.assertAlmostEqual(target, 11.0)
        changed = maybe_arm_be_lock_on_1r(leg, trail, quote_high=10.99)
        self.assertFalse(changed)
        self.assertIsNone(leg.get("structure_stop"))

    @_flag("1")
    def test_post_1r_be_lock_set(self):
        self.assertTrue(structure_stop_exits_enabled())
        leg, trail = self._leg_and_trail()
        changed = maybe_arm_be_lock_on_1r(leg, trail, quote_high=11.0)
        self.assertTrue(changed)
        expected = be_lock_price(10.0)
        self.assertAlmostEqual(expected, round(10.0 * (1 - BE_LOCK_PCT), 2))
        self.assertEqual(leg["structure_stop"], expected)
        self.assertEqual(leg["structure_stop_reason"], "be_lock_1r")
        self.assertTrue(leg["one_r_locked"])

    def test_day2_tighten_disabled(self):
        leg, trail = self._leg_and_trail()
        leg["structure_stop"] = None
        leg["time"] = "2026-08-28T10:00:00-04:00"
        trail["trading_day"] = 2
        trail["opened_at"] = "2026-08-28T10:00:00-04:00"
        apply_day_structure_rules(leg, trail)
        self.assertIsNone(leg.get("structure_stop"))
        self.assertNotEqual(leg.get("structure_stop_reason"), "day2_tighten")

    def test_day2_tighten_never_raises_toward_99pct(self):
        leg, trail = self._leg_and_trail(entry=10.0)
        leg["structure_stop"] = be_lock_price(10.0)
        leg["one_r_locked"] = True
        leg["time"] = "2026-08-29T10:00:00-04:00"
        trail["trading_day"] = 2
        trail["opened_at"] = "2026-08-28T10:00:00-04:00"
        apply_day_structure_rules(leg, trail)
        self.assertNotEqual(leg["structure_stop"], 9.9)

    def test_structure_breach_only_when_set(self):
        self.assertFalse(structure_stop_breached(7.10, None))
        self.assertTrue(structure_stop_breached(7.10, 7.16))

    def test_idle_no_1r_day6(self):
        trail = {
            "trading_day": 6,
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
                    "trailing": False,
                    "run_high": 0.0,
                    "closed": False,
                },
            ],
        }
        self.assertTrue(should_thesis_fail_exit(trail))

        trail["trading_day"] = 5
        self.assertFalse(should_thesis_fail_exit(trail))


class TestStructureStopExitFlag(unittest.TestCase):
    """Live Peak Hour default: PHP_STRUCTURE_STOP_EXITS off — no BE dumps."""

    def _leg_and_trail(self, *, entry=10.0, kill_pct=0.1):
        trail = {
            "entry_price": entry,
            "kill_price": entry * (1 - kill_pct),
            "kill_pct": kill_pct,
            "trail_pct": 0.04,
            "peak_high": entry,
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
            "structure_stop_reason": None,
            "one_r_locked": False,
            "breakeven_locked": False,
            "trail": trail,
        }
        return leg, trail

    @_flag("0")
    def test_default_flag_off(self):
        self.assertFalse(structure_stop_exits_enabled())

    @_flag("0")
    def test_flag_off_plus_1r_does_not_arm_structure_stop(self):
        leg, trail = self._leg_and_trail()
        changed = maybe_arm_be_lock_on_1r(leg, trail, quote_high=11.0)
        self.assertTrue(changed)
        self.assertTrue(leg["one_r_locked"])
        self.assertIsNone(leg.get("structure_stop"))
        self.assertNotEqual(leg.get("structure_stop_reason"), "be_lock_1r")
        self.assertFalse(leg.get("breakeven_locked"))

    @_flag("0")
    def test_flag_off_stale_structure_stop_does_not_close(self):
        leg, trail = self._leg_and_trail()
        leg["structure_stop"] = be_lock_price(10.0)
        trail["structure_stop"] = be_lock_price(10.0)
        leg["structure_stop_reason"] = "be_lock_1r"
        self.assertTrue(structure_stop_breached(9.90, leg["structure_stop"]))
        self.assertFalse(should_exit_on_structure_stop(leg, trail, 9.90))

    @_flag("1")
    def test_flag_on_stale_structure_stop_closes(self):
        self.assertTrue(structure_stop_exits_enabled())
        leg, trail = self._leg_and_trail()
        leg["structure_stop"] = be_lock_price(10.0)
        trail["structure_stop"] = be_lock_price(10.0)
        self.assertTrue(should_exit_on_structure_stop(leg, trail, 9.90))

    @_flag("0")
    def test_flag_off_ratchet_is_noop(self):
        leg, trail = self._leg_and_trail()
        leg["one_r_locked"] = True
        trail["one_r_locked"] = True
        self.assertFalse(maybe_ratchet_breakeven(leg, trail, quote_high=12.0))
        self.assertIsNone(leg.get("structure_stop"))

    @_flag("1")
    def test_flag_on_ratchet_raises_structure_stop(self):
        leg, trail = self._leg_and_trail()
        maybe_arm_be_lock_on_1r(leg, trail, quote_high=11.0)
        self.assertEqual(leg["structure_stop"], be_lock_price(10.0))
        # Force a lower stop then ratchet so the raise is observable.
        leg["structure_stop"] = 9.50
        trail["structure_stop"] = 9.50
        changed = maybe_ratchet_breakeven(leg, trail, quote_high=10.5)
        self.assertTrue(changed)
        self.assertEqual(leg["structure_stop_reason"], "breakeven_ratchet")
        self.assertGreater(leg["structure_stop"], 9.50)

    @_flag("0")
    def test_flag_off_kill_trail_still_closes(self):
        """Emergency kill path is independent of the structure BE flag."""
        trail = init_php_trail_state(10.0, 20, kill_pct=0.05)
        self.assertAlmostEqual(trail["kill_price"], 9.5)
        trail, exits = evaluate_trail_tick(
            trail, high=10.10, low=9.40, close=9.45, when="kill",
        )
        self.assertTrue(any(e["reason"] == "kill" for e in exits))
        self.assertEqual(remaining_shares(trail), 0)

    @_flag("0")
    def test_flag_off_lock_profit_ratchets_kill_up_only(self):
        leg, trail = self._leg_and_trail(entry=10.0, kill_pct=0.05)
        # MFE +4% (above 3.5% default) — should arm tighter trail and raise kill.
        changed = maybe_lock_profit_via_trail(
            leg, trail, quote_high=10.40, quote_last=10.30,
        )
        self.assertTrue(changed)
        self.assertGreater(float(trail["kill_price"]), 9.50)
        self.assertLess(float(trail["kill_price"]), 10.30)
        self.assertTrue(trail.get("lock_profit_armed"))
        for t in trail["tranches"]:
            self.assertLessEqual(float(t["trail_pct"]), 0.02 + 1e-12)

    @_flag("0")
    def test_lock_profit_never_lowers_kill(self):
        leg, trail = self._leg_and_trail(entry=10.0, kill_pct=0.01)
        trail["kill_price"] = 9.97  # already near BE
        prior = trail["kill_price"]
        maybe_lock_profit_via_trail(
            leg, trail, quote_high=10.40, quote_last=10.00,
        )
        self.assertGreaterEqual(float(trail["kill_price"]), prior)

    @_flag("0")
    def test_lock_profit_does_not_remove_kill(self):
        leg, trail = self._leg_and_trail(entry=10.0, kill_pct=0.05)
        maybe_lock_profit_via_trail(leg, trail, quote_high=10.10, quote_last=10.05)
        self.assertGreater(float(trail["kill_price"]), 0)

    @_flag("1")
    def test_lock_profit_skipped_when_structure_exits_on(self):
        leg, trail = self._leg_and_trail(entry=10.0, kill_pct=0.05)
        changed = maybe_lock_profit_via_trail(
            leg, trail, quote_high=10.40, quote_last=10.30,
        )
        self.assertFalse(changed)
        self.assertAlmostEqual(float(trail["kill_price"]), 9.50)

    @_flag("0")
    def test_clear_structure_stop_keeps_kill(self):
        leg, trail = self._leg_and_trail()
        leg["structure_stop"] = 9.97
        trail["structure_stop"] = 9.97
        leg["kill_order_id"] = 4242
        trail_out = clear_structure_stop_fields(leg, trail)
        self.assertIsNone(leg.get("structure_stop"))
        self.assertIsNone(trail_out.get("structure_stop"))
        self.assertEqual(leg["kill_order_id"], 4242)
        self.assertAlmostEqual(float(trail_out["kill_price"]), 9.0)


class TestTrailMonitorCallSite(unittest.TestCase):
    def test_monitor_gates_structure_stop_exit(self):
        src = (
            ROOT / "candidates" / "tsd_scan_pipeline" / "tsd_trail_monitor.py"
        ).read_text(encoding="utf-8")
        self.assertIn("should_exit_on_structure_stop", src)
        self.assertIn("reason=\"structure_stop\"", src)
        self.assertIn("maybe_lock_profit_via_trail", src)
        # MT3 remains imported for paper/shadow comparison only (not live banks).
        self.assertIn("tsd_shadow_multi_target", src)


if __name__ == "__main__":
    unittest.main()
