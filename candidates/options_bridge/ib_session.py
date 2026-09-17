"""
Thin READ-ONLY ib_insync session for the options bridge.

WHAT: connect to laptop TWS paper, snapshots, option chain, qualify, hist bars.
WHY: BSF Phase 9A needs option/underlying data without opening ib_insync itself.

Never imports Peak Hour / TSD / book-state modules.
Never calls placeOrder / cancelOrder / reqGlobalCancel / whatIfOrder.
"""
from __future__ import annotations

import asyncio
import math
import queue
import threading
import time
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import date, datetime, timezone
from typing import Any, Callable, TypeVar

from options_bridge.config import (
    CONNECT_TIMEOUT_SEC,
    DEFAULT_BAR_SIZE,
    DEFAULT_DURATION,
    DEFAULT_WHAT,
    INTER_QUOTE_SLEEP_SEC,
    MAX_BATCH_QUOTES,
    MAX_QUALIFY_CONTRACTS,
    RECONNECT_ATTEMPTS,
    SNAPSHOT_WAIT_SEC,
    TWS_CLIENT_ID,
    TWS_HOST,
    TWS_PORT,
    request_timeout_sec,
    validate_client_id,
)
from options_bridge.errors import BridgeError, TwsDisconnected, TwsTimeout

T = TypeVar("T")


