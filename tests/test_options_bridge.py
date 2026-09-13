"""
Unit tests for the BSF Phase 9A read-only options bridge.

CODE ONLY — no TWS / IB Gateway / ib.connect. FakeSession only.
"""
from __future__ import annotations

import ast
import json
import sys
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CAND = ROOT / "candidates"
sys.path.insert(0, str(CAND))

from options_bridge.config import (  # noqa: E402
    BIND_HOST,
    DEFAULT_PORT,
    REQUEST_TIMEOUT_SEC,
    RESERVED_CLIENT_IDS,
    TWS_CLIENT_ID,
    TWS_PORT,
    is_reserved_client_id,
    validate_bind_host,
    validate_client_id,
)
from options_bridge.errors import BridgeError, TwsDisconnected, TwsTimeout  # noqa: E402
from options_bridge.ib_session import (  # noqa: E402
    ReadOnlyIBSession,
    _OrderBlockedIB,
    _next_lower_strike,
    _nearest_strike_at_or_below,
    _pick_expiry,
    short_put_target_strike,
)
from options_bridge.server import (  # noqa: E402
    OptionsBridgeServer,
    error_envelope,
    handle_request,
    path_looks_like_order,
    success_envelope,
)


BRIDGE_PY = list((CAND / "options_bridge").glob("*.py"))
FORBIDDEN_IMPORTS = {
    "tsd_scan_pipeline",
    "tsd_trail_monitor",
    "php_scan_funnel",
    "autonomous_agent",
    "ibkr_connector",
    "paper_trader",
    "tws_intraday_sync",
    "tsd_book_state",
    "tsd_pool",
    "intraday_monitor",
}


class FakeSession:
    """In-memory stand-in for ReadOnlyIBSession — never opens TWS."""

    def __init__(self, *, connected: bool = True) -> None:
        self._connected = connected
        self.tws_host = "127.0.0.1"
        self.tws_port = 7497
        self.client_id = 71

    @property
    def connected(self) -> bool:
        return self._connected

    def health_payload(self) -> dict:
        return {
            "tws_connected": self._connected,
            "host": self.tws_host,
            "port": self.tws_port,
            "readonly": True,
            "accounts_n": 1 if self._connected else 0,
            "client_id": self.client_id,
        }

    def ensure_connected(self) -> None:
        if not self._connected:
            raise TwsDisconnected()

    def underlying_quote(self, symbol: str) -> dict:
        self.ensure_connected()
        if not symbol:
            raise BridgeError("BAD_REQUEST", "symbol is required", status=400)
        return {
            "symbol": symbol.upper(),
            "conId": 756733,
            "bid": 560.10,
            "ask": 560.14,
            "last": 560.12,
            "close": 558.90,
            "mid": 560.12,
            "market_price": 560.12,
        }

    def option_chain(self, symbol: str) -> dict:
        self.ensure_connected()
        return {
            "symbol": symbol.upper(),
            "underlying_conId": 756733,
            "exchanges": [
                {
                    "exchange": "SMART",
                    "tradingClass": "SPY",
                    "multiplier": "100",
                    "expirations": ["20260918", "20261016"],
                    "strikes": [550.0, 555.0, 560.0],
                }
            ],
        }

    def qualify_contracts(self, specs: list) -> list:
        self.ensure_connected()
        if not specs:
            from options_bridge.errors import BridgeError

            raise BridgeError("BAD_REQUEST", "body.contracts must be a non-empty list", status=400)
        out = []
        for i, spec in enumerate(specs):
            out.append(
                {
                    **spec,
                    "conId": 1000 + i,
                    "ok": True,
                    "error": None,
                }
            )
        return out

    def option_quote(self, **kwargs) -> dict:
        self.ensure_connected()
        return {
            "symbol": (kwargs.get("symbol") or "SPY"),
            "conId": kwargs.get("con_id") or 123456789,
            "expiry": kwargs.get("expiry") or "20260918",
            "strike": kwargs.get("strike") or 555.0,
            "right": kwargs.get("right") or "P",
            "bid": 1.20,
            "ask": 1.28,
            "last": 1.24,
            "close": 1.10,
            "mid": 1.24,
        }

    def option_quotes(self, con_ids: list) -> list:
        self.ensure_connected()
        if not con_ids:
            from options_bridge.errors import BridgeError

            raise BridgeError("BAD_REQUEST", "body.conIds must be a non-empty list", status=400)
        return [
            {
                "conId": cid,
                "ok": True,
                "error": None,
                "bid": 1.2,
                "ask": 1.3,
                "last": 1.25,
                "close": 1.1,
                "mid": 1.25,
            }
            for cid in con_ids
        ]

    def hist_bars(self, **kwargs) -> dict:
        self.ensure_connected()
        return {
            "conId": kwargs.get("con_id") or 123456789,
            "symbol": kwargs.get("symbol") or "SPY",
            "bar_size": kwargs.get("bar_size") or "1 hour",
            "duration": kwargs.get("duration") or "10 D",
            "what": kwargs.get("what") or "MIDPOINT",
            "use_rth": kwargs.get("use_rth", True),
            "bars": [
                {
                    "ts": "2026-09-11T14:00:00-04:00",
                    "open": 1.10,
                    "high": 1.30,
                    "low": 1.05,
                    "close": 1.22,
                }
            ],
        }

    def put_credit_snapshot(self, **kwargs) -> dict:
        self.ensure_connected()
        return {
            "symbol": str(kwargs.get("symbol") or "SPY").upper(),
            "und_px": kwargs.get("und_px") or 560.0,
            "expiry": "20261016",
            "short": {
                "conId": 111,
                "strike": 532.0,
                "right": "P",
                "bid": 2.10,
                "ask": 2.20,
                "mid": 2.15,
            },
            "long": {
                "conId": 222,
                "strike": 527.0,
                "right": "P",
                "bid": 1.40,
                "ask": 1.50,
                "mid": 1.45,
            },
            "credit_mid": 0.70,
            "width": 5.0,
        }


