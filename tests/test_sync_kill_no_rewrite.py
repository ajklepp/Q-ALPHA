"""Regression: trail-monitor kill backstop must not cancel/replace a good STP LMT.

ATRC 2026-09-15: sync_kill_quantity read Order.stopPrice (always 0 on ib_insync;
the trigger lives on auxPrice) and ratcheted 0.0->kill every tick.
"""
from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline import tsd_exit


class _FakePos:
    def __init__(self, symbol: str, qty: float):
        self.contract = SimpleNamespace(symbol=symbol)
        self.position = qty


class _FakeOrder:
    def __init__(
        self,
        order_id: int,
        *,
        action: str = "SELL",
        order_type: str = "STP LMT",
        qty: int = 2,
        aux_price: float = 51.96,
        lmt_price: float = 51.70,
        stop_price: float | None = None,
        status: str = "Submitted",
        client_id: int = 95,
    ):
        self.orderId = order_id
        self.action = action
        self.orderType = order_type
        self.totalQuantity = qty
        self.auxPrice = aux_price
        self.lmtPrice = lmt_price
        self.clientId = client_id
        # Real ib_insync Order has no stopPrice field. Tests may set 0.0 to
        # reproduce the ATRC getattr(..., "stopPrice", 0) misread.
        if stop_price is not None:
            self.stopPrice = stop_price


class _FakeTrade:
    def __init__(self, order: _FakeOrder, symbol: str = "ATRC"):
        self.order = order
        self.orderStatus = SimpleNamespace(status="Submitted")
        self.contract = SimpleNamespace(symbol=symbol)


class _FakeIB:
    def __init__(self, trades: list | None = None, positions: list | None = None):
        self._trades = list(trades or [])
        self._positions = list(positions or [])
        self.cancelled: list[int] = []
        self.placed: list = []
        self.next_oid = 99000

    def reqAllOpenOrders(self):
        return None

    def sleep(self, _seconds):
        return None

    def openTrades(self):
        return list(self._trades)

    def positions(self):
        return list(self._positions)

    def qualifyContracts(self, contract):
        return [contract]

    def cancelOrder(self, order):
        oid = int(order.orderId)
        self.cancelled.append(oid)
        self._trades = [
            t for t in self._trades if int(t.order.orderId) != oid
        ]

    def placeOrder(self, contract, order):
        self.placed.append(order)
        if not getattr(order, "orderId", 0):
            order.orderId = self.next_oid
            self.next_oid += 1
        self._trades.append(_FakeTrade(order, symbol=getattr(contract, "symbol", "ATRC")))
        return SimpleNamespace(order=order)


def _atrc_leg(*, kill_oid=43126, kill_price=51.95775, shares=2):
    return {
        "shares": shares,
        "kill_order_id": kill_oid,
        "status": "OPEN",
        "trail": {
            "entry_price": 54.692,
            "kill_price": kill_price,
            "kill_pct": 0.05,
            "trail_pct": 0.04,
            "kill_stop_cancelled": False,
            "tranches": [
                {
                    "id": "T4",
                    "shares": shares,
                    "weight": 0.1,
                    "trigger_pct": 0.10,
                    "trigger_price": 60.16,
                    "trail_pct": 0.04,
                    "closed": False,
                }
            ],
        },
    }


class TestOrderStopPrice(unittest.TestCase):
    def test_reads_aux_price_not_missing_stop_price(self):
        order = _FakeOrder(43126, aux_price=51.96)
        self.assertFalse(hasattr(order, "stopPrice"))
        self.assertEqual(tsd_exit.order_stop_price(order), 51.96)

    def test_zero_stop_price_attr_does_not_win_over_aux(self):
        order = _FakeOrder(43126, aux_price=51.96, stop_price=0.0)
        self.assertEqual(tsd_exit.order_stop_price(order), 51.96)

    def test_ignores_ib_unset_double(self):
        order = _FakeOrder(1, aux_price=1.7976931348623157e308, stop_price=0.0)
        self.assertEqual(tsd_exit.order_stop_price(order), 0.0)

    def test_plain_stp_uses_aux(self):
        order = _FakeOrder(2, order_type="STP", aux_price=51.96, lmt_price=0)
        self.assertTrue(tsd_exit.is_protective_sell_stop(order))
        self.assertEqual(tsd_exit.order_stop_price(order), 51.96)

    def test_ratchet_ignores_zero_current(self):
        self.assertFalse(tsd_exit.kill_stop_needs_ratchet(0.0, 51.95775))
        self.assertFalse(tsd_exit.kill_stop_needs_ratchet(51.96, 51.95775))
        self.assertTrue(tsd_exit.kill_stop_needs_ratchet(50.00, 51.95775))
        self.assertFalse(tsd_exit.kill_stop_needs_ratchet(52.50, 51.95775))


