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
# Exit px must match TWS sell within this relative band or repair overwrites.
EXIT_PX_TOL_FRAC = 0.0025  # 0.25%
# Post-sync mark verify: Cloud current_price must match TWS within this $ band.
MARK_PX_TOL = 0.05


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


def _fill_side(ex) -> str:
    return str(getattr(ex, "side", "") or "").upper()


def _fill_record(ex) -> dict[str, Any] | None:
    px = _finite(getattr(ex, "price", None) or getattr(ex, "avgPrice", None))
    qty = _finite(getattr(ex, "shares", None) or getattr(ex, "cumQty", None))
    if px is None or px <= 0:
        return None
    return {
        "price": px,
        "qty": qty or 0.0,
        "order_id": getattr(ex, "orderId", None),
        "time": str(getattr(ex, "time", "") or ""),
        "side": _fill_side(ex) or "",
    }


def _collect_symbol_fills(ib, symbol: str, since_date: str) -> tuple[list[dict], list[dict]]:
    """
    Buys + sells for symbol from session fills and reqExecutions.
    Never set ExecutionFilter.side (IB Error 321 on some builds).
    """
    buys: list[dict[str, Any]] = []
    sells: list[dict[str, Any]] = []
    seen: set[tuple] = set()

    def _ingest(ex, contract_sym: str = "") -> None:
        sym = (contract_sym or symbol).upper()
        if sym != symbol.upper():
            return
        rec = _fill_record(ex)
        if rec is None:
            return
        key = (rec["side"], rec["price"], rec["qty"], rec["time"], rec["order_id"])
        if key in seen:
            return
        seen.add(key)
        side = rec["side"]
        if side in {"BOT", "BUY"}:
            buys.append(rec)
        elif side in {"SLD", "SELL"} or not side:
            # Empty side: treat as sell candidate only if we already have buys
            # for this symbol in the same pull — else keep as sell (exit path).
            sells.append(rec)

    try:
        for fill in ib.fills() or []:
            c = getattr(fill, "contract", None)
            sym = str(getattr(c, "symbol", "") or "").upper() if c else ""
            ex = getattr(fill, "execution", None) or fill
            _ingest(ex, sym)
    except Exception as exc:
        print(f"  ib.fills warn: {exc}")

    try:
        from ib_insync import ExecutionFilter

        filt = ExecutionFilter()
        filt.symbol = symbol
        if since_date:
            filt.time = f"{since_date.replace('-', '')}-00:00:00"
        for fill in ib.reqExecutions(filt) or []:
            c = getattr(fill, "contract", None)
            sym = str(getattr(c, "symbol", "") or symbol).upper()
            ex = getattr(fill, "execution", None) or fill
            _ingest(ex, sym)
    except Exception as exc:
        print(f"  executions {symbol} warn: {exc}")

    # Drop empty-side rows that are clearly buys if price≈entry handled later.
    sells = [s for s in sells if s["side"] in {"SLD", "SELL", ""}]
    return buys, sells


def _vwap(fills: list[dict]) -> float | None:
    if not fills:
        return None
    num = 0.0
    den = 0.0
    for f in fills:
        q = float(f.get("qty") or 0)
        p = float(f.get("price") or 0)
        if q > 0 and p > 0:
            num += p * q
            den += q
    if den > 0:
        return num / den
    return float(fills[-1]["price"])


def _infer_exit(
    trade: dict,
    sell_fills: list[dict],
    mark: float | None,
    *,
    allow_stop_fallback: bool = True,
) -> tuple[float, str]:
    """
    Prefer TWS sell VWAP over stop_price / marks / Polygon.
    When sell fills exist, exit px is ALWAYS the sell VWAP (never stop_price).
    """
    entry = float(trade.get("entry_price") or 0)
    stop = float(trade.get("stop_price") or 0)
    target_2r = float(trade.get("target_2r") or 0)
    tol = max(0.02, entry * 0.002) if entry > 0 else 0.02

    px = _vwap(sell_fills)
    if px is not None and px > 0:
        # Classify from fill vs levels; price itself stays the TWS fill.
        if target_2r > 0 and px >= target_2r - tol:
            return float(px), "TARGET_2R"
        if stop > 0 and px <= stop + tol:
            return float(px), "STOP"
        return float(px), "BROKER_EXIT"

    # No TWS sell tape — only then consider mark / stop fallback.
    if mark is not None and mark > 0:
        if target_2r > 0 and mark >= target_2r - tol:
            return float(mark), "TARGET_2R"
        if stop > 0 and mark <= stop + tol:
            # Use mark (through-stop fill), not the theoretical stop_price.
            return float(mark), "STOP"
        return float(mark), "BROKER_EXIT"

    if allow_stop_fallback and stop > 0:
        return float(stop), "STOP"
    if entry > 0:
        return float(entry), "BROKER_EXIT"
    return 0.0, "BROKER_EXIT"


