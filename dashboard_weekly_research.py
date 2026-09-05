"""
Dashboard Weekly Review — on-the-go results from Supabase (no local funnel / no process detail).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import pytz
import streamlit as st

from dashboard_theme import section_header

ET = pytz.timezone("America/New_York")


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        v = float(x)
        return v if v == v else default  # NaN check
    except (TypeError, ValueError):
        return default


def _parse_et_date(val: Any) -> datetime | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    try:
        cleaned = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned[:26] if "T" in cleaned else cleaned)
        if dt.tzinfo is None:
            dt = ET.localize(dt)
        return dt.astimezone(ET)
    except (ValueError, TypeError):
        try:
            return ET.localize(datetime.strptime(s[:10], "%Y-%m-%d"))
        except (ValueError, TypeError):
            return None


def _ran_up_pct(entry: float, peak: float) -> float | None:
    if entry <= 0 or peak <= 0:
        return None
    return (peak / entry - 1.0) * 100.0


def _in_window(dt: datetime | None, start: datetime) -> bool:
    return dt is not None and dt >= start


def tab_weekly_research(get_sync: Callable[..., Any]) -> None:
    """
    Mobile-friendly weekly scoreboard from cloud book data.

    Shows results only — never scores, gates, hours, reject reasons, or research docs.
    """
    days = st.selectbox("Last", [7, 14, 30], index=0, format_func=lambda d: f"{d} days")
    now = datetime.now(ET)
    start = now - timedelta(days=int(days))

    closed: list[dict] = []
    opens: list[dict] = []
    pool: dict = {}
    err: str | None = None
    try:
        sync = get_sync()
        closed = list(sync.get_tsd_closed_legs() or [])
        opens = list(sync.get_tsd_positions(status="OPEN") or [])
        pool = sync.get_latest_tsd_pool() or {}
    except Exception as exc:
        err = str(exc)

    if err:
        st.warning("Could not load book data.")
        st.caption(err)
        return

    # Closed in window (by close time, else entry date)
    closed_w: list[dict] = []
    for r in closed:
        dt = _parse_et_date(r.get("closed_at")) or _parse_et_date(r.get("entry_date"))
        if _in_window(dt, start):
            closed_w.append(r)

    wins = [r for r in closed_w if _safe_float(r.get("pnl_dollars")) > 0]
    losses = [r for r in closed_w if _safe_float(r.get("pnl_dollars")) <= 0]
    pnl_closed = sum(_safe_float(r.get("pnl_dollars")) for r in closed_w)
    pnl_open = sum(_safe_float(r.get("pnl_dollars")) for r in opens)
    wr = (len(wins) / len(closed_w)) if closed_w else None

    avg_win = (sum(_safe_float(r.get("pnl_dollars")) for r in wins) / len(wins)) if wins else None
    avg_loss = (sum(_safe_float(r.get("pnl_dollars")) for r in losses) / len(losses)) if losses else None

    # Best run-up among closed + open
    best_run: tuple[str, float] | None = None
    for r in list(closed_w) + list(opens):
        entry = _safe_float(r.get("entry_price"))
        peak = _safe_float(r.get("peak_high"), float("nan"))
        mfe = r.get("mfe_pct")
        if mfe is not None:
            pct = _safe_float(mfe)
        else:
            pct_opt = _ran_up_pct(entry, peak)
            pct = pct_opt if pct_opt is not None else float("-inf")
        if pct == float("-inf"):
            continue
        sym = str(r.get("symbol") or "").upper()
        if best_run is None or pct > best_run[1]:
            best_run = (sym, pct)

    cash = _safe_float(pool.get("pool"), 3000.0)
    starting = _safe_float(pool.get("starting_pool"), 3000.0)
    in_mkt = sum(
        _safe_float(r.get("current_price")) * int(_safe_float(r.get("shares")))
        for r in opens
    )
    equity = cash + in_mkt
    total_pnl = equity - starting

    with st.container(border=True):
        section_header("Week", f"Last {int(days)} days")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("P&L (closed)", f"${pnl_closed:+,.2f}")
        c2.metric("Wins", str(len(wins)))
        c3.metric("Losses", str(len(losses)))
        c4.metric("Win rate", f"{wr:.0%}" if wr is not None else "—")

        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Taken", str(len(closed_w) + len(opens)))
        d2.metric("Still open", str(len(opens)))
        d3.metric("Avg win", f"${avg_win:+,.2f}" if avg_win is not None else "—")
        d4.metric("Avg loss", f"${avg_loss:+,.2f}" if avg_loss is not None else "—")

        e1, e2, e3 = st.columns(3)
        e1.metric("Book equity", f"${equity:,.2f}")
        e2.metric("Book P&L", f"${total_pnl:+,.2f}")
        if best_run:
            e3.metric("Best run", f"{best_run[0]} {best_run[1]:+.1f}%")
        else:
            e3.metric("Best run", "—")
        if opens:
            st.caption(f"Open MTM {pnl_open:+,.2f}")

    rows: list[dict[str, str]] = []
    for r in opens:
        entry = _safe_float(r.get("entry_price"))
        peak = _safe_float(r.get("peak_high"), float("nan"))
        ran = _ran_up_pct(entry, peak)
        rows.append({
            "Symbol": str(r.get("symbol") or "").upper(),
            "Status": "Open",
            "P&L": f"${_safe_float(r.get('pnl_dollars')):+.2f}",
            "Ran up": f"{ran:+.1f}%" if ran is not None else "—",
        })
    for r in sorted(
        closed_w,
        key=lambda x: str(x.get("closed_at") or x.get("entry_date") or ""),
        reverse=True,
    ):
        entry = _safe_float(r.get("entry_price"))
        peak = _safe_float(r.get("peak_high"), float("nan"))
        ran = _ran_up_pct(entry, peak)
        if r.get("mfe_pct") is not None:
            ran = _safe_float(r.get("mfe_pct"))
        pnl = _safe_float(r.get("pnl_dollars"))
        rows.append({
            "Symbol": str(r.get("symbol") or "").upper(),
            "Status": "Win" if pnl > 0 else "Loss",
            "P&L": f"${pnl:+.2f}",
            "Ran up": f"{ran:+.1f}%" if ran is not None else "—",
        })

    with st.container(border=True):
        section_header("Trades", "")
        if not rows:
            st.info("No trades in this window yet.")
        else:
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
