"""
Local TWS intraday sync — Live book marks + flat→CLOSED (NOT Modal).

Modal cannot reach 127.0.0.1:7497. This job must run on the PC with TWS open
via Windows Task Scheduler (every ~30m RTH). Polygon Modal intraday_monitor is
fallback marks only and must never reopen NEVER_FILLED / CLOSED.

ClientId 96 — free of agent=5, connector=1, spike/scan=97, MD probes=98–99.

Usage (TWS paper open):
  .\\venv\\Scripts\\python.exe candidates/tws_intraday_sync.py
  .\\venv\\Scripts\\python.exe candidates/tws_intraday_sync.py --repair
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytz

CANDIDATES = Path(__file__).resolve().parent
ROOT = CANDIDATES.parent
if str(CANDIDATES) not in sys.path:
    sys.path.insert(0, str(CANDIDATES))

from dotenv import load_dotenv
from state_paths import state_path
from paper_trader import PaperTradesStore
from position_sizer import PoolManager

load_dotenv(ROOT / ".env")

TWS_HOST = "127.0.0.1"
TWS_PORT = 7497
# Documented free id — do not collide with agent(5) / connector(1) / spike(97).
TWS_CLIENT_ID = 96
ET = pytz.timezone("America/New_York")

MANAGED_SOURCES = frozenset({"autonomous_agent", "telegram_yes"})
OPEN_LEDGER = frozenset({"OPEN", "T1_HIT", "T2_HIT", "T3_TRAIL", "PENDING_MOC"})
FILL_CONFIRMED = frozenset({"Filled", "FILLED"})


def _finite(val: Any) -> float | None:
    try:
        if val is None:
            return None
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _send_telegram(message: str) -> None:
    """Best-effort Telegram (same bot as agent)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("  Telegram skipped (credentials missing)")
        return
    try:
        import requests

        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=15,
        )
    except Exception as exc:
        print(f"  Telegram warn: {exc}")


def _ib_position_map(ib) -> dict[str, float]:
    out: dict[str, float] = {}
    for p in ib.positions() or []:
        sym = str(getattr(p.contract, "symbol", "") or "").upper()
        if not sym:
            continue
        out[sym] = out.get(sym, 0.0) + float(p.position or 0)
    return out


def _tws_mark_price(ib, symbol: str) -> float | None:
    """Snapshot last/close for an open long; fail-soft."""
    from ib_insync import Stock

    try:
        contract = Stock(symbol, "SMART", "USD")
        q = ib.qualifyContracts(contract)
        if not q:
            return None
        contract = q[0]
        ticker = ib.reqMktData(contract, "", True, False)
        ib.sleep(1.5)
        for raw in (
            getattr(ticker, "last", None),
            getattr(ticker, "close", None),
            getattr(ticker, "bid", None),
            getattr(ticker, "ask", None),
        ):
            px = _finite(raw)
            if px is not None and px > 0:
                try:
                    ib.cancelMktData(contract)
                except Exception:
                    pass
                return px
        try:
            mp = ticker.marketPrice()
            px = _finite(mp)
            if px is not None and px > 0:
                try:
                    ib.cancelMktData(contract)
                except Exception:
                    pass
                return px
        except Exception:
            pass
        try:
            ib.cancelMktData(contract)
        except Exception:
            pass
    except Exception as exc:
        print(f"  mark {symbol} fail: {exc}")
    return None