def _original_shares(trade: dict) -> int:
    """Recover share count even after shares_total was zeroed on close."""
    for key in ("shares_total", "shares_t1", "shares"):
        try:
            n = int(trade.get(key) or 0)
            if n > 0:
                return n
        except (TypeError, ValueError):
            pass
    plan = trade.get("order_plan") or {}
    for key in ("shares", "tranche_1_shares", "shares_t1"):
        try:
            n = int(plan.get(key) or 0)
            if n > 0:
                return n
        except (TypeError, ValueError):
            pass
    return 0


def _position_cost(trade: dict) -> float:
    """Dollar cost basis reserved at open (prefer order_plan)."""
    plan = trade.get("order_plan") or {}
    for raw in (
        trade.get("position_value"),
        trade.get("position_size"),
        plan.get("position_value"),
    ):
        px = _finite(raw)
        if px is not None and px > 0:
            return float(px)
    shares = _original_shares(trade)
    entry = _finite(trade.get("entry_price")) or _finite(plan.get("entry_price")) or 0.0
    if shares > 0 and entry > 0:
        return round(entry * shares, 2)
    return 0.0


def _set_closed_fields(
    trade: dict,
    *,
    exit_price: float,
    exit_reason: str,
) -> None:
    """Write CLOSED fields + realized PnL; does not touch pool (recalc later)."""
    shares = _original_shares(trade)
    cost = _position_cost(trade)
    if shares <= 0 and cost > 0 and float(trade.get("entry_price") or 0) > 0:
        shares = max(1, int(round(cost / float(trade["entry_price"]))))
    proceeds = round(exit_price * shares, 2) if shares > 0 else 0.0
    pnl = round(proceeds - cost, 2) if cost else 0.0
    pnl_pct = round(pnl / cost, 4) if cost > 0 else 0.0

    # Keep share memory for future repairs.
    if shares > 0:
        trade["shares_t1"] = int(trade.get("shares_t1") or shares)
        plan = dict(trade.get("order_plan") or {})
        if not plan.get("shares"):
            plan["shares"] = shares
        if not plan.get("position_value") and cost > 0:
            plan["position_value"] = cost
        trade["order_plan"] = plan

    trade["status"] = "CLOSED"
    trade["exit_reason"] = exit_reason
    trade["exit_price"] = round(exit_price, 4)
    trade["tranche_1_exit"] = round(exit_price, 4)
    # Clear stale stop_hit when exit was target/broker — Trade Log used to
    # fall through to stop_hit_price and show 24.98 after a 26.51 target fill.
    if exit_reason == "STOP":
        trade["stop_hit_price"] = round(exit_price, 4)
        trade["stop_hit_date"] = datetime.now(ET).strftime("%Y-%m-%d")
    else:
        trade["stop_hit_price"] = None
        trade["stop_hit_date"] = None
    trade["pnl_dollars"] = pnl
    trade["pnl_pct"] = pnl_pct
    trade["current_price"] = round(exit_price, 4)
    trade["shares_total"] = 0
    trade["remaining_t1"] = 0
    trade["remaining_t2"] = 0
    trade["remaining_t3"] = 0
    trade["position_value"] = 0.0
    trade["last_updated"] = datetime.now(timezone.utc).isoformat()
    trade["ibkr_status"] = "Filled"
    print(
        f"  CLOSED {trade.get('ticker')} @ ${exit_price:.4f} "
        f"({exit_reason}) pnl=${pnl:+.2f} shares={shares} cost=${cost:.2f}"
    )


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
    reason = str(trade.get("exit_reason") or "").upper()
    if "NEVER_FILLED" in reason or reason.startswith("REJECTED"):
        return False
    if int(trade.get("shares_total") or 0) > 0 and st not in {
        "SUBMITTED", "PreSubmitted", "PendingSubmit", "Cancelled", "Inactive",
        "Rejected", "",
    }:
        return True
    # Already CLOSED with a prior fill booking.
    if str(trade.get("status") or "").upper() == "CLOSED" and st in FILL_CONFIRMED:
        return True
    return st == "Filled"


