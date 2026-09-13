"""
Local-only HTTP JSON server for the BSF Phase 9A options bridge.

WHAT: stdlib ThreadingHTTPServer on 127.0.0.1:8787, Phase 9A paths only.
WHY: BSF calls HTTP; this process is the only one that opens ib_insync.

No Peak Hour / TSD imports. No order routes. Bind loopback only.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# Allow `python candidates/options_bridge/server.py` from repo root.
_BRIDGE_DIR = Path(__file__).resolve().parent
_CANDIDATES_DIR = _BRIDGE_DIR.parent
if str(_CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(_CANDIDATES_DIR))

from options_bridge.config import (  # noqa: E402
    ALLOWED_GET_PATHS,
    ALLOWED_POST_PATHS,
    BIND_HOST,
    DEFAULT_BAR_SIZE,
    DEFAULT_DURATION,
    DEFAULT_PORT,
    DEFAULT_WHAT,
    MAX_BODY_BYTES,
    ORDER_PATH_FRAGMENTS,
    TWS_CLIENT_ID,
    TWS_HOST,
    TWS_PORT,
    env_int,
    env_str,
    validate_bind_host,
    validate_client_id,
)
from options_bridge.errors import (  # noqa: E402
    BridgeError,
    OrderRouteForbidden,
    TwsDisconnected,
    TwsTimeout,
)
from options_bridge.ib_session import ReadOnlyIBSession, _parse_bool  # noqa: E402


def utc_now_iso() -> str:
    """UTC ISO-8601 timestamp with Z suffix (Phase 9A envelope)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def success_envelope(data: Any) -> dict[str, Any]:
    """{"ok":true,"ts_utc":"...","data":...}"""
    return {"ok": True, "ts_utc": utc_now_iso(), "data": data}


def error_envelope(code: str, message: str) -> dict[str, Any]:
    """{"ok":false,"error":{"code":"...","message":"..."}}"""
    return {"ok": False, "error": {"code": code, "message": message}}


def path_looks_like_order(path: str) -> bool:
    """True when the URL is an order/cancel/modify/bracket/trade route."""
    token = (path or "").lower()
    return any(frag in token for frag in ORDER_PATH_FRAGMENTS)


def _qfirst(query: dict[str, list[str]], *names: str, default: str | None = None) -> str | None:
    """First non-empty query value among alias names."""
    for name in names:
        vals = query.get(name) or []
        if vals and str(vals[0]).strip() != "":
            return str(vals[0]).strip()
    return default


def _qfloat(query: dict[str, list[str]], *names: str) -> float | None:
    raw = _qfirst(query, *names)
    if raw is None:
        return None
    return float(raw)


def _qint(query: dict[str, list[str]], *names: str) -> int | None:
    raw = _qfirst(query, *names)
    if raw is None:
        return None
    return int(float(raw))


def handle_request(
    method: str,
    path: str,
    query: dict[str, list[str]],
    body: Any,
    session: ReadOnlyIBSession,
) -> tuple[int, dict[str, Any]]:
    """
    Pure router used by the HTTP handler and unit tests.

    Returns (http_status, envelope_dict). Never opens a raw TWS socket itself.
    """
    method_u = (method or "GET").upper()
    path_n = (path or "/").rstrip("/") or "/"

    if path_looks_like_order(path_n):
        # 403 for order nouns; 405 if they also used a write method on a fake order API.
        err = OrderRouteForbidden(path_n)
        status = 405 if method_u in {"POST", "PUT", "PATCH", "DELETE"} else 403
        err.status = status
        return status, error_envelope(err.code, err.message)

    if method_u == "GET" and path_n in ALLOWED_GET_PATHS:
        return _handle_get(path_n, query, session)
    if method_u == "POST" and path_n in ALLOWED_POST_PATHS:
        return _handle_post(path_n, body if isinstance(body, dict) else {}, session)

    if method_u in {"POST", "PUT", "PATCH", "DELETE"}:
        return 405, error_envelope(
            "METHOD_NOT_ALLOWED",
            f"{method_u} {path_n} is not a read-only Phase 9A route. "
            "Allowed POST: /v1/options/qualify, /v1/options/quotes, "
            "/v1/phase9a/put_credit_snapshot.",
        )
    return 404, error_envelope(
        "NOT_FOUND",
        f"unknown path {path_n}. See candidates/options_bridge/README.md",
    )


