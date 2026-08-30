"""
Q-ALPHA TSD pipeline — session-aware IBKR exit orders.

Mirrors tsd_entry session rules for SELL legs (trail / kill / time_cap).
"""
from __future__ import annotations

import time
from typing import Any

from ib_insync import IB, LimitOrder, MarketOrder, Stock

from tsd_scan_pipeline.tsd_entry import FILL_WAIT_SEC, POLL_SEC, SessionKind, classify_session
from tsd_scan_pipeline.tsd_pool import release_on_exit

EXIT_LIMIT_SLIP = 0.998


def build_exit_order(
    session: SessionKind,
    shares: int,
    ref_price: float,
) -> MarketOrder | LimitOrder:
    """Build session-appropriate SELL order."""
    if session == "RTH":
        return MarketOrder(action="SELL", totalQuantity=shares, tif="DAY")

    lmt = round(ref_price * EXIT_LIMIT_SLIP, 2)
    order = LimitOrder(action="SELL", totalQuantity=shares, lmtPrice=lmt, tif="DAY")
    order.outsideRth = True
    return order


def cancel_order_safe(ib: IB, order_id: int | None) -> bool:
    """Best-effort cancel of an open IBKR order."""
    if order_id is None:
        return False
    try:
        for trade in ib.openTrades():
            if int(trade.order.orderId) == int(order_id):
                ib.cancelOrder(trade.order)
                ib.sleep(0.3)
                return True
    except Exception:
        pass
    return False


def place_tsd_exit(
    ib: IB,
    symbol: str,
    shares: int,
    *,
    ref_price: float | None = None,
    reason: str = "trail",
) -> dict[str, Any]:
    """
    Place session-aware SELL for a tranche exit. Updates pool on fill.
    """
    sym = symbol.upper()
    if shares <= 0:
        return {"status": "SKIPPED", "reason": "shares_zero", "symbol": sym}

    contract = Stock(sym, "SMART", "USD")
    ib.qualifyContracts(contract)

    px = ref_price
    if px is None or px <= 0:
        t = ib.reqMktData(contract, "", False, False)
        ib.sleep(1.5)
        for attr in ("last", "bid", "close"):
            v = getattr(t, attr, None)
            try:
                f = float(v)
                if f > 0:
                    px = f
                    break
            except (TypeError, ValueError):
                continue
        try:
            ib.cancelMktData(contract)
        except Exception:
            pass

    if px is None or px <= 0:
        return {"status": "REJECTED", "reason": "no_price", "symbol": sym}

    session = classify_session()
    order = build_exit_order(session, shares, px)
    trade = ib.placeOrder(contract, order)

    deadline = time.time() + FILL_WAIT_SEC
    filled = 0.0
    avg_fill = px
    while time.time() < deadline:
        ib.sleep(POLL_SEC)
        st = trade.orderStatus.status
        filled = float(trade.orderStatus.filled or 0)
        if filled > 0:
            avg_fill = float(trade.orderStatus.avgFillPrice or px)
            break
        if st in ("Cancelled", "Inactive", "ApiCancelled"):
            return {
                "status": "REJECTED",
                "reason": f"order_{st}",
                "symbol": sym,
                "exit_reason": reason,
            }

    if filled <= 0:
        try:
            ib.cancelOrder(trade.order)
        except Exception:
            pass
        return {"status": "REJECTED", "reason": "no_fill_timeout", "symbol": sym, "exit_reason": reason}

    release_on_exit(int(filled), avg_fill)
    return {
        "status": "FILLED",
        "symbol": sym,
        "shares": int(filled),
        "fill_price": avg_fill,
        "session": session,
        "order_id": trade.order.orderId,
        "exit_reason": reason,
    }