def _ok(status: int, payload: dict) -> dict:
    assert status == 200
    assert payload["ok"] is True
    assert "ts_utc" in payload
    assert payload["ts_utc"].endswith("Z") or "+" in payload["ts_utc"]
    assert "data" in payload
    return payload["data"]


class TestConstantsAndGuards(unittest.TestCase):
    def test_bind_and_client_id_defaults(self) -> None:
        self.assertEqual(BIND_HOST, "127.0.0.1")
        self.assertEqual(DEFAULT_PORT, 8787)
        self.assertEqual(TWS_CLIENT_ID, 71)
        self.assertEqual(TWS_PORT, 7497)
        self.assertEqual(validate_bind_host("127.0.0.1"), "127.0.0.1")
        self.assertEqual(validate_bind_host("localhost"), "127.0.0.1")
        self.assertEqual(validate_client_id(71), 71)
        self.assertEqual(REQUEST_TIMEOUT_SEC, 20.0)

    def test_refuse_non_loopback_bind(self) -> None:
        with self.assertRaises(ValueError):
            validate_bind_host("0.0.0.0")
        with self.assertRaises(ValueError):
            validate_bind_host("192.168.1.10")

    def test_reserved_client_ids(self) -> None:
        for cid in RESERVED_CLIENT_IDS:
            self.assertTrue(is_reserved_client_id(cid), cid)
            with self.assertRaises(ValueError):
                validate_client_id(cid)
        self.assertTrue(is_reserved_client_id(3910))
        self.assertTrue(is_reserved_client_id(39105))
        with self.assertRaises(ValueError):
            validate_client_id(39105)


