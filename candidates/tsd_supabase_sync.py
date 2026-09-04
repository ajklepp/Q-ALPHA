"""
Q-ALPHA — sync TSD book (tsd_book_state.json) open legs to Supabase.

Separate from gap-agent paper_trades / trades table. Called from
tws_intraday_sync.py after agent marks (TWS SoT for current_price).

Usage:
  py -3 candidates/tsd_supabase_sync.py
"""
from __future__ import annotations

import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pytz

CANDIDATES = Path(__file__).resolve().parent
ROOT = CANDIDATES.parent
if str(CANDIDATES) not in sys.path:
    sys.path.insert(0, str(CANDIDATES))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tsd_scan_pipeline.tsd_capacity import (  # noqa: E402
    full_slots_used,
    load_state,
    open_symbols,
)
from tsd_scan_pipeline.tsd_pool import load_pool  # noqa: E402

from dashboard_tsd_helpers import map_exit_layer, mfe_in_r, next_trail_stop  # noqa: E402

ET = pytz.timezone("America/New_York")
MARK_PX_TOL = 0.05
TSD_TABLE_MISSING_MSG = (
    "*** TSD: run candidates/sql/tsd_cloud.sql in Supabase SQL editor ***"
)
TWS_MARK_TIMEOUT_SEC = 8.0

WATCHLIST_CACHE = (
    CANDIDATES / "tsd_scan_pipeline" / "results" / "last_watchlist.json"
)
# Peak Hour Live Paper SoT for dashboard "launches" panel (not 3H last_watchlist).
LAUNCH_CACHE = (
    CANDIDATES / "tsd_scan_pipeline" / "results" / "last_1h_launch.json"
)

try:
    from state_paths import state_path

    WATCH_QUEUE_PATH = state_path("tsd_watch_queue.json")
except Exception:
    WATCH_QUEUE_PATH = CANDIDATES / "tsd_watch_queue.json"


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


def _tranche_summary(trail: dict[str, Any]) -> str:
    """Human-readable T1–T4 state for dashboard."""
    parts: list[str] = []
    for t in (trail or {}).get("tranches") or []:
        tid = str(t.get("id") or "?")
        if t.get("closed"):
            parts.append(f"{tid} closed")
        elif t.get("trailing"):
            parts.append(f"{tid} trail")
        else:
            parts.append(f"{tid} open")
    return " / ".join(parts) if parts else "—"


def _serialize_tranches(trail: dict[str, Any]) -> list[dict[str, Any]]:
    """Dashboard-friendly tranche rows from trail doc."""
    out: list[dict[str, Any]] = []
    for t in (trail or {}).get("tranches") or []:
        if not isinstance(t, dict):
            continue
        armed = bool(t.get("trailing")) and not t.get("closed")
        stop = None
        if armed:
            rh = _finite(t.get("run_high"))
            tp = _finite(t.get("trail_pct"))
            if rh and tp:
                stop = round(rh * (1.0 - tp), 4)
        out.append({
            "id": t.get("id"),
            "shares": t.get("shares"),
            "trigger_price": _finite(t.get("trigger_price")),
            "trail_pct": _finite(t.get("trail_pct")),
            "run_high": _finite(t.get("run_high")),
            "armed": armed,
            "trail_stop": stop,
            "closed": bool(t.get("closed")),
        })
    return out


