"""
Q-ALPHA TSD pipeline — session-aware IBKR entry orders.

LONG ONLY. Phase 4 software trail replaces emergency T1 kill stop.

Session rules:
  - RTH (09:30-16:00 ET weekdays): MarketOrder BUY
  - Pre-market / after-hours: LimitOrder BUY, outsideRth=True
  - Overnight (20:00-04:00 ET): LimitOrder BUY only, outsideRth=True

Emergency T1 kill: StopLimitOrder SELL at kill_pct (fallback 7%) until Phase 4.
"""
from __future__ import annotations

from datetime import datetime, time
from typing import Any, Literal

import pytz
from ib_insync import IB, LimitOrder, MarketOrder, StopLimitOrder, Stock

from tsd_scan_pipeline.tsd_pool import available_pool, deploy_on_entry, load_pool

ET = pytz.timezone("America/New_York")
SessionKind = Literal["RTH", "EXTENDED", "OVERNIGHT"]

FALLBACK_KILL_PCT = 0.07
FILL_WAIT_SEC = 45
POLL_SEC = 0.5
KILL_LIMIT_SLIP = 0.995


def classify_session(now: datetime | None = None) -> SessionKind:
    """Classify current ET session for order type selection."""
    dt = now or datetime.now(ET)
    if dt.tzinfo is None:
        dt = ET.localize(dt)
    else:
        dt = dt.astimezone(ET)

    t = dt.time()
    overnight = time(20, 0) <= t or t < time(4, 0)
    if overnight:
        return "OVERNIGHT"

    if dt.weekday() < 5 and time(9, 30) <= t < time(16, 0):
        return "RTH"

    return "EXTENDED"


def load_tsd_pool() -> float:
    """Available TSD pool cash for sizing."""
    return available_pool()


def _ref_price(ib: IB, contract: Stock) -> float | None:
    t = ib.reqMktData(contract, "", False, False)
    ib.sleep(2)
    for attr in ("last", "close", "ask", "bid"):
        v = getattr(t, attr, None)
        try:
            f = float(v)
            if f > 0:
                ib.cancelMktData(contract)
                return f
        except (TypeError, ValueError):
            continue
    try:
        ib.cancelMktData(contract)
    except Exception:
        pass
    return None


def build_entry_order(
    session: SessionKind,
    shares: int,
    ref_price: float,
) -> MarketOrder | LimitOrder:
    """Build session-appropriate BUY order."""
    if session == "RTH":
        return MarketOrder(action="BUY", totalQuantity=shares, tif="DAY")

    lmt = round(ref_price * 1.002, 2)
    order = LimitOrder(action="BUY", totalQuantity=shares, lmtPrice=lmt, tif="DAY")
    order.outsideRth = True
    return order


def place_kill_stop(
    ib: IB,
    contract: Stock,
    shares: int,
    fill_price: float,
    session: SessionKind,
    *,
    kill_pct: float = FALLBACK_KILL_PCT,
) -> dict[str, Any]:
    """
    Emergency T1 kill stop — 100% shares at kill_pct below entry.
    Phase 4 software trail will replace/merge with this order.
    """
    stop_px = round(fill_price * (1.0 - kill_pct), 2)
    limit_px = round(stop_px * KILL_LIMIT_SLIP, 2)
    order = StopLimitOrder(
        action="SELL",
        totalQuantity=shares,
        stopPrice=stop_px,
        lmtPrice=limit_px,
        tif="GTC",
    )
    if session != "RTH":
        order.outsideRth = True
    trade = ib.placeOrder(contract, order)
    ib.sleep(0.5)
    return {
        "kill_order_id": trade.order.orderId,
        "kill_stop_price": stop_px,
        "kill_limit_price": limit_px,
        "kill_pct": kill_pct,
    }


def place_tsd_entry(
    ib: IB,
    symbol: str,
    *,
    entry_price: float | None = None,
    pool: float | None = None,
    kill_pct: float | None = None,
) -> dict[str, Any]:
    """
    Place session-aware BUY, emergency kill stop, and update pool on fill.
    """
    sym = symbol.upper()
    pool_val = pool if pool is not None else load_tsd_pool()
    px = entry_price
    contract = Stock(sym, "SMART", "USD")
    ib.qualifyContracts(contract)

    if px is None or px <= 0:
        px = _ref_price(ib, contract)
    if px is None or px <= 0:
        return {"status": "REJECTED", "reason": "no_price", "symbol": sym}

    from tsd_scan_pipeline.tsd_capacity import (
        deploy_budget,
        full_slots_used,
        load_state,
        shares_for_budget,
    )
    from tsd_scan_pipeline.tsd_pool import load_pool

    pool_doc = load_pool()
    cash = float(pool_doc.get("pool") or 0.0)
    deployed = float(pool_doc.get("deployed") or 0.0)
    equity = cash + deployed
    open_n = full_slots_used(load_state())
    budget = deploy_budget(equity, cash, open_n)
    shares = shares_for_budget(budget, px)
    if shares <= 0:
        return {"status": "REJECTED", "reason": "shares_zero", "symbol": sym, "price": px}

    session = classify_session()
    order = build_entry_order(session, shares, px)
    trade = ib.placeOrder(contract, order)

    import time as time_mod

    deadline = time_mod.time() + FILL_WAIT_SEC
    filled = 0.0
    avg_fill = px
    while time_mod.time() < deadline:
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
                "session": session,
            }

    if filled <= 0:
        try:
            ib.cancelOrder(trade.order)
        except Exception:
            pass
        return {"status": "REJECTED", "reason": "no_fill_timeout", "symbol": sym, "session": session}

    kill = kill_pct if kill_pct is not None else FALLBACK_KILL_PCT
    kill_meta = place_kill_stop(ib, contract, int(filled), avg_fill, session, kill_pct=kill)
    deploy_on_entry(int(filled), avg_fill)

    return {
        "status": "FILLED",
        "symbol": sym,
        "shares": int(filled),
        "fill_price": avg_fill,
        "session": session,
        "order_id": trade.order.orderId,
        "kill_pct": kill,
        **kill_meta,
    }
