"""Durable Peak Hour ops: broker-truth qty, single protective kill, Cap scrub."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline import tsd_exit, tsd_trail, tsd_watch_queue as wq


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
        aux_price: float = 53.13,
        lmt_price: float = 52.97,
        stop_price: float | None = None,
        client_id: int = 95,
    ):
        self.orderId = order_id
        self.action = action
        self.orderType = order_type
        self.totalQuantity = qty
        self.auxPrice = aux_price
        self.lmtPrice = lmt_price
        self.clientId = client_id
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
        self.client = SimpleNamespace(clientId=95)

    def reqAllOpenOrders(self):
        return None

    def reqPositions(self):
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
        self._trades.append(
            _FakeTrade(order, symbol=getattr(contract, "symbol", "ATRC"))
        )
        return SimpleNamespace(order=order)


def _open_leg(*, shares=7, kill_oid=43760, kill_price=1.90, symbol_shares=None):
    n = int(symbol_shares if symbol_shares is not None else shares)
    return {
        "shares": n,
        "kill_order_id": kill_oid,
        "status": "OPEN",
        "price": 2.00,
        "trail": {
            "entry_price": 2.00,
            "kill_price": kill_price,
            "kill_pct": 0.05,
            "trail_pct": 0.04,
            "kill_stop_cancelled": False,
            "tranches": [
                {
                    "id": "T4",
                    "shares": n,
                    "weight": 0.1,
                    "trigger_pct": 0.10,
                    "trigger_price": 2.20,
                    "trail_pct": 0.04,
                    "closed": False,
                }
            ],
        },
    }


def _open_pos(leg, symbol="MMED"):
    return {
        "symbol": symbol,
        "status": "OPEN",
        "legs": [leg],
        "entry_count": 1,
    }


class TestSetRemainingShares(unittest.TestCase):
    def test_shrink_runner_to_broker_qty(self):
        trail = _open_leg(shares=7)["trail"]
        tsd_trail.set_remaining_shares(trail, 2)
        self.assertEqual(tsd_trail.remaining_shares(trail), 2)
        self.assertEqual(trail["tranches"][0]["shares"], 2)
        self.assertFalse(trail["tranches"][0]["closed"])

    def test_zero_closes_open_tranches(self):
        trail = _open_leg(shares=7)["trail"]
        tsd_trail.set_remaining_shares(trail, 0)
        self.assertEqual(tsd_trail.remaining_shares(trail), 0)
        self.assertTrue(trail["tranches"][0]["closed"])


class TestApplyBrokerQty(unittest.TestCase):
    def test_mmed_book_seven_broker_two(self):
        """Fill/log 12, book 7, TWS 2 → remaining forced to 2."""
        leg = _open_leg(shares=7)
        result = tsd_exit.apply_broker_qty_to_open_leg(leg, 2)
        self.assertTrue(result["changed"])
        self.assertEqual(result["book_was"], 7)
        self.assertEqual(result["broker_qty"], 2)
        self.assertEqual(leg["shares"], 2)
        self.assertEqual(tsd_exit._leg_remaining(leg), 2)
        self.assertEqual(leg["status"], "OPEN")

    def test_broker_flat_closes_leg(self):
        leg = _open_leg(shares=7)
        result = tsd_exit.apply_broker_qty_to_open_leg(leg, 0)
        self.assertTrue(result["closed"])
        self.assertEqual(leg["status"], "CLOSED")
        self.assertEqual(leg["shares"], 0)
        self.assertTrue(leg["trail"]["kill_stop_cancelled"])
        self.assertIsNone(leg["kill_order_id"])


class TestSelectKeepProtective(unittest.TestCase):
    def test_prefer_book_oid_when_qty_matches(self):
        trades = [
            _FakeTrade(_FakeOrder(oid, qty=2, aux_price=53.13))
            for oid in (43126, 43140, 43155)
        ]
        keep = tsd_exit.select_keep_protective_trade(
            trades, prefer_oid=43140, target_qty=2,
        )
        self.assertEqual(int(keep.order.orderId), 43140)

    def test_highest_aux_among_qty_matched(self):
        trades = [
            _FakeTrade(_FakeOrder(10, qty=2, aux_price=51.96)),
            _FakeTrade(_FakeOrder(20, qty=2, aux_price=53.13)),
            _FakeTrade(_FakeOrder(30, qty=12, aux_price=53.50)),
        ]
        keep = tsd_exit.select_keep_protective_trade(
            trades, prefer_oid=None, target_qty=2,
        )
        self.assertEqual(int(keep.order.orderId), 20)

    def test_stale_old_qty_is_not_preferred(self):
        """MMED oid 43760 qty=12 must lose to a qty-matched kill."""
        trades = [
            _FakeTrade(_FakeOrder(43760, qty=12, aux_price=1.90, client_id=93)),
            _FakeTrade(_FakeOrder(44001, qty=2, aux_price=1.90, client_id=95)),
        ]
        keep = tsd_exit.select_keep_protective_trade(
            trades, prefer_oid=43760, target_qty=2,
        )
        self.assertEqual(int(keep.order.orderId), 44001)


class TestEnforceSingleProtectiveKill(unittest.TestCase):
    def test_three_identical_atrc_keeps_one(self):
        trades = [
            _FakeTrade(_FakeOrder(oid, qty=2, aux_price=53.13, stop_price=0.0, client_id=cid))
            for oid, cid in ((43126, 95), (43140, 85), (43155, 86))
        ]
        ib = _FakeIB(trades=trades, positions=[_FakePos("ATRC", 2)])
        leg = _open_leg(shares=2, kill_oid=43126, kill_price=53.13)
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            result = tsd_exit.enforce_single_protective_kill(ib, "ATRC", leg)
        self.assertEqual(result["action"], "SINGLE_KILL")
        self.assertEqual(result["keep_oid"], 43126)
        self.assertEqual(sorted(result["cancelled"]), [43140, 43155])
        self.assertEqual([int(t.order.orderId) for t in ib.openTrades()], [43126])
        self.assertFalse(result.get("hard_fail"))
        self.assertIn("SINGLE_KILL keep=43126 cancel=", buf.getvalue())

    def test_atrc_live_fail_clients_85_and_95(self):
        """2026-09-18 ATRC: 3× qty=2 @53.13 from trail 85+95 → keep book oid."""
        trades = [
            _FakeTrade(_FakeOrder(43126, qty=2, aux_price=53.13, stop_price=0.0, client_id=95)),
            _FakeTrade(_FakeOrder(43140, qty=2, aux_price=53.13, stop_price=0.0, client_id=85)),
            _FakeTrade(_FakeOrder(43155, qty=2, aux_price=53.13, stop_price=0.0, client_id=85)),
        ]
        ib = _FakeIB(trades=trades, positions=[_FakePos("ATRC", 2)])
        ib.client = SimpleNamespace(clientId=95)
        leg = _open_leg(shares=2, kill_oid=43126, kill_price=53.13)
        result = tsd_exit.enforce_single_protective_kill(ib, "ATRC", leg)
        self.assertEqual(result["keep_oid"], 43126)
        self.assertEqual(sorted(result["cancelled"]), [43140, 43155])
        self.assertEqual(
            [int(getattr(t.order, "clientId", 0) or 0) for t in ib.openTrades()],
            [95],
        )
        self.assertIn(85, tsd_exit.OWNER_CANCEL_CLIENT_IDS)
        self.assertIn(75, tsd_exit.OWNER_CANCEL_CLIENT_IDS)
        self.assertIn(95, tsd_exit.OWNER_CANCEL_CLIENT_IDS)

    def test_hard_fail_when_foreign_client_kill_stays(self):
        """10147 / busy 85: leftover extras must log AG HARD FAIL, not silent."""
        trades = [
            _FakeTrade(_FakeOrder(43126, qty=2, aux_price=53.13, client_id=95)),
            _FakeTrade(_FakeOrder(43140, qty=2, aux_price=53.13, client_id=85)),
        ]
        ib = _FakeIB(trades=trades, positions=[_FakePos("ATRC", 2)])
        leg = _open_leg(shares=2, kill_oid=43126, kill_price=53.13)
        buf = io.StringIO()
        with patch.object(tsd_exit, "_cancel_oids_with_owner_retry", return_value=[]):
            with patch("sys.stdout", buf):
                result = tsd_exit.enforce_single_protective_kill(ib, "ATRC", leg)
        self.assertTrue(result.get("hard_fail"))
        self.assertIn("AG HARD FAIL", buf.getvalue())
        self.assertIn("clients=", buf.getvalue())

    def test_single_kill_flag_off_skips(self):
        ib = _FakeIB(
            trades=[_FakeTrade(_FakeOrder(1, qty=2)), _FakeTrade(_FakeOrder(2, qty=2))],
            positions=[_FakePos("ATRC", 2)],
        )
        leg = _open_leg(shares=2, kill_oid=1, kill_price=53.13)
        with patch.dict(os.environ, {tsd_exit.SINGLE_KILL_ENFORCE_ENV: "0"}):
            result = tsd_exit.enforce_single_protective_kill(ib, "ATRC", leg)
        self.assertTrue(result.get("skipped"))
        self.assertEqual(len(ib.openTrades()), 2)

    def test_naked_rearm(self):
        ib = _FakeIB(trades=[], positions=[_FakePos("ATRC", 2)])
        leg = _open_leg(shares=2, kill_oid=None, kill_price=53.13)
        with patch.object(tsd_exit, "classify_session", return_value="EXTENDED"):
            result = tsd_exit.enforce_single_protective_kill(ib, "ATRC", leg)
        self.assertEqual(result["action"], "NAKED_REARM")
        self.assertEqual(len(ib.placed), 1)
        self.assertEqual(int(ib.placed[0].totalQuantity), 2)
        self.assertEqual(leg["kill_order_id"], result["keep_oid"])

    def test_qty_sync_cancels_old_sized_kill(self):
        """Stale entry kill qty=12 is replaced with broker qty=2 — no duplicate."""
        old = _FakeTrade(_FakeOrder(43760, qty=12, aux_price=1.90, stop_price=0.0), symbol="MMED")
        ib = _FakeIB(trades=[old], positions=[_FakePos("MMED", 2)])
        leg = _open_leg(shares=2, kill_oid=43760, kill_price=1.90)
        buf = io.StringIO()
        with patch.object(tsd_exit, "classify_session", return_value="RTH"):
            with patch("sys.stdout", buf):
                result = tsd_exit.enforce_single_protective_kill(ib, "MMED", leg)
        self.assertEqual(result["action"], "QTY_SYNC")
        self.assertEqual(len(ib.placed), 1)
        self.assertEqual(int(ib.placed[0].totalQuantity), 2)
        self.assertIn(43760, ib.cancelled)
        self.assertNotEqual(leg["kill_order_id"], 43760)
        working = [int(t.order.orderId) for t in ib.openTrades()]
        self.assertEqual(len(working), 1)
        self.assertIn("QTY_SYNC", buf.getvalue())


class TestReconcileOpenPosition(unittest.TestCase):
    def test_qty_sync_shrinks_book_to_broker(self):
        leg = _open_leg(shares=7, kill_oid=43760, kill_price=1.90)
        pos = _open_pos(leg, "MMED")
        old = _FakeTrade(
            _FakeOrder(43760, qty=12, aux_price=1.90, stop_price=0.0),
            symbol="MMED",
        )
        ib = _FakeIB(trades=[old], positions=[_FakePos("MMED", 2)])
        buf = io.StringIO()
        with patch.object(tsd_exit, "classify_session", return_value="RTH"):
            with patch("sys.stdout", buf):
                rec = tsd_exit.reconcile_open_position_to_broker(ib, pos)
        self.assertTrue(rec["changed"])
        self.assertEqual(pos["legs"][0]["shares"], 2)
        self.assertEqual(tsd_exit._leg_remaining(pos["legs"][0]), 2)
        self.assertEqual(pos["status"], "OPEN")
        self.assertIn("QTY_SYNC book→broker 7→2", buf.getvalue())

    def test_flag_off_skips_book_mutation(self):
        leg = _open_leg(shares=7)
        pos = _open_pos(leg, "MMED")
        ib = _FakeIB(positions=[_FakePos("MMED", 2)])
        with patch.dict(os.environ, {tsd_exit.BROKER_QTY_RECONCILE_ENV: "0"}):
            rec = tsd_exit.reconcile_open_position_to_broker(ib, pos)
        self.assertTrue(rec["skipped"])
        self.assertEqual(tsd_exit._leg_remaining(pos["legs"][0]), 7)

    def test_broker_flat_closes_and_scrubs_cap(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "tsd_watch_queue.json"
        queue_path.write_text(
            json.dumps({
                "queue": [{
                    "symbol": "MMED",
                    "status": "CONFIRMED",
                    "confirmed_at": "2026-09-17T10:00:00-04:00",
                }],
                "last_updated": None,
            }),
            encoding="utf-8",
        )
        leg = _open_leg(shares=7)
        pos = _open_pos(leg, "MMED")
        ib = _FakeIB(positions=[])
        with patch.object(wq, "QUEUE_PATH", queue_path):
            rec = tsd_exit.reconcile_open_position_to_broker(ib, pos)
        self.assertTrue(rec["closed"])
        self.assertEqual(pos["status"], "CLOSED")
        self.assertEqual(pos["legs"][0]["status"], "CLOSED")
        row = json.loads(queue_path.read_text(encoding="utf-8"))["queue"][0]
        self.assertEqual(row["status"], "CLOSED_SCRUB")
        self.assertFalse(
            wq.is_confirmed_in_flight(row, open_syms=set()),
        )


class TestBrokerQtyFlag(unittest.TestCase):
    def test_default_on(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(tsd_exit.BROKER_QTY_RECONCILE_ENV, None)
            os.environ.pop(tsd_exit.SINGLE_KILL_ENFORCE_ENV, None)
            self.assertTrue(tsd_exit.broker_qty_reconcile_enabled())
            self.assertTrue(tsd_exit.single_kill_enforce_enabled())

    def test_off_values(self):
        for raw in ("0", "false", "off", "no"):
            with patch.dict(os.environ, {tsd_exit.BROKER_QTY_RECONCILE_ENV: raw}):
                self.assertFalse(tsd_exit.broker_qty_reconcile_enabled())
            with patch.dict(os.environ, {tsd_exit.SINGLE_KILL_ENFORCE_ENV: raw}):
                self.assertFalse(tsd_exit.single_kill_enforce_enabled())


if __name__ == "__main__":
    unittest.main()