class TestEnvelopeAndRouter(unittest.TestCase):
    def test_success_and_error_envelope_shape(self) -> None:
        ok = success_envelope({"x": 1})
        self.assertEqual(set(ok), {"ok", "ts_utc", "data"})
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["data"], {"x": 1})
        err = error_envelope("TWS_DISCONNECTED", "down")
        self.assertEqual(set(err), {"ok", "error"})
        self.assertFalse(err["ok"])
        self.assertEqual(err["error"], {"code": "TWS_DISCONNECTED", "message": "down"})

    def test_health_no_account_ids(self) -> None:
        status, payload = handle_request("GET", "/v1/health", {}, None, FakeSession())
        data = _ok(status, payload)
        self.assertEqual(
            set(data.keys()),
            {"tws_connected", "host", "port", "readonly", "accounts_n", "client_id"},
        )
        self.assertTrue(data["tws_connected"])
        self.assertTrue(data["readonly"])
        self.assertEqual(data["client_id"], 71)
        self.assertEqual(data["port"], 7497)
        blob = json.dumps(data)
        self.assertNotIn("DUR", blob)
        self.assertNotIn("account", blob.lower().replace("accounts_n", ""))

    def test_health_works_when_tws_down(self) -> None:
        status, payload = handle_request(
            "GET", "/v1/health", {}, None, FakeSession(connected=False)
        )
        data = _ok(status, payload)
        self.assertFalse(data["tws_connected"])
        self.assertEqual(data["accounts_n"], 0)

    def test_underlying_quote(self) -> None:
        status, payload = handle_request(
            "GET", "/v1/underlying/quote", {"symbol": ["SPY"]}, None, FakeSession()
        )
        data = _ok(status, payload)
        for key in ("conId", "bid", "ask", "last", "close", "mid", "market_price"):
            self.assertIn(key, data)

    def test_option_chain(self) -> None:
        status, payload = handle_request(
            "GET", "/v1/options/chain", {"symbol": ["SPY"]}, None, FakeSession()
        )
        data = _ok(status, payload)
        self.assertEqual(data["underlying_conId"], 756733)
        self.assertIn("exchanges", data)
        ex0 = data["exchanges"][0]
        for key in ("exchange", "tradingClass", "multiplier", "expirations", "strikes"):
            self.assertIn(key, ex0)

    def test_qualify(self) -> None:
        body = {
            "contracts": [
                {
                    "symbol": "SPY",
                    "expiry": "20260918",
                    "strike": 555,
                    "right": "P",
                }
            ]
        }
        status, payload = handle_request(
            "POST", "/v1/options/qualify", {}, body, FakeSession()
        )
        data = _ok(status, payload)
        self.assertTrue(data["contracts"][0]["ok"])
        self.assertEqual(data["contracts"][0]["conId"], 1000)

    def test_option_quote_by_conid_and_fields(self) -> None:
        s = FakeSession()
        status, payload = handle_request(
            "GET", "/v1/options/quote", {"conId": ["123456789"]}, None, s
        )
        data = _ok(status, payload)
        for key in ("bid", "ask", "last", "close", "mid"):
            self.assertIn(key, data)
        status, payload = handle_request(
            "GET",
            "/v1/options/quote",
            {
                "symbol": ["SPY"],
                "expiry": ["20260918"],
                "strike": ["555"],
                "right": ["P"],
            },
            None,
            s,
        )
        _ok(status, payload)

    def test_batch_quotes(self) -> None:
        status, payload = handle_request(
            "POST", "/v1/options/quotes", {}, {"conIds": [1, 2]}, FakeSession()
        )
        data = _ok(status, payload)
        self.assertEqual(len(data["quotes"]), 2)

    def test_hist_bars(self) -> None:
        status, payload = handle_request(
            "GET",
            "/v1/options/hist",
            {
                "conId": ["9"],
                "bar_size": ["1 hour"],
                "duration": ["10 D"],
                "what": ["MIDPOINT"],
                "use_rth": ["true"],
            },
            None,
            FakeSession(),
        )
        data = _ok(status, payload)
        self.assertEqual(data["bars"][0]["close"], 1.22)
        self.assertEqual(data["what"], "MIDPOINT")

    def test_put_credit_snapshot(self) -> None:
        status, payload = handle_request(
            "POST",
            "/v1/phase9a/put_credit_snapshot",
            {},
            {
                "symbol": "SPY",
                "und_px": 560,
                "dte_min": 20,
                "dte_max": 45,
                "short_moneyness": 0.95,
            },
            FakeSession(),
        )
        data = _ok(status, payload)
        for key in ("und_px", "expiry", "short", "long", "credit_mid", "width"):
            self.assertIn(key, data)
        self.assertEqual(data["short"]["right"], "P")
        self.assertEqual(data["width"], 5.0)

    def test_data_route_503_when_disconnected(self) -> None:
        dead = FakeSession(connected=False)
        for method, path, query, body in (
            ("GET", "/v1/underlying/quote", {"symbol": ["SPY"]}, None),
            ("GET", "/v1/options/chain", {"symbol": ["SPY"]}, None),
            ("GET", "/v1/options/quote", {"conId": ["1"]}, None),
            ("GET", "/v1/options/hist", {"conId": ["1"]}, None),
            ("POST", "/v1/options/qualify", {}, {"contracts": [{"symbol": "SPY"}]}),
            ("POST", "/v1/options/quotes", {}, {"conIds": [1]}),
            (
                "POST",
                "/v1/phase9a/put_credit_snapshot",
                {},
                {
                    "symbol": "SPY",
                    "dte_min": 1,
                    "dte_max": 2,
                    "short_moneyness": 0.95,
                },
            ),
        ):
            status, payload = handle_request(method, path, query, body, dead)
            self.assertEqual(status, 503, path)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error"]["code"], "TWS_DISCONNECTED")

    def test_data_route_504_on_tws_timeout(self) -> None:
        class TimeoutSession(FakeSession):
            def underlying_quote(self, symbol: str) -> dict:
                raise TwsTimeout("underlying_quote SPY exceeded 20.0s")

            def option_chain(self, symbol: str) -> dict:
                raise TwsTimeout("reqSecDefOptParams SPY exceeded 20.0s")

        sess = TimeoutSession()
        for path in ("/v1/underlying/quote", "/v1/options/chain"):
            status, payload = handle_request(
                "GET", path, {"symbol": ["SPY"]}, None, sess
            )
            self.assertEqual(status, 504, path)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error"]["code"], "TWS_TIMEOUT")
            self.assertIn("exceeded", payload["error"]["message"])

    def test_old_sketch_paths_are_gone(self) -> None:
        for path in ("/health", "/v1/option-chain", "/v1/underlying", "/v1/hist-bars"):
            status, payload = handle_request("GET", path, {"symbol": ["SPY"]}, None, FakeSession())
            self.assertEqual(status, 404, path)
            self.assertFalse(payload["ok"])

    def test_order_routes_refused(self) -> None:
        s = FakeSession()
        for path in (
            "/v1/order",
            "/v1/orders",
            "/placeOrder",
            "/v1/place_order",
            "/cancel",
            "/v1/modify",
            "/bracket",
            "/v1/trade",
        ):
            self.assertTrue(path_looks_like_order(path), path)
            st_get, p_get = handle_request("GET", path, {}, None, s)
            self.assertEqual(st_get, 403, path)
            self.assertEqual(p_get["error"]["code"], "ORDER_ROUTE_FORBIDDEN")
            st_post, p_post = handle_request("POST", path, {}, {"qty": 1}, s)
            self.assertEqual(st_post, 405, path)
            self.assertEqual(p_post["error"]["code"], "ORDER_ROUTE_FORBIDDEN")

    def test_qualify_is_not_an_order_route(self) -> None:
        self.assertFalse(path_looks_like_order("/v1/options/qualify"))
        self.assertFalse(path_looks_like_order("/v1/options/quote"))
        self.assertFalse(path_looks_like_order("/v1/phase9a/put_credit_snapshot"))