def _handle_get(
    path: str,
    query: dict[str, list[str]],
    session: ReadOnlyIBSession,
) -> tuple[int, dict[str, Any]]:
    if path == "/v1/health":
        return 200, success_envelope(session.health_payload())

    # Remaining GETs need TWS. Health stays up when paper is closed.
    try:
        if path == "/v1/underlying/quote":
            symbol = _qfirst(query, "symbol")
            return 200, success_envelope(session.underlying_quote(symbol or ""))

        if path == "/v1/options/chain":
            symbol = _qfirst(query, "symbol")
            return 200, success_envelope(session.option_chain(symbol or ""))

        if path == "/v1/options/quote":
            data = session.option_quote(
                con_id=_qint(query, "conId", "conid"),
                symbol=_qfirst(query, "symbol"),
                expiry=_qfirst(query, "expiry"),
                strike=_qfloat(query, "strike"),
                right=_qfirst(query, "right"),
                exchange=_qfirst(query, "exchange") or "SMART",
                currency=_qfirst(query, "currency") or "USD",
            )
            return 200, success_envelope(data)

        if path == "/v1/options/hist":
            data = session.hist_bars(
                con_id=_qint(query, "conId", "conid"),
                symbol=_qfirst(query, "symbol"),
                expiry=_qfirst(query, "expiry"),
                strike=_qfloat(query, "strike"),
                right=_qfirst(query, "right"),
                sec_type=_qfirst(query, "secType", "sec_type"),
                bar_size=_qfirst(query, "bar_size", "barSize") or DEFAULT_BAR_SIZE,
                duration=_qfirst(query, "duration") or DEFAULT_DURATION,
                what=_qfirst(query, "what", "whatToShow") or DEFAULT_WHAT,
                use_rth=_parse_bool(_qfirst(query, "use_rth", "useRTH"), True),
                exchange=_qfirst(query, "exchange") or "SMART",
                currency=_qfirst(query, "currency") or "USD",
            )
            return 200, success_envelope(data)
    except TwsTimeout as exc:
        return exc.status, error_envelope(exc.code, exc.message)
    except TwsDisconnected as exc:
        return exc.status, error_envelope(exc.code, exc.message)
    except BridgeError as exc:
        return exc.status, error_envelope(exc.code, exc.message)
    except ValueError as exc:
        return 400, error_envelope("BAD_REQUEST", str(exc))

    return 404, error_envelope("NOT_FOUND", f"unknown path {path}")


def _handle_post(
    path: str,
    body: dict[str, Any],
    session: ReadOnlyIBSession,
) -> tuple[int, dict[str, Any]]:
    try:
        if path == "/v1/options/qualify":
            contracts = session.qualify_contracts(body.get("contracts") or [])
            return 200, success_envelope({"contracts": contracts})

        if path == "/v1/options/quotes":
            quotes = session.option_quotes(body.get("conIds") or body.get("con_ids") or [])
            return 200, success_envelope({"quotes": quotes})

        if path == "/v1/phase9a/put_credit_snapshot":
            und_raw = body.get("und_px")
            data = session.put_credit_snapshot(
                symbol=str(body.get("symbol") or ""),
                und_px=float(und_raw) if und_raw is not None and und_raw != "" else None,
                dte_min=int(body.get("dte_min")),
                dte_max=int(body.get("dte_max")),
                short_moneyness=float(body.get("short_moneyness")),
            )
            return 200, success_envelope(data)
    except TwsTimeout as exc:
        return exc.status, error_envelope(exc.code, exc.message)
    except TwsDisconnected as exc:
        return exc.status, error_envelope(exc.code, exc.message)
    except BridgeError as exc:
        return exc.status, error_envelope(exc.code, exc.message)
    except (TypeError, ValueError, KeyError) as exc:
        return 400, error_envelope("BAD_REQUEST", str(exc))

    return 404, error_envelope("NOT_FOUND", f"unknown path {path}")


class OptionsBridgeHandler(BaseHTTPRequestHandler):
    """stdlib HTTP handler — JSON only, loopback server injects `.session`."""

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        """Write access lines to stdout (start script redirects to the daily log)."""
        sys.stdout.write("%s - %s\n" % (utc_now_iso(), fmt % args))
        sys.stdout.flush()

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def do_OPTIONS(self) -> None:
        # No CORS wildcard. Local BSF clients do not need it; refuse verb clearly.
        self._write(405, error_envelope("METHOD_NOT_ALLOWED", "OPTIONS is not served"))

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path or "/"
        query = parse_qs(parsed.query, keep_blank_values=False)
        body: Any = {}
        if method in {"POST", "PUT", "PATCH"}:
            try:
                body = self._read_json_body()
            except BridgeError as exc:
                self._write(exc.status, error_envelope(exc.code, exc.message))
                return
        session = getattr(self.server, "session", None)
        if session is None:
            self._write(500, error_envelope("INTERNAL", "server session missing"))
            return
        try:
            status, payload = handle_request(method, path, query, body, session)
        except TwsTimeout as exc:
            status, payload = exc.status, error_envelope(exc.code, exc.message)
        except TwsDisconnected as exc:
            status, payload = exc.status, error_envelope(exc.code, exc.message)
        except BridgeError as exc:
            status, payload = exc.status, error_envelope(exc.code, exc.message)
        except Exception as exc:
            traceback.print_exc()
            status, payload = 500, error_envelope("INTERNAL", f"unhandled: {exc}")
        self._write(status, payload)

    def _read_json_body(self) -> dict[str, Any]:
        raw_len = self.headers.get("Content-Length") or "0"
        try:
            length = int(raw_len)
        except ValueError as exc:
            raise BridgeError("BAD_REQUEST", "invalid Content-Length", status=400) from exc
        if length > MAX_BODY_BYTES:
            raise BridgeError("BAD_REQUEST", "request body too large", status=413)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BridgeError("BAD_REQUEST", f"invalid JSON body: {exc}", status=400) from exc
        if parsed is None:
            return {}
        if not isinstance(parsed, dict):
            raise BridgeError("BAD_REQUEST", "JSON body must be an object", status=400)
        return parsed

    def _write(self, status: int, payload: dict[str, Any]) -> None:
        blob = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(blob)


