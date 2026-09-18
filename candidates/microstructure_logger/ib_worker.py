"""
Read-only IBKR worker: dedicated thread + asyncio loop.

WHY: ib_insync only drains its queue while the event loop on *its* thread
runs (same lesson as the options bridge). All waits are bounded.
This module never places or cancels orders and never sends Telegram.

Depth path (no BATS/BEX required):
- reqMktData (L1/tape) for the full symbol list.
- reqMktDepth capped at --depth-max (default 3; IB Error 309).
- SMART depth aggregates entitled venues (ARCA / NYSE / IEX).
- Error 2152 for NASDAQ/BATS/BEX is expected: log once, honest PARTIAL label.
- Empty SMART DOM may fall back to IEX-native depth (still entitled, no BATS/BEX).
"""
from __future__ import annotations

import asyncio
import math
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .constants import (
    DEPTH_ROWS,
    DEPTH_SOURCE,
    DEPTH_SOURCE_IEX_NATIVE,
    DEPTH_SOURCE_L1_ONLY,
    DEPTH_SOURCE_PARTIAL_SMART,
    FORBIDDEN_CLIENT_IDS,
    IB_CALL_TIMEOUT_SEC,
    IB_CONNECT_TIMEOUT_SEC,
    IB_DISCONNECT_TIMEOUT_SEC,
    IB_QUALIFY_TIMEOUT_SEC,
    IB_SUBSCRIBE_TIMEOUT_SEC,
    IB_WORKER_READY_TIMEOUT_SEC,
    IEX_FALLBACK_WAIT_SEC,
    SMART_DEPTH,
    TAPE_POLL_SEC,
    TWS_CLIENT_ID,
    TWS_HOST,
    TWS_LIVE_PORT,
    TWS_PAPER_PORT,
)
from .depth import (
    DEFAULT_DEPTH_MAX,
    IB_ACCOUNT_DEPTH_CAP,
    IB_ERR_DEPTH_PERMISSION,
    ExpectedDepthErrorGate,
    classify_depth_source,
    install_ib_depth_log_filter,
    select_depth_symbols,
    should_try_iex_fallback,
)
from .features import BookLevel, _finite


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def assert_paper_endpoint(host: str, port: int, client_id: int) -> None:
    """Refuse live port, cloud hosts, and occupied clientIds."""
    if host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError(f"refusing non-local TWS host {host!r} (no cloud Gateway)")
    if int(port) == TWS_LIVE_PORT:
        raise RuntimeError("refusing live TWS port 7496 — paper 7497 only")
    if int(port) != TWS_PAPER_PORT:
        raise RuntimeError(f"refusing TWS port {port} — paper 7497 only")
    if int(client_id) != TWS_CLIENT_ID or int(client_id) in FORBIDDEN_CLIENT_IDS:
        raise RuntimeError(
            f"refusing clientId {client_id} — microstructure logger is hard-pinned to {TWS_CLIENT_ID}"
        )


def _block_orders(*_args: Any, **_kwargs: Any) -> None:
    """Installed over IB order methods so a mistake cannot submit."""
    raise RuntimeError("READ-ONLY microstructure logger: orders are forbidden")


def _contract_symbol(contract: Any) -> str | None:
    sym = getattr(contract, "symbol", None) if contract is not None else None
    if not sym:
        return None
    text = str(sym).strip().upper()
    return text or None


@dataclass
class DepthSub:
    """One live reqMktDepth line (counts toward IB's concurrent cap)."""

    symbol: str
    contract: Any
    is_smart: bool
    iex_native: bool = False


@dataclass
class BookQuote:
    """Latest L1 + depth snapshot pulled off the IB ticker."""

    ts: datetime
    bids: list[BookLevel] = field(default_factory=list)
    asks: list[BookLevel] = field(default_factory=list)
    depth_source: str = DEPTH_SOURCE
    error_2152: bool = False


@dataclass
class RawPrint:
    """One tape print captured on the IB thread."""

    ts: datetime
    price: float
    size: float


