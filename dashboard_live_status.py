"""
TSD-primary Live Status tab — imported by dashboard.py (lazy import avoids cycles).
"""
from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime, timedelta, time as dtime
from typing import Any

import pandas as pd
import pytz
import streamlit as st

from dashboard_theme import MUTED, NEGATIVE, POSITIVE, ACCENT, BG, BORDER, TEXT, section_header
from dashboard_tsd_helpers import (
    hold_time_display,
)

TSD_STARTING_POOL = 3000.0
# Peak Hour Performers scan slots (bar close + lag) — used for countdown only
PHP_LAUNCH_HOURS_ET = (5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15)
PHP_LAUNCH_LAG_MIN = 15
ET = pytz.timezone("America/New_York")

# Legacy 3H watchlist labels — ignore for Peak Hour launches board.
_LEGACY_WATCH_LABELS = {
    "watching", "profiler ok", "trade pick", "in book",
}


def _is_peak_hour_watchlist(rows: list[dict]) -> bool:
    """True if cloud watchlist looks like Peak Hour board (not stale 3H)."""
    if not rows:
        return False
    labels = {str(r.get("status_label") or "").strip().lower() for r in rows}
    php_ok = {"entered", "queued", "skip", "ranked", "take", "signal"}
    if labels & php_ok:
        return True
    if labels <= _LEGACY_WATCH_LABELS:
        return False
    # Hour reused in wt_gap as 7/11/12/13 is a Peak Hour signal
    hours = []
    for r in rows:
        try:
            hours.append(int(float(r.get("wt_gap"))))
        except (TypeError, ValueError):
            pass
    return bool(set(hours) & set(PHP_LAUNCH_HOURS_ET))


def _load_local_queue_rows() -> list[dict]:
    """Local tsd_watch_queue.json rows (dashboard fallback)."""
    try:
        import json
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parent
        cand = root / "candidates"
        if str(cand) not in sys.path:
            sys.path.insert(0, str(cand))
        from state_paths import state_path

        path = state_path("tsd_watch_queue.json")
        if not path.exists():
            return []
        doc = json.loads(path.read_text(encoding="utf-8"))
        return list(doc.get("queue") or [])
    except Exception:
        return []


def _load_local_open_board_rows() -> list[dict]:
    """Synthesize Peak Hour board rows from local open book legs."""
    try:
        import json
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parent
        cand = root / "candidates"
        if str(cand) not in sys.path:
            sys.path.insert(0, str(cand))
        from state_paths import state_path

        path = state_path("tsd_book_state.json")
        if not path.exists():
            return []
        doc = json.loads(path.read_text(encoding="utf-8"))
        out: list[dict] = []
        for i, pos in enumerate(doc.get("positions") or [], 1):
            if str(pos.get("status") or "").upper() != "OPEN":
                continue
            sym = str(pos.get("symbol") or "").upper()
            if not sym:
                continue
            for leg in pos.get("legs") or []:
                if str(leg.get("status") or "").upper() != "OPEN":
                    continue
                out.append({
                    "rank": i,
                    "symbol": sym,
                    "status": "ENTERED",
                    "status_label": "ENTERED",
                    "entry_price": leg.get("price") or leg.get("entry_price"),
                    "peak_high": leg.get("peak_high") or leg.get("high_water"),
                })
                break
        return out
    except Exception:
        return []


def _public_result_label(status: Any) -> str:
    """Map internal status to non-strategy public labels."""
    s = str(status or "").strip().upper()
    if s in ("ENTERED", "OPEN", "IN BOOK", "TAKE"):
        return "Taken"
    if s in ("SKIP", "SKIPPED", "REJECTED", "PASSED"):
        return "Skipped"
    if s in ("QUEUED", "WATCHING", "SIGNAL", "RANKED", "ADDED", "UPDATED"):
        return "Queued"
    return s.title() if s and s != "—" else "—"