def flatten_open_legs(book: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten TSD book open legs into Supabase row dicts."""
    pos_meta = {
        str(p.get("symbol") or "").upper(): p
        for p in (book.get("positions") or [])
    }
    rows: list[dict[str, Any]] = []
    for pos in book.get("positions") or []:
        if str(pos.get("status") or "").upper() != "OPEN":
            continue
        symbol = str(pos.get("symbol") or "").upper()
        if not symbol:
            continue
        t4_only = bool(pos.get("t4_only"))
        for leg in pos.get("legs") or []:
            if str(leg.get("status") or "").upper() != "OPEN":
                continue
            trail = leg.get("trail") or {}
            leg_time = str(leg.get("time") or trail.get("opened_at") or "")
            entry_date = leg_time[:10] if leg_time else ""
            if not entry_date:
                entry_date = datetime.now(ET).strftime("%Y-%m-%d")
            entry_price = _finite(trail.get("entry_price")) or _finite(leg.get("price"))
            shares = int(leg.get("shares") or 0)
            kill_price = _finite(trail.get("kill_price"))
            current_price = _finite(trail.get("last_close")) or entry_price
            scan_score = _finite(leg.get("scan_score"))

            pnl_dollars = None
            pnl_pct = None
            if entry_price and shares > 0 and current_price:
                pnl_dollars = round((current_price - entry_price) * shares, 2)
                pnl_pct = round((current_price - entry_price) / entry_price, 4)

            tranche_rows = _serialize_tranches(trail)
            raw_tranches = trail.get("tranches") or []
            t1_trigger = (
                _finite(raw_tranches[0].get("trigger_price"))
                if raw_tranches else None
            )
            nxt_trail = next_trail_stop(raw_tranches)
            peak = _finite(trail.get("peak_high"))
            mfe_r_val = None
            if entry_price and kill_price and peak:
                mfe_r_val = mfe_in_r(entry_price, peak, kill_price)

            rows.append(
                {
                    "symbol": symbol,
                    "entry_date": entry_date,
                    "leg_opened_at": leg_time or f"{entry_date}T00:00:00",
                    "entry_price": entry_price,
                    "shares": shares,
                    "kill_price": kill_price,
                    "current_price": current_price,
                    "pnl_dollars": pnl_dollars,
                    "pnl_pct": pnl_pct,
                    "status": "OPEN",
                    "last_bar_time": trail.get("last_bar_time"),
                    "scan_score": scan_score,
                    "peak_high": peak,
                    "kill_pct": _finite(trail.get("kill_pct") or leg.get("kill_pct")),
                    "kill_source": leg.get("kill_source") or trail.get("kill_source"),
                    "bar_state": leg.get("bar_state"),
                    "trail_pct": _finite(trail.get("trail_pct")),
                    "trading_day": int(trail.get("trading_day") or 0) or None,
                    "t4_only": t4_only,
                    "tranche_summary": _tranche_summary(trail),
                    "structure_stop": _finite(leg.get("structure_stop")),
                    "rth_armed": bool(leg.get("rth_armed")),
                    "structure_stop_reason": leg.get("structure_stop_reason"),
                    "one_r_locked": bool(leg.get("one_r_locked")),
                    "breakeven_locked": bool(leg.get("breakeven_locked")),
                    "tranche_json": tranche_rows,
                    "t1_trigger_price": t1_trigger,
                    "next_trail_stop": nxt_trail,
                    "launch_score": _finite(leg.get("launch_score")),
                    "phase": leg.get("phase"),
                    "pre_catalyst": bool(leg.get("pre_catalyst")),
                    "mfe_r": mfe_r_val,
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                }
            )
    return rows


def flatten_closed_legs(book: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten CLOSED legs from TSD book into tsd_closed_legs row dicts."""
    rows: list[dict[str, Any]] = []
    for pos in book.get("positions") or []:
        symbol = str(pos.get("symbol") or "").upper()
        if not symbol:
            continue
        for leg in pos.get("legs") or []:
            if str(leg.get("status") or "").upper() != "CLOSED":
                continue
            exits = leg.get("exits") or []
            if not exits:
                continue
            trail = leg.get("trail") or {}
            leg_time = str(leg.get("time") or trail.get("opened_at") or "")
            entry_date = leg_time[:10] if leg_time else ""
            entry_price = _finite(trail.get("entry_price")) or _finite(leg.get("price"))
            leg_shares = int(leg.get("shares") or 0)
            scan_score = _finite(leg.get("scan_score"))

            exit_shares = 0
            exit_notional = 0.0
            pnl_dollars = 0.0
            closed_at = ""
            exit_reason = ""
            for ex in exits:
                sh = int(ex.get("shares") or 0)
                px = _finite(ex.get("exit_price"))
                if sh <= 0 or px is None:
                    continue
                exit_shares += sh
                exit_notional += px * sh
                if entry_price:
                    pnl_dollars += (px - entry_price) * sh
                ex_time = str(ex.get("time") or "")
                if ex_time >= closed_at:
                    closed_at = ex_time
                    exit_reason = str(ex.get("reason") or exit_reason)

            if exit_shares <= 0 or entry_price is None or entry_price <= 0:
                continue
            exit_price = round(exit_notional / exit_shares, 4)
            cost_basis = entry_price * exit_shares
            pnl_pct = round(pnl_dollars / cost_basis, 4) if cost_basis > 0 else 0.0
            layer = map_exit_layer(exit_reason)

            rows.append(
                {
                    "symbol": symbol,
                    "leg_opened_at": leg_time or f"{entry_date}T00:00:00",
                    "entry_date": entry_date,
                    "entry_price": entry_price,
                    "shares": leg_shares or exit_shares,
                    "exit_price": exit_price,
                    "exit_reason": exit_reason or "CLOSED",
                    "exit_layer": layer,
                    "pnl_dollars": round(pnl_dollars, 2),
                    "pnl_pct": pnl_pct,
                    "closed_at": closed_at or datetime.now(ET).isoformat(),
                    "scan_score": scan_score,
                    "launch_score": _finite(leg.get("launch_score")),
                    "phase": leg.get("phase"),
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                }
            )
    return rows


def apply_tws_marks(
    rows: list[dict[str, Any]],
    ib,
    mark_fn: Callable,
    *,
    timeout_sec: float = TWS_MARK_TIMEOUT_SEC,
) -> None:
    """Refresh current_price / PnL from TWS snapshot marks; book last_close fallback."""
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        if not symbol:
            continue
        book_px = _finite(row.get("current_price"))
        print(f"  TSD TWS mark {symbol} ...")
        try:
            mark = mark_fn(ib, symbol, timeout_sec=timeout_sec)
        except TypeError:
            # Legacy mark_fn(ib, symbol) without timeout kwarg.
            mark = mark_fn(ib, symbol)
        if mark is None or mark <= 0:
            if book_px is not None and book_px > 0:
                print(
                    f"  TSD mark {symbol} fallback book last_close=${book_px:.4f}"
                )
            else:
                print(f"  TSD mark {symbol} no TWS px and no book fallback")
            continue
        entry = _finite(row.get("entry_price")) or 0.0
        shares = int(row.get("shares") or 0)
        row["current_price"] = round(mark, 4)
        if entry > 0 and shares > 0:
            row["pnl_dollars"] = round((mark - entry) * shares, 2)
            row["pnl_pct"] = round((mark - entry) / entry, 4)
        row["last_updated"] = datetime.now(timezone.utc).isoformat()


def _assert_tsd_tables(sync) -> None:
    """Fail loudly when cloud schema is missing."""
    try:
        sync.client.table("tsd_positions").select("symbol").limit(1).execute()
    except Exception as exc:
        err = str(exc)
        if "PGRST205" in err or "tsd_positions" in err:
            raise RuntimeError(TSD_TABLE_MISSING_MSG) from exc
        raise


def _pool_snapshot_from_local(book: dict[str, Any]) -> dict[str, Any]:
    pool_doc = load_pool()
    cash = float(pool_doc.get("pool") or 0.0)
    deployed = float(pool_doc.get("deployed") or 0.0)
    starting = float(pool_doc.get("starting_pool") or 3000.0)
    spy_regime = "UNKNOWN"
    try:
        from tsd_scan_pipeline.tsd_entry_gates import fetch_regime_bull

        bull, label, _ = fetch_regime_bull()
        spy_regime = str(label) if label else ("BULL" if bull else "BEAR")
    except Exception:
        pass
    return {
        "snapshot_date": datetime.now(ET).strftime("%Y-%m-%d"),
        "pool": round(cash, 2),
        "deployed": round(deployed, 2),
        "open_positions": full_slots_used(book),
        "open_names": len(open_symbols(book)),
        "starting_pool": starting,
        "spy_regime": spy_regime,
        "vix_regime": "NORMAL",
        "sizing_pct": "100%",
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


def _board_rows_from_local_queue_and_book(
    *,
    open_symbols_set: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Build Peak Hour watchlist rows from local queue + open book (cloud queue table may be absent)."""
    open_set = {s.upper() for s in (open_symbols_set or set())}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    # Open book first → ENTERED
    try:
        book = load_state()
        for i, pos in enumerate(book.get("positions") or [], 1):
            if str(pos.get("status") or "").upper() != "OPEN":
                continue
            sym = str(pos.get("symbol") or "").upper()
            if not sym:
                continue
            for leg in pos.get("legs") or []:
                if str(leg.get("status") or "").upper() != "OPEN":
                    continue
                open_set.add(sym)
                rows.append({
                    "symbol": sym,
                    "rank": i,
                    "scan_score": _finite(leg.get("scan_score")),
                    "launch_score": _finite(leg.get("launch_score") or leg.get("scan_score")),
                    "phase": leg.get("phase"),
                    "buy_signal": True,
                    "entry_price": _finite(leg.get("price")),
                    "wt_gap": _finite(leg.get("htf_1h_bar_hour")),
                    "in_book": True,
                    "trade_pick": True,
                    "status_label": "ENTERED",
                    "profiler_pass": True,
                    "tags": ["ENTERED", "from_book"],
                })
                seen.add(sym)
                break
    except Exception as exc:
        print(f"  board-from-book warn: {exc}")

    # Local queue
    try:
        if WATCH_QUEUE_PATH.exists():
            payload = json.loads(WATCH_QUEUE_PATH.read_text(encoding="utf-8"))
            for q in payload.get("queue") or []:
                sym = str(q.get("symbol") or "").upper()
                if not sym or sym in seen:
                    continue
                st = str(q.get("status") or "QUEUED").upper()
                if sym in open_set:
                    label = "ENTERED"
                elif st in ("WATCHING", "ADDED", "UPDATED", "CONFIRMED", "QUEUED"):
                    label = "QUEUED"
                elif st in ("SKIP", "SKIPPED"):
                    label = "SKIP"
                else:
                    label = st
                rows.append({
                    "symbol": sym,
                    "rank": len(rows) + 1,
                    "scan_score": _finite(q.get("htf_score") or q.get("scan_score")),
                    "launch_score": _finite(q.get("launch_score") or q.get("combined_rank_score")),
                    "phase": q.get("phase"),
                    "buy_signal": bool(q.get("buy_signal") or q.get("htf_1h_buy_signal")),
                    "entry_price": _finite(q.get("htf_1h_close") or q.get("cross_level")),
                    "wt_gap": _finite(q.get("htf_1h_bar_hour")),
                    "in_book": sym in open_set,
                    "trade_pick": label == "ENTERED",
                    "status_label": label,
                    "profiler_pass": True,
                    "tags": [f"hour={q.get('htf_1h_bar_hour')}", label, "from_queue"],
                })
                seen.add(sym)
    except Exception as exc:
        print(f"  board-from-queue warn: {exc}")
    return rows


def sync_tsd_watchlist_from_file(
    *,
    open_symbols_set: set[str] | None = None,
    trade_symbols: set[str] | None = None,
) -> int:
    """
    Upsert Peak Hour 1H launch board to tsd_watchlist (dashboard SoT).

    Prefer today's last_1h_launch.json even when empty (clears stale board).
    Always merge current OPEN book legs as ENTERED.
    Fall back to local queue only when launch cache is missing/stale (not today).
    """
    open_set = set(open_symbols_set or set())
    trade_set = set(trade_symbols or set())
    rows: list[dict[str, Any]] = []
    scan_at = datetime.now(ET).isoformat()
    today = datetime.now(ET).strftime("%Y-%m-%d")
    cache_is_today = False

    if LAUNCH_CACHE.exists():
        try:
            payload = json.loads(LAUNCH_CACHE.read_text(encoding="utf-8"))
            updated = str(payload.get("updated_at") or "")
            cache_is_today = updated.startswith(today)
            scan_at = updated or scan_at
            for r in payload.get("rows") or []:
                sym = str(r.get("symbol") or "").upper()
                if not sym:
                    continue
                status = str(r.get("status") or "SIGNAL").upper()
                if sym in open_set or status == "ENTERED":
                    status_label = "ENTERED"
                elif status in ("QUEUED", "WATCHING", "ADDED", "UPDATED", "TAKE"):
                    status_label = "QUEUED"
                elif status in ("SKIP", "SKIPPED"):
                    status_label = "SKIP"
                else:
                    status_label = status
                rows.append({
                    "symbol": sym,
                    "rank": int(r.get("rank") or 0),
                    "scan_score": _finite(r.get("htf_score")),
                    "launch_score": _finite(r.get("launch_score") or r.get("combined_rank_score")),
                    "phase": r.get("phase"),
                    "buy_signal": bool(r.get("buy_signal")),
                    "entry_price": _finite(r.get("htf_1h_close")),
                    "wt_gap": _finite(r.get("htf_1h_bar_hour")),
                    "early_bull": False,
                    "in_book": sym in open_set,
                    "trade_pick": sym in trade_set or status_label == "ENTERED",
                    "status_label": status_label,
                    "profiler_pass": True,
                    "tags": [f"hour={r.get('htf_1h_bar_hour')}", status_label],
                    "scan_at": scan_at,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                })
        except Exception as exc:
            print(f"  launch cache read warn: {exc}")

    # Always surface open Peak Hour legs on the board.
    seen = {str(r.get("symbol") or "").upper() for r in rows}
    try:
        from tsd_scan_pipeline.tsd_capacity import load_state

        book = load_state()
        for pos in book.get("positions") or []:
            if str(pos.get("status") or "").upper() != "OPEN":
                continue
            sym = str(pos.get("symbol") or "").upper()
            if not sym or sym in seen:
                continue
            open_set.add(sym)
            for leg in pos.get("legs") or []:
                if str(leg.get("status") or "").upper() != "OPEN":
                    continue
                rows.append({
                    "symbol": sym,
                    "rank": len(rows) + 1,
                    "scan_score": _finite(leg.get("scan_score")),
                    "launch_score": _finite(leg.get("launch_score") or leg.get("scan_score")),
                    "phase": leg.get("phase"),
                    "buy_signal": True,
                    "entry_price": _finite(leg.get("price")),
                    "wt_gap": _finite(leg.get("htf_1h_bar_hour") or leg.get("bar_hour")),
                    "in_book": True,
                    "trade_pick": True,
                    "status_label": "ENTERED",
                    "profiler_pass": True,
                    "tags": ["ENTERED", "from_book"],
                    "scan_at": scan_at,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                })
                seen.add(sym)
                break
    except Exception as exc:
        print(f"  board open-merge warn: {exc}")

    # Only use stale queue/book synthesis when there is no fresh launch cache.
    if not rows and not cache_is_today:
        rows = _board_rows_from_local_queue_and_book(open_symbols_set=open_set)
        if rows:
            print(f"  Peak Hour board from local queue/book: {len(rows)} row(s)")
            scan_at = datetime.now(ET).isoformat()

    if not rows:
        print("  Peak Hour board empty — clearing stale tsd_watchlist")
        try:
            from supabase_sync import SupabaseSync

            SupabaseSync().replace_tsd_watchlist([])
        except Exception as exc:
            print(f"  clear watchlist warn: {exc}")
        return 0

    return sync_tsd_watchlist_to_supabase(
        rows,
        scan_at=scan_at,
        open_symbols_set=open_set,
        trade_symbols=trade_set,
    )


def sync_tsd_watchlist_to_supabase(
    watch_rows: list[dict[str, Any]],
    *,
    scan_at: str,
    open_symbols_set: set[str] | None = None,
    trade_symbols: set[str] | None = None,
) -> int:
    """Replace-all current TSD watchlist in Supabase (Peak Hour launch board)."""
    from supabase_sync import SupabaseSync

    opens = {s.upper() for s in (open_symbols_set or set())}
    trades = {s.upper() for s in (trade_symbols or set())}
    rows: list[dict[str, Any]] = []
    for i, w in enumerate(watch_rows[:25], start=1):
        sym = str(w.get("symbol") or "").upper()
        if not sym:
            continue
        prof = w.get("profiler") or {}
        tsd_prof = w.get("tsd_profile") or prof.get("profile") or {}
        in_book = bool(w.get("in_book")) or sym in opens
        trade_pick = bool(w.get("trade_pick")) or sym in trades
        # Prefer Peak Hour status from last_1h_launch; else legacy labels.
        status_label = w.get("status_label")
        if not status_label:
            if in_book:
                status_label = "ENTERED"
            elif trade_pick:
                status_label = "QUEUED"
            else:
                status_label = "SIGNAL"
        close_px = (
            _finite(w.get("entry_price"))
            or _finite(w.get("htf_1h_close"))
            or _finite(w.get("close"))
        )
        tags = w.get("tags") or []
        rows.append(
            {
                "symbol": sym,
                "rank": int(w.get("rank") or i),
                "scan_score": _finite(w.get("scan_score") or w.get("htf_score")),
                "trend_strength": _finite(w.get("trend_strength")),
                "mfi": _finite(w.get("mfi")),
                "buy_signal": bool(w.get("buy_signal")),
                "profiler_pass": bool(w.get("profiler_pass", True)),
                "in_book": in_book,
                "trade_pick": trade_pick,
                "status_label": status_label,
                "entry_price": close_px,
                "kill_price": _finite(w.get("kill_price")),
                "launch_score": _finite(w.get("launch_score")),
                "phase": w.get("phase"),
                "wt_gap": _finite(w.get("wt_gap")),  # Peak Hour: bar hour
                "early_bull": bool(w.get("early_bull")),
                "analog_count": tsd_prof.get("analog_count") if tsd_prof else w.get("analog_count"),
                "analog_win_rate": _finite(
                    (tsd_prof or {}).get("analog_win_rate") or w.get("analog_win_rate")
                ),
                "pre_catalyst": bool(w.get("pre_catalyst")),
                "tags": tags if tags else None,
                "scan_at": scan_at,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    sync = SupabaseSync()
    sync.replace_tsd_watchlist(rows)
    print(f"  Peak Hour watchlist upserted {len(rows)} rows (cleared stale if empty)")
    return len(rows)


def sync_tsd_watch_queue_from_file() -> int:
    """Sync local tsd_watch_queue.json to Supabase entry pipeline table."""
    from supabase_sync import SupabaseSync

    today = datetime.now(ET).strftime("%Y-%m-%d")
    queue_rows: list[dict[str, Any]] = []
    if WATCH_QUEUE_PATH.exists():
        try:
            payload = json.loads(WATCH_QUEUE_PATH.read_text(encoding="utf-8"))
            queue_rows = payload.get("queue") or []
        except Exception:
            queue_rows = []

    # Only today's queue rows — drop stale WATCHING names from prior days.
    rows: list[dict[str, Any]] = []
    for q in queue_rows:
        sym = str(q.get("symbol") or "").upper()
        if not sym:
            continue
        added = str(q.get("added_at") or q.get("scan_at") or "")
        if added and not added.startswith(today):
            continue
        gates = q.get("gates")
        rows.append({
            "symbol": sym,
            "status": str(q.get("status") or "WATCHING").upper(),
            "signal_lane": q.get("signal_lane"),
            "launch_score": _finite(q.get("launch_score")),
            "launch_score_display": _finite(
                q.get("launch_score_display") or q.get("entry_score")
            ),
            "phase": q.get("phase"),
            "scan_score": _finite(q.get("scan_score")),
            "wt_gap": _finite(q.get("wt_gap")),
            "cross_level": _finite(q.get("cross_level") or q.get("close")),
            "early_bull": bool(q.get("early_bull")),
            "buy_signal": bool(q.get("buy_signal")),
            "pre_catalyst": bool(q.get("pre_catalyst")),
            "analog_count": q.get("analog_count"),
            "analog_win_rate": _finite(q.get("analog_win_rate")),
            "gates": gates,
            "quality_gates": q.get("quality_gates"),
            "tags": q.get("tags"),
            "size_mult": _finite(q.get("size_mult")) or 1.0,
            "news_summary": q.get("news_summary"),
            "catalyst_tier": int(q.get("catalyst_tier") or 0),
            "sentiment_score": _finite(q.get("sentiment_score")),
            "regime": q.get("regime"),
            "skip_reason": q.get("skip_reason"),
            "added_at": q.get("added_at") or q.get("scan_at"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    sync = SupabaseSync()
    sync.replace_tsd_watch_queue(rows)
    print(f"  TSD watch queue upserted {len(rows)} rows (today-only; cleared stale)")
    return len(rows)


def push_dashboard_best_effort(
    ib=None,
    *,
    mark_fn: Callable | None = None,
    book: dict[str, Any] | None = None,
    telegram_on_fail: bool = False,
) -> dict[str, Any]:
    """
    Best-effort book → Supabase push after entry/exit/queue admit.

    Never raises. Optional Telegram if sync fails hard.
    """
    try:
        summary = sync_tsd_positions_to_supabase(
            ib, mark_fn=mark_fn, book=book,
        )
    except Exception as exc:
        print(f"  dashboard sync warn: {exc}")
        summary = {
            "verify_errors": [f"push:{exc}"],
            "upserted": 0,
            "watch_queue_synced": 0,
            "watchlist_synced": 0,
        }
    print(
        f"  push_dashboard: open={summary.get('upserted')} "
        f"closed={summary.get('closed_upserted')} "
        f"pool={summary.get('pool_synced')} "
        f"watchlist={summary.get('watchlist_synced')} "
        f"queue={summary.get('watch_queue_synced')}"
    )
    errs = summary.get("verify_errors") or []
    if telegram_on_fail and errs:
        try:
            from tsd_scan_pipeline.tsd_notify import notify_tsd

            notify_tsd(f"Peak Hour sync failed\n{errs[0]}")
        except Exception:
            pass
    return summary


def sync_tsd_positions_to_supabase(
    ib=None,
    *,
    mark_fn: Callable | None = None,
    book: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Upsert open TSD legs + pool snapshot to Supabase; prune stale OPEN rows.
    Returns summary dict with upserted count and verify errors.

    If ``book`` is passed, use it (caller just mutated in-memory state).
    Otherwise load from disk.
    """
    from supabase_sync import SupabaseSync

    summary: dict[str, Any] = {
        "upserted": 0,
        "closed_upserted": 0,
        "pruned": 0,
        "pool_synced": False,
        "watchlist_synced": 0,
        "watch_queue_synced": 0,
        "verify_errors": [],
    }

    book = book if book is not None else load_state()
    rows = flatten_open_legs(book)
    closed_rows = flatten_closed_legs(book)
    if ib is not None and mark_fn is not None and rows:
        apply_tws_marks(rows, ib, mark_fn)

    try:
        sync = SupabaseSync()
        _assert_tsd_tables(sync)
    except RuntimeError as exc:
        print(f"  {exc}")
        summary["verify_errors"].append(str(exc))
        return summary
    except Exception as exc:
        print(f"  TSD Supabase sync skipped: {exc}")
        summary["verify_errors"].append(f"sync_init:{exc}")
        return summary

    open_keys: list[tuple[str, str]] = []
    for row in rows:
        key = (str(row["symbol"]), str(row["leg_opened_at"]))
        open_keys.append(key)
        try:
            sync.upsert_tsd_position(row)
            summary["upserted"] += 1
            print(
                f"  TSD upsert {row['symbol']} "
                f"px={row.get('current_price')} "
                f"kill={row.get('kill_price')} "
                f"shares={row.get('shares')}"
            )
        except Exception as exc:
            msg = f"tsd_upsert:{row.get('symbol')}:{exc}"
            summary["verify_errors"].append(msg)
            print(f"  *** TSD SYNC FAILED {row.get('symbol')}: {exc} ***")

    for row in closed_rows:
        try:
            sync.upsert_tsd_closed_leg(row)
            summary["closed_upserted"] += 1
            print(
                f"  TSD closed {row['symbol']} "
                f"exit={row.get('exit_price')} "
                f"pnl=${row.get('pnl_dollars'):+.2f} "
                f"reason={row.get('exit_reason')}"
            )
        except Exception as exc:
            msg = f"tsd_closed_upsert:{row.get('symbol')}:{exc}"
            summary["verify_errors"].append(msg)
            print(f"  *** TSD CLOSED SYNC FAILED {row.get('symbol')}: {exc} ***")

    try:
        summary["pruned"] = sync.prune_stale_tsd_positions(open_keys)
    except Exception as exc:
        summary["verify_errors"].append(f"tsd_prune:{exc}")

    try:
        pool_snap = _pool_snapshot_from_local(book)
        sync.upsert_tsd_pool_snapshot(pool_snap)
        summary["pool_synced"] = True
        print(
            f"  >>> TSD pool cash=${pool_snap['pool']:.2f} "
            f"deployed=${pool_snap['deployed']:.2f} "
            f"opens={pool_snap['open_positions']} "
            f"names={pool_snap['open_names']}"
        )
    except Exception as exc:
        summary["verify_errors"].append(f"tsd_pool:{exc}")

    try:
        open_set = set(open_symbols(book))
        summary["watchlist_synced"] = sync_tsd_watchlist_from_file(
            open_symbols_set=open_set,
        )
    except Exception as exc:
        summary["verify_errors"].append(f"tsd_watchlist:{exc}")

    try:
        summary["watch_queue_synced"] = sync_tsd_watch_queue_from_file()
        print(f"  watch_queue_synced={summary['watch_queue_synced']}")
    except Exception as exc:
        err = str(exc)
        if "PGRST205" in err or "tsd_watch_queue" in err:
            msg = (
                "*** TSD watch_queue table MISSING (PGRST205) — "
                "run candidates/sql/tsd_cloud.sql; board uses tsd_watchlist mirror ***"
            )
            print(f"  {msg}")
            summary["watch_queue_missing"] = True
            # One Telegram per process run (not every trail tick).
            if not getattr(sync_tsd_positions_to_supabase, "_queue_tg_sent", False):
                try:
                    from tsd_scan_pipeline.tsd_notify import notify_tsd

                    notify_tsd(
                        "Peak Hour: tsd_watch_queue table missing in Supabase\n"
                        "Board mirrored to tsd_watchlist; run tsd_cloud.sql"
                    )
                    sync_tsd_positions_to_supabase._queue_tg_sent = True  # type: ignore[attr-defined]
                except Exception:
                    pass
        else:
            summary["verify_errors"].append(f"tsd_watch_queue:{exc}")
            print(f"  *** watch_queue sync FAILED: {exc} ***")

    verify_errors = _verify_tsd_supabase_rows(rows)
    summary["verify_errors"].extend(verify_errors)

    try:
        sync.log_health(
            "tsd_sync",
            "OK" if not summary["verify_errors"] else "WARN",
            f"upserted={summary['upserted']} closed={summary['closed_upserted']} "
            f"pruned={summary['pruned']} "
            f"errors={len(summary['verify_errors'])}",
        )
    except Exception:
        pass

    return summary


def _verify_tsd_supabase_rows(local_rows: list[dict[str, Any]]) -> list[str]:
    """Confirm Cloud px matches local/TWS after upsert."""
    errors: list[str] = []
    try:
        from supabase_sync import SupabaseSync

        sync = SupabaseSync()
        print("\n  TSD Supabase verify:")
        if not local_rows:
            print("    (no open TSD legs locally)")
            return errors
        for row in local_rows:
            symbol = str(row.get("symbol") or "").upper()
            leg_opened_at = str(row.get("leg_opened_at") or "")
            result = (
                sync.client.table("tsd_positions")
                .select(
                    "symbol,status,shares,current_price,kill_price,last_updated"
                )
                .eq("symbol", symbol)
                .eq("leg_opened_at", leg_opened_at)
                .limit(1)
                .execute()
            )
            cloud = (result.data or [{}])[0] if result.data else {}
            if not cloud:
                msg = f"tsd_verify_missing:{symbol}"
                errors.append(msg)
                print(f"    {symbol}: (no row)")
                continue
            local_px = _finite(row.get("current_price"))
            cloud_px = _finite(cloud.get("current_price"))
            mismatch = ""
            if (
                local_px is not None
                and cloud_px is not None
                and abs(local_px - cloud_px) > MARK_PX_TOL
            ):
                msg = (
                    f"tsd_mark_mismatch:{symbol} "
                    f"cloud={cloud_px} local={local_px:.2f}"
                )
                errors.append(msg)
                mismatch = " *** MISMATCH ***"
            print(
                f"    {symbol}: status={cloud.get('status')} "
                f"shares={cloud.get('shares')} "
                f"px={cloud.get('current_price')} "
                f"kill={cloud.get('kill_price')}"
                f"{mismatch}"
            )
    except Exception as exc:
        errors.append(f"tsd_verify:{exc}")
        print(f"  TSD Supabase verify warn: {exc}")
    return errors


def main() -> int:
    summary = sync_tsd_positions_to_supabase()
    print(
        f"TSD sync done upserted={summary.get('upserted')} "
        f"closed={summary.get('closed_upserted')} "
        f"pruned={summary.get('pruned')} "
        f"pool={summary.get('pool_synced')} "
        f"errors={summary.get('verify_errors') or 'none'}"
    )
    return 1 if summary.get("verify_errors") else 0


if __name__ == "__main__":
    sys.exit(main())