def _collect_sell_fills(ib, symbol: str, since_date: str) -> list[dict[str, Any]]:
    """
    Executions for symbol on/after entry_date that look like sells (side SLD).
    Best-effort — paper may return sparse history.
    """
    from ib_insync import ExecutionFilter

    fills: list[dict[str, Any]] = []
    try:
        filt = ExecutionFilter()
        filt.symbol = symbol
        # Do NOT set filt.side — IB Error 321 "Invalid side" on some builds.
        if since_date:
            # IB wants yyyymmdd-hh:mm:ss in some builds; date alone often works.
            filt.time = f"{since_date.replace('-', '')}-00:00:00"
        for fill in ib.reqExecutions(filt) or []:
            ex = getattr(fill, "execution", None) or fill
            side = str(getattr(ex, "side", "") or "").upper()
            if side in {"BOT", "BUY"}:
                continue
            # Keep SLD/SELL/empty (empty: rare wrappers)
            if side and side not in {"SLD", "SELL"}:
                continue
            px = _finite(getattr(ex, "price", None) or getattr(ex, "avgPrice", None))
            qty = _finite(getattr(ex, "shares", None))
            if px is None or px <= 0:
                continue
            order_id = getattr(ex, "orderId", None)
            fills.append({
                "price": px,
                "qty": qty or 0.0,
                "order_id": order_id,
                "time": str(getattr(ex, "time", "") or ""),
                "side": side or "SLD",
            })
    except Exception as exc:
        print(f"  executions {symbol} warn: {exc}")
    return fills


def _infer_exit(
    trade: dict,
    sell_fills: list[dict],
    mark: float | None,
) -> tuple[float, str]:
    """
    Prefer TWS sell fill evidence; classify STOP / TARGET_2R / BROKER_EXIT.
    Never leave without an exit price when TWS is flat.
    """
    entry = float(trade.get("entry_price") or 0)
    stop = float(trade.get("stop_price") or 0)
    target_2r = float(trade.get("target_2r") or 0)
    tol = max(0.02, entry * 0.002) if entry > 0 else 0.02

    if sell_fills:
        # Use most recent / volume-weighted last fill price.
        last = sell_fills[-1]
        px = float(last["price"])
        if stop > 0 and px <= stop + tol:
            return px, "STOP"
        if target_2r > 0 and px >= target_2r - tol:
            return px, "TARGET_2R"
        return px, "BROKER_EXIT"

    # No fill tape — infer from stop/mark.
    if mark is not None and mark > 0:
        if stop > 0 and mark <= stop + tol:
            return float(stop if stop > 0 else mark), "STOP"
        return float(mark), "BROKER_EXIT"

    if stop > 0:
        return float(stop), "STOP"
    if entry > 0:
        return float(entry), "BROKER_EXIT"
    return 0.0, "BROKER_EXIT"


def _original_shares(trade: dict) -> int:
    shares = int(trade.get("shares_total") or 0)
    if shares > 0:
        return shares
    plan = trade.get("order_plan") or {}
    for key in ("shares", "tranche_1_shares", "shares_t1"):
        try:
            n = int(plan.get(key) or trade.get(key) or 0)
            if n > 0:
                return n
        except (TypeError, ValueError):
            pass
    return 0


def _book_closed(
    trade: dict,
    *,
    exit_price: float,
    exit_reason: str,
    pool: PoolManager,
) -> dict:
    """Mutate trade → CLOSED and update pool cash/deployed from cost vs proceeds."""
    shares = _original_shares(trade)
    cost = float(
        trade.get("position_value")
        or trade.get("position_size")
        or (float(trade.get("entry_price") or 0) * shares)
    )
    if shares <= 0 and cost > 0 and float(trade.get("entry_price") or 0) > 0:
        shares = max(1, int(round(cost / float(trade["entry_price"]))))
    proceeds = round(exit_price * shares, 2) if shares > 0 else 0.0
    entry = float(trade.get("entry_price") or 0)
    pnl = round(proceeds - cost, 2) if cost else 0.0
    pnl_pct = round(pnl / cost, 4) if cost > 0 else 0.0

    trade["status"] = "CLOSED"
    trade["exit_reason"] = exit_reason
    trade["tranche_1_exit"] = exit_price
    trade["stop_hit_price"] = exit_price if exit_reason == "STOP" else trade.get("stop_hit_price")
    trade["stop_hit_date"] = (
        datetime.now(ET).strftime("%Y-%m-%d") if exit_reason == "STOP" else trade.get("stop_hit_date")
    )
    trade["pnl_dollars"] = pnl
    trade["pnl_pct"] = pnl_pct
    trade["current_price"] = exit_price
    trade["shares_total"] = 0
    trade["remaining_t1"] = 0
    trade["remaining_t2"] = 0
    trade["remaining_t3"] = 0
    trade["position_value"] = 0.0
    trade["last_updated"] = datetime.now(timezone.utc).isoformat()
    trade["ibkr_status"] = trade.get("ibkr_status") or "Closed"

    # Accounting: open_trade did pool-=cost, deployed+=cost.
    # Close: pool+=proceeds, deployed-=cost → equity moves by PnL.
    if cost > 0 and pool.deployed + 1e-6 >= min(cost, pool.deployed):
        pool.state["deployed"] = round(max(0.0, pool.deployed - cost), 2)
    pool.state["pool"] = round(pool.pool + proceeds, 2)
    pool.state["open_positions"] = max(0, pool.open_positions - 1)
    if pnl > 0:
        pool.state["winning_trades"] = int(pool.state.get("winning_trades") or 0) + 1
    pool.save_state()

    print(
        f"  CLOSED {trade.get('ticker')} @ ${exit_price:.2f} "
        f"({exit_reason}) pnl=${pnl:+.2f} shares={shares}"
    )
    return trade


