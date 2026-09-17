"""Regression: place_tsd_entry must not stack working BUY limits (MLYS).

MLYS 2026-09: multiple Day BUY LMT @~27.95/27.98 qty 0/8 appeared after
failed cancel-on-requote and re-entry without clearing prior rests.
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

from tsd_scan_pipeline import tsd_entry


class _FakeOrder:
    def __init__(
        self,
        order_id: int,
        *,
        action: str = "BUY",
        order_type: str = "LMT",
        qty: int = 8,
        lmt_price: float = 27.95,
    ):
        self.orderId = order_id
        self.action = action
        self.orderType = order_type
        self.totalQuantity = qty
        self.lmtPrice = lmt_price
        self.auxPrice = 0.0
        self.clientId = 93


class _FakeTrade:
    def __init__(self, order: _FakeOrder, symbol: str = "MLYS", status: str = "Submitted"):
        self.order = order
        self.orderStatus = SimpleNamespace(status=status, filled=0.0, avgFillPrice=0.0)
        self.contract = SimpleNamespace(symbol=symbol)


class _FakeIB:
    def __init__(self, trades: list | None = None):
        self._trades = list(trades or [])
        self.cancelled: list[int] = []
        self.placed: list = []
        self.next_oid = 50000

    def reqAllOpenOrders(self):
        return None

    def sleep(self, _seconds):
        return None

    def openTrades(self):
        return list(self._trades)

    def qualifyContracts(self, contract):
        return [contract]

    def cancelOrder(self, order):
        oid = int(order.orderId)
        self.cancelled.append(oid)
        self._trades = [t for t in self._trades if int(t.order.orderId) != oid]

    def placeOrder(self, contract, order):
        if not getattr(order, "orderId", 0):
            order.orderId = self.next_oid
            self.next_oid += 1
        self.placed.append(order)
        trade = _FakeTrade(order, symbol=getattr(contract, "symbol", "MLYS"))
        # Leave as Submitted so cancel_working_buys can see it until cancelled.
        self._trades.append(trade)
        return trade


class TestCancelWorkingBuys(unittest.TestCase):
    def test_cancels_all_working_buys_for_symbol(self):
        ib = _FakeIB(
            trades=[
                _FakeTrade(_FakeOrder(11, lmt_price=27.95)),
                _FakeTrade(_FakeOrder(12, lmt_price=27.98)),
                _FakeTrade(_FakeOrder(13, action="SELL", order_type="STP LMT", lmt_price=25.0)),
            ]
        )
        cancelled = tsd_entry.cancel_working_buys_for_symbol(ib, "MLYS")
        self.assertEqual(sorted(cancelled), [11, 12])
        remaining = [
            (int(t.order.orderId), str(t.order.action)) for t in ib.openTrades()
        ]
        self.assertEqual(remaining, [(13, "SELL")])

    def test_ignores_terminal_buys(self):
        ib = _FakeIB(
            trades=[
                _FakeTrade(_FakeOrder(11), status="Cancelled"),
                _FakeTrade(_FakeOrder(12), status="Submitted"),
            ]
        )
        cancelled = tsd_entry.cancel_working_buys_for_symbol(ib, "MLYS")
        self.assertEqual(cancelled, [12])


class TestPlaceEntryClearsPriorBuys(unittest.TestCase):
    def test_no_fill_cancels_all_working_buys(self):
        """Timeout path must clear every working BUY, not only the last oid."""
        prior = _FakeTrade(_FakeOrder(11, lmt_price=27.95))
        ib = _FakeIB(trades=[prior])

        with patch.object(tsd_entry, "classify_session", return_value="EXTENDED"), patch.object(
            tsd_entry, "load_tsd_pool", return_value=10000.0
        ), patch.object(tsd_entry, "_ref_price", return_value=28.00), patch(
            "tsd_scan_pipeline.tsd_capacity.deploy_budget", return_value=250.0
        ), patch(
            "tsd_scan_pipeline.tsd_capacity.full_slots_used", return_value=0
        ), patch(
            "tsd_scan_pipeline.tsd_capacity.load_state", return_value={"legs": []}
        ), patch(
            "tsd_scan_pipeline.tsd_capacity.shares_for_budget", return_value=8
        ), patch(
            "tsd_scan_pipeline.tsd_pool.load_pool",
            return_value={"pool": 10000.0, "deployed": 0.0},
        ), patch.object(tsd_entry, "FILL_WAIT_SEC", 0), patch.object(
            tsd_entry, "LIMIT_FILL_EXTRA_SEC", 0
        ), patch.object(tsd_entry, "REQUOTE_WAIT_SEC", 0), patch.object(
            tsd_entry, "POLL_SEC", 0
        ):
            result = tsd_entry.place_tsd_entry(
                ib,
                "MLYS",
                entry_price=28.00,
                limit_price=27.95,
                prefer_limit=True,
            )

        self.assertEqual(result.get("status"), "REJECTED")
        self.assertEqual(result.get("reason"), "no_fill_timeout")
        # Prior 11 cleared before place; placed order(s) cleared on timeout.
        self.assertIn(11, ib.cancelled)
        buy_left = [
            t
            for t in ib.openTrades()
            if str(t.order.action).upper() == "BUY"
            and str(t.orderStatus.status) not in tsd_entry._TERMINAL_BUY_STATUS
        ]
        self.assertEqual(buy_left, [])
        # Original + one requote = 2 places max; never leave them resting.
        self.assertLessEqual(len(ib.placed), 2)

    def test_pre_place_clears_stacked_limits(self):
        """Four stacked MLYS BUYs are wiped before a new entry attempt."""
        stacked = [
            _FakeTrade(_FakeOrder(oid, lmt_price=px))
            for oid, px in ((1, 27.95), (2, 27.95), (3, 27.98), (4, 27.98))
        ]
        ib = _FakeIB(trades=stacked)
        buf = io.StringIO()
        with patch("sys.stdout", buf), patch.object(
            tsd_entry, "classify_session", return_value="EXTENDED"
        ), patch.object(tsd_entry, "load_tsd_pool", return_value=10000.0), patch.object(
            tsd_entry, "_ref_price", return_value=28.00
        ), patch(
            "tsd_scan_pipeline.tsd_capacity.deploy_budget", return_value=250.0
        ), patch(
            "tsd_scan_pipeline.tsd_capacity.full_slots_used", return_value=0
        ), patch(
            "tsd_scan_pipeline.tsd_capacity.load_state", return_value={"legs": []}
        ), patch(
            "tsd_scan_pipeline.tsd_capacity.shares_for_budget", return_value=8
        ), patch(
            "tsd_scan_pipeline.tsd_pool.load_pool",
            return_value={"pool": 10000.0, "deployed": 0.0},
        ), patch.object(tsd_entry, "FILL_WAIT_SEC", 0), patch.object(
            tsd_entry, "LIMIT_FILL_EXTRA_SEC", 0
        ), patch.object(tsd_entry, "REQUOTE_WAIT_SEC", 0), patch.object(
            tsd_entry, "POLL_SEC", 0
        ):
            tsd_entry.place_tsd_entry(
                ib,
                "MLYS",
                entry_price=28.00,
                limit_price=27.95,
                prefer_limit=True,
            )
        for oid in (1, 2, 3, 4):
            self.assertIn(oid, ib.cancelled)
        self.assertIn("cancelled working BUY", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
