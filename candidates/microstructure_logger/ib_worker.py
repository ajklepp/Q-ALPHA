"""
Read-only IBKR worker: dedicated thread + asyncio loop.

WHY: ib_insync only drains its queue while the event loop on *its* thread
runs (same lesson as the options bridge). All waits are bounded.
This module never places or cancels orders.
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
    FORBIDDEN_CLIENT_IDS,
    IB_CALL_TIMEOUT_SEC,
    IB_CONNECT_TIMEOUT_SEC,
    IB_DISCONNECT_TIMEOUT_SEC,
    IB_QUALIFY_TIMEOUT_SEC,
    IB_SUBSCRIBE_TIMEOUT_SEC,
    IB_WORKER_READY_TIMEOUT_SEC,
    SMART_DEPTH,
    TAPE_POLL_SEC,
    TWS_CLIENT_ID,
    TWS_HOST,
    TWS_LIVE_PORT,
    TWS_PAPER_PORT,
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
    ) -> None:
        assert_paper_endpoint(host, port, client_id)
        self.host = host
        self.port = int(port)
        self.client_id = int(client_id)
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ib: Any = None
        self._ready = threading.Event()
        self._fail: BaseException | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._tickers: dict[str, Any] = {}
        self._books: dict[str, BookQuote] = {}
        self._prints: dict[str, deque[RawPrint]] = defaultdict(deque)
        self._last_print_key: dict[str, tuple[Any, ...]] = {}
        self._last_volume: dict[str, float] = {}
        self.depth_source = DEPTH_SOURCE
        self.error_2152 = False
        self.saw_error_codes: set[int] = set()

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

    def _on_error(self, req_id: Any, error_code: Any, error_string: Any, _contract: Any) -> None:
        """Record IB codes. 2152 = missing venue depth — stay PARTIAL_IEX_SMART."""
        try:
            code = int(error_code)
        except (TypeError, ValueError):
            return
        self.saw_error_codes.add(code)
        if code == 2152:
            self.error_2152 = True
            self.depth_source = DEPTH_SOURCE

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
        for symbol, ticker in items:
            book = self._book_from_ticker(ticker, now)
            prints = self._prints_from_ticker(symbol, ticker, now)
            with self._lock:
                self._books[symbol] = book
                q = self._prints[symbol]
                for rec in prints:
                    q.append(rec)
                while len(q) > 5_000:
                    q.popleft()

    def _book_from_ticker(self, ticker: Any, now: datetime) -> BookQuote:
        bids = _dom_levels(getattr(ticker, "domBids", None))
        asks = _dom_levels(getattr(ticker, "domAsks", None))
        if not bids:
            bid = _finite(getattr(ticker, "bid", None))
            bid_sz = _finite(getattr(ticker, "bidSize", None))
            if bid is not None and bid_sz is not None and bid > 0 and bid_sz >= 0:
                bids = [BookLevel(price=bid, size=bid_sz)]
        if not asks:
            ask = _finite(getattr(ticker, "ask", None))
            ask_sz = _finite(getattr(ticker, "askSize", None))
            if ask is not None and ask_sz is not None and ask > 0 and ask_sz >= 0:
                asks = [BookLevel(price=ask, size=ask_sz)]
        return BookQuote(
            ts=now,
            bids=bids,
            asks=asks,
            depth_source=self.depth_source,
            error_2152=self.error_2152,
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
        """Qualify + reqMktData + reqMktDepth. Returns subscribed symbols."""
        return self._call(self._async_subscribe(symbols), IB_SUBSCRIBE_TIMEOUT_SEC + IB_QUALIFY_TIMEOUT_SEC * max(len(symbols), 1))

    async def _async_subscribe(self, symbols: list[str]) -> list[str]:
        from ib_insync import Stock

        ib = self._ib
        if ib is None:
            raise RuntimeError("IB not connected")
        ok: list[str] = []
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
            ib.reqMktDepth(contract, numRows=DEPTH_ROWS, isSmartDepth=SMART_DEPTH)
            with self._lock:
                self._tickers[symbol] = ticker
            ok.append(symbol)
            print(
                f"  subscribed {symbol} md+depth rows={DEPTH_ROWS} "
                f"smart={SMART_DEPTH} depth_source={DEPTH_SOURCE}"
            )
        return ok

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
            self._tickers.clear()
        for _symbol, ticker in tickers:
            contract = getattr(ticker, "contract", None)
            if contract is None:
                continue
            try:
                ib.cancelMktData(contract)
            except Exception:
                pass
            try:
                ib.cancelMktDepth(contract, isSmartDepth=SMART_DEPTH)
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