def _booked_exit_px(trade: dict) -> float | None:
    """Best booked exit for disagreement checks."""
    for key in ("exit_price", "tranche_1_exit", "current_price", "stop_hit_price"):
        px = _finite(trade.get(key))
        if px is not None and px > 0:
            return px
    return None


def _exit_disagrees(trade: dict, exit_px: float, exit_reason: str) -> bool:
    """True if booked exit price or reason disagrees with TWS sell evidence."""
    booked_reason = str(trade.get("exit_reason") or "").upper().strip()
    want_reason = str(exit_reason or "").upper().strip()
    if booked_reason != want_reason:
        return True
    booked = _booked_exit_px(trade)
    if booked is None or booked <= 0:
        return True
    if exit_px <= 0:
        return False
    tol = max(0.01, exit_px * EXIT_PX_TOL_FRAC)
    return abs(booked - exit_px) > tol


def _rebuild_pool_counters(pool: PoolManager, trades: list[dict]) -> None:
    """Rebuild cash/deployed + trade counters from ledger (no double-count)."""
    from position_monitor import recalculate_pool_from_trades

    recalculate_pool_from_trades(pool, trades)
    closed = [
        t for t in trades if str(t.get("status") or "").upper() == "CLOSED"
    ]
    opens = [
        t for t in trades if str(t.get("status") or "").upper() in OPEN_LEDGER
    ]
    pool.state["total_trades"] = len(closed)
    pool.state["winning_trades"] = sum(
        1 for t in closed if float(t.get("pnl_dollars") or 0) > 0
    )
    pool.state["open_positions"] = len(opens)
    pool.state["last_updated"] = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S")
    pool.save_state()


def _verify_supabase_trades(
    trades: list[dict],
    *,
    tickers: list[str] | None = None,
    expected_marks: dict[tuple[str, str], float] | None = None,
) -> list[str]:
    """
    One-line Cloud verification after force upsert.
    Returns error strings when mark px disagrees with TWS by > MARK_PX_TOL.
    """
    want = {t.upper() for t in tickers} if tickers else None
    checks: list[tuple[str, str]] = []
    for t in trades:
        sym = str(t.get("ticker") or "").upper()
        if want is not None and sym not in want:
            continue
        entry = str(t.get("entry_date") or "")[:10]
        if entry:
            checks.append((sym, entry))
    if not checks:
        return []

    errors: list[str] = []
    try:
        from supabase_sync import SupabaseSync

        sync = SupabaseSync()
        print("\n  Supabase verify:")
        for ticker, entry_date in checks:
            result = (
                sync.client.table("trades")
                .select(
                    "ticker,entry_date,status,shares_total,exit_price,"
                    "current_price,last_updated"
                )
                .eq("ticker", ticker)
                .eq("entry_date", entry_date)
                .limit(1)
                .execute()
            )
            rows = result.data or []
            if not rows:
                print(f"    {ticker} {entry_date}: (no row)")
                errors.append(f"verify_missing:{ticker}")
                continue
            r = rows[0]
            cloud_px = _finite(r.get("current_price"))
            key = (ticker, entry_date)
            mark_note = ""
            if expected_marks and key in expected_marks:
                tws_px = float(expected_marks[key])
                if cloud_px is None or abs(cloud_px - tws_px) > MARK_PX_TOL:
                    msg = (
                        f"mark_mismatch:{ticker} "
                        f"cloud={cloud_px} tws={tws_px:.2f}"
                    )
                    errors.append(msg)
                    mark_note = " *** MISMATCH ***"
            print(
                f"    {r.get('ticker')} {r.get('entry_date')}: "
                f"status={r.get('status')} shares={r.get('shares_total')} "
                f"exit={r.get('exit_price')} px={r.get('current_price')} "
                f"updated={str(r.get('last_updated') or '')[:19]}"
                f"{mark_note}"
            )
    except Exception as exc:
        print(f"  Supabase verify warn: {exc}")
        errors.append(f"verify_error:{exc}")
    return errors


