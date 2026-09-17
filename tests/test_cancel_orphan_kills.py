"""Unit tests for cancel_orphan_kills keep-oid selection (no live TWS)."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "candidates"
if str(CANDIDATES) not in sys.path:
    sys.path.insert(0, str(CANDIDATES))

_MOD_PATH = CANDIDATES / "uts_v2" / "cancel_orphan_kills.py"
_SPEC = importlib.util.spec_from_file_location(
    "cancel_orphan_kills_under_test", _MOD_PATH
)
assert _SPEC and _SPEC.loader
cok = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cok)


class _FakeOrder:
    def __init__(self, oid: int, aux: float, qty: int = 2):
        self.orderId = oid
        self.auxPrice = aux
        self.stopPrice = 0.0
        self.lmtPrice = round(aux * 0.997, 2)
        self.action = "SELL"
        self.orderType = "STP LMT"
        self.totalQuantity = qty
        self.clientId = 85


class _FakeTrade:
    def __init__(self, oid: int, aux: float):
        self.order = _FakeOrder(oid, aux)
        self.orderStatus = SimpleNamespace(status="Submitted", filled=0)
        self.contract = SimpleNamespace(symbol="ATRC", secType="STK")


class TestChooseKeepOid(unittest.TestCase):
    """ATRC three identical kills → keep highest stop then lowest oid."""

    def test_prefer_explicit_keep_oid(self):
        trades = [_FakeTrade(oid, 53.13) for oid in (43126, 43140, 43155)]
        self.assertEqual(cok.choose_keep_oid(trades, prefer_oid=43140), 43140)

    def test_identical_stops_keeps_lowest_oid(self):
        trades = [_FakeTrade(oid, 53.13) for oid in (43155, 43126, 43140)]
        self.assertEqual(cok.choose_keep_oid(trades), 43126)

    def test_higher_stop_wins(self):
        trades = [
            _FakeTrade(10, 51.96),
            _FakeTrade(20, 53.13),
            _FakeTrade(30, 52.50),
        ]
        self.assertEqual(cok.choose_keep_oid(trades), 20)

    def test_empty(self):
        self.assertIsNone(cok.choose_keep_oid([]))


class TestTcpReachable(unittest.TestCase):
    def test_closed_loopback_paper_port(self):
        # Cloud agents have no TWS; expect False without raising.
        self.assertFalse(cok.tcp_reachable("127.0.0.1", 7497, timeout=0.3))


if __name__ == "__main__":
    unittest.main()