def _ran_up_label(entry: float, peak: float) -> str:
    """How far price ran from entry (public MFE %), no R / stop jargon."""
    if entry <= 0 or not math.isfinite(peak) or peak <= 0:
        return "—"
    return f"{(peak / entry - 1.0) * 100.0:+.1f}%"


def _board_row_from_sources(
    *,
    rank: int,
    symbol: str,
    hour: Any,
    htf: Any,
    launch: Any,
    phase: Any,
    buy: Any,
    status: Any,
    entry: Any = None,
    peak: Any = None,
) -> dict[str, str | int]:
    # hour/htf/launch/phase/buy kept in signature for call-site compatibility;
    # intentionally omitted from the public board (no strategy leak).
    _ = (hour, htf, launch, phase, buy)
    return {
        "Symbol": str(symbol or "").upper(),
        "Result": _public_result_label(status),
        "Ran up": _ran_up_label(_safe_float(entry, 0.0), _safe_float(peak, float("nan"))),
    }


def _build_peak_hour_launch_board(
    tsd_queue: list[dict],
    tsd_rows: list[dict],
    tsd_watch: list[dict],
) -> tuple[list[dict], str | None]:
    """
    Build Peak Hour launches table rows.

    Preference: today's cloud queue + open positions → local queue/book → empty.
    Never prefer legacy 3H watchlist over queue/opens.
    Returns (rows, fallback_caption_or_None).
    """
    today = datetime.now(ET).strftime("%Y-%m-%d")

    def _is_today(row: dict) -> bool:
        for key in ("added_at", "scan_at", "updated_at", "last_updated", "opened_at"):
            v = str(row.get(key) or "")
            if v.startswith(today):
                return True
        # Open positions without date still count as live board
        if str(row.get("status") or "").upper() == "OPEN":
            return True
        if str(row.get("status_label") or "").upper() == "ENTERED":
            return True
        return False

    cloud_queue = [r for r in tsd_queue if _is_today(r)]
    # Do NOT fall back to stale prior-day queue — that masquerades as today's launches.
    cloud_opens = list(tsd_rows or [])
    use_watch = _is_peak_hour_watchlist(tsd_watch)

    merged: dict[str, dict] = {}
    source = "cloud"

    def _put(sym: str, row: dict) -> None:
        if not sym:
            return
        prev = merged.get(sym)
        if prev and str(prev.get("Result")) == "Taken":
            return
        merged[sym] = row

    # 1) Cloud opens + queue (+ Peak Hour–shaped watchlist only as supplement)
    if cloud_opens or cloud_queue or use_watch:
        for i, r in enumerate(cloud_opens, 1):
            sym = str(r.get("symbol") or "").upper()
            _put(sym, _board_row_from_sources(
                rank=i,
                symbol=sym,
                hour=None,
                htf=None,
                launch=None,
                phase=None,
                buy=True,
                status="ENTERED",
                entry=r.get("entry_price"),
                peak=r.get("peak_high"),
            ))
        for i, r in enumerate(cloud_queue, 1):
            sym = str(r.get("symbol") or "").upper()
            if sym in merged:
                continue
            _put(sym, _board_row_from_sources(
                rank=len(merged) + 1,
                symbol=sym,
                hour=None,
                htf=None,
                launch=None,
                phase=None,
                buy=None,
                status=r.get("status") or "QUEUED",
            ))
        if use_watch:
            for i, r in enumerate(tsd_watch, 1):
                sym = str(r.get("symbol") or "").upper()
                if sym in merged:
                    continue
                _put(sym, _board_row_from_sources(
                    rank=int(r.get("rank") or len(merged) + 1),
                    symbol=sym,
                    hour=None,
                    htf=None,
                    launch=None,
                    phase=None,
                    buy=None,
                    status=r.get("status_label") or r.get("status") or "—",
                ))
        if merged:
            rows = list(merged.values())
            return rows, None

    # 2) Local queue + local open book
    local_q = _load_local_queue_rows()
    local_o = _load_local_open_board_rows()
    for i, r in enumerate(local_o, 1):
        sym = str(r.get("symbol") or "").upper()
        _put(sym, _board_row_from_sources(
            rank=i,
            symbol=sym,
            hour=None,
            htf=None,
            launch=None,
            phase=None,
            buy=True,
            status="ENTERED",
            entry=r.get("entry_price"),
            peak=r.get("peak_high"),
        ))
    for r in local_q:
        sym = str(r.get("symbol") or "").upper()
        if sym in merged and str(merged[sym].get("Result")) == "Taken":
            continue
        st = str(r.get("status") or "QUEUED").upper()
        if st in ("WATCHING", "ADDED", "UPDATED", "CONFIRMED"):
            st = "QUEUED"
        if st == "SKIPPED":
            st = "SKIP"
        if sym in {str(x.get("symbol") or "").upper() for x in local_o}:
            st = "ENTERED"
        _put(sym, _board_row_from_sources(
            rank=len(merged) + 1,
            symbol=sym,
            hour=None,
            htf=None,
            launch=None,
            phase=None,
            buy=None,
            status=st,
        ))
    if merged:
        return list(merged.values()), "local fallback — Supabase lag"

    return [], None


