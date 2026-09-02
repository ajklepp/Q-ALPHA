"""Unit tests for TSD kill-until-1R structure stop (Phase 2.5)."""
import asyncio
import sys
import unittest
from pathlib import Path

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline.tsd_structure import (
    BE_LOCK_PCT,
    apply_day_structure_rules,
    be_lock_price,
    maybe_arm_be_lock_on_1r,
    one_r_price,
    should_thesis_fail_exit,
    structure_stop_breached,
)


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

    def test_post_1r_be_lock_set(self):
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

    def test_thesis_fail_day5(self):
        trail = {
            "trading_day": 5,
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

        trail["trading_day"] = 4
        self.assertFalse(should_thesis_fail_exit(trail))


if __name__ == "__main__":
    unittest.main()
