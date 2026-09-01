"""
Q-ALPHA TSD pipeline — session-aware IBKR exit orders.

Mirrors tsd_entry session rules for SELL legs (trail / kill / time_cap).
"""
from __future__ import annotations

import time
from typing import Any

from ib_insync import IB, LimitOrder, MarketOrder, Stock, StopLimitOrder

from tsd_scan_pipeline.tsd_entry import (
    FILL_WAIT_SEC,
    KILL_LIMIT_SLIP,
    POLL_SEC,
    SessionKind,
    classify_session,
)
from tsd_scan_pipeline.tsd_pool import release_on_exit
from tsd_scan_pipeline.tsd_trail import remaining_shares

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
    """Best-effort cancel of an open IBKR order (all client IDs)."""
    if order_id is None:
        return False
    try:
        ib.reqAllOpenOrders()
        ib.sleep(0.3)
        for trade in ib.openTrades():
            if int(trade.order.orderId) == int(order_id):
                ib.cancelOrder(trade.order)
                ib.sleep(0.3)
                return True
    except Exception:
        pass
    return False


def sync_kill_quantity(
    ib: IB,
    leg: dict[str, Any],
    symbol: str,
    *,
    dry_run: bool = False,
) -> bool:
    """
    Keep emergency kill stop aligned with remaining shares.

    - remaining == 0 → cancel kill (only when fully flat)
    - remaining > 0 → modify kill qty to match (cancel+replace if needed)
    """
    trail = leg.get("trail") or {}
    remaining = remaining_shares(trail) if trail else int(leg.get("shares") or 0)
    kill_oid = leg.get("kill_order_id")

    if remaining <= 0:
        if kill_oid and not trail.get("kill_stop_cancelled"):
            if dry_run:
                trail["kill_stop_cancelled"] = True
                leg["trail"] = trail
                print(f"  {symbol} DRY_RUN cancel kill oid={kill_oid} (flat)")
                return True
            if cancel_order_safe(ib, kill_oid):
                trail["kill_stop_cancelled"] = True
                leg["trail"] = trail
                print(f"  {symbol} cancelled kill oid={kill_oid} (fully flat)")
                return True
        return True

    if kill_oid is None:
        return False

    if trail.get("kill_stop_cancelled"):
        return False

    try:
        ib.reqAllOpenOrders()
        ib.sleep(0.3)
        for trade in ib.openTrades():
            if int(trade.order.orderId) != int(kill_oid):
                continue
            cur_qty = int(trade.order.totalQuantity or 0)
            if cur_qty == remaining:
                return True
            if dry_run:
                print(
                    f"  {symbol} DRY_RUN sync kill {cur_qty} -> {remaining} "
                    f"oid={kill_oid}"
                )
                return True
            trade.order.totalQuantity = remaining
            ib.placeOrder(trade.contract, trade.order)
            ib.sleep(0.3)
            print(f"  {symbol} kill qty synced {cur_qty} -> {remaining}")
            return True

        # Kill missing — re-place at trail kill price
        kill_price = float(trail.get("kill_price") or 0)
        if kill_price <= 0 or dry_run:
            return False
        contract = Stock(symbol.upper(), "SMART", "USD")
        ib.qualifyContracts(contract)
        session = classify_session()
        limit_px = round(kill_price * KILL_LIMIT_SLIP, 2)
        order = StopLimitOrder(
            action="SELL",
            totalQuantity=remaining,
            stopPrice=round(kill_price, 2),
            lmtPrice=limit_px,
            tif="GTC",
        )
        if session != "RTH":
            order.outsideRth = True
        new_trade = ib.placeOrder(contract, order)
        ib.sleep(0.3)
        leg["kill_order_id"] = new_trade.order.orderId
        print(f"  {symbol} replaced missing kill oid={leg['kill_order_id']} qty={remaining}")
        return True
    except Exception as exc:
        print(f"  {symbol} sync_kill_quantity warn: {exc}")
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