class OptionsBridgeServer(ThreadingHTTPServer):
    """
    Threading HTTP server that holds the read-only IB session.

    WHY ThreadingHTTPServer: a hung MD/secdef call on one worker must not
    stall /v1/health or order-refuse on another worker.
    """

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], session: ReadOnlyIBSession) -> None:
        self.session = session
        super().__init__(server_address, OptionsBridgeHandler)


def build_arg_parser() -> argparse.ArgumentParser:
    """CLI / env overrides. Host must remain loopback; clientId must not be reserved."""
    parser = argparse.ArgumentParser(
        description="Q-ALPHA read-only options data bridge (BSF Phase 9A). TWS paper 7497 / clientId 71.",
    )
    parser.add_argument(
        "--host",
        default=env_str("OPTIONS_BRIDGE_HOST", BIND_HOST),
        help="HTTP bind host (127.0.0.1 only)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=env_int("OPTIONS_BRIDGE_PORT", DEFAULT_PORT),
        help="HTTP port (default 8787)",
    )
    parser.add_argument(
        "--tws-host",
        default=env_str("OPTIONS_BRIDGE_TWS_HOST", TWS_HOST),
        help="TWS host (default 127.0.0.1)",
    )
    parser.add_argument(
        "--tws-port",
        type=int,
        default=env_int("OPTIONS_BRIDGE_TWS_PORT", TWS_PORT),
        help="TWS paper API port (default 7497)",
    )
    parser.add_argument(
        "--client-id",
        type=int,
        default=env_int("OPTIONS_BRIDGE_CLIENT_ID", TWS_CLIENT_ID),
        help="Dedicated TWS clientId (default 71)",
    )
    parser.add_argument(
        "--no-connect",
        action="store_true",
        help="Do not connect to TWS at startup (health still serves; data endpoints reconnect lazily)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """
    Start the loopback server. Tries one TWS connect unless --no-connect.

    Does not exit if TWS is down — /v1/health stays available for BSF.
    """
    args = build_arg_parser().parse_args(argv)
    bind_host = validate_bind_host(args.host)
    client_id = validate_client_id(args.client_id)
    tws_host = validate_bind_host(args.tws_host)

    session = ReadOnlyIBSession(
        tws_host=tws_host,
        tws_port=int(args.tws_port),
        client_id=client_id,
    )
    if not args.no_connect:
        try:
            session.connect()
            print(
                f"[{utc_now_iso()}] TWS connected "
                f"{session.tws_host}:{session.tws_port} clientId={session.client_id} "
                f"accounts_n={session.health_payload().get('accounts_n')}",
                flush=True,
            )
        except TwsDisconnected as exc:
            print(
                f"[{utc_now_iso()}] TWS not connected yet: {exc.message} "
                "(HTTP up; data endpoints return 503 until paper API is up)",
                flush=True,
            )

    server = OptionsBridgeServer((bind_host, int(args.port)), session)
    actual_host, actual_port = server.server_address[:2]
    print(
        f"[{utc_now_iso()}] options_bridge listening http://{actual_host}:{actual_port} "
        f"readonly=true TWS={session.tws_host}:{session.tws_port} clientId={session.client_id}",
        flush=True,
    )
    print(
        f"[{utc_now_iso()}] Phase 9A: /v1/health /v1/underlying/quote /v1/options/chain "
        f"/v1/options/qualify /v1/options/quote /v1/options/quotes /v1/options/hist "
        f"/v1/phase9a/put_credit_snapshot — order routes refused",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print(f"[{utc_now_iso()}] shutting down", flush=True)
    finally:
        try:
            closer = getattr(session, "close", None) or session.disconnect
            closer()
        except Exception:
            pass
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
