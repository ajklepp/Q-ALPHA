#!/usr/bin/env python3
"""
Peak Hour Performers — one-shot TWS *paper* flatten.

Cancels ALL working orders, then flats ALL stock positions (long or short).
Covering shorts is cleanup only — strategy remains LONG-ONLY.

Usage (TWS paper API on 7497; use repo venv Python 3.12):
  .\\venv\\Scripts\\python.exe candidates\\uts_v2\\flatten_tws_paper.py --dry-run
  .\\venv\\Scripts\\python.exe candidates\\uts_v2\\flatten_tws_paper.py --live

Safety: port 7497 only unless --i-really-mean-live-port.

Notes:
  - Connects as clientId 97; if cancels fail (Error 10147), reconnects as
    owner clientIds (0/93-96) so foreign STP/LMT orders still get cancelled.
  - Flatten legs always use SMART (never primary exchange) to avoid Error 10311.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path
from typing import Any

try:
    asyncio.set_event_loop(asyncio.new_event_loop())
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = ROOT / "candidates"
sys.path.insert(0, str(CANDIDATES))

from ib_insync import IB, LimitOrder, Stock, util  # noqa: E402

from tsd_scan_pipeline.tsd_entry import (  # noqa: E402
    POLL_SEC,
    _ref_price,
    build_entry_order,
    classify_session,
)
from tsd_scan_pipeline.tsd_exit import build_exit_order, cancel_order_safe  # noqa: E402

TWS_HOST = "127.0.0.1"
PAPER_PORT = 7497
LIVE_PORT = 7496
# Dedicated flatten client (not trail/watch/scan). Cancel path also reconnects
# as owner clientIds including 0 when foreign cancels return Error 10147.
TWS_CLIENT_ID = 97
# Fallback owners if primary cancel still leaves orphans (trail/watch/scan/etc.).
OWNER_CLIENT_IDS = (0, 91, 93, 94, 95, 96, 97)
ORDER_SETTLE_SEC = 30.0
# Overnight short-cover needs a bit more room than entry slip.
COVER_LIMIT_SLIP = 1.005
EXIT_LIMIT_SLIP = 0.995
FLATTEN_FILL_WAIT_SEC = 45.0


def _prefer_venv_hint() -> None:
    """ib_insync on system Python 3.14 breaks; repo venv is 3.12."""
    if sys.version_info >= (3, 14):
        print(
            "WARN: Python >=3.14 often breaks ib_insync connect. "
            "Prefer: .\\venv\\Scripts\\python.exe candidates\\uts_v2\\flatten_tws_paper.py ..."
        )


def _guard_port(port: int, *, allow_live_port: bool) -> None:
    if port == PAPER_PORT:
        return
    if port == LIVE_PORT and allow_live_port:
        print("WARNING: live port 7496 authorized via --i-really-mean-live-port")
        return
    raise SystemExit(
        f"REFUSE port {port}: paper flatten uses {PAPER_PORT} only "
        f"(pass --i-really-mean-live-port for {LIVE_PORT})"
    )


def _smart_stock(contract: Any) -> Stock:
    """Rebuild as SMART so flatten never direct-routes (Error 10311)."""
    sym = str(getattr(contract, "symbol", "") or "").upper()
    return Stock(sym, "SMART", "USD")


def _stock_positions(ib: IB) -> list[tuple[Any, int]]:
    """Non-zero STK positions as (contract, signed_qty)."""
    out: list[tuple[Any, int]] = []
    for pos in ib.positions():
        qty = int(pos.position or 0)
        if qty == 0:
            continue
        sec = str(getattr(pos.contract, "secType", "") or "").upper()
        if sec and sec != "STK":
            continue
        out.append((pos.contract, qty))
    return out


def _open_order_rows(ib: IB) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for trade in ib.openTrades():
        o = trade.order
        c = trade.contract
        rows.append({
            "symbol": str(getattr(c, "symbol", "") or "").upper(),
            "orderId": int(o.orderId or 0),
            "clientId": int(getattr(o, "clientId", 0) or 0),
            "action": str(o.action or ""),
            "type": str(o.orderType or ""),
            "qty": int(o.totalQuantity or 0),
            "status": str(trade.orderStatus.status or ""),
        })
    return rows


def _cancel_open_on_connection(ib: IB, *, skip_oids: set[int] | None = None) -> int:
    """Cancel every open trade on this connection. Returns attempts."""
    skip_oids = skip_oids or set()
    ib.reqAllOpenOrders()
    ib.sleep(0.5)
    n = 0
    for trade in list(ib.openTrades()):
        oid = int(trade.order.orderId or 0)
        if oid in skip_oids:
            continue
        sym = str(getattr(trade.contract, "symbol", "") or "").upper()
        try:
            ib.cancelOrder(trade.order)
            n += 1
            print(f"    CANCEL req oid={oid} {sym} (conn clientId={ib.client.clientId})")
        except Exception as exc:
            print(f"    CANCEL FAIL oid={oid}: {exc}")
    try:
        ib.reqGlobalCancel()
        print(f"  reqGlobalCancel() via clientId={ib.client.clientId}")
    except Exception as exc:
        print(f"  reqGlobalCancel skipped: {exc}")
    ib.sleep(1.0)
    return n


def cancel_all_working(ib: IB, *, dry_run: bool, port: int, keep: set[str] | None = None) -> int:
    """Cancel every open trade across bot clientIds. Returns cancels attempted."""
    keep = keep or set()
    ib.reqAllOpenOrders()
    ib.sleep(0.5)
    rows = _open_order_rows(ib)
    print(f"Open orders: {len(rows)}")
    kept_oids: set[int] = set()
    for r in rows:
        sym = r["symbol"].upper()
        tag = " [KEPT]" if sym in keep else ""
        print(
            f"  oid={r['orderId']} client={r['clientId']} {r['symbol']:<6} "
            f"{r['action']} {r['type']} qty={r['qty']} status={r['status']}{tag}"
        )
        if sym in keep:
            kept_oids.add(r["orderId"])
        elif dry_run:
            print(f"    DRY_RUN cancel oid={r['orderId']}")

    if dry_run:
        return len(rows) - len(kept_oids)

    attempted = _cancel_open_on_connection(ib, skip_oids=kept_oids)

    for r in rows:
        if r["orderId"] not in kept_oids:
            cancel_order_safe(ib, r["orderId"])

    deadline = time.time() + ORDER_SETTLE_SEC
    while time.time() < deadline:
        ib.reqAllOpenOrders()
        ib.sleep(0.5)
        left = _open_order_rows(ib)
        if not left:
            print("Open orders cleared.")
            return attempted
        ib.sleep(1.0)

    left = _open_order_rows(ib)
    if left:
        # Reconnect as each owner clientId — foreign cancels return Error 10147.
        owner_ids = sorted({int(r["clientId"]) for r in left} | set(OWNER_CLIENT_IDS))
        print(f"WARN: {len(left)} open order(s) remain — retry as owner clients {owner_ids}")
        host = TWS_HOST
        for cid in owner_ids:
            if cid == int(ib.client.clientId):
                _cancel_open_on_connection(ib)
                continue
            try:
                ib.disconnect()
            except Exception:
                pass
            ib.sleep(0.5)
            try:
                ib.connect(host, port, clientId=cid, timeout=12)
            except Exception as exc:
                print(f"  reconnect clientId={cid} failed: {exc}")
                continue
            _cancel_open_on_connection(ib)
            ib.sleep(1.0)

        # Restore master connection for flatten legs.
        if int(ib.client.clientId) != TWS_CLIENT_ID:
            try:
                ib.disconnect()
            except Exception:
                pass
            ib.sleep(0.5)
            ib.connect(host, port, clientId=TWS_CLIENT_ID, timeout=12)

        ib.reqAllOpenOrders()
        ib.sleep(0.5)
        left = _open_order_rows(ib)

    if left:
        print(f"WARN: {len(left)} open order(s) remain after settle")
        for r in left:
            print(
                f"  LEFTOVER oid={r['orderId']} client={r['clientId']} "
                f"{r['symbol']} {r['action']} {r['type']}"
            )
    else:
        print("Open orders cleared.")
    return attempted


def _nbbo(ib: IB, contract: Stock) -> tuple[float, float, float]:
    """Return (bid, ask, last_or_close). Zeros when missing."""
    bid = ask = last = 0.0
    try:
        ticker = ib.reqMktData(contract, "", False, False)
        ib.sleep(2.0)
        for attr, dest in (("bid", "bid"), ("ask", "ask")):
            v = getattr(ticker, attr, None)
            try:
                f = float(v)
                if f > 0:
                    if dest == "bid":
                        bid = f
                    else:
                        ask = f
            except (TypeError, ValueError):
                pass
        for attr in ("last", "close"):
            v = getattr(ticker, attr, None)
            try:
                f = float(v)
                if f > 0:
                    last = f
                    break
            except (TypeError, ValueError):
                continue
        ib.cancelMktData(contract)
    except Exception:
        pass
    return bid, ask, last


def _wait_fill(ib: IB, trade: Any, need: int, wait_sec: float) -> tuple[float, str]:
    """Poll until filled, terminal reject, or timeout."""
    filled = 0.0
    st = ""
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        ib.sleep(POLL_SEC)
        filled = float(trade.orderStatus.filled or 0)
        st = str(trade.orderStatus.status or "")
        if filled >= need or st in ("Filled",):
            break
        if st in ("Cancelled", "Inactive", "ApiCancelled"):
            break
    return filled, st


def flatten_positions(ib: IB, *, dry_run: bool, keep: set[str] | None = None) -> list[dict[str, Any]]:
    """Flat every non-zero STK position. Returns result rows."""
    keep = keep or set()
    session = classify_session()
    positions = _stock_positions(ib)
    print(f"Stock positions: {len(positions)}  session={session}")
    results: list[dict[str, Any]] = []

    for raw_contract, qty in positions:
        sym = str(getattr(raw_contract, "symbol", "") or "").upper()
        abs_qty = abs(int(qty))
        if sym in keep:
            print(f"  {sym}: qty={qty} -> KEPT (protected)")
            results.append({"symbol": sym, "qty": qty, "status": "KEPT"})
            continue
        action = "SELL" if qty > 0 else "BUY"
        print(f"  {sym}: qty={qty} -> {action} {abs_qty} to flat")

        if dry_run:
            results.append({"symbol": sym, "qty": qty, "status": "DRY_RUN", "action": action})
            continue

        contract = _smart_stock(raw_contract)
        try:
            ib.qualifyContracts(contract)
        except Exception as exc:
            print(f"    QUALIFY FAIL {sym}: {exc}")
            results.append({"symbol": sym, "qty": qty, "status": "QUALIFY_FAIL", "reason": str(exc)})
            continue

        bid, ask, last = _nbbo(ib, contract)
        ref = _ref_price(ib, contract) or last or 0.0
        if ref <= 0 and ask <= 0 and bid <= 0:
            print(f"    NO PRICE {sym} - skip")
            results.append({"symbol": sym, "qty": qty, "status": "NO_PRICE"})
            continue

        if session == "RTH":
            if action == "SELL":
                order = build_exit_order(session, abs_qty, ref if ref > 0 else bid)
            else:
                order = build_entry_order(session, abs_qty, ref if ref > 0 else ask)
        else:
            # Overnight paper often has no size at displayed NBBO — use GTC outsideRth.
            # BUY cover: pay toward ask but never absurd vs last (mktCap holds).
            # SELL flatten: hit toward bid.
            if action == "BUY":
                px = ask if ask > 0 else ref
                if ref > 0 and ask > 0:
                    px = min(ask, ref * 1.08)  # cap runaway overnight ask
                lmt_px = round(px * COVER_LIMIT_SLIP, 2)
            else:
                px = bid if bid > 0 else ref
                if ref > 0 and bid > 0:
                    px = max(bid, ref * 0.92)
                lmt_px = round(px * EXIT_LIMIT_SLIP, 2)
            order = LimitOrder(action=action, totalQuantity=abs_qty, lmtPrice=lmt_px, tif="GTC")
            order.outsideRth = True

        lmt = getattr(order, "lmtPrice", None)
        print(
            f"    session={session} bid={bid:.2f} ask={ask:.2f} last={last:.2f} "
            f"order={order.orderType} lmt={lmt} outsideRth={getattr(order, 'outsideRth', False)}"
        )

        trade = ib.placeOrder(contract, order)
        filled, st = _wait_fill(ib, trade, abs_qty, FLATTEN_FILL_WAIT_SEC)
        avg = float(trade.orderStatus.avgFillPrice or 0)
        ok = filled >= abs_qty
        if avg:
            print(f"    {st} filled={filled:.0f}/{abs_qty} avg={avg:.4f}")
        else:
            print(f"    {st} filled={filled:.0f}/{abs_qty}")
        results.append({
            "symbol": sym,
            "qty": qty,
            "action": action,
            "status": "FLAT" if ok else st,
            "filled": filled,
            "avg": avg,
            "orderId": int(trade.order.orderId or 0),
        })
        if not ok and session == "RTH":
            try:
                ib.cancelOrder(trade.order)
            except Exception:
                pass
        elif not ok:
            print(
                f"    LEAVING working oid={trade.order.orderId} "
                f"(overnight paper may not fill until premarket/RTH — re-run --live later)"
            )

    return results


def run(*, live: bool, port: int, allow_live_port: bool, keep: set[str] | None = None) -> int:
    _prefer_venv_hint()
    _guard_port(port, allow_live_port=allow_live_port)
    mode = "LIVE" if live else "DRY_RUN"
    print("=" * 64)
    print(f"Peak Hour Performers - TWS paper flatten ({mode})")
    print(f"host={TWS_HOST}:{port} clientId={TWS_CLIENT_ID}")
    print("LONG-ONLY strategy: short covers are cleanup only.")
    if keep:
        print(f"KEEP (protected): {', '.join(sorted(keep))}")
    print("=" * 64)

    util.startLoop()
    ib = IB()
    try:
        ib.connect(TWS_HOST, port, clientId=TWS_CLIENT_ID, timeout=12)
    except Exception as exc:
        print(f"CONNECT FAILED: {exc}")
        return 1

    dry_run = not live
    try:
        cancel_all_working(ib, dry_run=dry_run, port=port, keep=keep or set())
        print("")
        flatten_positions(ib, dry_run=dry_run, keep=keep or set())
        print("")

        ib.reqAllOpenOrders()
        ib.sleep(0.5)
        orders_left = _open_order_rows(ib)
        pos_left = _stock_positions(ib)
        print("--- FINAL ---")
        print(f"Open orders: {len(orders_left)}")
        for r in orders_left:
            print(
                f"  oid={r['orderId']} client={r['clientId']} "
                f"{r['symbol']} {r['action']} {r['type']}"
            )
        print(f"Positions: {len(pos_left)}")
        for c, q in pos_left:
            print(f"  {getattr(c, 'symbol', '?')}: {q}")

        if dry_run:
            print("DRY_RUN complete - re-run with --live to mutate.")
            return 0

        # Filter out kept symbols from residual check
        non_kept_orders = [r for r in orders_left if r["symbol"].upper() not in (keep or set())]
        non_kept_pos = [(c, q) for c, q in pos_left if str(getattr(c, "symbol", "")).upper() not in (keep or set())]
        if non_kept_orders or non_kept_pos:
            print("FAIL: residuals remain (excluding kept symbols)")
            return 2
        print("OK: broker paper flat (kept symbols preserved, rest cleared)")
        return 0
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Cancel all paper orders + flatten positions")
    parser.add_argument("--dry-run", action="store_true", help="List planned actions only")
    parser.add_argument("--live", action="store_true", help="Cancel orders and flatten positions")
    parser.add_argument("--port", type=int, default=PAPER_PORT)
    parser.add_argument(
        "--i-really-mean-live-port",
        action="store_true",
        help="Allow port 7496 (live). Default refuse.",
    )
    parser.add_argument(
        "--keep",
        type=str,
        default="",
        help="Comma-separated symbols to KEEP (skip flatten + keep their orders).",
    )
    args = parser.parse_args()
    if not args.dry_run and not args.live:
        print("Pass --dry-run or --live")
        return 1
    if args.dry_run and args.live:
        print("Pass only one of --dry-run / --live")
        return 1
    keep = {s.strip().upper() for s in args.keep.split(",") if s.strip()}
    return run(
        live=args.live,
        port=args.port,
        allow_live_port=args.i_really_mean_live_port,
        keep=keep,
    )


if __name__ == "__main__":
    raise SystemExit(main())