class IBWorker:
    """
    Background ib_insync connection.

    Public methods are called from the main thread and hop onto the worker
    loop via run_coroutine_threadsafe with a timeout.
    """

    def __init__(
        self,
        *,
        host: str = TWS_HOST,
        port: int = TWS_PAPER_PORT,
        client_id: int = TWS_CLIENT_ID,
        depth_max: int = DEFAULT_DEPTH_MAX,
        depth_symbols: list[str] | None = None,
    ) -> None:
        assert_paper_endpoint(host, port, client_id)
        self.host = host
        self.port = int(port)
        self.client_id = int(client_id)
        self.depth_max = max(0, int(depth_max))
        self._depth_override = [s.strip().upper() for s in (depth_symbols or []) if str(s).strip()]
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ib: Any = None
        self._ready = threading.Event()
        self._fail: BaseException | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._tickers: dict[str, Any] = {}
        self._depth_tickers: dict[str, Any] = {}
        self._depth_subs: dict[str, DepthSub] = {}
        self._md_contracts: dict[str, Any] = {}
        self._depth_source_by_symbol: dict[str, str] = {}
        self._error_2152_by_symbol: dict[str, bool] = {}
        self._books: dict[str, BookQuote] = {}
        self._prints: dict[str, deque[RawPrint]] = defaultdict(deque)
        self._last_print_key: dict[str, tuple[Any, ...]] = {}
        self._last_volume: dict[str, float] = {}
        self.depth_source = DEPTH_SOURCE
        self.error_2152 = False
        self.saw_error_codes: set[int] = set()
        self.error_gate = ExpectedDepthErrorGate()
        self.depth_symbols: list[str] = []

    def start(self) -> None:
        """Spawn the IB thread, connect, and wait (bounded) until ready."""
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._thread_main,
            name="qalpha-l2-ib72",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=IB_WORKER_READY_TIMEOUT_SEC):
            raise TimeoutError(
                f"IB worker did not become ready in {IB_WORKER_READY_TIMEOUT_SEC:.0f}s"
            )
        if self._fail is not None:
            raise RuntimeError(f"IB worker failed to start: {self._fail}") from self._fail

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._async_connect())
            loop.create_task(self._poll_loop())
            self._ready.set()
            loop.run_forever()
        except Exception as exc:
            self._fail = exc
            self._ready.set()
        finally:
            try:
                if self._ib is not None and getattr(self._ib, "isConnected", lambda: False)():
                    self._ib.disconnect()
            except Exception:
                pass
            try:
                loop.stop()
            except Exception:
                pass

    async def _async_connect(self) -> None:
        from ib_insync import IB

        install_ib_depth_log_filter()
        ib = IB()
        ib.placeOrder = _block_orders
        ib.cancelOrder = _block_orders
        ib.reqGlobalCancel = _block_orders
        ib.whatIfOrder = _block_orders
        ib.errorEvent += self._on_error
        await asyncio.wait_for(
            ib.connectAsync(
                self.host,
                self.port,
                clientId=self.client_id,
                timeout=IB_CONNECT_TIMEOUT_SEC,
                readonly=True,
            ),
            timeout=IB_CONNECT_TIMEOUT_SEC + 2.0,
        )
        self._ib = ib

    def _on_error(self, req_id: Any, error_code: Any, error_string: Any, contract: Any) -> None:
        """
        Record IB codes. 2152 NASDAQ/BATS/BEX = expected partial SMART depth.

        Logs once per process (ExpectedDepthErrorGate). Never Telegram.
        """
        try:
            code = int(error_code)
        except (TypeError, ValueError):
            return
        self.saw_error_codes.add(code)
        line = self.error_gate.handle(code, error_string, contract)
        if line:
            print(f"  {line}")
        if code != IB_ERR_DEPTH_PERMISSION:
            return
        self.error_2152 = True
        sym = _contract_symbol(contract)
        if not sym:
            return
        self._error_2152_by_symbol[sym] = True
        # Do not clobber an IEX-native label with the SMART partial tag.
        if self._depth_source_by_symbol.get(sym) == DEPTH_SOURCE_IEX_NATIVE:
            return
        if self._depth_subs.get(sym) is not None:
            self._depth_source_by_symbol[sym] = DEPTH_SOURCE_PARTIAL_SMART
            self.depth_source = DEPTH_SOURCE_PARTIAL_SMART

    async def _poll_loop(self) -> None:
        """Ingest L1/L2/tape at 100ms so 1s snapshots do not miss prints."""
        while not self._stop.is_set():
            try:
                self._ingest_all()
            except Exception:
                pass
            await asyncio.sleep(TAPE_POLL_SEC)

    def _ingest_all(self) -> None:
        now = _utcnow()
        with self._lock:
            items = list(self._tickers.items())
            depth_tickers = dict(self._depth_tickers)
        for symbol, ticker in items:
            depth_ticker = depth_tickers.get(symbol, ticker)
            book = self._book_from_ticker(symbol, ticker, depth_ticker, now)
            prints = self._prints_from_ticker(symbol, ticker, now)
            with self._lock:
                self._books[symbol] = book
                q = self._prints[symbol]
                for rec in prints:
                    q.append(rec)
                while len(q) > 5_000:
                    q.popleft()

    def _label_for(self, symbol: str) -> str:
        return self._depth_source_by_symbol.get(
            symbol.upper(),
            classify_depth_source(
                has_depth_sub=symbol.upper() in self._depth_subs,
                iex_native=bool(getattr(self._depth_subs.get(symbol.upper()), "iex_native", False)),
                saw_2152=self.error_2152 or self._error_2152_by_symbol.get(symbol.upper(), False),
            ),
        )

    def _book_from_ticker(
        self,
        symbol: str,
        md_ticker: Any,
        depth_ticker: Any,
        now: datetime,
    ) -> BookQuote:
        bids = _dom_levels(getattr(depth_ticker, "domBids", None))
        asks = _dom_levels(getattr(depth_ticker, "domAsks", None))
        if not bids:
            bid = _finite(getattr(md_ticker, "bid", None))
            bid_sz = _finite(getattr(md_ticker, "bidSize", None))
            if bid is not None and bid_sz is not None and bid > 0 and bid_sz >= 0:
                bids = [BookLevel(price=bid, size=bid_sz)]
        if not asks:
            ask = _finite(getattr(md_ticker, "ask", None))
            ask_sz = _finite(getattr(md_ticker, "askSize", None))
            if ask is not None and ask_sz is not None and ask > 0 and ask_sz >= 0:
                asks = [BookLevel(price=ask, size=ask_sz)]
        label = self._label_for(symbol)
        return BookQuote(
            ts=now,
            bids=bids,
            asks=asks,
            depth_source=label,
            error_2152=self._error_2152_by_symbol.get(symbol.upper(), self.error_2152),
        )

    def _prints_from_ticker(self, symbol: str, ticker: Any, now: datetime) -> list[RawPrint]:
        """
        Detect new prints via last/lastSize identity or session volume delta.

        WHY: identical px+sz in the same second still increments volume.
        """
        out: list[RawPrint] = []
        last = _finite(getattr(ticker, "last", None))
        last_sz = _finite(getattr(ticker, "lastSize", None))
        tstamp = getattr(ticker, "time", None)
        key = (last, last_sz, str(tstamp) if tstamp is not None else None)
        prev_key = self._last_print_key.get(symbol)
        if last is not None and last_sz is not None and last > 0 and last_sz > 0:
            if key != prev_key:
                out.append(RawPrint(ts=now, price=last, size=last_sz))
                self._last_print_key[symbol] = key

        vol = _finite(getattr(ticker, "volume", None))
        if vol is not None and vol >= 0:
            prev_vol = self._last_volume.get(symbol)
            self._last_volume[symbol] = vol
            if prev_vol is not None and vol > prev_vol and last is not None and last > 0:
                delta = vol - prev_vol
                # Skip if we already recorded this increment via lastSize.
                if not out or abs(out[-1].size - delta) > 1e-9:
                    if delta > 0 and math.isfinite(delta):
                        out.append(RawPrint(ts=now, price=last, size=delta))
        return out

    def _call(self, coro: Any, timeout: float) -> Any:
        if self._loop is None:
            raise RuntimeError("IB worker loop is not running")
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=timeout)

    def subscribe(self, symbols: list[str]) -> list[str]:
        """Qualify + reqMktData (all) + reqMktDepth (capped). Returns L1 symbols."""
        n = max(len(symbols), 1)
        timeout = (
            IB_SUBSCRIBE_TIMEOUT_SEC
            + IB_QUALIFY_TIMEOUT_SEC * n
            + IEX_FALLBACK_WAIT_SEC
            + IB_QUALIFY_TIMEOUT_SEC * max(self.depth_max, 1)
            + 5.0
        )
        return self._call(self._async_subscribe(symbols), timeout)

    def _can_request_depth(self) -> bool:
        """Hard stop before IB Error 309: never open more depth lines than depth_max."""
        return len(self._depth_subs) < self.depth_max

    def _request_mkt_depth(
        self,
        ib: Any,
        contract: Any,
        *,
        symbol: str,
        is_smart: bool,
        iex_native: bool,
        ticker: Any | None = None,
    ) -> bool:
        """
        Open one reqMktDepth if under the concurrent cap.

        Returns False (and does not call IB) when the cap is already full.
        """
        if not self._can_request_depth():
            print(
                f"  skip depth {symbol}: depth-max={self.depth_max} "
                f"(IB Error 309 cap={IB_ACCOUNT_DEPTH_CAP})"
            )
            self._depth_source_by_symbol[symbol] = DEPTH_SOURCE_L1_ONLY
            return False
        ib.reqMktDepth(contract, numRows=DEPTH_ROWS, isSmartDepth=is_smart)
        self._depth_subs[symbol] = DepthSub(
            symbol=symbol,
            contract=contract,
            is_smart=is_smart,
            iex_native=iex_native,
        )
        if ticker is not None:
            self._depth_tickers[symbol] = ticker
        self._depth_source_by_symbol[symbol] = classify_depth_source(
            has_depth_sub=True,
            iex_native=iex_native,
            saw_2152=self.error_2152,
        )
        return True

    def _cancel_one_depth(self, ib: Any, sub: DepthSub) -> None:
        try:
            ib.cancelMktDepth(sub.contract, isSmartDepth=sub.is_smart)
        except Exception:
            pass

    async def _async_subscribe(self, symbols: list[str]) -> list[str]:
        from ib_insync import Stock

        ib = self._ib
        if ib is None:
            raise RuntimeError("IB not connected")
        if self.depth_max > IB_ACCOUNT_DEPTH_CAP:
            print(
                f"  warn: --depth-max={self.depth_max} exceeds IB paper cap "
                f"{IB_ACCOUNT_DEPTH_CAP} (Error 309 may fire)"
            )
        ok: list[str] = []
        primary: dict[str, str] = {}
        for raw in symbols:
            symbol = raw.upper()
            contract = Stock(symbol, "SMART", "USD")
            qualified = await asyncio.wait_for(
                ib.qualifyContractsAsync(contract),
                timeout=IB_QUALIFY_TIMEOUT_SEC,
            )
            if not qualified:
                print(f"  qualify FAIL {symbol}")
                continue
            contract = qualified[0]
            ticker = ib.reqMktData(contract, "", False, False)
            with self._lock:
                self._tickers[symbol] = ticker
                self._md_contracts[symbol] = contract
            primary[symbol] = str(getattr(contract, "primaryExchange", "") or "")
            self._depth_source_by_symbol[symbol] = DEPTH_SOURCE_L1_ONLY
            ok.append(symbol)
            print(f"  subscribed {symbol} L1/tape exchange=SMART")

        depth_list = select_depth_symbols(
            ok,
            self.depth_max,
            depth_symbols=self._depth_override or None,
            primary_exchange=primary,
        )
        self.depth_symbols = list(depth_list)
        for symbol in depth_list:
            contract = self._md_contracts.get(symbol)
            ticker = self._tickers.get(symbol)
            if contract is None:
                continue
            opened = self._request_mkt_depth(
                ib,
                contract,
                symbol=symbol,
                is_smart=SMART_DEPTH,
                iex_native=False,
                ticker=ticker,
            )
            if opened:
                print(
                    f"  subscribed {symbol} SMART depth rows={DEPTH_ROWS} "
                    f"depth_source={DEPTH_SOURCE_PARTIAL_SMART} "
                    f"concurrent={len(self._depth_subs)}/{self.depth_max}"
                )

        l1_only = [s for s in ok if s not in self._depth_subs]
        if l1_only:
            print(f"  L1-only (no reqMktDepth): {','.join(l1_only)}")
        print(
            f"  depth plan: max={self.depth_max} symbols={','.join(self.depth_symbols) or '-'} "
            f"label={DEPTH_SOURCE_PARTIAL_SMART} (no BATS/BEX required)"
        )

        await self._maybe_iex_fallback(ib)
        return ok

    async def _maybe_iex_fallback(self, ib: Any) -> None:
        """
        If SMART DOM is empty after a short wait, swap that slot to IEX-native.

        WHY: 2152 is expected even when ARCA/NYSE/IEX contribute levels. Only a
        dead SMART book should drop those venues for a single-exchange IEX book.
        Cancel SMART first so concurrent depth never exceeds depth_max.
        """
        if not self._depth_subs:
            return
        await asyncio.sleep(IEX_FALLBACK_WAIT_SEC)
        from ib_insync import Stock

        for symbol in list(self._depth_subs):
            sub = self._depth_subs.get(symbol)
            if sub is None or sub.iex_native:
                continue
            ticker = self._depth_tickers.get(symbol) or self._tickers.get(symbol)
            if ticker is None:
                continue
            if not should_try_iex_fallback(
                getattr(ticker, "domBids", None),
                getattr(ticker, "domAsks", None),
            ):
                continue
            print(f"  SMART DOM empty for {symbol} — trying IEX-native depth (entitled, no BATS/BEX)")
            self._cancel_one_depth(ib, sub)
            self._depth_subs.pop(symbol, None)
            self._depth_tickers.pop(symbol, None)
            iex_contract = Stock(symbol, "IEX", "USD")
            try:
                qualified = await asyncio.wait_for(
                    ib.qualifyContractsAsync(iex_contract),
                    timeout=IB_QUALIFY_TIMEOUT_SEC,
                )
            except Exception as exc:
                print(f"  IEX qualify FAIL {symbol}: {exc} — restoring SMART depth")
                md = self._md_contracts.get(symbol)
                if md is not None:
                    self._request_mkt_depth(
                        ib,
                        md,
                        symbol=symbol,
                        is_smart=SMART_DEPTH,
                        iex_native=False,
                        ticker=self._tickers.get(symbol),
                    )
                continue
            if not qualified:
                print(f"  IEX qualify FAIL {symbol} — restoring SMART depth")
                md = self._md_contracts.get(symbol)
                if md is not None:
                    self._request_mkt_depth(
                        ib,
                        md,
                        symbol=symbol,
                        is_smart=SMART_DEPTH,
                        iex_native=False,
                        ticker=self._tickers.get(symbol),
                    )
                continue
            iex_contract = qualified[0]
            iex_ticker = ib.reqMktData(iex_contract, "", False, False)
            opened = self._request_mkt_depth(
                ib,
                iex_contract,
                symbol=symbol,
                is_smart=False,
                iex_native=True,
                ticker=iex_ticker,
            )
            if opened:
                self._depth_source_by_symbol[symbol] = DEPTH_SOURCE_IEX_NATIVE
                print(
                    f"  subscribed {symbol} IEX-native depth "
                    f"depth_source={DEPTH_SOURCE_IEX_NATIVE} "
                    f"concurrent={len(self._depth_subs)}/{self.depth_max}"
                )

    def latest_book(self, symbol: str) -> BookQuote | None:
        """Thread-safe copy of the latest book (or None)."""
        with self._lock:
            book = self._books.get(symbol.upper())
            if book is None:
                return None
            return BookQuote(
                ts=book.ts,
                bids=list(book.bids),
                asks=list(book.asks),
                depth_source=book.depth_source,
                error_2152=book.error_2152,
            )

    def depth_source_for(self, symbol: str) -> str:
        """Current honest label for one symbol (L1_ONLY / SMART partial / IEX)."""
        return self._label_for(symbol)

    def drain_prints(self, symbol: str) -> list[RawPrint]:
        """Pop queued prints for one symbol (main thread)."""
        with self._lock:
            q = self._prints[symbol.upper()]
            out = list(q)
            q.clear()
            return out

    def cancel_subscriptions(self) -> None:
        """Drop market data and depth (still no orders)."""
        try:
            self._call(self._async_cancel(), IB_CALL_TIMEOUT_SEC)
        except Exception as exc:
            print(f"  cancel subscriptions: {exc}")

    async def _async_cancel(self) -> None:
        ib = self._ib
        if ib is None:
            return
        with self._lock:
            tickers = list(self._tickers.items())
            depth_subs = list(self._depth_subs.values())
            self._tickers.clear()
            self._depth_tickers.clear()
            self._depth_subs.clear()
            self._md_contracts.clear()
        for _symbol, ticker in tickers:
            contract = getattr(ticker, "contract", None)
            if contract is None:
                continue
            try:
                ib.cancelMktData(contract)
            except Exception:
                pass
        for sub in depth_subs:
            self._cancel_one_depth(ib, sub)
            if sub.iex_native:
                try:
                    ib.cancelMktData(sub.contract)
                except Exception:
                    pass

    def stop(self) -> None:
        """Cancel data, disconnect, and join the worker thread (bounded)."""
        self._stop.set()
        try:
            self.cancel_subscriptions()
        except Exception:
            pass
        loop = self._loop
        ib = self._ib
        if loop is not None and ib is not None:
            try:
                fut = asyncio.run_coroutine_threadsafe(self._async_disconnect(), loop)
                fut.result(timeout=IB_DISCONNECT_TIMEOUT_SEC)
            except Exception:
                pass
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=IB_DISCONNECT_TIMEOUT_SEC)
        self._thread = None
        self._loop = None
        self._ib = None

    async def _async_disconnect(self) -> None:
        ib = self._ib
        if ib is not None and ib.isConnected():
            ib.disconnect()


def _dom_levels(rows: Any) -> list[BookLevel]:
    """Parse ib_insync DOM rows; skip invalid prices/sizes."""
    out: list[BookLevel] = []
    if not rows:
        return out
    for row in rows:
        px = _finite(getattr(row, "price", None))
        sz = _finite(getattr(row, "size", None))
        if px is None or sz is None or px <= 0 or sz < 0:
            continue
        out.append(BookLevel(price=px, size=sz))
    return out
