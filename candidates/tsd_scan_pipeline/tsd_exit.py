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
    session_allows_exit_orders,
)
from tsd_scan_pipeline.tsd_pool import release_on_exit
from tsd_scan_pipeline.tsd_trail import remaining_shares

EXIT_LIMIT_SLIP = 0.998
# Aggressive AH/overnight limit for kill_escalate when STP LMT is stuck through.
# Slightly wider than 0.97 so 04:00 overnight-open / AH prints have a better shot.
KILL_ESCALATE_SLIP = 0.95
# One retry after no-fill with a modestly wider band (overnight open / RTH only).
KILL_ESCALATE_RETRY_SLIP = 0.93
# Stuck if last is this far through the kill limit (relative) or by absolute cents.
STUCK_KILL_LIMIT_FRAC = 0.995
STUCK_KILL_LIMIT_EPS = 0.01


def build_exit_order(
    session: SessionKind,
    shares: int,
    ref_price: float,
    *,
    limit_slip: float | None = None,
) -> MarketOrder | LimitOrder:
    """Build session-appropriate SELL order."""
    if session == "RTH":
        return MarketOrder(action="SELL", totalQuantity=shares, tif="DAY")

    slip = EXIT_LIMIT_SLIP if limit_slip is None else float(limit_slip)
    lmt = round(ref_price * slip, 2)
    order = LimitOrder(action="SELL", totalQuantity=shares, lmtPrice=lmt, tif="DAY")
    order.outsideRth = True
    return order


def kill_stop_is_stuck(
    *,
    last_price: float,
    stop_price: float,
    limit_price: float,
) -> bool:
    """
    True when last has traded through a working STP LMT kill.

    Cases:
      - last meaningfully below limit (crash gap / no fill at lmtPrice)
      - last at/below stop while order still working (caller confirms open)
    """
    try:
        last = float(last_price or 0)
        stop = float(stop_price or 0)
        limit = float(limit_price or 0)
    except (TypeError, ValueError):
        return False
    if last <= 0:
        return False
    if limit > 0 and (
        last < limit * STUCK_KILL_LIMIT_FRAC
        or last < limit - STUCK_KILL_LIMIT_EPS
    ):
        return True
    if stop > 0 and last <= stop:
        return True
    return False


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


OWNER_CANCEL_CLIENT_IDS = (0, 91, 93, 94, 95, 96, 97, 88, 89, 90)


def _open_sell_rows(ib: IB) -> list[dict[str, Any]]:
    """Snapshot working SELL orders (symbol, orderId, clientId, type)."""
    rows: list[dict[str, Any]] = []
    try:
        ib.reqAllOpenOrders()
        ib.sleep(0.35)
    except Exception:
        pass
    for trade in list(ib.openTrades() or []):
        try:
            action = str(trade.order.action or "").upper()
            if action != "SELL":
                continue
            status = str(trade.orderStatus.status or "")
            if status in ("Filled", "Cancelled", "ApiCancelled", "Inactive"):
                continue
            rows.append({
                "symbol": str(getattr(trade.contract, "symbol", "") or "").upper(),
                "order_id": int(trade.order.orderId or 0),
                "client_id": int(getattr(trade.order, "clientId", 0) or 0),
                "order_type": str(trade.order.orderType or ""),
            })
        except Exception:
            continue
    return [r for r in rows if r["symbol"] and r["order_id"] > 0]


def _broker_qty_map(ib: IB) -> dict[str, float]:
    out: dict[str, float] = {}
    for pos in ib.positions() or []:
        sym = str(getattr(pos.contract, "symbol", "") or "").upper()
        if not sym:
            continue
        out[sym] = out.get(sym, 0.0) + float(pos.position or 0)
    return out


