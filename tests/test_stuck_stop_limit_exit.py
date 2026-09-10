"""Fail-closed exit: unfillable STP LMT residual longs must flatten."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

# ib_insync may be absent in CI — stub before importing tsd_exit.
sys.modules.setdefault("pytz", __import__("types").ModuleType("pytz"))
sys.modules["pytz"].timezone = lambda name: __import__("datetime").timezone.utc

if "ib_insync" not in sys.modules:
    ib = MagicMock()
    sys.modules["ib_insync"] = ib
    ib.IB = MagicMock
    ib.LimitOrder = MagicMock
    ib.MarketOrder = MagicMock
    ib.Stock = MagicMock
    ib.StopLimitOrder = MagicMock

from tsd_scan_pipeline import tsd_exit


class TestUnfillableStopLimit(unittest.TestCase):
    def test_janx_gap_through_is_unfillable(self):
        # Screenshot: last 17.69, STP LMT ~18.89
        self.assertTrue(
            tsd_exit.sell_stop_limit_unfillable(17.69, stop_px=18.89, lmt_px=18.80)
        )
        self.assertTrue(tsd_exit.kill_price_already_through(17.69, 18.89))

    def test_healthy_kill_above_market_is_fillable_path(self):
        # last still above stop — not a triggered unfillable limit
        self.assertFalse(
            tsd_exit.sell_stop_limit_unfillable(20.50, stop_px=18.89, lmt_px=18.80)
        )
        self.assertFalse(tsd_exit.kill_price_already_through(20.50, 18.89))

    def test_last_between_stop_and_limit_still_fillable(self):
        # triggered but last still at/above limit — can fill
        self.assertFalse(
            tsd_exit.sell_stop_limit_unfillable(18.82, stop_px=18.89, lmt_px=18.80)
        )

    def test_repair_dry_run_flags_stuck_long(self):
        fake = MagicMock()
        row = {
            "symbol": "JANX",
            "order_id": 99,
            "client_id": 95,
            "order_type": "STP LMT",
            "stop_price": 18.89,
            "lmt_price": 18.80,
            "remaining": 1.0,
        }
        with (
            patch.object(tsd_exit, "_broker_qty_map", return_value={"JANX": 1.0}),
            patch.object(tsd_exit, "_open_sell_rows", return_value=[row]),
        ):
            actions = tsd_exit.repair_unfillable_stop_limit_sells(
                fake, dry_run=True, last_by_symbol={"JANX": 17.69}
            )
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["symbol"], "JANX")
        self.assertEqual(actions[0]["status"], "DRY_RUN")
        self.assertEqual(actions[0]["type"], "stuck_stop_limit_flatten")

    def test_repair_skips_when_stop_still_valid(self):
        fake = MagicMock()
        row = {
            "symbol": "JANX",
            "order_id": 99,
            "client_id": 95,
            "order_type": "STP LMT",
            "stop_price": 18.89,
            "lmt_price": 18.80,
            "remaining": 1.0,
        }
        with (
            patch.object(tsd_exit, "_broker_qty_map", return_value={"JANX": 1.0}),
            patch.object(tsd_exit, "_open_sell_rows", return_value=[row]),
        ):
            actions = tsd_exit.repair_unfillable_stop_limit_sells(
                fake, dry_run=True, last_by_symbol={"JANX": 19.50}
            )
        self.assertEqual(actions, [])

    def test_repair_ignores_flat_symbol_orphans(self):
        fake = MagicMock()
        row = {
            "symbol": "FLAT",
            "order_id": 1,
            "client_id": 95,
            "order_type": "STP LMT",
            "stop_price": 10.0,
            "lmt_price": 9.95,
            "remaining": 4.0,
        }
        with (
            patch.object(tsd_exit, "_broker_qty_map", return_value={"FLAT": 0.0}),
            patch.object(tsd_exit, "_open_sell_rows", return_value=[row]),
        ):
            actions = tsd_exit.repair_unfillable_stop_limit_sells(
                fake, dry_run=True, last_by_symbol={"FLAT": 5.0}
            )
        self.assertEqual(actions, [])


if __name__ == "__main__":
    unittest.main()