def _safe_float(x, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        if isinstance(x, str) and not x.strip():
            return default
        v = float(x)
        if not math.isfinite(v):
            return default
        return v
    except (TypeError, ValueError):
        return default


def _updated_hhmm_et(updated) -> str | None:
    if not isinstance(updated, str):
        return None
    s = updated.strip()
    if not s:
        return None
    try:
        cleaned = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = pytz.UTC.localize(dt)
        et = pytz.timezone("America/New_York")
        return dt.astimezone(et).strftime("%H:%M")
    except (ValueError, TypeError):
        return None


def _next_tsd_scan_countdown() -> str:
    """Countdown to next Peak Hour Performers 1H launch tick (:15 after bar close)."""
    et = pytz.timezone("America/New_York")
    now_et = datetime.now(et)
    candidates: list[datetime] = []
    for day_offset in range(4):
        d = now_et.date() + timedelta(days=day_offset)
        if d.weekday() >= 5:
            continue
        for hour in PHP_LAUNCH_HOURS_ET:
            slot = et.localize(datetime.combine(d, dtime(hour, PHP_LAUNCH_LAG_MIN)))
            if slot > now_et:
                candidates.append(slot)
    if not candidates:
        return "—"
    nxt = min(candidates)
    diff = nxt - now_et
    hours, rem = divmod(int(diff.total_seconds()), 3600)
    mins = rem // 60
    return f"{hours}h {mins}m"


def tsd_closed_stats(closed_legs: list[dict]) -> dict[str, Any]:
    """Win/loss counts for TSD closed legs."""
    total = len(closed_legs)
    winners = sum(
        1 for r in closed_legs if _safe_float(r.get("pnl_dollars"), 0.0) > 0
    )
    losers = total - winners
    win_rate = winners / total if total else None
    return {
        "total": total,
        "winners": winners,
        "losers": losers,
        "win_rate": win_rate,
    }


def _render_gap_open_card(
    trade,
    oneshot_polygon_mark_fn: Callable[[str], float | None],
) -> None:
    """Gap-agent runoff position card (stop / 2R style)."""
    ticker = str(trade.get("ticker") or "")
    entry_price = _safe_float(trade.get("entry_price"), 0.0)
    stop_price = _safe_float(trade.get("stop_price"), 0.0)
    target_2r = _safe_float(trade.get("target_2r"), 0.0)
    shares = int(_safe_float(trade.get("shares_total"), 0.0))

    raw_mark = _safe_float(trade.get("current_price"), float("nan"))
    if not math.isfinite(raw_mark) or raw_mark <= 0:
        fetched = oneshot_polygon_mark_fn(ticker)
        if fetched is not None and fetched > 0:
            raw_mark = fetched
    current_price = (
        raw_mark if math.isfinite(raw_mark) and raw_mark > 0 else entry_price
    )

    pnl_dollars = _safe_float(trade.get("pnl_dollars"), float("nan"))
    pnl_pct_val = _safe_float(trade.get("pnl_pct"), float("nan"))
    r_mult = _safe_float(trade.get("r_multiple"), float("nan"))
    dist_stop = _safe_float(trade.get("dist_to_stop"), float("nan"))

    if (
        (not math.isfinite(pnl_dollars) or not math.isfinite(pnl_pct_val))
        and entry_price > 0
        and current_price > 0
    ):
        pnl_per = current_price - entry_price
        if not math.isfinite(pnl_dollars) and shares > 0:
            pnl_dollars = pnl_per * shares
        if not math.isfinite(pnl_pct_val):
            pnl_pct_val = pnl_per / entry_price
        risk = entry_price - stop_price
        if not math.isfinite(r_mult) and risk > 0:
            r_mult = pnl_per / risk
        if not math.isfinite(dist_stop) and current_price > 0:
            dist_stop = (current_price - stop_price) / current_price

    pnl_dollars = _safe_float(pnl_dollars, 0.0)
    pnl_pct_val = _safe_float(pnl_pct_val, 0.0)
    r_mult = _safe_float(r_mult, 0.0)
    dist_stop = _safe_float(dist_stop, 0.0)

    col1, col2, col3, col4, col5 = st.columns([1.5, 1.5, 1.5, 1.5, 2])
    with col1:
        st.metric(ticker, f"${current_price:.2f}", f"{pnl_pct_val:+.1%}")
    with col2:
        st.metric("P&L", f"${pnl_dollars:+.2f}", f"{r_mult:+.2f}R")
    with col3:
        if math.isfinite(stop_price) and stop_price > 0 and current_price <= stop_price:
            stop_delta = "HIT / through stop"
        else:
            stop_color = "🔴" if dist_stop < 0.02 else "🟡" if dist_stop < 0.05 else "🟢"
            stop_delta = f"{stop_color} {dist_stop:.1%} away"
        st.metric("Stop", f"${stop_price:.2f}", stop_delta)
    with col4:
        to_go = (target_2r - current_price) / current_price if current_price > 0 else 0.0
        if not math.isfinite(to_go):
            to_go = 0.0
        if math.isfinite(target_2r) and target_2r > 0 and current_price >= target_2r:
            target_delta = "HIT / past"
        else:
            target_delta = f"{to_go:.1%} to go"
        st.metric("Target", f"${target_2r:.2f}", target_delta)
    with col5:
        price_ok = (
            math.isfinite(current_price) and current_price > 0
            and math.isfinite(stop_price) and math.isfinite(target_2r)
            and target_2r > stop_price
        )
        if price_ok:
            if current_price < stop_price:
                progress, bar_label = 0.0, f"below stop · ${current_price:.2f}"
            elif current_price > target_2r:
                progress, bar_label = 1.0, f"past target · ${current_price:.2f}"
            else:
                progress = (current_price - stop_price) / (target_2r - stop_price)
                progress = max(0.0, min(1.0, progress)) if math.isfinite(progress) else 0.0
                bar_label = (
                    f"Stop ${stop_price:.2f} ──── ${current_price:.2f} "
                    f"──── Target ${target_2r:.2f}"
                )
            st.progress(progress, text=bar_label)
        hhmm = _updated_hhmm_et(trade.get("last_updated"))
        st.caption(f"Updated: {hhmm} ET" if hhmm else "Updated: —")
    st.divider()


def _render_tsd_open_card(row: dict) -> None:
    """Public open-leg card — price / P&L / ran-up only (no stop-layer detail)."""
    symbol = str(row.get("symbol") or "")
    entry_price = _safe_float(row.get("entry_price"), 0.0)
    peak_high = _safe_float(row.get("peak_high"), float("nan"))
    current_price = _safe_float(row.get("current_price"), entry_price)
    shares = int(_safe_float(row.get("shares"), 0.0))
    pnl_dollars = _safe_float(row.get("pnl_dollars"), 0.0)
    pnl_pct_val = _safe_float(row.get("pnl_pct"), 0.0)
    ran = _ran_up_label(entry_price, peak_high)

    c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
    with c1:
        st.markdown(f"**{symbol}**")
        st.caption(f"{shares} sh · entry ${entry_price:.2f}" if entry_price else f"{shares} sh")
    with c2:
        st.metric("Price", f"${current_price:.2f}", f"{pnl_pct_val:+.1%}")
    with c3:
        st.metric("P&L", f"${pnl_dollars:+.2f}")
    with c4:
        st.metric("Ran up", ran)

    hhmm = _updated_hhmm_et(row.get("last_updated"))
    st.caption(f"Updated: {hhmm} ET" if hhmm else "Updated: —")
    st.divider()


def render_live_status_tab(
    trades: list,
    pool_history: list,
    get_sync: Callable[..., Any],
    trades_df_fn: Callable[[list], pd.DataFrame],
    open_positions_df_fn: Callable[[pd.DataFrame], pd.DataFrame],
    oneshot_polygon_mark_fn: Callable[[str], float | None],
) -> None:
    """TSD-primary Live Status (gap runoff demoted)."""
    df = trades_df_fn(trades)
    gap_open_df = open_positions_df_fn(df)

    tsd_err: str | None = None
    tsd_rows: list[dict] = []
    tsd_closed: list[dict] = []
    tsd_pool: dict = {}
    tsd_watch: list[dict] = []
    tsd_queue: list[dict] = []
    try:
        sync = get_sync()
        tsd_rows = sync.get_tsd_positions(status="OPEN")
        tsd_closed = sync.get_tsd_closed_legs()
        tsd_pool = sync.get_latest_tsd_pool() or {}
        tsd_watch = sync.get_tsd_watchlist()
        tsd_queue = sync.get_tsd_watch_queue()
    except Exception as exc:
        tsd_err = str(exc)
        if "tsd_positions" in tsd_err or "PGRST205" in tsd_err:
            tsd_err = (
                "TSD cloud tables missing — run candidates/sql/tsd_cloud.sql "
                "in Supabase, then tws_intraday_sync --repair"
            )

    cash = _safe_float(tsd_pool.get("pool"), TSD_STARTING_POOL)
    starting = _safe_float(tsd_pool.get("starting_pool"), TSD_STARTING_POOL)
    open_names = int(tsd_pool.get("open_names") or len({r.get("symbol") for r in tsd_rows}))

    in_market_mtm = sum(
        _safe_float(r.get("current_price"), 0.0) * int(_safe_float(r.get("shares"), 0))
        for r in tsd_rows
    )
    total_equity = cash + in_market_mtm
    realized_pnl = sum(_safe_float(r.get("pnl_dollars"), 0.0) for r in tsd_closed)
    unrealized_pnl = sum(_safe_float(r.get("pnl_dollars"), 0.0) for r in tsd_rows)
    total_pnl = total_equity - starting
    total_pnl_pct = (total_pnl / starting * 100.0) if starting > 0 else 0.0
    closed_stats = tsd_closed_stats(tsd_closed)

    with st.container(border=True):
        section_header("Scoreboard", "")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Equity", f"${total_equity:,.2f}")
        c2.metric(
            "P&L",
            f"${total_pnl:+,.2f}",
            f"{total_pnl_pct:+.1f}%",
        )
        c3.metric("Wins", str(closed_stats["winners"]))
        c4.metric("Losses", str(closed_stats["losers"]))

        d1, d2, d3 = st.columns(3)
        with d1:
            if closed_stats["total"] and closed_stats["win_rate"] is not None:
                st.metric("Win rate", f"{closed_stats['win_rate']:.0%}")
            else:
                st.metric("Win rate", "—")
        with d2:
            st.metric("Open", str(open_names))
        with d3:
            st.metric("Cash", f"${cash:,.2f}")
        st.caption(
            f"Unrealized ${unrealized_pnl:+,.2f} · realized ${realized_pnl:+,.2f}"
        )

    with st.container(border=True):
        section_header("Today", "")
        board_rows, fallback_cap = _build_peak_hour_launch_board(
            tsd_queue, tsd_rows, tsd_watch,
        )
        if tsd_err and not board_rows:
            st.caption(tsd_err)
        elif not board_rows:
            st.info("Nothing yet today.")
        else:
            if fallback_cap:
                st.caption(fallback_cap)
            st.dataframe(pd.DataFrame(board_rows), hide_index=True, use_container_width=True)

    with st.container(border=True):
        section_header("Open", "")
        open_rows = list(tsd_rows)
        open_fallback = False
        if not open_rows:
            try:
                import json
                import sys
                from pathlib import Path

                root = Path(__file__).resolve().parent
                cand = root / "candidates"
                if str(cand) not in sys.path:
                    sys.path.insert(0, str(cand))
                from state_paths import state_path
                from tsd_supabase_sync import flatten_open_legs

                book = json.loads(state_path("tsd_book_state.json").read_text(encoding="utf-8"))
                open_rows = flatten_open_legs(book)
                open_fallback = bool(open_rows)
            except Exception:
                open_rows = []
        if tsd_err and not open_rows:
            st.error(tsd_err)
        elif not open_rows:
            st.info("No open positions.")
        else:
            if open_fallback:
                st.caption("local fallback — Supabase lag")
            for row in open_rows:
                _render_tsd_open_card(row)

    if not gap_open_df.empty:
        with st.container(border=True):
            section_header("Legacy runoff", "Closing only")
            for _, trade in gap_open_df.iterrows():
                _render_gap_open_card(trade, oneshot_polygon_mark_fn)

    st.caption("Auto-refresh ~90s")


def render_live_header(
    get_sync: Callable[..., Any],
    system_version: str,
    days_running: int,
) -> None:
    """TSD-primary header row."""
    from dashboard_theme import brand_block

    col1, col2, col3, col4 = st.columns([3, 1, 1, 1])
    live_et = ""
    try:
        sync = get_sync()
        for component in ("tws_sync", "tsd_sync", "intraday_monitor"):
            row = sync.get_last_health(component)
            if row and row.get("last_run"):
                live_et = _updated_hhmm_et(row["last_run"]) or ""
                if live_et:
                    break
        if not live_et and sync.get_tsd_positions(status="OPEN"):
            rows = sync.get_tsd_positions(status="OPEN")
            if rows:
                live_et = _updated_hhmm_et(rows[0].get("last_updated")) or ""
    except Exception:
        pass
    with col1:
        brand_block(live_et, subtitle="Live Paper")
    with col2:
        st.metric("Version", f"v{system_version}")
    with col3:
        st.metric("Days Running", f"{days_running}d")
    with col4:
        st.metric("Next update", _next_tsd_scan_countdown())


def render_tsd_trade_log(
    get_sync: Callable[..., Any],
    style_pnl_fn: Callable,
) -> None:
    """TSD closed legs table for Trade Log tab."""
    closed: list[dict] = []
    err: str | None = None
    try:
        closed = get_sync().get_tsd_closed_legs()
    except Exception as exc:
        err = str(exc)

    with st.container(border=True):
        section_header("Trade Log", "")
        if err:
            st.caption(f"Closed legs unavailable: {err}")
        elif not closed:
            st.info("No closed trades yet.")
        else:
            log = pd.DataFrame(closed)
            log["Ticker"] = log["symbol"]
            log["Entry"] = log["entry_price"].apply(
                lambda x: f"${_safe_float(x, 0):.2f}" if x is not None else "—"
            )
            log["Exit"] = log["exit_price"].apply(
                lambda x: f"${_safe_float(x, 0):.2f}" if x is not None else "—"
            )
            log["P&L%"] = log["pnl_pct"].apply(
                lambda x: f"{_safe_float(x, 0):+.1%}" if x is not None else "—"
            )
            log["Result"] = log["pnl_dollars"].apply(
                lambda x: "Win" if _safe_float(x, 0) > 0 else "Loss"
            )
            log["Hold"] = log.apply(
                lambda r: hold_time_display(
                    r.get("leg_opened_at"), r.get("closed_at"),
                ),
                axis=1,
            )
            cols = ["Ticker", "Entry", "Exit", "P&L%", "Result", "Hold"]
            show = log[[c for c in cols if c in log.columns]]
            styled = show.style.map(style_pnl_fn, subset=["P&L%"])
            st.dataframe(styled, use_container_width=True, hide_index=True)


def render_tsd_performance(get_sync: Callable[..., Any]) -> None:
    """TSD Performance section for Performance tab."""
    import plotly.express as px
    import plotly.graph_objects as go

    closed: list[dict] = []
    pool_history: list[dict] = []
    err: str | None = None
    try:
        sync = get_sync()
        closed = sync.get_tsd_closed_legs()
        pool_history = sync.get_tsd_pool_history()
    except Exception as exc:
        err = str(exc)

    with st.container(border=True):
        section_header("Performance", "")
        if err:
            st.caption(f"Performance unavailable: {err}")
            return

        stats = tsd_closed_stats(closed)
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Closed", stats["total"])
        s2.metric("Wins", stats["winners"])
        s3.metric("Losses", stats["losers"])
        if stats["total"]:
            s4.metric("Win rate", f"{(stats['win_rate'] or 0):.0%}")
        else:
            s4.metric("Win rate", "—")

    with st.container(border=True):
        section_header("Equity", "")
        if pool_history:
            hist_df = pd.DataFrame(pool_history)
            dates = pd.to_datetime(hist_df["snapshot_date"])
            equity = hist_df["pool"].astype(float) + hist_df["deployed"].astype(float)
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=dates,
                y=equity,
                mode="lines",
                name="Equity",
                line=dict(color=ACCENT, width=2.5),
                fill="tozeroy",
                fillcolor="rgba(45, 212, 191, 0.12)",
            ))
            fig.add_hline(
                y=TSD_STARTING_POOL,
                line_dash="dash",
                line_color=MUTED,
                annotation_text=f"Start ${TSD_STARTING_POOL:,.0f}",
            )
            fig.update_layout(
                plot_bgcolor=BG,
                paper_bgcolor=BG,
                font=dict(color=TEXT, family="Sora"),
                yaxis=dict(gridcolor=BORDER),
                xaxis=dict(gridcolor=BORDER),
                height=400,
                margin=dict(l=40, r=20, t=30, b=40),
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No equity history yet.")

    with st.container(border=True):
        section_header("P&L per trade", "")
        if closed:
            plot_df = pd.DataFrame(closed)
            plot_df["label"] = (
                plot_df["entry_date"].astype(str) + " " + plot_df["symbol"].astype(str)
            )
            fig2 = px.bar(
                plot_df,
                x="label",
                y="pnl_dollars",
                color="pnl_dollars",
                color_continuous_scale=[NEGATIVE, POSITIVE],
            )
            fig2.update_layout(
                template="plotly_dark",
                height=400,
                showlegend=False,
                plot_bgcolor=BG,
                paper_bgcolor=BG,
                font=dict(color=TEXT, family="Sora"),
            )
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("No closed trades for P&L chart.")