def _apply_mark(trade: dict, mark: float) -> None:
    entry = float(trade.get("entry_price") or 0)
    shares = _original_shares(trade)
    stop = float(trade.get("stop_price") or 0)
    target_2r = float(trade.get("target_2r") or 0)
    if entry <= 0 or shares <= 0 or mark <= 0:
        return
    pnl_per = mark - entry
    trade["current_price"] = round(mark, 2)
    trade["pnl_dollars"] = round(pnl_per * shares, 2)
    trade["pnl_pct"] = round(pnl_per / entry, 4)
    risk = entry - stop
    trade["r_multiple"] = round(pnl_per / risk, 2) if risk > 0 else 0.0
    trade["dist_to_stop"] = (
        round((mark - stop) / mark, 4) if mark > 0 else None
    )
    trade["dist_to_target"] = (
        round((target_2r - mark) / mark, 4) if mark > 0 else None
    )
    trade["last_updated"] = datetime.now(timezone.utc).isoformat()


def _was_filled(trade: dict) -> bool:
    st = str(trade.get("ibkr_status") or "")
    if st in FILL_CONFIRMED:
        return True
    # Explicit NEVER_FILLED / reject reasons → not a fill.
    reason = str(trade.get("exit_reason") or "").upper()
    if "NEVER_FILLED" in reason or reason.startswith("REJECTED"):
        return False
    # OPEN with positive shares booked after agent fill path.
    if int(trade.get("shares_total") or 0) > 0 and st not in {
        "SUBMITTED", "PreSubmitted", "PendingSubmit", "Cancelled", "Inactive",
        "Rejected", "",
    }:
        return True
    return st == "Filled"


