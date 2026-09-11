"""
Q-ALPHA TSD pipeline — session-aware IBKR entry orders.

LONG ONLY. Phase 4 software trail replaces emergency T1 kill stop.

Session rules:
  - RTH (09:30-16:00 ET weekdays): MarketOrder BUY
  - EXTENDED (04:00-09:30 and 16:00-20:00 ET): LimitOrder BUY, outsideRth=True
    IBKR overnight open for many US stocks is typically ~04:00–20:00 ET.
  - OVERNIGHT (20:00-04:00 ET): dead window for most names — no reliable fills.
    Broker GTC STP LMT kills may rest; do not cancel/escalate software exits.

Emergency T1 kill: StopLimitOrder SELL at kill_pct (fallback 5%) until Phase 4.
Never place broker kill at structure/area-low (Chat A bakeoff failed).
"""
from __future__ import annotations

from datetime import datetime, time
from typing import Any, Literal

import pytz
from ib_insync import IB, LimitOrder, MarketOrder, StopLimitOrder, Stock

from tsd_scan_pipeline.tsd_kill import FALLBACK_KILL_PCT, resolve_kill_pct
from tsd_scan_pipeline.tsd_pool import available_pool, deploy_on_entry, load_pool

ET = pytz.timezone("America/New_York")
SessionKind = Literal["RTH", "EXTENDED", "OVERNIGHT"]

FILL_WAIT_SEC = 45
POLL_SEC = 0.5
# Limit below stop so STP LMT can fill in a fast crash; escalate_stuck_kill_stops
# is still the safety net if last trades through this band.
KILL_LIMIT_SLIP = 0.97

# IBKR overnight session for many US equities (~04:00–20:00 ET).
# Outside that window (OVERNIGHT classifier) exits generally cannot print.
OVERNIGHT_OPEN_ET = time(4, 0)
OVERNIGHT_CLOSE_ET = time(20, 0)


def classify_session(now: datetime | None = None) -> SessionKind:
    """Classify current ET session for order type selection.

    OVERNIGHT here means the post-overnight-close dead window (20:00–04:00 ET),
    not the IBKR overnight *trading* session (which maps to EXTENDED + RTH).
    """
    dt = now or datetime.now(ET)
    if dt.tzinfo is None:
        dt = ET.localize(dt)
    else:
        dt = dt.astimezone(ET)

    t = dt.time()
    overnight = t >= OVERNIGHT_CLOSE_ET or t < OVERNIGHT_OPEN_ET
    if overnight:
        return "OVERNIGHT"

    if dt.weekday() < 5 and time(9, 30) <= t < time(16, 0):
        return "RTH"

    return "EXTENDED"


def session_allows_exit_orders(
    session: SessionKind | None = None,
    now: datetime | None = None,
) -> bool:
    """True when software exits / kill escalate can reasonably fill.

    False during OVERNIGHT dead window (~20:00–04:00 ET) after IBKR overnight
    close until overnight open. RTH and EXTENDED (overnight open / pre / AH) OK.
    """
    s = session if session is not None else classify_session(now)
    return s != "OVERNIGHT"


def exits_tradeable_now(now: datetime | None = None) -> bool:
    """Time-based alias: exits allowed now in America/New_York."""
    return session_allows_exit_orders(now=now)


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
    *,
    limit_price: float | None = None,
    prefer_limit: bool = False,
) -> MarketOrder | LimitOrder:
    """
    Build session-appropriate BUY order.

    prefer_limit / limit_price: pullback entry — sit near signal instead of
    chasing last (RTH uses LimitOrder when set; EH always limit).
    """
    if limit_price is not None and limit_price > 0:
        lmt = round(float(limit_price), 2)
    else:
        lmt = round(ref_price * 1.002, 2)

    if session == "RTH" and not prefer_limit and limit_price is None:
        return MarketOrder(action="BUY", totalQuantity=shares, tif="DAY")

    order = LimitOrder(action="BUY", totalQuantity=shares, lmtPrice=lmt, tif="DAY")
    if session != "RTH":
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
    limit_price: float | None = None,
    prefer_limit: bool = False,
) -> dict[str, Any]:
    """
    Place session-aware BUY, emergency kill stop, and update pool on fill.

    limit_price / prefer_limit: micro-confirm pullback — LimitOrder near signal
    instead of market-chasing an extended print.
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
    size_px = float(limit_price) if limit_price and limit_price > 0 else px
    shares = shares_for_budget(budget, size_px)
    if shares <= 0:
        return {"status": "REJECTED", "reason": "shares_zero", "symbol": sym, "price": px}

    session = classify_session()
    # Pullback limits need a bit longer to rest on the book
    fill_wait = FILL_WAIT_SEC + (30 if (prefer_limit or limit_price) else 0)
    order = build_entry_order(
        session, shares, px, limit_price=limit_price, prefer_limit=prefer_limit,
    )
    trade = ib.placeOrder(contract, order)

    import time as time_mod

    deadline = time_mod.time() + fill_wait
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

    kill, kill_source = resolve_kill_pct(kill_pct)
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
        "kill_source": kill_source,
        **kill_meta,
    }