def _is_managed_ibkr(t: dict) -> bool:
    if t.get("approved_by") not in MANAGED_SOURCES:
        return False
    return str(t.get("execution_mode") or "") == "IBKR_PAPER"


def _sync_trade_row(trade: dict, *, reason: str, retries: int = 2) -> bool:
    """
    Upsert one trade to Supabase with retry. Never skip CLOSED transitions
    silently — callers log failures in summary['sync_errors'].
    """
    from supabase_sync import sync_live_book_safe

    row = dict(trade)
    st = str(row.get("status") or "").upper()
    if st in {"CLOSED", "NEVER_FILLED"}:
        row["position_size"] = 0.0
        row["shares_total"] = int(row.get("shares_total") or 0)

    ticker = str(row.get("ticker") or "?")
    for attempt in range(1, retries + 1):
        ok = sync_live_book_safe(trade=row, pool_state=None, label=f"{reason}:{ticker}")
        if ok:
            return True
        if attempt < retries:
            time.sleep(0.5)
    print(
        f"  *** SYNC FAILED {ticker} ({reason}) "
        f"status={st} after {retries} attempts ***"
    )
    return False


def run_tws_intraday_sync(*, repair: bool = False) -> dict[str, Any]:
    """
    Connect TWS → mark opens still long → CLOSE filled-then-flat →
    repair wrong exit px on already-CLOSED flats → rebuild pool → sync Supabase.
    """
    from ib_insync import IB, util

    util.startLoop()
    ib = IB()
    summary: dict[str, Any] = {
        "marked": [],
        "closed": [],
        "repaired": [],
        "reconciled": [],
        "errors": [],
        "sync_errors": [],
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
        # Warm fills cache.
        try:
            ib.reqExecutions()
            ib.sleep(1.0)
        except Exception:
            pass

        pos_map = _ib_position_map(ib)
        print(f"  TWS positions: {pos_map or '(flat)'}")

        store = PaperTradesStore()
        data = store.load()
        trades = data.get("trades") or []
        pool = PoolManager(state_path=state_path("pool_state.json"))

        # --- 1) NEVER_FILLED ghosts (unfilled + flat) via existing reconcile ---
        try:
            from autonomous_agent import reconcile_unfilled_opens
            reconciled = reconcile_unfilled_opens(ib)
            summary["reconciled"] = [c.get("ticker") for c in reconciled]
            data = store.load()
            trades = data.get("trades") or []
            pool = PoolManager(state_path=state_path("pool_state.json"))
        except Exception as exc:
            print(f"  reconcile warn: {exc}")
            summary["errors"].append(f"reconcile: {exc}")

        closed_now: list[str] = []
        repaired_now: list[str] = []
        marked_now: list[str] = []
        mark_expectations: dict[tuple[str, str], float] = {}
        ledger_dirty = False
        today_et = datetime.now(ET).strftime("%Y-%m-%d")

        for t in trades:
            if not _is_managed_ibkr(t):
                continue
            status = str(t.get("status") or "").upper()
            ticker = str(t.get("ticker") or "").upper()
            if not ticker:
                continue

            tws_qty = float(pos_map.get(ticker, 0.0))
            entry_date = str(t.get("entry_date") or "")[:10]

            # Still long → mark from TWS (OPEN ledger only).
            if abs(tws_qty) >= 1e-6:
                if status not in OPEN_LEDGER:
                    print(f"  WARN {ticker}: TWS long but ledger={status}")
                mark = _tws_mark_price(ib, ticker)
                if mark is not None and status in OPEN_LEDGER:
                    _apply_mark(t, mark)
                    marked_now.append(ticker)
                    mark_expectations[(ticker, entry_date)] = float(mark)
                    ledger_dirty = True
                    if not _sync_trade_row(t, reason="mark"):
                        summary["sync_errors"].append(f"mark:{ticker}")
                    print(
                        f"  MARK {ticker} TWS@{mark:.2f} "
                        f"pnl=${float(t.get('pnl_dollars') or 0):+.2f}"
                    )
                continue

            # TWS flat — pull executions (SoT for exit px).
            buys, sells = _collect_symbol_fills(ib, ticker, entry_date)
            sell_vwap = _vwap(sells)
            buy_vwap = _vwap(buys)
            if buy_vwap and buy_vwap > 0 and status in OPEN_LEDGER | {"CLOSED"}:
                booked_entry = _finite(t.get("entry_price")) or 0.0
                if (
                    repair
                    and booked_entry > 0
                    and abs(booked_entry - buy_vwap)
                    > max(0.01, buy_vwap * EXIT_PX_TOL_FRAC)
                ):
                    print(
                        f"  ENTRY note {ticker}: ledger ${booked_entry:.4f} "
                        f"vs TWS bot VWAP ${buy_vwap:.4f} (keeping ledger entry)"
                    )

            filled = _was_filled(t) or bool(sells) or bool(buys)

            if status in OPEN_LEDGER:
                if not filled:
                    print(f"  SKIP {ticker}: flat + not confirmed fill (reconcile owns)")
                    continue
                # Prefer TWS sells; do not substitute stop_price when fills exist.
                exit_px, exit_reason = _infer_exit(
                    t,
                    sells,
                    sell_vwap or _finite(t.get("current_price")),
                    allow_stop_fallback=not bool(sells),
                )
                if exit_px <= 0:
                    print(f"  SKIP {ticker}: flat+filled but no usable exit px")
                    continue
                _set_closed_fields(t, exit_price=exit_px, exit_reason=exit_reason)
                closed_now.append(ticker)
                ledger_dirty = True
                print(
                    f"  >>> LIVE CLOSED {ticker} @ ${exit_px:.2f} ({exit_reason}) "
                    f"— pushing to Supabase"
                )
                if not _sync_trade_row(t, reason="flat_to_closed"):
                    summary["sync_errors"].append(f"closed:{ticker}")
                _send_telegram(
                    f"LIVE CLOSED {ticker} @ ${exit_px:.2f} ({exit_reason})\n"
                    f"P&L ${float(t.get('pnl_dollars') or 0):+.2f}"
                )
                continue

            # --- CLOSED repair: TWS sell wins over booked stop/Polygon ---
            # --repair: always re-check today's CLOSED IBKR_PAPER flats.
            # Normal runs: repair if sell VWAP disagrees with book.
            if status != "CLOSED":
                continue
            if repair and entry_date and entry_date != today_et:
                continue
            if not sells:
                if repair and entry_date == today_et:
                    print(
                        f"  REPAIR skip {ticker}: no TWS sell fills "
                        f"(keeping booked exit={_booked_exit_px(t)})"
                    )
                continue
            exit_px, exit_reason = _infer_exit(
                t, sells, sell_vwap, allow_stop_fallback=False,
            )
            if exit_px <= 0:
                continue
            if not _exit_disagrees(t, exit_px, exit_reason):
                # Still clear stale stop_hit if reason is not STOP.
                if exit_reason != "STOP" and t.get("stop_hit_price") is not None:
                    t["stop_hit_price"] = None
                    t["stop_hit_date"] = None
                    t["exit_price"] = round(exit_px, 4)
                    ledger_dirty = True
                    print(f"  CLEAN {ticker}: cleared stale stop_hit_price")
                continue
            old_px = _booked_exit_px(t)
            old_reason = t.get("exit_reason")
            old_pnl = float(t.get("pnl_dollars") or 0)
            _set_closed_fields(t, exit_price=exit_px, exit_reason=exit_reason)
            new_pnl = float(t.get("pnl_dollars") or 0)
            repaired_now.append(ticker)
            ledger_dirty = True
            print(f"  >>> REPAIR CLOSED {ticker} — pushing to Supabase")
            if not _sync_trade_row(t, reason="repair_closed"):
                summary["sync_errors"].append(f"repair:{ticker}")
            print(
                f"  REPAIR {ticker}: {old_px} ({old_reason}) pnl={old_pnl:+.2f} "
                f"-> {exit_px:.4f} ({exit_reason}) pnl={new_pnl:+.2f} "
                f"[pool delta via recalc, no double-close]"
            )

        summary["marked"] = marked_now
        summary["closed"] = closed_now
        summary["repaired"] = repaired_now

        # Always rebuild pool from ledger so cash/deployed/Total Trades match.
        _rebuild_pool_counters(pool, trades)

        open_n = sum(
            1 for x in trades if str(x.get("status") or "").upper() in OPEN_LEDGER
        )
        data["summary"] = {
            **(data.get("summary") or {}),
            "open_trades": open_n,
            "closed_trades": int(pool.state.get("total_trades") or 0),
        }
        store.save(data)

        # Force-upsert every managed trade + pool so Cloud cannot lag.
        from supabase_sync import sync_live_book_safe

        force_closed_n = 0
        force_closed_ok = 0
        for t in trades:
            if not _is_managed_ibkr(t):
                continue
            reason = "force_upsert"
            st = str(t.get("status") or "").upper()
            if st == "CLOSED":
                reason = "force_closed"
                force_closed_n += 1
            if _sync_trade_row(t, reason=reason):
                if reason == "force_closed":
                    force_closed_ok += 1
            else:
                summary["sync_errors"].append(f"{reason}:{t.get('ticker')}")

        if not sync_live_book_safe(trade=None, pool_state=pool.state, label="pool"):
            summary["sync_errors"].append("pool_snapshot")

        summary["force_closed_n"] = force_closed_n
        summary["force_closed_ok"] = force_closed_ok

        gap_open_tickers = sorted({
            str(t.get("ticker") or "").upper()
            for t in trades
            if _is_managed_ibkr(t)
            and str(t.get("status") or "").upper() in OPEN_LEDGER
        })
        verify_errors = _verify_supabase_trades(
            trades,
            tickers=gap_open_tickers,
            expected_marks=mark_expectations,
        )
        if verify_errors:
            summary["sync_errors"].extend(verify_errors)

        # TSD 3HR swing book → Supabase (separate from gap-agent trades).
        try:
            from tsd_supabase_sync import sync_tsd_positions_to_supabase

            tsd_summary = sync_tsd_positions_to_supabase(
                ib, mark_fn=_tws_mark_price,
            )
            summary["tsd_upserted"] = tsd_summary.get("upserted", 0)
            if tsd_summary.get("verify_errors"):
                summary["sync_errors"].extend(tsd_summary["verify_errors"])
        except Exception as exc:
            print(f"  TSD sync warn: {exc}")
            summary["sync_errors"].append(f"tsd_sync:{exc}")

        print(
            f"\nDONE marked={marked_now} closed={closed_now} "
            f"repaired={repaired_now} reconciled={summary['reconciled']} "
            f"force_closed={force_closed_ok}/{force_closed_n} "
            f"sync_errors={summary['sync_errors'] or 'none'} "
            f"pool=${pool.pool:.2f} deployed=${pool.deployed:.2f} "
            f"open_slots={pool.open_positions} "
            f"total_trades={pool.state.get('total_trades')} "
            f"dirty={ledger_dirty}"
        )
        if summary["sync_errors"]:
            summary["errors"].extend(summary["sync_errors"])

        try:
            from supabase_sync import SupabaseSync

            SupabaseSync().log_health(
                "tws_sync",
                "OK" if not summary["sync_errors"] else "WARN",
                f"marked={marked_now} tsd={summary.get('tsd_upserted', 0)} "
                f"errors={len(summary.get('sync_errors') or [])}",
            )
        except Exception:
            pass
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
        help="Re-price CLOSED exits from TWS sells + force Supabase upsert.",
    )
    args = parser.parse_args()
    if args.repair:
        print("  --repair: re-price CLOSED from TWS sell fills + full sync")
    result = run_tws_intraday_sync(repair=bool(args.repair))
    if result.get("sync_errors"):
        return 1
    if result.get("errors") and not (
        result.get("marked") or result.get("closed") or result.get("repaired")
    ):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