class TestMoneynessHelpers(unittest.TestCase):
    def test_short_put_target_strike(self) -> None:
        self.assertAlmostEqual(short_put_target_strike(100.0, 0.95), 95.0)
        self.assertAlmostEqual(short_put_target_strike(100.0, 0.05), 95.0)

    def test_strike_picks(self) -> None:
        strikes = [90.0, 95.0, 100.0, 105.0]
        self.assertEqual(_nearest_strike_at_or_below(strikes, 96.0), 95.0)
        self.assertEqual(_next_lower_strike(strikes, 95.0), 90.0)
        self.assertIsNone(_next_lower_strike(strikes, 90.0))

    def test_pick_expiry_in_dte_window(self) -> None:
        from datetime import datetime, timedelta, timezone

        today = datetime.now(timezone.utc).date()
        mid = today + timedelta(days=30)
        far = today + timedelta(days=90)
        chosen = _pick_expiry(
            [mid.strftime("%Y%m%d"), far.strftime("%Y%m%d")],
            20,
            45,
        )
        self.assertEqual(chosen, mid.strftime("%Y%m%d"))


class TestIsolationAndNoOrders(unittest.TestCase):
    def test_no_strategy_imports(self) -> None:
        for path in BRIDGE_PY:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            names: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        names.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names.add(node.module.split(".")[0])
            hit = names & FORBIDDEN_IMPORTS
            self.assertFalse(hit, f"{path.name} imports {hit}")

    def test_source_has_no_order_api_calls(self) -> None:
        """placeOrder may appear only as a blocked-method name, never as a call."""
        for path in BRIDGE_PY:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    attr = getattr(func, "attr", "") if isinstance(func, ast.Attribute) else ""
                    name = getattr(func, "id", "") if isinstance(func, ast.Name) else ""
                    self.assertNotIn(
                        attr,
                        {"placeOrder", "cancelOrder", "reqGlobalCancel", "whatIfOrder"},
                        f"{path.name} calls {attr}()",
                    )
                    self.assertNotIn(
                        name,
                        {"placeOrder", "cancelOrder"},
                        f"{path.name} calls {name}()",
                    )

    def test_order_blocked_proxy(self) -> None:
        class _Raw:
            def placeOrder(self, *_a, **_k):  # noqa: N802 — mirrors ib_insync
                return "placed"

            def reqMktData(self, *_a, **_k):  # noqa: N802
                return "ok"

        proxy = _OrderBlockedIB(_Raw())
        self.assertEqual(proxy.reqMktData(), "ok")
        with self.assertRaises(Exception) as ctx:
            proxy.placeOrder("SPY", object())
        self.assertIn("ORDER", getattr(ctx.exception, "code", "") or str(ctx.exception))


