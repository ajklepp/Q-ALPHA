"""LIVE Peak Hour: structure_stop / be_lock_1r gated off; kill + trail remain."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))
sys.path.insert(0, str(ROOT / "strategy_lab"))

from tsd_scan_pipeline.tsd_exit import kill_stop_needs_ratchet  # noqa: E402
from tsd_scan_pipeline.tsd_keep_profit import (  # noqa: E402
    init_php_trail_state,
    php_process_bar,
)
from tsd_scan_pipeline.tsd_structure import (  # noqa: E402
    LIVE_STRUCTURE_STOP_ENV,
    STRUCTURE_DISARM_REASON,
    apply_live_structure_policy,
    be_lock_price,
    disarm_live_structure_stop,
    live_structure_stop_enabled,
    mark_one_r_if_touched,
    maybe_arm_be_lock_on_1r,
    maybe_lock_profit_via_trail,
    should_fire_live_structure_stop,
    should_idle_no_1r,
    structure_stop_breached,
)
from tsd_scan_pipeline.tsd_trail import remaining_shares  # noqa: E402
from tsd_scan_pipeline import tsd_trail_monitor  # noqa: E402


def _clear_flag() -> None:
    os.environ.pop(LIVE_STRUCTURE_STOP_ENV, None)
    os.environ.pop("PHP_STRUCTURE_STOP_EXITS", None)


class TestLiveStructureFlag(unittest.TestCase):
    def tearDown(self) -> None:
        _clear_flag()

    def test_default_off(self):
        _clear_flag()
        self.assertFalse(live_structure_stop_enabled())

    def test_explicit_zero_off(self):
        os.environ[LIVE_STRUCTURE_STOP_ENV] = "0"
        self.assertFalse(live_structure_stop_enabled())

    def test_on_values(self):
        for raw in ("1", "true", "YES", "on"):
            os.environ[LIVE_STRUCTURE_STOP_ENV] = raw
            self.assertTrue(live_structure_stop_enabled(), raw)


class TestOpenMigrationAndFireGate(unittest.TestCase):
    def tearDown(self) -> None:
        _clear_flag()

    def _armed_leg(self, *, entry: float = 10.0, kill_pct: float = 0.05):
        trail = init_php_trail_state(entry, 20, kill_pct=kill_pct)
        be = be_lock_price(entry)
        trail["structure_stop"] = be
        trail["one_r_locked"] = True
        trail["breakeven_locked"] = True
        trail["kill_pct"] = kill_pct
        leg = {
            "price": entry,
            "shares": 20,
            "status": "OPEN",
            "rth_armed": True,
            "structure_stop": be,
            "structure_stop_reason": "be_lock_1r",
            "one_r_locked": True,
            "breakeven_locked": True,
            "trail": trail,
        }
        return leg, trail

    def test_disarm_clears_be_lock_and_keeps_one_r(self):
        leg, trail = self._armed_leg()
        self.assertTrue(disarm_live_structure_stop(leg, trail))
        self.assertIsNone(leg.get("structure_stop"))
        self.assertIsNone(trail.get("structure_stop"))
        self.assertEqual(leg.get("structure_stop_reason"), STRUCTURE_DISARM_REASON)
        self.assertEqual(leg.get("structure_disarmed_from"), "be_lock_1r")
        self.assertTrue(leg.get("one_r_locked"))
        self.assertFalse(leg.get("breakeven_locked"))
        self.assertFalse(disarm_live_structure_stop(leg, trail))

    def test_flag_off_does_not_fire_leftover_structure_stop(self):
        _clear_flag()
        leg, trail = self._armed_leg()
        apply_live_structure_policy(leg, trail, quote_high=11.0)
        be = be_lock_price(10.0)
        self.assertTrue(structure_stop_breached(be - 0.02, be))
        self.assertFalse(
            should_fire_live_structure_stop(be - 0.02, leg.get("structure_stop"))
        )
        self.assertIsNone(leg.get("structure_stop"))

    def test_flag_on_still_fires_structure_stop(self):
        os.environ[LIVE_STRUCTURE_STOP_ENV] = "1"
        leg, trail = self._armed_leg()
        apply_live_structure_policy(leg, trail, quote_high=11.0)
        be = float(leg["structure_stop"])
        self.assertTrue(should_fire_live_structure_stop(be - 0.02, be))

    def test_flag_off_marks_one_r_without_arming_be_lock(self):
        _clear_flag()
        trail = init_php_trail_state(10.0, 20, kill_pct=0.05)
        trail["kill_pct"] = 0.05
        leg = {
            "price": 10.0,
            "structure_stop": None,
            "one_r_locked": False,
            "trail": trail,
        }
        policy = apply_live_structure_policy(leg, trail, quote_high=10.60)
        self.assertTrue(policy["marked_one_r"])
        self.assertFalse(policy["armed_be_lock"])
        self.assertTrue(leg["one_r_locked"])
        self.assertIsNone(leg.get("structure_stop"))
        self.assertFalse(leg.get("breakeven_locked"))

    def test_flag_off_one_r_skips_idle_flatten(self):
        _clear_flag()
        trail = init_php_trail_state(10.0, 20, kill_pct=0.05)
        trail["kill_pct"] = 0.05
        trail["trading_day"] = 6
        leg = {"price": 10.0, "one_r_locked": False, "trail": trail}
        mark_one_r_if_touched(leg, trail, quote_high=10.60)
        self.assertFalse(should_idle_no_1r(trail, leg))

    def test_formula_helper_still_arms_when_called_directly(self):
        """maybe_arm_be_lock_on_1r remains available for REVERT / tests."""
        trail = init_php_trail_state(10.0, 20, kill_pct=0.10)
        trail["kill_pct"] = 0.10
        leg = {
            "price": 10.0,
            "structure_stop": None,
            "one_r_locked": False,
            "trail": trail,
        }
        self.assertTrue(maybe_arm_be_lock_on_1r(leg, trail, quote_high=11.0))
        self.assertEqual(leg["structure_stop_reason"], "be_lock_1r")


class TestKillStillWorks(unittest.TestCase):
    def tearDown(self) -> None:
        _clear_flag()

    def test_kill_fires_after_structure_disarmed(self):
        _clear_flag()
        trail = init_php_trail_state(10.0, 20, kill_pct=0.05)
        trail["kill_pct"] = 0.05
        be = be_lock_price(10.0)
        trail["structure_stop"] = be
        leg = {
            "price": 10.0,
            "structure_stop": be,
            "structure_stop_reason": "be_lock_1r",
            "one_r_locked": True,
            "trail": trail,
        }
        apply_live_structure_policy(leg, trail, quote_high=11.0)
        self.assertFalse(
            should_fire_live_structure_stop(be - 0.01, leg.get("structure_stop"))
        )
        trail, exits = php_process_bar(
            trail,
            high=9.55,
            low=9.40,
            close=9.45,
            when="kill-tick",
        )
        self.assertTrue(any(e["reason"] == "kill" for e in exits))
        self.assertEqual(remaining_shares(trail), 0)

    def test_dip_to_be_lock_does_not_kill_when_above_kill(self):
        trail = init_php_trail_state(10.0, 20, kill_pct=0.05)
        be = be_lock_price(10.0)
        trail, exits = php_process_bar(
            trail,
            high=10.10,
            low=be - 0.01,
            close=10.00,
            when="be-dip",
        )
        self.assertFalse(any(e["reason"].startswith("kill") for e in exits))
        self.assertGreater(remaining_shares(trail), 0)


class TestKillRatchetUpOnly(unittest.TestCase):
    """UP-only kill rewrite; unread 0.0 (stopPrice without auxPrice) must not storm."""

    def test_raises_when_target_higher(self):
        self.assertTrue(kill_stop_needs_ratchet(9.50, 9.75))

    def test_never_loosens(self):
        self.assertFalse(kill_stop_needs_ratchet(9.75, 9.50))
        self.assertFalse(kill_stop_needs_ratchet(9.75, 9.75))

    def test_unreadable_current_stop_skips_rewrite(self):
        # 0.0 is unread/missing trigger (IB stores STP LMT on auxPrice), not ratchet-from-zero.
        self.assertFalse(kill_stop_needs_ratchet(0.0, 9.50))
        self.assertFalse(kill_stop_needs_ratchet(9.50, 0.0))


class TestProcessLegLivePath(unittest.TestCase):
    def tearDown(self) -> None:
        _clear_flag()

    def _open_pos(self, *, structure_stop: float | None, one_r: bool = True):
        trail = init_php_trail_state(10.0, 20, kill_pct=0.05)
        trail["kill_pct"] = 0.05
        trail["rth_armed"] = True
        trail["structure_stop"] = structure_stop
        trail["one_r_locked"] = one_r
        trail["last_session_date"] = "2026-09-16"
        trail["trading_day"] = 1
        leg = {
            "price": 10.0,
            "shares": 20,
            "status": "OPEN",
            "rth_armed": True,
            "structure_stop": structure_stop,
            "structure_stop_reason": "be_lock_1r" if structure_stop else None,
            "one_r_locked": one_r,
            "breakeven_locked": bool(structure_stop),
            "kill_pct": 0.05,
            "trail": trail,
            "exits": [],
        }
        pos = {"symbol": "TEST", "status": "OPEN", "legs": [leg]}
        return pos, leg

    def test_process_leg_does_not_structure_dump_when_flag_off(self):
        _clear_flag()
        be = be_lock_price(10.0)
        pos, _leg = self._open_pos(structure_stop=be)
        # Dip through BE lock (9.97) but stay above 5% kill (9.50); keep the
        # high below T1 so this tick only tests the structure dump gate.
        quote = {"high": 10.10, "low": be - 0.05, "close": 10.05, "last": 10.05}
        with (
            patch.object(
                tsd_trail_monitor, "fetch_3h_bars", side_effect=RuntimeError("skip")
            ),
            patch.object(tsd_trail_monitor, "_exit_all_remaining") as exit_all,
        ):
            results = tsd_trail_monitor._process_leg(
                MagicMock(),
                pos,
                0,
                pos["legs"][0],
                "TEST",
                quote,
                dry_run=True,
                when="2026-09-16T10:00:00-04:00",
            )
        exit_all.assert_not_called()
        self.assertFalse(any(r.get("reason") == "structure_stop" for r in results))
        self.assertIsNone(pos["legs"][0].get("structure_stop"))
        self.assertTrue(pos["legs"][0].get("one_r_locked"))
        self.assertEqual(pos["legs"][0].get("status"), "OPEN")

    def test_process_leg_flag_on_still_structure_dumps(self):
        os.environ[LIVE_STRUCTURE_STOP_ENV] = "1"
        be = be_lock_price(10.0)
        pos, _leg = self._open_pos(structure_stop=be)
        quote = {"high": 10.10, "low": be - 0.05, "close": 10.05, "last": 10.05}
        with (
            patch.object(
                tsd_trail_monitor, "fetch_3h_bars", side_effect=RuntimeError("skip")
            ),
            patch.object(
                tsd_trail_monitor,
                "_exit_all_remaining",
                return_value=[{"reason": "structure_stop", "status": "DRY_RUN"}],
            ) as exit_all,
        ):
            results = tsd_trail_monitor._process_leg(
                MagicMock(),
                pos,
                0,
                pos["legs"][0],
                "TEST",
                quote,
                dry_run=True,
                when="2026-09-16T10:00:00-04:00",
            )
        exit_all.assert_called_once()
        self.assertEqual(exit_all.call_args.kwargs.get("reason"), "structure_stop")
        self.assertTrue(any(r.get("reason") == "structure_stop" for r in results))

    def test_process_leg_kill_still_exits_when_flag_off(self):
        _clear_flag()
        pos, _leg = self._open_pos(structure_stop=be_lock_price(10.0))
        quote = {"high": 9.60, "low": 9.40, "close": 9.45, "last": 9.45}
        with patch.object(
            tsd_trail_monitor, "fetch_3h_bars", side_effect=RuntimeError("skip")
        ):
            results = tsd_trail_monitor._process_leg(
                MagicMock(),
                pos,
                0,
                pos["legs"][0],
                "TEST",
                quote,
                dry_run=True,
                when="2026-09-16T10:00:00-04:00",
            )
        self.assertFalse(any(r.get("reason") == "structure_stop" for r in results))
        self.assertTrue(any(str(r.get("reason") or "") == "kill" for r in results))
        self.assertEqual(remaining_shares(pos["legs"][0]["trail"]), 0)


class TestAliasAndLockProfit(unittest.TestCase):
    def tearDown(self) -> None:
        _clear_flag()

    def test_php_alias_restores_dumps(self):
        _clear_flag()
        os.environ["PHP_STRUCTURE_STOP_EXITS"] = "1"
        self.assertTrue(live_structure_stop_enabled())

    def test_either_flag_zero_keeps_off(self):
        os.environ[LIVE_STRUCTURE_STOP_ENV] = "0"
        os.environ["PHP_STRUCTURE_STOP_EXITS"] = "0"
        self.assertFalse(live_structure_stop_enabled())

    def test_lock_profit_ratchets_kill_up_after_mfe(self):
        _clear_flag()
        trail = init_php_trail_state(10.0, 20, kill_pct=0.05)
        trail["kill_pct"] = 0.05
        trail["trail_pct"] = 0.04
        for t in trail["tranches"]:
            t["trail_pct"] = 0.04
        leg = {"price": 10.0, "trail": trail, "kill_price": trail["kill_price"]}
        changed = maybe_lock_profit_via_trail(
            leg, trail, quote_high=10.40, quote_last=10.30,
        )
        self.assertTrue(changed)
        self.assertGreater(float(trail["kill_price"]), 9.50)
        self.assertLess(float(trail["kill_price"]), 10.30)
        self.assertTrue(trail.get("lock_profit_armed"))
        for t in trail["tranches"]:
            if not t.get("closed"):
                self.assertLessEqual(float(t["trail_pct"]), 0.02 + 1e-12)

    def test_lock_profit_never_lowers_or_removes_kill(self):
        _clear_flag()
        trail = init_php_trail_state(10.0, 20, kill_pct=0.01)
        trail["kill_price"] = 9.97
        prior = float(trail["kill_price"])
        leg = {"price": 10.0, "trail": trail}
        maybe_lock_profit_via_trail(leg, trail, quote_high=10.40, quote_last=10.00)
        self.assertGreaterEqual(float(trail["kill_price"]), prior)
        self.assertGreater(float(trail["kill_price"]), 0)

    def test_lock_profit_skipped_when_structure_dumps_on(self):
        os.environ[LIVE_STRUCTURE_STOP_ENV] = "1"
        trail = init_php_trail_state(10.0, 20, kill_pct=0.05)
        leg = {"price": 10.0, "trail": trail}
        changed = maybe_lock_profit_via_trail(
            leg, trail, quote_high=10.40, quote_last=10.30,
        )
        self.assertFalse(changed)
        self.assertAlmostEqual(float(trail["kill_price"]), 9.5)


if __name__ == "__main__":
    unittest.main()