class TestSyncKillNoRewrite(unittest.TestCase):
    def test_existing_kill_same_price_no_cancel_replace(self):
        """ATRC loop: working STP LMT @51.96 qty=2, target 51.95775 → skip."""
        order = _FakeOrder(43126, qty=2, aux_price=51.96, stop_price=0.0)
        ib = _FakeIB(
            trades=[_FakeTrade(order)],
            positions=[_FakePos("ATRC", 2)],
        )
        leg = _atrc_leg()
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            result = tsd_exit.sync_kill_quantity(ib, leg, "ATRC")
        self.assertTrue(result)
        self.assertEqual(ib.cancelled, [])
        self.assertEqual(ib.placed, [])
        self.assertEqual(leg["kill_order_id"], 43126)
        self.assertIn("ATRC kill already working @51.96 oid=43126 — skip rewrite", buf.getvalue())

    def test_stop_price_attr_zero_does_not_rewrite(self):
        """Even if we only mis-read stopPrice=0, matching qty skip rewrite."""
        order = SimpleNamespace(
            orderId=43126,
            action="SELL",
            orderType="STP LMT",
            totalQuantity=2,
            stopPrice=0.0,
            auxPrice=51.96,
            lmtPrice=51.70,
            clientId=95,
        )
        ib = _FakeIB(
            trades=[_FakeTrade(order)],
            positions=[_FakePos("ATRC", 2)],
        )
        result = tsd_exit.sync_kill_quantity(ib, _atrc_leg(), "ATRC")
        self.assertTrue(result)
        self.assertEqual(ib.cancelled, [])
        self.assertEqual(ib.placed, [])

    def test_stale_oid_10147_does_not_place_when_good_kill_exists(self):
        """Recorded oid is gone (Error 10147); another working kill is good."""
        live = _FakeOrder(43212, qty=2, aux_price=51.96, stop_price=0.0)
        ib = _FakeIB(
            trades=[_FakeTrade(live)],
            positions=[_FakePos("ATRC", 2)],
        )
        leg = _atrc_leg(kill_oid=43148)
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            result = tsd_exit.sync_kill_quantity(ib, leg, "ATRC")
        self.assertTrue(result)
        self.assertEqual(ib.cancelled, [])
        self.assertEqual(ib.placed, [])
        self.assertEqual(leg["kill_order_id"], 43212)
        self.assertIn("skip rewrite", buf.getvalue())

    def test_plain_stp_same_price_skips(self):
        order = _FakeOrder(43126, order_type="STP", qty=2, aux_price=51.96, lmt_price=0)
        ib = _FakeIB(
            trades=[_FakeTrade(order)],
            positions=[_FakePos("ATRC", 2)],
        )
        result = tsd_exit.sync_kill_quantity(ib, _atrc_leg(), "ATRC")
        self.assertTrue(result)
        self.assertEqual(ib.cancelled, [])
        self.assertEqual(ib.placed, [])

    def test_better_existing_stop_skips(self):
        order = _FakeOrder(43126, qty=2, aux_price=52.50)
        ib = _FakeIB(
            trades=[_FakeTrade(order)],
            positions=[_FakePos("ATRC", 2)],
        )
        result = tsd_exit.sync_kill_quantity(ib, _atrc_leg(), "ATRC")
        self.assertTrue(result)
        self.assertEqual(ib.cancelled, [])
        self.assertEqual(ib.placed, [])

    def test_worse_stop_does_ratchet(self):
        order = _FakeOrder(43126, qty=2, aux_price=50.00, stop_price=0.0)
        ib = _FakeIB(
            trades=[_FakeTrade(order)],
            positions=[_FakePos("ATRC", 2)],
        )
        with patch.object(tsd_exit, "classify_session", return_value="EXTENDED"):
            result = tsd_exit.sync_kill_quantity(ib, _atrc_leg(), "ATRC")
        self.assertTrue(result)
        self.assertEqual(ib.cancelled, [43126])
        self.assertEqual(len(ib.placed), 1)
        placed = ib.placed[0]
        self.assertEqual(placed.orderType, "STP LMT")
        self.assertEqual(placed.totalQuantity, 2)
        self.assertAlmostEqual(placed.auxPrice, 51.96, places=2)

    def test_wrong_qty_does_not_skip(self):
        order = _FakeOrder(43126, qty=5, aux_price=51.96)
        ib = _FakeIB(
            trades=[_FakeTrade(order)],
            positions=[_FakePos("ATRC", 2)],
        )
        result = tsd_exit.sync_kill_quantity(ib, _atrc_leg(), "ATRC")
        self.assertTrue(result)
        self.assertEqual(len(ib.placed), 1)
        self.assertEqual(ib.placed[0].totalQuantity, 2)

    def test_cancel_stale_oid_is_benign(self):
        ib = _FakeIB(trades=[], positions=[_FakePos("ATRC", 2)])
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            ok = tsd_exit.cancel_order_safe(ib, 43148)
        self.assertFalse(ok)
        self.assertEqual(ib.cancelled, [])
        self.assertIn("stale/10147", buf.getvalue())

    def test_two_ticks_do_not_loop(self):
        order = _FakeOrder(43126, qty=2, aux_price=51.96, stop_price=0.0)
        ib = _FakeIB(
            trades=[_FakeTrade(order)],
            positions=[_FakePos("ATRC", 2)],
        )
        leg = _atrc_leg()
        tsd_exit.sync_kill_quantity(ib, leg, "ATRC")
        tsd_exit.sync_kill_quantity(ib, leg, "ATRC")
        self.assertEqual(ib.cancelled, [])
        self.assertEqual(ib.placed, [])
        self.assertEqual(leg["kill_order_id"], 43126)

    def test_missing_kill_is_replaced(self):
        ib = _FakeIB(trades=[], positions=[_FakePos("ATRC", 2)])
        leg = _atrc_leg()
        with patch.object(tsd_exit, "classify_session", return_value="EXTENDED"):
            result = tsd_exit.sync_kill_quantity(ib, leg, "ATRC")
        self.assertTrue(result)
        self.assertEqual(len(ib.placed), 1)
        self.assertNotEqual(leg["kill_order_id"], 43126)

    def test_broker_flat_does_not_place_kill(self):
        ib = _FakeIB(trades=[], positions=[])
        leg = _atrc_leg()
        result = tsd_exit.sync_kill_quantity(ib, leg, "ATRC")
        self.assertTrue(result)
        self.assertEqual(ib.placed, [])
        self.assertTrue(leg["trail"]["kill_stop_cancelled"])


if __name__ == "__main__":
    unittest.main()