def ensure_thread_event_loop() -> asyncio.AbstractEventLoop:
    """
    Give this thread an asyncio loop before ib_insync / eventkit import.

    WHY: eventkit calls asyncio.get_event_loop() at import time. The dedicated
    options-bridge-ib worker is not the main thread, so Python 3.10+ raises
    RuntimeError: There is no current event loop in thread 'options-bridge-ib'.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop is not None and not loop.is_closed():
            return loop
    except RuntimeError:
        pass
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop

# IB write / order methods — blocked even if a future caller reaches the IB object.
_BLOCKED_IB_METHODS = frozenset(
    {
        "placeOrder",
        "placeOrderAsync",
        "cancelOrder",
        "cancelOrderAsync",
        "reqGlobalCancel",
        "whatIfOrder",
        "whatIfOrderAsync",
        "exercisePositions",
    }
)


def _finite(val: Any) -> float | None:
    """Return a finite float or None (IB uses NaN for missing ticks)."""
    if val is None:
        return None
    try:
        num = float(val)
    except (TypeError, ValueError):
        return None
    if math.isnan(num) or math.isinf(num):
        return None
    return num


def _mid(bid: float | None, ask: float | None) -> float | None:
    """Mid from bid/ask when both sides are live."""
    if bid is None or ask is None:
        return None
    if bid <= 0 or ask <= 0:
        return None
    return (bid + ask) / 2.0


def _market_price(
    last: float | None,
    mid: float | None,
    close: float | None,
    bid: float | None,
    ask: float | None,
) -> float | None:
    """Best available mark: last, else mid, else close, else one-sided quote."""
    for candidate in (last, mid, close, bid, ask):
        if candidate is not None:
            return candidate
    return None


def _iso_utc_now() -> str:
    """UTC timestamp for envelopes and logs (ISO-8601, Z suffix)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _bar_ts(bar: Any) -> str:
    """Normalize an ib_insync bar date to an ISO-8601 string."""
    raw = getattr(bar, "date", None)
    if raw is None:
        return ""
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            return raw.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        return raw.isoformat()
    if isinstance(raw, date):
        return datetime(raw.year, raw.month, raw.day, tzinfo=timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
    return str(raw)


def _norm_right(right: str) -> str:
    """Normalize CALL/PUT / C/P to IB's C or P."""
    token = str(right or "").strip().upper()
    if token in {"C", "CALL"}:
        return "C"
    if token in {"P", "PUT"}:
        return "P"
    raise BridgeError("BAD_REQUEST", f"right must be C/P or CALL/PUT (got {right!r})", status=400)


def _norm_expiry(expiry: str) -> str:
    """Accept YYYYMMDD or YYYY-MM-DD; store YYYYMMDD."""
    token = str(expiry or "").strip().replace("-", "")
    if len(token) != 8 or not token.isdigit():
        raise BridgeError(
            "BAD_REQUEST",
            f"expiry must be YYYYMMDD (got {expiry!r})",
            status=400,
        )
    return token


def _parse_bool(raw: Any, default: bool) -> bool:
    """Parse use_rth query/body flags."""
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    token = str(raw).strip().lower()
    if token in {"1", "true", "yes", "y"}:
        return True
    if token in {"0", "false", "no", "n"}:
        return False
    return default


def short_put_target_strike(und_px: float, short_moneyness: float) -> float:
    """
    Target short-put strike from spot and moneyness.

    WHY: Phase 9A put-credit snapshot needs one short strike near a moneyness.
    If short_moneyness > 0.5 treat as K/S (0.95 = 5% OTM put).
    If short_moneyness <= 0.5 treat as OTM fraction (0.05 = 5% OTM put).
    """
    if und_px <= 0:
        raise BridgeError("BAD_REQUEST", "und_px must be positive", status=400)
    if short_moneyness <= 0:
        raise BridgeError("BAD_REQUEST", "short_moneyness must be positive", status=400)
    if short_moneyness > 0.5:
        return und_px * short_moneyness
    return und_px * (1.0 - short_moneyness)


class _OrderBlockedIB:
    """Proxy that raises if any order-entry method is invoked."""

    def __init__(self, ib: Any) -> None:
        self._ib = ib

    def __getattr__(self, name: str) -> Any:
        if name in _BLOCKED_IB_METHODS:
            def _blocked(*_a: Any, **_k: Any) -> None:
                raise BridgeError(
                    "ORDER_ROUTE_FORBIDDEN",
                    f"Read-only IB session blocked {name}()",
                    status=403,
                )

            return _blocked
        return getattr(self._ib, name)


class ReadOnlyIBSession:
    """
    Long-lived TWS paper session used by the HTTP handler.

    ib_insync is not thread-safe. All IB calls run on one dedicated worker
    thread. HTTP threads wait on a Future capped at REQUEST_TIMEOUT_SEC.
    /v1/health never joins that queue and never calls into ib_insync.
    """

    def __init__(
        self,
        *,
        tws_host: str = TWS_HOST,
        tws_port: int = TWS_PORT,
        client_id: int = TWS_CLIENT_ID,
        connect_timeout: float = CONNECT_TIMEOUT_SEC,
    ) -> None:
        self.tws_host = tws_host
        self.tws_port = int(tws_port)
        self.client_id = validate_client_id(client_id)
        self.connect_timeout = float(connect_timeout)
        self._state_lock = threading.Lock()
        self._ib: Any = None
        self._raw_ib: Any = None
        self._accounts_n = 0
        self._connected_flag = False
        self._jobs: queue.Queue[tuple[Callable[[], Any], Future] | None] = queue.Queue()
        self._stop = threading.Event()
        self._worker_loop: asyncio.AbstractEventLoop | None = None
        self._ib_thread = threading.Thread(
            target=self._ib_worker_loop,
            name="options-bridge-ib",
            daemon=True,
        )
        self._ib_thread.start()

    @property
    def connected(self) -> bool:
        """True when the last successful connect is still believed live.

        WHY: health must not call ib.isConnected() — that can block if MD is wedged.
        """
        with self._state_lock:
            return bool(self._connected_flag)

    def health_payload(self) -> dict[str, Any]:
        """Phase 9A /v1/health data — no IB wait, no account ids, no secrets."""
        with self._state_lock:
            connected = bool(self._connected_flag)
            accounts_n = int(self._accounts_n) if connected else 0
        return {
            "tws_connected": connected,
            "host": self.tws_host,
            "port": self.tws_port,
            "readonly": True,
            "accounts_n": accounts_n,
            "client_id": self.client_id,
        }

    def connect(self) -> None:
        """Connect once. Raises TwsDisconnected on failure (bounded timeout)."""
        self._submit(
            self._connect_unlocked,
            timeout=self.connect_timeout + 1.0,
            what="tws_connect",
        )

    def disconnect(self) -> None:
        """Drop the TWS socket if present (bounded)."""
        try:
            self._submit(
                self._disconnect_unlocked,
                timeout=min(self.connect_timeout, request_timeout_sec()),
                what="tws_disconnect",
            )
        except TwsTimeout:
            self._abandon_hung_socket("tws_disconnect")

    def close(self) -> None:
        """Disconnect and stop the IB worker thread (process shutdown)."""
        try:
            self.disconnect()
        except Exception:
            pass
        self._stop.set()
        self._jobs.put(None)

    def ensure_connected(self) -> None:
        """Reconnect up to RECONNECT_ATTEMPTS times; then raise 503."""
        self._submit(
            self._ensure_connected_impl,
            timeout=self.connect_timeout * RECONNECT_ATTEMPTS + 2.0,
            what="tws_ensure_connected",
        )

    def underlying_quote(self, symbol: str) -> dict[str, Any]:
        """Snapshot bid/ask/last/close/mid/market_price for a stock/ETF."""
        sym = _require_symbol(symbol)
        return self._submit(
            lambda: self._underlying_quote_impl(sym),
            what=f"underlying_quote {sym}",
        )

    def option_chain(self, symbol: str) -> dict[str, Any]:
        """
        SecDef option params for symbol.

        exchanges are richest-strikes-first so BSF can take [0] as the
        primary SMART/CBOE-style listing without scanning empty venues.
        """
        sym = _require_symbol(symbol)
        return self._submit(
            lambda: self._option_chain_impl(sym),
            what=f"option_chain {sym}",
        )

    def qualify_contracts(self, specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Qualify option specs; each row gets conId/ok/error (no 500 on one miss)."""
        if not isinstance(specs, list) or not specs:
            raise BridgeError("BAD_REQUEST", "body.contracts must be a non-empty list", status=400)
        if len(specs) > MAX_QUALIFY_CONTRACTS:
            raise BridgeError(
                "BAD_REQUEST",
                f"at most {MAX_QUALIFY_CONTRACTS} contracts per qualify call",
                status=400,
            )
        return self._submit(
            lambda: self._qualify_contracts_impl(specs),
            what="qualify_contracts",
        )

    def option_quote(
        self,
        *,
        con_id: int | None = None,
        symbol: str | None = None,
        expiry: str | None = None,
        strike: float | None = None,
        right: str | None = None,
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> dict[str, Any]:
        """Single option (or stock-by-conId) snapshot."""
        return self._submit(
            lambda: self._option_quote_impl(
                con_id=con_id,
                symbol=symbol,
                expiry=expiry,
                strike=strike,
                right=right,
                exchange=exchange,
                currency=currency,
            ),
            what="option_quote",
        )

    def option_quotes(self, con_ids: list[int]) -> list[dict[str, Any]]:
        """Batch snapshots by conId. Paced to stay under TWS snapshot load."""
        if not isinstance(con_ids, list) or not con_ids:
            raise BridgeError("BAD_REQUEST", "body.conIds must be a non-empty list", status=400)
        if len(con_ids) > MAX_BATCH_QUOTES:
            raise BridgeError(
                "BAD_REQUEST",
                f"at most {MAX_BATCH_QUOTES} conIds per quotes call",
                status=400,
            )
        return self._submit(
            lambda: self._option_quotes_impl(con_ids),
            what="option_quotes",
        )

    def hist_bars(
        self,
        *,
        con_id: int | None = None,
        symbol: str | None = None,
        expiry: str | None = None,
        strike: float | None = None,
        right: str | None = None,
        sec_type: str | None = None,
        bar_size: str = DEFAULT_BAR_SIZE,
        duration: str = DEFAULT_DURATION,
        what: str = DEFAULT_WHAT,
        use_rth: bool = True,
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> dict[str, Any]:
        """
        Historical bars for a stock or option contract.

        Default what=MIDPOINT / bar_size=1 hour matches Phase 9A.
        """
        return self._submit(
            lambda: self._hist_bars_impl(
                con_id=con_id,
                symbol=symbol,
                expiry=expiry,
                strike=strike,
                right=right,
                sec_type=sec_type,
                bar_size=bar_size,
                duration=duration,
                what=what,
                use_rth=use_rth,
                exchange=exchange,
                currency=currency,
            ),
            what="hist_bars",
        )

    def put_credit_snapshot(
        self,
        *,
        symbol: str,
        und_px: float | None,
        dte_min: int,
        dte_max: int,
        short_moneyness: float,
    ) -> dict[str, Any]:
        """
        Phase 9A put-credit helper: pick expiry in DTE window, short/long puts.

        Short strike ≈ moneyness target; long strike = next lower listed strike.
        credit_mid = short mid − long mid. width = short strike − long strike.
        """
        if dte_min < 0 or dte_max < 0 or dte_min > dte_max:
            raise BridgeError("BAD_REQUEST", "dte_min/dte_max must satisfy 0 <= min <= max", status=400)
        return self._submit(
            lambda: self._put_credit_impl(
                symbol=symbol,
                und_px=und_px,
                dte_min=dte_min,
                dte_max=dte_max,
                short_moneyness=short_moneyness,
            ),
            what="put_credit_snapshot",
        )

    def _put_credit_impl(
        self,
        *,
        symbol: str,
        und_px: float | None,
        dte_min: int,
        dte_max: int,
        short_moneyness: float,
    ) -> dict[str, Any]:
        quote = None
        if und_px is None:
            quote = self._underlying_quote_impl(symbol)
            mark = quote.get("market_price")
            if mark is None:
                raise BridgeError(
                    "NO_UNDERLYING_PX",
                    f"no mark for {symbol}; pass und_px",
                    status=502,
                )
            und_px = float(mark)
        else:
            und_px = float(und_px)
        chain = self._option_chain_impl(symbol)
        exchanges = chain.get("exchanges") or []
        if not exchanges:
            raise BridgeError("CHAIN_EMPTY", f"no option exchanges for {symbol}", status=502)
        primary = exchanges[0]
        expiry = _pick_expiry(primary.get("expirations") or [], dte_min, dte_max)
        strikes = [float(s) for s in (primary.get("strikes") or [])]
        if len(strikes) < 2:
            raise BridgeError("CHAIN_EMPTY", "need at least two strikes for a put credit", status=502)
        target = short_put_target_strike(und_px, float(short_moneyness))
        short_k = _nearest_strike_at_or_below(strikes, target)
        long_k = _next_lower_strike(strikes, short_k)
        if long_k is None:
            raise BridgeError(
                "NO_LONG_STRIKE",
                f"no lower strike under short {short_k} for a put credit",
                status=502,
            )
        exchange = str(primary.get("exchange") or "SMART")
        qualified = self._qualify_contracts_impl(
            [
                {
                    "symbol": symbol,
                    "expiry": expiry,
                    "strike": short_k,
                    "right": "P",
                    "exchange": exchange,
                },
                {
                    "symbol": symbol,
                    "expiry": expiry,
                    "strike": long_k,
                    "right": "P",
                    "exchange": exchange,
                },
            ]
        )
        short_q = qualified[0]
        long_q = qualified[1]
        if not short_q.get("ok") or not long_q.get("ok"):
            raise BridgeError(
                "QUALIFY_FAILED",
                f"put legs: short={short_q.get('error')} long={long_q.get('error')}",
                status=502,
            )
        short_px = self._option_quote_impl(con_id=int(short_q["conId"]))
        long_px = self._option_quote_impl(con_id=int(long_q["conId"]))
        short_mid = short_px.get("mid")
        long_mid = long_px.get("mid")
        credit = None
        if short_mid is not None and long_mid is not None:
            credit = float(short_mid) - float(long_mid)
        return {
            "symbol": _require_symbol(symbol),
            "und_px": und_px,
            "expiry": expiry,
            "short": {
                "conId": short_px.get("conId"),
                "strike": short_k,
                "right": "P",
                "bid": short_px.get("bid"),
                "ask": short_px.get("ask"),
                "mid": short_mid,
            },
            "long": {
                "conId": long_px.get("conId"),
                "strike": long_k,
                "right": "P",
                "bid": long_px.get("bid"),
                "ask": long_px.get("ask"),
                "mid": long_mid,
            },
            "credit_mid": credit,
            "width": float(short_k) - float(long_k),
        }

    # ----- IB worker + timeouts (never block /v1/health) -----

    def _ib_worker_loop(self) -> None:
        """Single thread that owns ib_insync. HTTP threads only wait on Futures."""
        # Must run BEFORE any ib_insync import/connect on this thread.
        loop = ensure_thread_event_loop()
        self._worker_loop = loop
        try:
            while not self._stop.is_set():
                try:
                    item = self._jobs.get(timeout=0.25)
                except queue.Empty:
                    continue
                if item is None:
                    return
                fn, fut = item
                try:
                    fut.set_result(fn())
                except BaseException as exc:
                    fut.set_exception(exc)
        finally:
            if loop is not None and not loop.is_closed() and not loop.is_running():
                try:
                    loop.close()
                except Exception:
                    pass
            self._worker_loop = None

    def _submit(self, fn: Callable[[], T], *, timeout: float | None = None, what: str = "TWS request") -> T:
        """
        Run fn on the IB thread. HTTP callers wait at most REQUEST_TIMEOUT_SEC.

        Nested calls from the IB thread run inline (no self-deadlock).
        """
        cap = float(timeout if timeout is not None else request_timeout_sec())
        if threading.current_thread() is self._ib_thread:
            return fn()
        fut: Future = Future()
        self._jobs.put((fn, fut))
        try:
            return fut.result(timeout=cap)
        except FuturesTimeout as exc:
            self._abandon_hung_socket(what)
            raise TwsTimeout(
                f"{what} exceeded {cap:.1f}s (TWS did not finish; retry when paper MD responds)"
            ) from exc

    def _watchdog(self, fn: Callable[[], T], *, timeout: float | None = None, what: str = "TWS request") -> T:
        """
        Bound one IB primitive on the IB thread.

        A Timer disconnects the socket if fn does not return — that is how we
        unblock reqSecDefOptParams / qualifyContracts / reqMktData with no timeout kwarg.
        """
        cap = float(timeout if timeout is not None else request_timeout_sec())
        timed_out = threading.Event()

        def kill() -> None:
            timed_out.set()
            self._abandon_hung_socket(what)

        timer = threading.Timer(cap, kill)
        timer.daemon = True
        timer.start()
        try:
            result = fn()
        except Exception as exc:
            if timed_out.is_set():
                raise TwsTimeout(f"{what} exceeded {cap:.1f}s") from exc
            raise
        finally:
            timer.cancel()
        if timed_out.is_set():
            raise TwsTimeout(f"{what} exceeded {cap:.1f}s")
        return result

    def _abandon_hung_socket(self, what: str) -> None:
        """Drop the TWS socket from a non-IB thread so a hung call can unblock."""
        with self._state_lock:
            raw = self._raw_ib
            self._raw_ib = None
            self._ib = None
            self._accounts_n = 0
            self._connected_flag = False
        if raw is None:
            return
        try:
            raw.disconnect()
        except Exception:
            pass

    def _ensure_connected_impl(self) -> None:
        if self._connected_flag and self._raw_ib is not None:
            try:
                if self._raw_ib.isConnected():
                    return
            except Exception:
                pass
        last_exc: Exception | None = None
        for _attempt in range(RECONNECT_ATTEMPTS):
            try:
                self._connect_unlocked()
                return
            except TwsDisconnected as exc:
                last_exc = exc
        raise last_exc or TwsDisconnected()

    def _underlying_quote_impl(self, sym: str) -> dict[str, Any]:
        self._ensure_connected_impl()
        contract = self._qualify_stock_unlocked(sym)
        snap = self._snapshot_unlocked(contract)
        return {
            "symbol": sym,
            "conId": int(contract.conId) if getattr(contract, "conId", None) else None,
            **snap,
            "market_price": _market_price(
                snap.get("last"),
                snap.get("mid"),
                snap.get("close"),
                snap.get("bid"),
                snap.get("ask"),
            ),
        }

    def _option_chain_impl(self, sym: str) -> dict[str, Any]:
        self._ensure_connected_impl()
        ib = self._require_ib_unlocked()
        stock = self._qualify_stock_unlocked(sym)
        und_id = int(stock.conId)
        try:
            chains = self._watchdog(
                lambda: ib.reqSecDefOptParams(sym, "", "STK", und_id),
                what=f"reqSecDefOptParams {sym}",
            )
        except TwsTimeout:
            raise
        except Exception as exc:
            raise BridgeError(
                "CHAIN_FAILED",
                f"reqSecDefOptParams failed for {sym}: {exc}",
                status=502,
            ) from exc
        exchanges: list[dict[str, Any]] = []
        for ch in chains or []:
            expirations = sorted({str(x) for x in (getattr(ch, "expirations", None) or [])})
            strikes = sorted({float(x) for x in (getattr(ch, "strikes", None) or [])})
            exchanges.append(
                {
                    "exchange": str(getattr(ch, "exchange", "") or ""),
                    "tradingClass": str(getattr(ch, "tradingClass", "") or ""),
                    "multiplier": str(getattr(ch, "multiplier", "") or ""),
                    "expirations": expirations,
                    "strikes": strikes,
                }
            )
        exchanges.sort(key=lambda row: len(row["strikes"]), reverse=True)
        return {
            "symbol": sym,
            "underlying_conId": und_id,
            "exchanges": exchanges,
        }

    def _qualify_contracts_impl(self, specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self._ensure_connected_impl()
        return [self._qualify_one_unlocked(spec) for spec in specs]

    def _option_quote_impl(
        self,
        *,
        con_id: int | None = None,
        symbol: str | None = None,
        expiry: str | None = None,
        strike: float | None = None,
        right: str | None = None,
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> dict[str, Any]:
        self._ensure_connected_impl()
        contract = self._resolve_contract_unlocked(
            con_id=con_id,
            symbol=symbol,
            expiry=expiry,
            strike=strike,
            right=right,
            exchange=exchange,
            currency=currency,
        )
        snap = self._snapshot_unlocked(contract)
        return {
            "symbol": str(getattr(contract, "symbol", "") or symbol or ""),
            "conId": int(contract.conId) if getattr(contract, "conId", None) else None,
            "expiry": str(getattr(contract, "lastTradeDateOrContractMonth", "") or expiry or ""),
            "strike": _finite(getattr(contract, "strike", None)) if hasattr(contract, "strike") else None,
            "right": str(getattr(contract, "right", "") or right or ""),
            **snap,
        }

    def _option_quotes_impl(self, con_ids: list[int]) -> list[dict[str, Any]]:
        self._ensure_connected_impl()
        rows: list[dict[str, Any]] = []
        for i, raw_id in enumerate(con_ids):
            try:
                cid = int(raw_id)
                contract = self._qualify_conid_unlocked(cid)
                snap = self._snapshot_unlocked(contract)
                rows.append({"conId": cid, "ok": True, "error": None, **snap})
            except BridgeError as exc:
                rows.append(
                    {
                        "conId": raw_id,
                        "ok": False,
                        "error": exc.message,
                        "bid": None,
                        "ask": None,
                        "last": None,
                        "close": None,
                        "mid": None,
                    }
                )
            if i + 1 < len(con_ids):
                time.sleep(INTER_QUOTE_SLEEP_SEC)
        return rows

    def _hist_bars_impl(
        self,
        *,
        con_id: int | None,
        symbol: str | None,
        expiry: str | None,
        strike: float | None,
        right: str | None,
        sec_type: str | None,
        bar_size: str,
        duration: str,
        what: str,
        use_rth: bool,
        exchange: str,
        currency: str,
    ) -> dict[str, Any]:
        self._ensure_connected_impl()
        ib = self._require_ib_unlocked()
        if con_id:
            contract = self._qualify_conid_unlocked(int(con_id))
        elif (sec_type or "").upper() in {"STK", "STOCK", ""} and not expiry:
            if not symbol:
                raise BridgeError(
                    "BAD_REQUEST",
                    "hist requires conId or symbol (+ option fields if OPT)",
                    status=400,
                )
            contract = self._qualify_stock_unlocked(_require_symbol(symbol))
        else:
            contract = self._resolve_contract_unlocked(
                con_id=con_id,
                symbol=symbol,
                expiry=expiry,
                strike=strike,
                right=right,
                exchange=exchange,
                currency=currency,
            )

        def _hist() -> Any:
            try:
                return ib.reqHistoricalData(
                    contract,
                    endDateTime="",
                    durationStr=duration,
                    barSizeSetting=bar_size,
                    whatToShow=what,
                    useRTH=bool(use_rth),
                    formatDate=1,
                    timeout=request_timeout_sec(),
                )
            except TypeError:
                return ib.reqHistoricalData(
                    contract,
                    endDateTime="",
                    durationStr=duration,
                    barSizeSetting=bar_size,
                    whatToShow=what,
                    useRTH=bool(use_rth),
                    formatDate=1,
                )

        try:
            bars = self._watchdog(_hist, what="reqHistoricalData")
        except TwsTimeout:
            raise
        except Exception as exc:
            raise BridgeError("HIST_FAILED", f"reqHistoricalData failed: {exc}", status=502) from exc
        out_bars = []
        for bar in bars or []:
            out_bars.append(
                {
                    "ts": _bar_ts(bar),
                    "open": _finite(getattr(bar, "open", None)),
                    "high": _finite(getattr(bar, "high", None)),
                    "low": _finite(getattr(bar, "low", None)),
                    "close": _finite(getattr(bar, "close", None)),
                }
            )
        return {
            "conId": int(contract.conId) if getattr(contract, "conId", None) else None,
            "symbol": str(getattr(contract, "symbol", "") or symbol or ""),
            "bar_size": bar_size,
            "duration": duration,
            "what": what,
            "use_rth": bool(use_rth),
            "bars": out_bars,
        }

    def _connect_unlocked(self) -> None:
        if self._connected_flag and self._raw_ib is not None:
            try:
                if self._raw_ib.isConnected():
                    return
            except Exception:
                pass
        self._disconnect_unlocked()
        # Defense in depth: loop must exist before ib_insync/eventkit import.
        ensure_thread_event_loop()
        try:
            from ib_insync import IB, util
        except ImportError as exc:
            raise TwsDisconnected(
                "ib_insync is not installed in this venv. Use repo venv python."
            ) from exc
        try:
            util.startLoop()
        except Exception:
            # Already running or no loop needed — continue with blocking API.
            pass
        raw = IB()
        try:
            raw.connect(
                self.tws_host,
                self.tws_port,
                clientId=self.client_id,
                timeout=self.connect_timeout,
                readonly=True,
            )
        except TypeError:
            # Older ib_insync may not accept readonly= — still never place orders.
            try:
                raw.connect(
                    self.tws_host,
                    self.tws_port,
                    clientId=self.client_id,
                    timeout=self.connect_timeout,
                )
            except Exception as exc:
                try:
                    raw.disconnect()
                except Exception:
                    pass
                raise TwsDisconnected(f"TWS connect failed: {exc}") from exc
        except Exception as exc:
            try:
                raw.disconnect()
            except Exception:
                pass
            raise TwsDisconnected(f"TWS connect failed: {exc}") from exc
        if not raw.isConnected():
            try:
                raw.disconnect()
            except Exception:
                pass
            raise TwsDisconnected("TWS connect returned but isConnected() is false.")
        try:
            accounts = list(raw.managedAccounts() or [])
            accounts_n = len(accounts)
        except Exception:
            accounts_n = 0
        with self._state_lock:
            self._raw_ib = raw
            self._ib = _OrderBlockedIB(raw)
            self._accounts_n = accounts_n
            self._connected_flag = True

    def _disconnect_unlocked(self) -> None:
        with self._state_lock:
            raw = self._raw_ib
            self._ib = None
            self._raw_ib = None
            self._accounts_n = 0
            self._connected_flag = False
        if raw is None:
            return
        try:
            raw.disconnect()
        except Exception:
            pass

    def _require_ib_unlocked(self) -> Any:
        if self._ib is None or not self._connected_flag:
            raise TwsDisconnected()
        return self._ib

    def _qualify_stock_unlocked(self, symbol: str) -> Any:
        from ib_insync import Stock

        ib = self._require_ib_unlocked()
        contract = Stock(symbol, "SMART", "USD")
        qualified = self._watchdog(
            lambda: ib.qualifyContracts(contract),
            what=f"qualifyContracts {symbol}",
        )
        if not qualified:
            raise BridgeError("QUALIFY_FAILED", f"could not qualify stock {symbol}", status=502)
        return qualified[0]

    def _qualify_conid_unlocked(self, con_id: int) -> Any:
        from ib_insync import Contract

        ib = self._require_ib_unlocked()
        contract = Contract(conId=int(con_id))
        qualified = self._watchdog(
            lambda: ib.qualifyContracts(contract),
            what=f"qualifyContracts conId={con_id}",
        )
        if not qualified:
            raise BridgeError("QUALIFY_FAILED", f"could not qualify conId={con_id}", status=502)
        return qualified[0]

    def _resolve_contract_unlocked(
        self,
        *,
        con_id: int | None,
        symbol: str | None,
        expiry: str | None,
        strike: float | None,
        right: str | None,
        exchange: str,
        currency: str,
    ) -> Any:
        if con_id:
            return self._qualify_conid_unlocked(int(con_id))
        if not symbol or not expiry or strike is None or not right:
            raise BridgeError(
                "BAD_REQUEST",
                "need conId or symbol+expiry+strike+right",
                status=400,
            )
        from ib_insync import Option

        ib = self._require_ib_unlocked()
        contract = Option(
            _require_symbol(symbol),
            _norm_expiry(expiry),
            float(strike),
            _norm_right(right),
            exchange or "SMART",
            currency=currency or "USD",
        )
        qualified = self._watchdog(
            lambda: ib.qualifyContracts(contract),
            what=f"qualifyContracts {_require_symbol(symbol)} {expiry} {strike}{right}",
        )
        if not qualified:
            raise BridgeError(
                "QUALIFY_FAILED",
                f"could not qualify {_require_symbol(symbol)} {expiry} {strike}{right}",
                status=502,
            )
        return qualified[0]

    def _qualify_one_unlocked(self, spec: dict[str, Any]) -> dict[str, Any]:
        symbol = str((spec or {}).get("symbol") or "").upper()
        expiry = spec.get("expiry")
        strike = spec.get("strike")
        right = spec.get("right")
        exchange = str(spec.get("exchange") or "SMART")
        currency = str(spec.get("currency") or "USD")
        row = {
            "symbol": symbol,
            "expiry": str(expiry or ""),
            "strike": strike,
            "right": str(right or ""),
            "exchange": exchange,
            "currency": currency,
            "conId": None,
            "ok": False,
            "error": None,
        }
        try:
            contract = self._resolve_contract_unlocked(
                con_id=int(spec["conId"]) if spec.get("conId") else None,
                symbol=symbol,
                expiry=str(expiry) if expiry else None,
                strike=float(strike) if strike is not None else None,
                right=str(right) if right else None,
                exchange=exchange,
                currency=currency,
            )
            row["conId"] = int(contract.conId) if getattr(contract, "conId", None) else None
            row["ok"] = row["conId"] is not None
            if not row["ok"]:
                row["error"] = "qualified but conId missing"
        except BridgeError as exc:
            row["error"] = exc.message
        except Exception as exc:
            row["error"] = str(exc)
        return row

    def _snapshot_unlocked(self, contract: Any) -> dict[str, Any]:
        ib = self._require_ib_unlocked()
        ticker = None
        try:
            ticker = self._watchdog(
                lambda: ib.reqMktData(contract, "", False, False),
                what="reqMktData",
            )
            wait = getattr(ib, "waitOnUpdate", None)
            if callable(wait):
                wait(timeout=SNAPSHOT_WAIT_SEC)
            else:
                # Wall-clock poll — do not use ib.sleep (can deadlock off-loop).
                deadline = time.monotonic() + SNAPSHOT_WAIT_SEC
                while time.monotonic() < deadline:
                    if any(
                        _finite(getattr(ticker, attr, None))
                        for attr in ("last", "close", "bid", "ask")
                    ):
                        break
                    time.sleep(0.05)
            bid = _finite(getattr(ticker, "bid", None))
            ask = _finite(getattr(ticker, "ask", None))
            last = _finite(getattr(ticker, "last", None))
            close = _finite(getattr(ticker, "close", None))
            mid = _mid(bid, ask)
            return {"bid": bid, "ask": ask, "last": last, "close": close, "mid": mid}
        except TwsTimeout:
            raise
        except Exception as exc:
            raise BridgeError("QUOTE_FAILED", f"reqMktData failed: {exc}", status=502) from exc
        finally:
            if ticker is not None:
                try:
                    ib.cancelMktData(contract)
                except Exception:
                    pass


def _require_symbol(symbol: str) -> str:
    token = str(symbol or "").strip().upper()
    if not token or not token.replace(".", "").replace("-", "").isalnum():
        raise BridgeError("BAD_REQUEST", "symbol is required (e.g. SPY)", status=400)
    return token


def _pick_expiry(expirations: list[str], dte_min: int, dte_max: int) -> str:
    """Pick YYYYMMDD in [dte_min, dte_max], closest to the window midpoint."""
    today = datetime.now(timezone.utc).date()
    target_dte = (int(dte_min) + int(dte_max)) / 2.0
    best: tuple[float, str] | None = None
    for raw in expirations:
        token = str(raw).replace("-", "")
        if len(token) != 8 or not token.isdigit():
            continue
        try:
            exp_d = date(int(token[:4]), int(token[4:6]), int(token[6:8]))
        except ValueError:
            continue
        dte = (exp_d - today).days
        if dte < dte_min or dte > dte_max:
            continue
        dist = abs(dte - target_dte)
        if best is None or dist < best[0]:
            best = (dist, token)
    if best is None:
        raise BridgeError(
            "NO_EXPIRY",
            f"no expiration with DTE in [{dte_min}, {dte_max}]",
            status=502,
        )
    return best[1]


def _nearest_strike_at_or_below(strikes: list[float], target: float) -> float:
    below = [s for s in strikes if s <= target + 1e-9]
    if below:
        return max(below)
    return min(strikes, key=lambda s: abs(s - target))


def _next_lower_strike(strikes: list[float], short_k: float) -> float | None:
    lower = [s for s in strikes if s < short_k - 1e-9]
    if not lower:
        return None
    return max(lower)
