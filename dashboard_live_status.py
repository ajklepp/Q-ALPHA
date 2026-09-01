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

from dashboard_theme import MUTED, NEGATIVE, POSITIVE, ACCENT, BG, BORDER, TEXT, regime_banner, section_header
from dashboard_tsd_helpers import (
    format_gate_summary,
    map_exit_layer,
    mfe_in_r,
    parse_tranche_json,
    progress_fraction,
    progress_milestones,
)

TSD_STARTING_POOL = 3000.0
TSD_MAX_FULL_SLOTS = 10
IBKR_3H_CLOSE_HOURS_ET = (1, 4, 5, 8, 11, 14, 17, 19, 22)
TWS_LAG = timedelta(hours=3, minutes=3)


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
    et = pytz.timezone("America/New_York")
    now_et = datetime.now(et)
    candidates: list[datetime] = []
    for day_offset in range(4):
        d = now_et.date() + timedelta(days=day_offset)
        if d.weekday() >= 5:
            continue
        for hour in IBKR_3H_CLOSE_HOURS_ET:
            close = et.localize(datetime.combine(d, dtime(hour, 0)))
            slot = close + TWS_LAG
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
    """UTS v2 open leg — 3-layer stops (kill / structure / trail)."""
    symbol = str(row.get("symbol") or "")
    entry_price = _safe_float(row.get("entry_price"), 0.0)
    kill_price = _safe_float(row.get("kill_price"), 0.0)
    structure_stop = _safe_float(row.get("structure_stop"), float("nan"))
    next_trail = _safe_float(row.get("next_trail_stop"), float("nan"))
    peak_high = _safe_float(row.get("peak_high"), float("nan"))
    current_price = _safe_float(row.get("current_price"), entry_price)
    shares = int(_safe_float(row.get("shares"), 0.0))
    pnl_dollars = _safe_float(row.get("pnl_dollars"), 0.0)
    pnl_pct_val = _safe_float(row.get("pnl_pct"), 0.0)
    phase = str(row.get("phase") or "")
    mfe_r = row.get("mfe_r")
    if mfe_r is None and entry_price > 0 and kill_price > 0:
        mfe_r = mfe_in_r(entry_price, peak_high, kill_price)

    badges: list[str] = []
    if phase:
        badges.append(phase)
    if row.get("rth_armed"):
        badges.append("RTH armed")
    if row.get("pre_catalyst"):
        badges.append("pre_catalyst")
    if row.get("breakeven_locked"):
        badges.append("BE locked")
    badge_line = " · ".join(badges) if badges else ""

    # Row 1: symbol, price, P&L, badges
    c1, c2, c3 = st.columns([2, 2, 3])
    with c1:
        st.markdown(f"**{symbol}**")
        if badge_line:
            st.caption(badge_line)
    with c2:
        st.metric("Price", f"${current_price:.2f}", f"{pnl_pct_val:+.1%}")
    with c3:
        mfe_label = f"{_safe_float(mfe_r, 0):.1f}R MFE" if mfe_r is not None else "—"
        st.metric("P&L", f"${pnl_dollars:+.2f}", f"{shares} sh · {mfe_label}")

    # Row 2: three stop metrics
    s1, s2, s3 = st.columns(3)
    with s1:
        if kill_price > 0 and current_price <= kill_price:
            k_delta = "AT / below kill"
        elif kill_price > 0:
            k_delta = f"{(current_price - kill_price) / current_price:.1%} above"
        else:
            k_delta = "—"
        st.metric("Kill", f"${kill_price:.2f}" if kill_price else "—", k_delta)
    with s2:
        if math.isfinite(structure_stop) and structure_stop > 0:
            reason = str(row.get("structure_stop_reason") or "")
            if current_price <= structure_stop:
                st_delta = "BREACHED"
            else:
                st_delta = f"{(current_price - structure_stop) / current_price:.1%} above"
            if reason:
                st_delta += f" ({reason})"
            st.metric("Structure", f"${structure_stop:.2f}", st_delta)
        else:
            st.metric("Structure", "—", "pending bootstrap")
    with s3:
        if math.isfinite(next_trail) and next_trail > 0:
            st.metric("Trail stop", f"${next_trail:.2f}", "nearest armed tranche")
        else:
            st.metric("Trail stop", "—", "no tranche armed")

    # Row 3: T1–T4 mini table
    tranches = parse_tranche_json(row.get("tranche_json"))
    if tranches:
        tbl = []
        for t in tranches:
            armed = "✓" if t.get("armed") else ("—" if not t.get("closed") else "closed")
            trig = t.get("trigger_price")
            tstop = t.get("trail_stop")
            tbl.append({
                "ID": t.get("id"),
                "Sh": t.get("shares"),
                "Trigger": f"${_safe_float(trig, 0):.2f}" if trig else "—",
                "Armed": armed,
                "Trail $": f"${_safe_float(tstop, 0):.2f}" if tstop else "—",
            })
        st.dataframe(pd.DataFrame(tbl), hide_index=True, use_container_width=True)

    # Progress: entry → structure → T1 → T2
    milestones = progress_milestones(row)
    if milestones:
        frac = progress_fraction(current_price, milestones)
        labels = " → ".join(f"{n} ${p:.2f}" for n, p in milestones)
        st.progress(frac, text=f"{labels} · now ${current_price:.2f}")
    hhmm = _updated_hhmm_et(row.get("last_updated"))
    bar_ts = str(row.get("last_bar_time") or "")[:19]
    cap = f"Updated: {hhmm} ET" if hhmm else "Updated: —"
    if bar_ts:
        cap += f" · 3H bar {bar_ts}"
    st.caption(cap)
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
    deployed = _safe_float(tsd_pool.get("deployed"), 0.0)
    starting = _safe_float(tsd_pool.get("starting_pool"), TSD_STARTING_POOL)
    full_slots = int(tsd_pool.get("open_positions") or 0)
    if not full_slots and tsd_rows:
        full_slots = len({r.get("symbol") for r in tsd_rows if not r.get("t4_only")})
    open_names = int(tsd_pool.get("open_names") or len({r.get("symbol") for r in tsd_rows}))

    mtm_notional = sum(
        _safe_float(r.get("current_price"), 0.0) * int(_safe_float(r.get("shares"), 0))
        for r in tsd_rows
    )
    mtm_total = cash + mtm_notional
    pnl_dollar = mtm_total - starting
    closed_stats = tsd_closed_stats(tsd_closed)

    with st.container(border=True):
        section_header("Session KPIs (TSD)", "3HR swing pool — separate from gap runoff")
        cap = f"TSD pool ${starting:,.0f} start · gap-agent residual book excluded from KPIs"
        if not gap_open_df.empty:
            gap_syms = ", ".join(
                sorted(str(t) for t in gap_open_df.get("ticker", pd.Series(dtype=str)))
            )
            cap += f" · Gap runoff: {len(gap_open_df)} open ({gap_syms})"
        st.caption(cap)
        col1, col2, col3, col4, col5, col6 = st.columns(6)
        with col1:
            st.metric("Pool", f"${cash + deployed:,.2f}", f"${cash:,.0f}/${deployed:,.0f}")
        with col2:
            st.metric("MTM P&L", f"${mtm_total:,.2f}", f"{pnl_dollar:+.0f}")
        with col3:
            st.metric("Total Trades", str(closed_stats["total"]), "closed legs")
        with col4:
            if closed_stats["total"]:
                wr = closed_stats["win_rate"] or 0.0
                st.metric(
                    "Win Rate",
                    f"{wr:.0%}",
                    f"{closed_stats['winners']}W / {closed_stats['losers']}L",
                )
            else:
                st.metric("Win Rate", "—", "0 closed legs")
        with col5:
            st.metric(
                "Full slots",
                f"{full_slots}/{TSD_MAX_FULL_SLOTS}",
                f"{TSD_MAX_FULL_SLOTS - full_slots} free",
            )
        with col6:
            st.metric("Open names", str(open_names), "legs in book")

    spy_regime = str(tsd_pool.get("spy_regime") or "UNKNOWN").upper()
    vix_regime = str(tsd_pool.get("vix_regime") or "NORMAL")
    sizing_pct = str(tsd_pool.get("sizing_pct") or "100%")
    if spy_regime == "UNKNOWN" and tsd_queue:
        spy_regime = str(tsd_queue[0].get("regime") or "UNKNOWN").upper()
    regime_banner(spy_regime, vix_regime, sizing_pct)
    st.caption(
        "Market context only — gap sizing unused while gap agent entries are disabled."
    )

    with st.container(border=True):
        section_header(
            "Entry Pipeline",
            "UTS v2 watch queue — LAUNCH + quality gates (news is context only)",
        )
        if tsd_err:
            st.caption(tsd_err)
        elif not tsd_queue:
            st.info("Queue empty — next `--live` scan")
        else:
            q_rows = []
            for r in tsd_queue:
                gates = r.get("gates") or r.get("quality_gates")
                q_rows.append({
                    "Symbol": str(r.get("symbol") or ""),
                    "Status": str(r.get("status") or ""),
                    "Launch": f"{_safe_float(r.get('launch_score_display') or r.get('launch_score'), 0):.0f}",
                    "Phase": r.get("phase") or "—",
                    "Cross": f"${_safe_float(r.get('cross_level'), 0):.2f}",
                    "Gates": format_gate_summary(gates),
                    "Tags": ", ".join(r.get("tags") or []) or "—",
                    "Added": str(r.get("added_at") or "")[:16],
                })
            st.dataframe(pd.DataFrame(q_rows), hide_index=True, use_container_width=True)

    with st.container(border=True):
        section_header(
            "Open Positions (TSD)",
            "3-layer: kill (catastrophe) · structure (thesis) · trail (profit)",
        )
        if tsd_err:
            st.error(tsd_err)
        elif not tsd_rows:
            st.info("No open TSD positions in Supabase.")
        else:
            for row in tsd_rows:
                _render_tsd_open_card(row)

    if not gap_open_df.empty:
        with st.container(border=True):
            section_header(
                "Gap runoff (legacy agent)",
                "Runoff only — new gap entries disabled",
            )
            st.caption("Residual gap-agent brackets; not in TSD pool KPIs.")
            for _, trade in gap_open_df.iterrows():
                _render_gap_open_card(trade, oneshot_polygon_mark_fn)

    with st.container(border=True):
        section_header(
            "TSD Watchlist",
            "Watch-10 from last scan — Launch score primary; Ext = extension risk",
        )
        if tsd_err:
            st.caption(tsd_err)
        elif not tsd_watch:
            st.info("No TSD watchlist in Supabase yet — runs after next TSD scan.")
        else:
            wl_rows = []
            for r in tsd_watch:
                analog_n = r.get("analog_count")
                analog_wr = r.get("analog_win_rate")
                prof_cell = "—"
                if analog_n is not None:
                    wr_s = f" / {float(analog_wr):.0f}%" if analog_wr is not None else ""
                    prof_cell = f"n={analog_n}{wr_s}"
                launch = r.get("launch_score")
                ext = r.get("scan_score")
                wl_rows.append({
                    "Rank": int(r.get("rank") or 0),
                    "Symbol": str(r.get("symbol") or ""),
                    "Launch": f"{_safe_float(launch, 0):.0f}" if launch is not None else "—",
                    "Ext": f"{_safe_float(ext, 0):.0f}" if ext is not None else "—",
                    "Phase": r.get("phase") or "—",
                    "WT gap": f"{_safe_float(r.get('wt_gap'), 0):.1f}",
                    "Early": "Y" if r.get("early_bull") else "—",
                    "Analogs": prof_cell,
                    "Signal": "BUY" if r.get("buy_signal") else "—",
                    "Status": r.get("status_label") or "Watching",
                    "Pre-cat": "Y" if r.get("pre_catalyst") else "—",
                })
            st.caption("Ext = scan_score — high values mean extended move (bad for new longs)")
            st.dataframe(pd.DataFrame(wl_rows), hide_index=True, use_container_width=True)

    st.caption(
        "Auto-refresh ~90s · TWS marks :10/:40 · TSD trail ~60s local · "
        "Modal skips IBKR_PAPER marks"
    )


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
        brand_block(live_et, subtitle="TSD 3HR Swing · Live Paper")
    with col2:
        st.metric("Version", f"v{system_version}")
    with col3:
        st.metric("Days Running", f"{days_running}d")
    with col4:
        st.metric("Next Scan", _next_tsd_scan_countdown())


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
        section_header("TSD Trade Log", "Closed 3HR swing legs")
        if err:
            st.caption(f"TSD closed legs unavailable: {err}")
        elif not closed:
            st.info("No closed TSD legs yet.")
        else:
            log = pd.DataFrame(closed)
            log["Date"] = log["entry_date"]
            log["Ticker"] = log["symbol"]
            log["Entry"] = log["entry_price"]
            log["Exit"] = log["exit_price"]
            log["P&L$"] = log["pnl_dollars"]
            log["P&L%"] = log["pnl_pct"]
            log["Layer"] = log.apply(
                lambda r: r.get("exit_layer") or map_exit_layer(r.get("exit_reason")),
                axis=1,
            )
            log["Reason"] = log["exit_reason"]
            if "launch_score" in log.columns:
                log["Launch"] = log["launch_score"]
            if "phase" in log.columns:
                log["Phase"] = log["phase"]
            cols = [
                c for c in [
                    "Date", "Ticker", "Entry", "Exit", "P&L$", "P&L%",
                    "Layer", "Launch", "Phase", "Reason",
                ]
                if c in log.columns
            ]
            show = log[cols]
            styled = show.style.map(style_pnl_fn, subset=["P&L$", "P&L%"])
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
        section_header("TSD Performance", "3HR swing book — closed legs + pool history")
        if err:
            st.caption(f"TSD performance unavailable: {err}")
            return

        stats = tsd_closed_stats(closed)
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Closed legs", stats["total"])
        s2.metric("Winners", stats["winners"])
        s3.metric("Losers", stats["losers"])
        if stats["total"]:
            s4.metric("Win Rate", f"{(stats['win_rate'] or 0):.0%}")
        else:
            s4.metric("Win Rate", "—")

    with st.container(border=True):
        section_header("TSD Equity Curve", "Pool cash + deployed over time")
        if pool_history:
            hist_df = pd.DataFrame(pool_history)
            dates = pd.to_datetime(hist_df["snapshot_date"])
            equity = hist_df["pool"].astype(float) + hist_df["deployed"].astype(float)
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=dates,
                y=equity,
                mode="lines",
                name="TSD MTM",
                line=dict(color=ACCENT, width=2.5),
                fill="tozeroy",
                fillcolor="rgba(45, 212, 191, 0.12)",
            ))
            fig.add_hline(
                y=TSD_STARTING_POOL,
                line_dash="dash",
                line_color=MUTED,
                annotation_text=f"Starting ${TSD_STARTING_POOL:,.0f}",
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
            st.info("No TSD pool history yet.")

    with st.container(border=True):
        section_header("TSD P&L Per Trade", "Closed legs only")
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
            st.info("No closed TSD legs for P&L chart.")

    with st.container(border=True):
        section_header("P&L by Exit Layer", "Kill vs structure vs trail")
        if closed:
            layer_df = pd.DataFrame(closed)
            layer_df["exit_layer"] = layer_df.apply(
                lambda r: r.get("exit_layer") or map_exit_layer(r.get("exit_reason")),
                axis=1,
            )
            by_layer = (
                layer_df.groupby("exit_layer", as_index=False)["pnl_dollars"]
                .sum()
                .sort_values("pnl_dollars")
            )
            fig3 = px.bar(
                by_layer,
                x="exit_layer",
                y="pnl_dollars",
                color="pnl_dollars",
                color_continuous_scale=[NEGATIVE, POSITIVE],
            )
            fig3.update_layout(
                template="plotly_dark",
                height=320,
                showlegend=False,
                plot_bgcolor=BG,
                paper_bgcolor=BG,
                font=dict(color=TEXT, family="Sora"),
            )
            st.plotly_chart(fig3, use_container_width=True)
        else:
            st.info("No closed legs for exit-layer breakdown.")