class TestBoundedIbWaits(unittest.TestCase):
    """Timeouts without TWS — hang a dummy job, never ib.connect."""

    def test_submit_times_out_instead_of_hanging(self) -> None:
        session = ReadOnlyIBSession()
        release = threading.Event()
        try:

            def hang() -> None:
                release.wait(timeout=30)

            t0 = time.monotonic()
            with self.assertRaises(TwsTimeout) as ctx:
                session._submit(hang, timeout=0.3, what="hang-test")
            elapsed = time.monotonic() - t0
            self.assertLess(elapsed, 2.0)
            self.assertEqual(ctx.exception.code, "TWS_TIMEOUT")
            self.assertEqual(ctx.exception.status, 504)
        finally:
            release.set()
            session.close()

    def test_health_payload_not_blocked_by_hung_job(self) -> None:
        session = ReadOnlyIBSession()
        started = threading.Event()
        release = threading.Event()
        try:

            def hang() -> None:
                started.set()
                release.wait(timeout=30)

            worker = threading.Thread(
                target=lambda: session._submit(hang, timeout=1.5, what="hang-health"),
                daemon=True,
            )
            worker.start()
            self.assertTrue(started.wait(timeout=1.0))
            t0 = time.monotonic()
            payload = session.health_payload()
            self.assertLess(time.monotonic() - t0, 0.4)
            self.assertIn("tws_connected", payload)
            self.assertIn("client_id", payload)
            release.set()
            worker.join(timeout=3.0)
        finally:
            release.set()
            session.close()


class TestHttpLoopback(unittest.TestCase):
    """Spin a real stdlib server on 127.0.0.1 — still no TWS."""

    def test_server_is_threading(self) -> None:
        self.assertTrue(issubclass(OptionsBridgeServer, ThreadingHTTPServer))

    def test_http_health_and_order_refuse(self) -> None:
        session = FakeSession()
        server = OptionsBridgeServer(("127.0.0.1", 0), session)
        host, port = server.server_address[:2]
        self.assertEqual(host, "127.0.0.1")
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.daemon = True
        thread.start()
        try:
            with urlopen(f"http://127.0.0.1:{port}/v1/health", timeout=2) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(body["ok"])
            self.assertTrue(body["data"]["readonly"])
            self.assertEqual(body["data"]["client_id"], 71)

            req = Request(
                f"http://127.0.0.1:{port}/v1/order",
                data=b"{}",
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(HTTPError) as ctx:
                urlopen(req, timeout=2)
            self.assertEqual(ctx.exception.code, 405)
            err_body = json.loads(ctx.exception.read().decode("utf-8"))
            self.assertEqual(err_body["error"]["code"], "ORDER_ROUTE_FORBIDDEN")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_http_health_stays_up_while_quote_is_slow(self) -> None:
        class SlowSession(FakeSession):
            def underlying_quote(self, symbol: str) -> dict:
                time.sleep(1.5)
                return super().underlying_quote(symbol)

        session = SlowSession()
        server = OptionsBridgeServer(("127.0.0.1", 0), session)
        _host, port = server.server_address[:2]
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.daemon = True
        thread.start()
        quote_err: list[BaseException] = []

        def _quote() -> None:
            try:
                urlopen(f"http://127.0.0.1:{port}/v1/underlying/quote?symbol=SPY", timeout=3)
            except BaseException as exc:  # noqa: BLE001 — collect for the test thread
                quote_err.append(exc)

        try:
            qthread = threading.Thread(target=_quote, daemon=True)
            qthread.start()
            time.sleep(0.15)
            t0 = time.monotonic()
            with urlopen(f"http://127.0.0.1:{port}/v1/health", timeout=0.6) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            self.assertLess(time.monotonic() - t0, 0.6)
            self.assertTrue(body["ok"])
            self.assertEqual(body["data"]["client_id"], 71)
            qthread.join(timeout=3.0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
