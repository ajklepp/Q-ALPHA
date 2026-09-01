"""Unit tests for TSD RTH structure stop (Layer 2)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline.tsd_structure import (
    BREAKEVEN_BUFFER_PCT,
    STRUCTURE_MAX_PCT,
    apply_day_structure_rules,
    compute_structure_stop,
    maybe_ratchet_breakeven,
    should_day3_force_exit,
    structure_stop_breached,
)


class TestComputeStructureStop(unittest.TestCase):
    def test_weav_example(self):
        """WEAV: entry=7.31, orb_low=7.22, kill=6.71, trail_pct=0.053932 → ~7.15–7.18."""
        stop, reason = compute_structure_stop(
            entry=7.31,
            orb_low=7.22,
            kill_price=6.71,
            trail_pct=0.053932,
            max_pct=STRUCTURE_MAX_PCT,
        )
        self.assertEqual(reason, "orb_structure")
        self.assertGreaterEqual(stop, 7.15)
        self.assertLessEqual(stop, 7.21)
        self.assertGreater(stop, 6.71)
        self.assertAlmostEqual(stop, 7.20, places=2)

    def test_stop_never_below_kill_buffer(self):
        stop, _ = compute_structure_stop(
            entry=10.0,
            orb_low=5.0,
            kill_price=9.0,
            trail_pct=0.10,
        )
        self.assertGreaterEqual(stop, 9.01)


class TestBreakevenRatchet(unittest.TestCase):
    def test_ratchet_at_half_r(self):
        leg = {
            "price": 10.0,
            "structure_stop": 9.5,
            "breakeven_locked": False,
        }
        trail = {
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
        changed = maybe_ratchet_breakeven(leg, trail, quote_high=10.6)
        self.assertTrue(changed)
        expected = round(10.0 * (1.0 - BREAKEVEN_BUFFER_PCT), 2)
        self.assertEqual(leg["structure_stop"], expected)
        self.assertTrue(leg["breakeven_locked"])


class TestDay2Tighten(unittest.TestCase):
    def test_tighten_when_eligible(self):
        leg = {
            "price": 10.0,
            "structure_stop": 9.5,
            "time": "2026-08-28T10:00:00-04:00",
        }
        trail = {
            "trading_day": 2,
            "entry_price": 10.0,
            "kill_price": 9.0,
            "kill_pct": 0.1,
            "trail_pct": 0.04,
            "opened_at": "2026-08-28T10:00:00-04:00",
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
        import pytz
        from datetime import datetime

        now = pytz.timezone("America/New_York").localize(datetime(2026, 9, 1, 10, 0))
        apply_day_structure_rules(leg, trail, now=now)
        self.assertEqual(leg["structure_stop"], 9.9)
        self.assertEqual(leg["structure_stop_reason"], "day2_tighten")

    def test_skip_first_session_after_entry(self):
        leg = {
            "price": 12.56,
            "structure_stop": 12.37,
            "time": "2026-08-31T15:26:05-04:00",
        }
        trail = {
            "trading_day": 2,
            "entry_price": 12.56,
            "kill_price": 10.82,
            "kill_pct": 0.1,
            "trail_pct": 0.04,
            "opened_at": "2026-08-31T15:26:05-04:00",
            "tranches": [
                {
                    "id": "T1",
                    "shares": 4,
                    "weight": 0.4,
                    "trigger_pct": 0.03,
                    "trigger_price": 12.9,
                    "trail_pct": 0.04,
                    "trailing": False,
                    "run_high": 0.0,
                    "closed": False,
                },
            ],
        }
        import pytz
        from datetime import datetime

        now = pytz.timezone("America/New_York").localize(datetime(2026, 9, 1, 9, 40))
        apply_day_structure_rules(leg, trail, now=now)
        self.assertEqual(leg["structure_stop"], 12.37)


class TestStructureExit(unittest.TestCase):
    def test_breach_on_low(self):
        self.assertTrue(structure_stop_breached(7.10, 7.16))
        self.assertFalse(structure_stop_breached(7.20, 7.16))

    def test_day3_force_when_no_trail(self):
        trail = {
            "trading_day": 3,
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
        self.assertTrue(should_day3_force_exit(trail))


if __name__ == "__main__":
    unittest.main()