def _cancel_oids_on_ib(ib: IB, order_ids: set[int]) -> list[int]:
    """
    Cancel specific order ids on this connection.

    Returns ids that are actually gone (Cancelled / ApiCancelled / missing)
    after the cancel attempt — not merely ids we *sent* a cancel for.
    IBKR Error 10147 (wrong clientId) must not count as success.
    """
    if not order_ids:
        return []
    try:
        ib.reqAllOpenOrders()
        ib.sleep(0.35)
    except Exception:
        pass

    attempted: set[int] = set()
    for trade in list(ib.openTrades() or []):
        try:
            oid = int(trade.order.orderId or 0)
        except Exception:
            continue
        if oid not in order_ids:
            continue
        try:
            ib.cancelOrder(trade.order)
            attempted.add(oid)
            sym = str(getattr(trade.contract, "symbol", "") or "").upper()
            print(
                f"  {sym} cancel sent oid={oid} "
                f"type={trade.order.orderType} via clientId={getattr(ib.client, 'clientId', '?')}"
            )
        except Exception as exc:
            print(f"  cancel oid={oid} warn: {exc}")

    if attempted:
        ib.sleep(0.6)
        try:
            ib.reqAllOpenOrders()
            ib.sleep(0.35)
        except Exception:
            pass

    still_open: set[int] = set()
    for trade in list(ib.openTrades() or []):
        try:
            oid = int(trade.order.orderId or 0)
        except Exception:
            continue
        if oid not in order_ids:
            continue
        status = str(trade.orderStatus.status or "")
        if status in ("Filled", "Cancelled", "ApiCancelled", "Inactive"):
            continue
        still_open.add(oid)

    confirmed = sorted(oid for oid in attempted if oid not in still_open)
    # Also treat already-absent target ids as done if they were in order_ids
    # and never appeared as working after refresh (no false success from 10147).
    for oid in list(order_ids):
        if oid not in still_open and oid not in attempted:
            # Was never visible on this client — do not claim success.
            pass
    for oid in confirmed:
        print(f"  confirm cancelled oid={oid}")
    if attempted and still_open:
        print(
            f"  cancel incomplete via clientId={getattr(ib.client, 'clientId', '?')}: "
            f"still open {sorted(still_open)}"
        )
    return confirmed


def cancel_working_sells_for_symbol(
    ib: IB,
    symbol: str,
    *,
    dry_run: bool = False,
) -> list[int]:
    """
    Cancel every working SELL order for *symbol* on this connection.

    Used after full exits and for orphan housekeeping so leftover STP/LMT
    kills cannot short a flat name. Returns cancelled order ids.
    """
    sym = symbol.upper()
    rows = [r for r in _open_sell_rows(ib) if r["symbol"] == sym]
    if not rows:
        return []
    if dry_run:
        for r in rows:
            print(
                f"  {sym} DRY_RUN cancel orphan SELL oid={r['order_id']} "
                f"type={r['order_type']}"
            )
        return [int(r["order_id"]) for r in rows]
    return _cancel_oids_on_ib(ib, {int(r["order_id"]) for r in rows})


def housekeep_orphan_sell_orders(
    ib: IB,
    *,
    dry_run: bool = False,
    host: str = "127.0.0.1",
    port: int = 7497,
    owner_retry: bool = True,
) -> list[dict[str, Any]]:
    """
    Cancel working SELL orders on symbols that are flat at the broker.

    Safety net for missed kill-cancels after structure / kill / manual exits.
    Never cancels SELL orders for symbols that still have a long.

    If ``owner_retry`` is True and cancels fail with Error 10147 (foreign
    clientId), reconnect as the recorded owner clientIds and retry — same
    pattern as flatten_tws_paper.
    """
    try:
        broker_qty = _broker_qty_map(ib)
    except Exception as exc:
        print(f"  orphan housekeep blocked: positions unavailable ({exc})")
        return []

    sell_rows = _open_sell_rows(ib)
    orphan_rows = [
        r for r in sell_rows
        if float(broker_qty.get(r["symbol"], 0.0)) <= 0
    ]
    if not orphan_rows:
        print("  orphan housekeep: no flat-symbol working sells")
        return []

    if dry_run:
        actions = []
        by_sym: dict[str, list[int]] = {}
        for r in orphan_rows:
            by_sym.setdefault(r["symbol"], []).append(int(r["order_id"]))
            print(
                f"  {r['symbol']} DRY_RUN cancel orphan SELL "
                f"oid={r['order_id']} type={r['order_type']} client={r['client_id']}"
            )
        for sym, oids in sorted(by_sym.items()):
            actions.append({"symbol": sym, "cancelled_order_ids": oids})
        return actions

    target_oids = {int(r["order_id"]) for r in orphan_rows}
    cancelled = set(_cancel_oids_on_ib(ib, target_oids))

    remaining = [
        r for r in orphan_rows if int(r["order_id"]) not in cancelled
    ]
    if remaining and owner_retry:
        # Prefer recorded owner clientIds first — foreign cancel returns 10147.
        owner_ids: list[int] = []
        for r in remaining:
            cid = int(r["client_id"])
            if cid not in owner_ids:
                owner_ids.append(cid)
        for cid in OWNER_CANCEL_CLIENT_IDS:
            if cid not in owner_ids:
                owner_ids.append(cid)
        primary_cid = int(getattr(getattr(ib, "client", None), "clientId", -1) or -1)
        print(
            f"  orphan housekeep: {len(remaining)} sell(s) remain — "
            f"retry as owner clients {owner_ids}"
        )
        for cid in owner_ids:
            if not (target_oids - cancelled):
                break
            if cid == primary_cid:
                cancelled.update(_cancel_oids_on_ib(ib, target_oids - cancelled))
                continue
            temp = IB()
            try:
                temp.connect(host, port, clientId=cid, timeout=8)
            except Exception as exc:
                print(f"  owner clientId={cid} connect skip: {exc}")
                try:
                    temp.disconnect()
                except Exception:
                    pass
                continue
            try:
                cancelled.update(_cancel_oids_on_ib(temp, target_oids - cancelled))
            finally:
                try:
                    temp.disconnect()
                except Exception:
                    pass

    by_sym: dict[str, list[int]] = {}
    for r in orphan_rows:
        oid = int(r["order_id"])
        if oid in cancelled:
            by_sym.setdefault(r["symbol"], []).append(oid)
    actions = [
        {"symbol": sym, "cancelled_order_ids": oids}
        for sym, oids in sorted(by_sym.items())
    ]
    if actions:
        print(f"  orphan housekeep: cancelled sells on {len(actions)} flat symbol(s)")
    else:
        print("  orphan housekeep: no flat-symbol working sells cancelled")
    return actions