def run_tws_intraday_sync(*, repair: bool = False) -> dict[str, Any]:
    """
    Connect TWS → mark opens still long → CLOSE filled-then-flat →
    reconcile never-filled ghosts → sync Supabase.
    """
    from ib_insync import IB, util

    util.startLoop()
    ib = IB()
    summary: dict[str, Any] = {
        "marked": [],
        "closed": [],
        "reconciled": [],
        "errors": [],
        "repair": repair,
    }

    print("=" * 64)
    print("Q-ALPHA LIVE TWS INTRADAY SYNC")
    print(f"  {datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"  host={TWS_HOST}:{TWS_PORT} clientId={TWS_CLIENT_ID}")
    print(f"  repair={repair}")
    print("=" * 64)

    try:
        ib.connect(TWS_HOST, TWS_PORT, clientId=TWS_CLIENT_ID, timeout=12)
    except Exception as exc:
        msg = f"CONNECT FAILED: {exc}"
        print(msg)
        summary["errors"].append(msg)
        return summary

    try:
        pos_map = _ib_position_map(ib)
        print(f"  TWS positions: {pos_map or '(flat)'}")

        store = PaperTradesStore()
        data = store.load()
        trades = data.get("trades") or []
        pool = PoolManager(state_path=state_path("pool_state.json"))

        from supabase_sync import sync_live_book_safe

        # --- 1) NEVER_FILLED ghosts (unfilled + flat) via existing reconcile ---
        try:
            from autonomous_agent import reconcile_unfilled_opens
            reconciled = reconcile_unfilled_opens(ib)
            summary["reconciled"] = [c.get("ticker") for c in reconciled]
            # Reload after reconcile mutated files.
            data = store.load()
            trades = data.get("trades") or []
            pool = PoolManager(state_path=state_path("pool_state.json"))
        except Exception as exc:
            print(f"  reconcile warn: {exc}")
            summary["errors"].append(f"reconcile: {exc}")

        closed_now: list[str] = []
        marked_now: list[str] = []

        for t in trades:
            status = str(t.get("status") or "").upper()
            if status not in OPEN_LEDGER:
                continue
            if t.get("approved_by") not in MANAGED_SOURCES:
                continue
            if str(t.get("execution_mode") or "") != "IBKR_PAPER":
                continue
            ticker = str(t.get("ticker") or "").upper()
            if not ticker:
                continue

            tws_qty = float(pos_map.get(ticker, 0.0))
            filled = _was_filled(t)

            # Still long → mark from TWS.
            if abs(tws_qty) >= 1e-6:
                mark = _tws_mark_price(ib, ticker)
                if mark is not None:
                    _apply_mark(t, mark)
                    marked_now.append(ticker)
                    sync_live_book_safe(trade=dict(t), pool_state=None)
                    print(
                        f"  MARK {ticker} TWS@{mark:.2f} "
                        f"pnl=${float(t.get('pnl_dollars') or 0):+.2f}"
                    )
                else:
                    print(f"  MARK {ticker}: no TWS price (left as-is)")
                continue

            # TWS flat.
            if not filled:
                # Unfilled flat should already be NEVER_FILLED via reconcile.
                print(f"  SKIP {ticker}: flat + not confirmed fill (reconcile owns)")
                continue

            # Filled then flat → BOOK CLOSED (the missing mid-day path).
            entry_date = str(t.get("entry_date") or "")[:10]
            sells = _collect_sell_fills(ib, ticker, entry_date)
            mark_hint = _finite(t.get("current_price"))
            exit_px, exit_reason = _infer_exit(t, sells, mark_hint)
            if exit_px <= 0:
                exit_px = float(t.get("stop_price") or t.get("entry_price") or 0)
                exit_reason = exit_reason or "BROKER_EXIT"
            _book_closed(t, exit_price=exit_px, exit_reason=exit_reason, pool=pool)
            closed_now.append(ticker)
            row = dict(t)
            row["position_size"] = 0.0
            sync_live_book_safe(trade=row, pool_state=None)
            _send_telegram(
                f"LIVE CLOSED {ticker} @ ${exit_px:.2f} ({exit_reason})\n"
                f"P&L ${float(t.get('pnl_dollars') or 0):+.2f}"
            )

        summary["marked"] = marked_now
        summary["closed"] = closed_now

        # Persist ledger + pool snapshot once.
        open_n = sum(
            1 for x in trades if str(x.get("status") or "").upper() in OPEN_LEDGER
        )
        data["summary"] = {
            **(data.get("summary") or {}),
            "open_trades": open_n,
        }
        store.save(data)
        sync_live_book_safe(trade=None, pool_state=pool.state)

        print(
            f"\nDONE marked={marked_now} closed={closed_now} "
            f"reconciled={summary['reconciled']} "
            f"pool=${pool.pool:.2f} deployed=${pool.deployed:.2f} "
            f"open_slots={pool.open_positions}"
        )
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Local TWS Live intraday sync")
    parser.add_argument(
        "--repair",
        action="store_true",
        help="Ops alias: run full sync (filled+flat → CLOSED). Default behavior.",
    )
    args = parser.parse_args()
    if args.repair:
        print("  --repair: full filled-flat->CLOSED sync (always on)")
    result = run_tws_intraday_sync(repair=bool(args.repair))
    if result.get("errors") and not result.get("marked") and not result.get("closed"):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