def sync_kill_quantity(
    ib: IB,
    leg: dict[str, Any],
    symbol: str,
    *,
    dry_run: bool = False,
) -> bool:
    """
    Keep emergency kill stop aligned with remaining shares AND trail kill price.

    After T1 bank, keep-profit may raise kill_price (e.g. to −2.5%); broker
    stop must move up with it.
    """
    trail = leg.get("trail") or {}
    remaining = remaining_shares(trail) if trail else int(leg.get("shares") or 0)
    kill_oid = leg.get("kill_order_id")
    target_kill = float(trail.get("kill_price") or 0)

    # Long-only invariant: never leave working SELL stops on a flat broker book.
    try:
        broker_qty = sum(
            float(pos.position or 0)
            for pos in (ib.positions() or [])
            if str(getattr(pos.contract, "symbol", "") or "").upper()
            == symbol.upper()
        )
    except Exception as exc:
        print(f"  {symbol} kill sync blocked: broker position unavailable ({exc})")
        broker_qty = None

    if remaining <= 0 or (broker_qty is not None and broker_qty <= 0):
        cancelled = cancel_working_sells_for_symbol(ib, symbol, dry_run=dry_run)
        if kill_oid and int(kill_oid) not in cancelled:
            cancel_order_safe(ib, kill_oid)
        trail["kill_stop_cancelled"] = True
        leg["trail"] = trail
        print(
            f"  {symbol} kill/orphan SELL cleanup "
            f"remaining={remaining} broker={broker_qty} cancelled={cancelled}"
        )
        return True

    remaining = min(remaining, int(broker_qty))

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
            cur_stop = float(getattr(trade.order, "stopPrice", 0) or 0)
            need_qty = cur_qty != remaining
            need_px = target_kill > 0 and abs(cur_stop - target_kill) >= 0.01
            if not need_qty and not need_px:
                return True
            if dry_run:
                print(
                    f"  {symbol} DRY_RUN sync kill qty {cur_qty}->{remaining} "
                    f"stop {cur_stop}->{target_kill} oid={kill_oid}"
                )
                return True
            # Cancel+replace when stop price ratchets up (modify stop in-place is flaky)
            if need_px:
                cancel_order_safe(ib, kill_oid)
                ib.sleep(0.3)
                contract = Stock(symbol.upper(), "SMART", "USD")
                ib.qualifyContracts(contract)
                session = classify_session()
                limit_px = round(target_kill * KILL_LIMIT_SLIP, 2)
                order = StopLimitOrder(
                    action="SELL",
                    totalQuantity=remaining,
                    stopPrice=round(target_kill, 2),
                    lmtPrice=limit_px,
                    tif="GTC",
                )
                if session != "RTH":
                    order.outsideRth = True
                new_trade = ib.placeOrder(contract, order)
                ib.sleep(0.3)
                leg["kill_order_id"] = new_trade.order.orderId
                print(
                    f"  {symbol} kill ratchet stop {cur_stop}->{target_kill} "
                    f"qty={remaining} oid={leg['kill_order_id']}"
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


def escalate_stuck_kill_stops(
    ib: IB,
    leg: dict[str, Any],
    symbol: str,
    last_price: float,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Cancel a stuck STP LMT kill and flatten with an aggressive software exit.

    Safety net for fast crashes where stop elects but limit never fills
    (e.g. SELL STP LMT @ 18.89 while last ~17.69 after hours).

    Skipped during OVERNIGHT dead window (~20:00–04:00 ET) when fills cannot
    happen — caller should keep/sync the broker kill only.
    """
    sym = symbol.upper()
    result: dict[str, Any] = {
        "symbol": sym,
        "escalated": False,
        "cancelled": [],
        "exit": None,
        "reason": None,
    }

    session = classify_session()
    if not session_allows_exit_orders(session):
        result["reason"] = "exits_not_tradeable"
        result["session"] = session
        return result

    try:
        last = float(last_price or 0)
    except (TypeError, ValueError):
        last = 0.0
    if last <= 0:
        result["reason"] = "no_last_price"
        return result

    try:
        broker_qty = float(_broker_qty_map(ib).get(sym, 0.0))
    except Exception as exc:
        result["reason"] = f"broker_qty_unavailable:{exc}"
        return result
    if broker_qty <= 0:
        result["reason"] = "no_broker_long"
        return result

    preferred_oid = leg.get("kill_order_id")
    try:
        preferred_oid_i = int(preferred_oid) if preferred_oid is not None else None
    except (TypeError, ValueError):
        preferred_oid_i = None

    stuck_trade = None
    try:
        ib.reqAllOpenOrders()
        ib.sleep(0.35)
    except Exception:
        pass

    candidates: list[Any] = []
    for trade in list(ib.openTrades() or []):
        try:
            if str(getattr(trade.contract, "symbol", "") or "").upper() != sym:
                continue
            if str(trade.order.action or "").upper() != "SELL":
                continue
            status = str(trade.orderStatus.status or "")
            if status in ("Filled", "Cancelled", "ApiCancelled", "Inactive"):
                continue
            otype = str(trade.order.orderType or "").upper().replace("_", " ")
            # IB reports StopLimitOrder as "STP LMT"
            if not ("STP" in otype and "LMT" in otype):
                continue
            candidates.append(trade)
        except Exception:
            continue

    # Prefer kill_order_id, but only if that order is actually stuck.
    # Otherwise scan other working STP LMTs (orphan / replace races).
    ordered: list[Any] = []
    if preferred_oid_i is not None:
        for trade in candidates:
            try:
                if int(trade.order.orderId or 0) == preferred_oid_i:
                    ordered.append(trade)
                    break
            except Exception:
                continue
    for trade in candidates:
        if trade not in ordered:
            ordered.append(trade)

    for trade in ordered:
        stop_px = float(getattr(trade.order, "stopPrice", 0) or 0)
        lmt_px = float(getattr(trade.order, "lmtPrice", 0) or 0)
        if kill_stop_is_stuck(last_price=last, stop_price=stop_px, limit_price=lmt_px):
            stuck_trade = trade
            break

    if stuck_trade is None:
        if not candidates:
            result["reason"] = "no_working_stp_lmt"
            return result
        # Surface the preferred / first order for diagnostics.
        sample = ordered[0] if ordered else candidates[0]
        result["reason"] = "not_stuck"
        result["stop_price"] = float(getattr(sample.order, "stopPrice", 0) or 0)
        result["limit_price"] = float(getattr(sample.order, "lmtPrice", 0) or 0)
        result["last_price"] = last
        return result

    stop_px = float(getattr(stuck_trade.order, "stopPrice", 0) or 0)
    lmt_px = float(getattr(stuck_trade.order, "lmtPrice", 0) or 0)
    oid = int(stuck_trade.order.orderId or 0)

    trail = leg.get("trail") or {}
    rem_book = remaining_shares(trail) if trail else int(leg.get("shares") or 0)
    qty = max(1, int(min(int(broker_qty), rem_book if rem_book > 0 else int(broker_qty))))

    print(
        f"  {sym} STUCK KILL escalate oid={oid} last={last:.4f} "
        f"stop={stop_px:.4f} lmt={lmt_px:.4f} qty={qty}"
    )

    if dry_run:
        result["escalated"] = True
        result["cancelled"] = [oid]
        result["reason"] = "dry_run"
        result["exit"] = {"status": "DRY_RUN", "shares": qty, "fill_price": last}
        return result

    cancelled = cancel_working_sells_for_symbol(ib, sym, dry_run=False)
    if oid and oid not in cancelled:
        if cancel_order_safe(ib, oid):
            cancelled = list(cancelled) + [oid]
    result["cancelled"] = cancelled
    # Keep prior kill_order_id until a successful flatten so sync_kill_quantity
    # can re-place if the aggressive exit rejects / times out.
    trail["kill_stop_cancelled"] = False
    leg["trail"] = trail

    entry_px = float(trail.get("entry_price") or leg.get("price") or 0) or None
    exit_res = place_tsd_exit(
        ib,
        sym,
        qty,
        ref_price=last,
        entry_price=entry_px,
        reason="kill_escalate",
    )
    # One modest retry on timeout / reject when session is still tradeable
    # (overnight open ~04:00 ET or RTH) — wider limit slip only.
    status0 = str(exit_res.get("status") or "")
    filled0 = int(exit_res.get("shares") or 0)
    if (
        not (status0 in ("FILLED", "PARTIAL") and filled0 > 0)
        and session_allows_exit_orders()
        and status0 in ("REJECTED",)
        and str(exit_res.get("reason") or "") in ("no_fill_timeout", "order_Cancelled", "order_Inactive")
    ):
        print(
            f"  {sym} kill_escalate retry slip={KILL_ESCALATE_RETRY_SLIP} "
            f"(first={status0}/{exit_res.get('reason')})"
        )
        exit_res = place_tsd_exit(
            ib,
            sym,
            qty,
            ref_price=last,
            entry_price=entry_px,
            reason="kill_escalate",
            escalate_slip=KILL_ESCALATE_RETRY_SLIP,
        )
    result["exit"] = exit_res
    status = str(exit_res.get("status") or "")
    filled = int(exit_res.get("shares") or 0)

    if status in ("FILLED", "PARTIAL") and filled > 0:
        result["escalated"] = True
        result["reason"] = "kill_escalate"
        if status == "FILLED" or filled >= qty:
            leg["kill_order_id"] = None
            trail["kill_stop_cancelled"] = True
            leg["trail"] = trail
        else:
            # Residual: caller updates trail then sync_kill_quantity.
            trail["kill_stop_cancelled"] = False
            leg["trail"] = trail
        return result

    # Exit failed — re-arm STP LMT kill for remaining long.
    trail["kill_stop_cancelled"] = False
    leg["trail"] = trail
    sync_kill_quantity(ib, leg, sym, dry_run=False)
    result["escalated"] = True
    result["reason"] = f"exit_{status or 'failed'}"
    return result


def place_tsd_exit(
    ib: IB,
    symbol: str,
    shares: int,
    *,
    ref_price: float | None = None,
    entry_price: float | None = None,
    reason: str = "trail",
    escalate_slip: float | None = None,
) -> dict[str, Any]:
    """
    Place session-aware SELL for a tranche exit. Updates pool on fill.
    """
    sym = symbol.upper()
    if shares <= 0:
        return {"status": "SKIPPED", "reason": "shares_zero", "symbol": sym}

    # Avoid stacking a software kill sell on top of a working STP LMT kill.
    if str(reason or "").lower().startswith("kill"):
        cancel_working_sells_for_symbol(ib, sym, dry_run=False)

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
    # IB does not allow unprotected MarketOrder outside RTH for US stocks —
    # use a wide LimitOrder so crash exits can still print AH/overnight-open.
    if str(reason or "") == "kill_escalate" and session != "RTH":
        slip = (
            float(escalate_slip)
            if escalate_slip is not None
            else KILL_ESCALATE_SLIP
        )
        order = build_exit_order(
            session, shares, float(px), limit_slip=slip,
        )
    else:
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
        # Wait for full fill — early return on partial caused book/broker orphans
        if filled >= float(shares) - 1e-9 or st == "Filled":
            break
        if st in ("Cancelled", "Inactive", "ApiCancelled"):
            if filled > 0:
                break
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

    # Cancel unfilled residual so we do not leave a working SELL
    if filled + 1e-9 < float(shares):
        try:
            ib.cancelOrder(trade.order)
            ib.sleep(0.3)
        except Exception:
            pass

    release_on_exit(
        int(round(filled)),
        avg_fill,
        entry_price=entry_price,
        symbol=sym,
    )
    status = "FILLED" if filled + 1e-9 >= float(shares) else "PARTIAL"
    return {
        "status": status,
        "symbol": sym,
        "shares": int(round(filled)),
        "requested_shares": int(shares),
        "fill_price": avg_fill,
        "session": session,
        "order_id": trade.order.orderId,
        "exit_reason": reason,
    }
