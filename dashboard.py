# =============================================================================
# Q-ALPHA STREAMLIT DASHBOARD
# =============================================================================
# LOCAL LAUNCH (DETACHED — never block Cursor / agents):
#   .\start_dashboard.ps1          # returns immediately; URL http://localhost:8501
#   .\stop_dashboard.ps1           # kills whatever owns port 8501
#
# Do NOT run `streamlit run dashboard.py` as a foreground command you wait on —
# Streamlit never exits and freezes the caller. Always use start_dashboard.ps1.
#
# DEPLOY TO STREAMLIT COMMUNITY CLOUD:
# 1. Push all changes to GitHub:
#    git add .
#    git commit -m "Dashboard upgrade — rich visuals + scan details"
#    git push
#
# 2. Go to: https://share.streamlit.io
# 3. Sign in with GitHub
# 4. Click "New app"
# 5. Repository: ajklepp/Q-ALPHA
# 6. Branch: main
# 7. Main file path: dashboard.py
# 8. Click Deploy
#
# 9. Add secrets in Streamlit Cloud:
#    App Settings → Secrets → paste:
#    SUPABASE_URL = "your_url"
#    SUPABASE_SECRET_KEY = "your_key"
#
# 10. URL will be: https://qalpha.streamlit.app
#     (or similar — customize in app settings)
# =============================================================================
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, time as dtime
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pytz
import streamlit as st
from streamlit_autorefresh import st_autorefresh

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from candidates.supabase_sync import SupabaseSync

STARTING_POOL = 3000.0
OPEN_STATUSES = {"OPEN", "T1_HIT", "T2_HIT", "T3_TRAIL", "PENDING_MOC"}
MAX_SLOTS = 10
SYSTEM_VERSION = "1.0.0"
SYSTEM_START_DATE = "2026-08-17"
SYSTEM_START = datetime.strptime(SYSTEM_START_DATE, "%Y-%m-%d").date()
DAYS_RUNNING = (datetime.now().date() - SYSTEM_START).days

st.set_page_config(
    page_title="Q-ALPHA Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st_autorefresh(interval=5 * 60 * 1000, key="main_refresh")


@st.cache_resource
def get_sync() -> SupabaseSync:
    return SupabaseSync()


def _safe_load() -> tuple[list, list, list, list]:
    try:
        sync = get_sync()
        return (
            sync.get_all_trades(),
            sync.get_pool_history(),
            sync.get_recent_scans(30),
            sync.get_system_health(),
        )
    except Exception as exc:
        st.error(f"Could not connect to Supabase: {exc}")
        return [], [], [], []


def _trades_df(trades: list) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame()
    df = pd.DataFrame(trades)
    for col in (
        "entry_price", "stop_price", "target_2r", "pnl_dollars", "pnl_pct",
        "current_price", "r_multiple", "dist_to_stop", "dist_to_target",
    ):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _latest_scan(scans: list) -> dict | None:
    return scans[0] if scans else None


def _parse_ts(ts_str: str) -> datetime:
    cleaned = ts_str.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return datetime.fromisoformat(cleaned.split("+")[0])


def get_time_ago(ts_str: str) -> str:
    """Human-readable time since timestamp."""
    dt = _parse_ts(ts_str)
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    delta = now - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def get_last_scan_time(health: list) -> str | None:
    """Return formatted last morning scan time from system_health."""
    for row in health:
        if row.get("component") == "morning_scan":
            ts = row.get("last_run") or row.get("created_at")
            if ts:
                dt = _parse_ts(ts)
                return dt.strftime("%Y-%m-%d %H:%M")
    return None


def get_last_health(component: str, health: list) -> dict | None:
    """Return most recent health row for a component."""
    for row in health:
        if row.get("component") == component:
            return row
    return None


def _next_scan_countdown() -> str:
    """Countdown to next 8:30 AM ET scan."""
    et = pytz.timezone("America/New_York")
    now_et = datetime.now(et)
    next_scan = now_et.replace(hour=8, minute=30, second=0, microsecond=0)
    if now_et >= next_scan:
        next_scan = next_scan + timedelta(days=1)
    diff = next_scan - now_et
    hours, rem = divmod(int(diff.total_seconds()), 3600)
    mins = rem // 60
    return f"{hours}h {mins}m"


def _style_pnl(val):
    if pd.isna(val):
        return ""
    color = "#00FF88" if val >= 0 else "#FF4444"
    return f"color: {color}; font-weight: bold"


def _color_gap(val: str) -> str:
    try:
        pct = float(str(val).replace("%", "").replace("+", "").strip())
        if pct > 0:
            return "color: #00FF88; font-weight: bold"
        if pct < 0:
            return "color: #FF4444; font-weight: bold"
        return "color: #AAAAAA"
    except Exception:
        return ""


def _candidate_price(ticker: str, latest_scan: dict | None, fallback: float) -> float:
    if not latest_scan:
        return fallback
    for c in json.loads(latest_scan.get("candidates_json") or "[]"):
        if c.get("ticker") == ticker:
            return float(c.get("premarket_price") or fallback)
    return fallback


def render_header() -> None:
    col1, col2, col3, col4 = st.columns([3, 1, 1, 1])
    with col1:
        st.markdown("# 📈 Q-ALPHA Dashboard")
        st.caption("Quantitative Momentum Trading System")
        try:
            last_intraday = get_sync().get_last_health("intraday_monitor")
            if last_intraday and last_intraday.get("last_run"):
                ts = last_intraday["last_run"]
                st.caption(
                    f"📡 Live data updated: {ts[11:16]} ET "
                    f"(every 30 min during market hours)"
                )
        except Exception:
            pass
    with col2:
        st.metric("Version", f"v{SYSTEM_VERSION}")
    with col3:
        st.metric("Days Running", f"{DAYS_RUNNING}d")
    with col4:
        st.metric("Next Scan", _next_scan_countdown())


def tab_live_status(trades: list, scans: list, pool_history: list) -> None:
    df = _trades_df(trades)
    latest_scan = _latest_scan(scans)

    pool = STARTING_POOL
    if pool_history:
        pool = float(pool_history[-1].get("pool", STARTING_POOL))

    pnl_dollar = pool - STARTING_POOL
    pnl_pct = (pnl_dollar / STARTING_POOL) * 100 if STARTING_POOL else 0
    open_df = df[df["status"].isin(OPEN_STATUSES)] if not df.empty else pd.DataFrame()
    open_pos = len(open_df)
    t3_count = len(df[df["status"] == "T3_TRAIL"]) if not df.empty else 0

    closed = df[df["status"] == "CLOSED"] if not df.empty else pd.DataFrame()
    total_trades = len(closed)
    winning_trades = len(closed[closed["pnl_dollars"] > 0]) if not closed.empty else 0
    losing_trades = total_trades - winning_trades
    win_rate = winning_trades / total_trades if total_trades else 0

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric(
            "Pool Value",
            f"${pool:,.2f}",
            f"{pnl_dollar:+.2f} ({pnl_pct:+.1f}%)",
            delta_color="normal",
        )
    with col2:
        st.metric(
            "Open Positions",
            f"{open_pos}/{MAX_SLOTS} slots",
            f"{MAX_SLOTS - open_pos} available",
        )
    with col3:
        st.metric("T3 Trailing", f"{t3_count} free-running", "slots released")
    with col4:
        st.metric(
            "Total Trades",
            str(total_trades),
            f"{winning_trades}W / {losing_trades}L",
        )
    with col5:
        st.metric("Win Rate", f"{win_rate:.0%}", "Base rate: ~39%")

    spy_regime = latest_scan.get("spy_regime", "UNKNOWN") if latest_scan else "UNKNOWN"
    vix_regime = latest_scan.get("vix_regime", "NORMAL") if latest_scan else "NORMAL"
    spy_price = float(latest_scan.get("spy_price", 0) or 0) if latest_scan else 0
    spy_sma50 = float(latest_scan.get("spy_sma50") or 0) if latest_scan else 0
    if not spy_sma50 and spy_price:
        spy_sma50 = spy_price * 0.97

    regime_color = "#00AA44" if spy_regime == "BULL" else "#CC2200"
    regime_emoji = "🐂" if spy_regime == "BULL" else "🐻"
    vix_color = "#FFaa00" if vix_regime == "ELEVATED" else "#00AA44"
    sizing_pct = "100%" if vix_regime == "NORMAL" else "50%"

    st.markdown(
        f"""
<div style="
    background: {regime_color}22;
    border: 2px solid {regime_color};
    border-radius: 8px;
    padding: 12px 24px;
    display: flex;
    justify-content: space-between;
    align-items: center;
">
    <div style="font-size: 24px; font-weight: bold; color: {regime_color};">
        {regime_emoji} {spy_regime} MARKET
    </div>
    <div style="color: #AAAAAA; font-size: 14px;">
        SPY: <b style="color: white;">${spy_price:.2f}</b> &nbsp;|&nbsp;
        SMA50: <b style="color: white;">${spy_sma50:.2f}</b> &nbsp;|&nbsp;
        VIX: <b style="color: {vix_color};">{vix_regime}</b> &nbsp;|&nbsp;
        Position sizing: <b style="color: white;">{sizing_pct}</b>
    </div>
</div>
""",
        unsafe_allow_html=True,
    )

    st.subheader("Open Positions")
    if open_df.empty:
        st.info("No open positions.")
    else:
        for _, trade in open_df.iterrows():
            ticker = trade["ticker"]
            entry_price = float(trade.get("entry_price") or 0)
            stop_price = float(trade.get("stop_price") or 0)
            target_2r = float(trade.get("target_2r") or 0)
            pnl_dollars = float(trade.get("pnl_dollars") or 0)
            pnl_pct_val = float(trade.get("pnl_pct") or 0)
            current_price = float(
                trade.get("current_price")
                or _candidate_price(ticker, latest_scan, entry_price)
            )
            r_mult = float(trade.get("r_multiple") or 0)
            dist_stop = float(trade.get("dist_to_stop") or 0)
            updated = trade.get("last_updated") or ""

            with st.container():
                col1, col2, col3, col4, col5 = st.columns([1.5, 1.5, 1.5, 1.5, 2])

                with col1:
                    st.metric(
                        ticker,
                        f"${current_price:.2f}",
                        f"{pnl_pct_val:+.1%}",
                    )

                with col2:
                    st.metric(
                        "P&L",
                        f"${pnl_dollars:+.2f}",
                        f"{r_mult:+.2f}R",
                    )

                with col3:
                    stop_color = (
                        "🔴" if dist_stop < 0.02
                        else "🟡" if dist_stop < 0.05
                        else "🟢"
                    )
                    st.metric(
                        "Stop",
                        f"${stop_price:.2f}",
                        f"{stop_color} {dist_stop:.1%} away",
                    )

                with col4:
                    to_go = (
                        (target_2r - current_price) / current_price
                        if current_price > 0 else 0.0
                    )
                    st.metric(
                        "Target",
                        f"${target_2r:.2f}",
                        f"{to_go:.1%} to go",
                    )

                with col5:
                    if target_2r > stop_price:
                        progress = (current_price - stop_price) / (target_2r - stop_price)
                        progress = max(0.0, min(1.0, progress))
                        st.progress(
                            progress,
                            text=(
                                f"Stop ${stop_price:.2f} ──── "
                                f"${current_price:.2f} ──── "
                                f"Target ${target_2r:.2f}"
                            ),
                        )
                    if updated:
                        st.caption(f"Updated: {updated[11:16]} ET")

                st.divider()

    st.subheader("Today's Scan Results")
    if not latest_scan:
        st.info("No scan data yet.")
        return

    candidates = json.loads(latest_scan.get("candidates_json") or "[]")
    if not candidates:
        st.info("No candidates in latest scan.")
        return

    scan_date = latest_scan.get("scan_date")
    approved_tickers = set(
        df[(df["entry_date"] == scan_date)]["ticker"].tolist()
    ) if not df.empty else set()

    rows = []
    for c in candidates:
        plan = c.get("order_plan") or {}
        gap = c.get("gap_estimate", 0) or 0
        vol = c.get("pm_vol_ratio", 0) or 0
        catalyst = c.get(
            "catalyst_summary",
            c.get("news_headline", "No news"),
        )
        rows.append({
            "Ticker": c.get("ticker"),
            "Score": f"{c.get('quality_score', 0):.0f}",
            "Gap %": f"+{gap * 100:.1f}%",
            "Vol Ratio": f"{vol:.1f}x",
            "Price": f"${c.get('premarket_price', 0):.2f}",
            "Entry Est": f"${plan.get('entry_price', 0):.2f}",
            "Stop": f"${plan.get('stop_price', 0):.2f}",
            "Target": f"${plan.get('target_2r', 0):.2f}",
            "Risk $": f"${plan.get('risk_dollars', 0):.0f}",
            "Catalyst": catalyst,
            "Approved": c.get("ticker") in approved_tickers,
        })

    scan_df = pd.DataFrame(rows)
    styled_df = scan_df.style.map(_color_gap, subset=["Gap %"])
    st.dataframe(
        styled_df,
        column_config={
            "Score": st.column_config.TextColumn(
                "Score",
                help="Composite signal quality (0-100)",
            ),
            "Gap %": st.column_config.TextColumn(
                "Gap %",
                help="Pre-market gap vs prior close",
            ),
            "Vol Ratio": st.column_config.TextColumn(
                "Vol Ratio",
                help="Pre-market volume vs 20-day average",
            ),
            "Risk $": st.column_config.TextColumn(
                "Risk $",
                help="Maximum dollar loss if stop hit",
            ),
            "Catalyst": st.column_config.TextColumn(
                "Catalyst",
                width="large",
                help="News headline driving the gap",
            ),
            "Approved": st.column_config.CheckboxColumn(
                "✅ Approved",
                help="Did you approve this trade today?",
            ),
        },
        hide_index=True,
        use_container_width=True,
        height=400,
    )


def tab_trade_log(trades: list) -> None:
    df = _trades_df(trades)
    closed = df[df["status"] == "CLOSED"] if not df.empty else pd.DataFrame()

    st.subheader("Closed Trades")
    if closed.empty:
        st.info("No closed trades yet.")
        return

    log = closed.copy()
    log["Exit"] = log.apply(
        lambda r: r.get("tranche_3_exit") or r.get("tranche_2_exit")
        or r.get("tranche_1_exit") or r.get("stop_hit_price") or r.get("entry_price"),
        axis=1,
    )
    log = log.rename(columns={
        "entry_date": "Date",
        "ticker": "Ticker",
        "entry_price": "Entry",
        "pnl_dollars": "P&L$",
        "pnl_pct": "P&L%",
        "days_held": "Days",
        "exit_reason": "Exit Reason",
    })
    cols = ["Date", "Ticker", "Entry", "Exit", "P&L$", "P&L%", "Days", "Exit Reason"]
    log = log[[c for c in cols if c in log.columns]]
    styled = log.style.map(_style_pnl, subset=["P&L$", "P&L%"])
    st.dataframe(styled, use_container_width=True, hide_index=True)

    winners = closed[closed["pnl_dollars"] > 0]
    losers = closed[closed["pnl_dollars"] <= 0]
    st.subheader("Summary Stats")
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Total Trades", len(closed))
    s2.metric("Winners", len(winners))
    s3.metric("Losers", len(losers))
    s4.metric(
        "Avg Win",
        f"${winners['pnl_dollars'].mean():.2f}" if not winners.empty else "$0.00",
    )
    s5, s6, s7 = st.columns(3)
    s5.metric(
        "Avg Loss",
        f"${losers['pnl_dollars'].mean():.2f}" if not losers.empty else "$0.00",
    )
    s6.metric(
        "Best Trade",
        f"${closed['pnl_dollars'].max():.2f}" if not closed.empty else "$0.00",
    )
    s7.metric(
        "Worst Trade",
        f"${closed['pnl_dollars'].min():.2f}" if not closed.empty else "$0.00",
    )


def tab_performance(trades: list, pool_history: list) -> None:
    st.subheader("Equity Curve")
    if pool_history:
        hist_df = pd.DataFrame(pool_history)
        dates = pd.to_datetime(hist_df["snapshot_date"])
        pool_values = hist_df["pool"]

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=dates,
            y=pool_values,
            mode="lines",
            name="Q-ALPHA",
            line=dict(color="#00FF88", width=2),
            fill="tozeroy",
            fillcolor="rgba(0, 255, 136, 0.1)",
        ))
        fig.add_hline(
            y=STARTING_POOL,
            line_dash="dash",
            line_color="#666666",
            annotation_text="Starting Capital $3,000",
        )
        fig.update_layout(
            title="Portfolio Equity Curve",
            xaxis_title="Date",
            yaxis_title="Portfolio Value ($)",
            plot_bgcolor="#0E1117",
            paper_bgcolor="#0E1117",
            font=dict(color="white"),
            yaxis=dict(gridcolor="#1E2130"),
            xaxis=dict(gridcolor="#1E2130"),
            hovermode="x unified",
            height=450,
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No pool history yet.")

    st.subheader("P&L Per Trade")
    df = _trades_df(trades)
    closed = df[df["status"] == "CLOSED"] if not df.empty else pd.DataFrame()
    if not closed.empty:
        closed = closed.copy()
        closed["label"] = closed["entry_date"] + " " + closed["ticker"]
        fig2 = px.bar(
            closed,
            x="label",
            y="pnl_dollars",
            color="pnl_dollars",
            color_continuous_scale=["#FF4444", "#00FF88"],
        )
        fig2.update_layout(template="plotly_dark", height=400, showlegend=False)
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No closed trades for P&L chart.")

    st.subheader("Monthly Returns")
    if not closed.empty:
        monthly = closed.copy()
        monthly["month"] = pd.to_datetime(monthly["entry_date"]).dt.to_period("M").astype(str)
        agg = monthly.groupby("month").agg(
            return_pct=("pnl_pct", "mean"),
            trades=("ticker", "count"),
            win_rate=("pnl_dollars", lambda s: (s > 0).mean()),
        ).reset_index()
        agg["win_rate"] = agg["win_rate"].apply(lambda x: f"{x:.0%}")
        agg["return_pct"] = agg["return_pct"].apply(lambda x: f"{x:+.2f}%")
        st.dataframe(agg, use_container_width=True, hide_index=True)
    else:
        st.info("No monthly data yet.")


def tab_system_health(health: list) -> None:
    st.subheader("Component Status")
    components = {
        "morning_scan": {"icon": "🔍", "name": "Morning Scanner"},
        "eod_monitor": {"icon": "📊", "name": "EOD Monitor"},
        "approval_processor": {"icon": "✅", "name": "Approval Processor"},
    }

    for key, info in components.items():
        last = get_last_health(key, health)
        if last:
            time_ago = get_time_ago(last.get("created_at") or last.get("last_run", ""))
            status_color = "#00AA44" if last.get("status") == "OK" else "#CC2200"
            status_icon = "🟢" if last.get("status") == "OK" else "🔴"
            status_text = last.get("status", "UNKNOWN")
            message = last.get("message", "—")
        else:
            time_ago = "Never"
            status_color = "#666666"
            status_icon = "⚫"
            status_text = "Never run"
            message = "—"

        st.markdown(
            f"""
<div style="
    background: #1E2130;
    border-left: 4px solid {status_color};
    border-radius: 4px;
    padding: 12px 16px;
    margin-bottom: 8px;
">
    <b>{info['icon']} {info['name']}</b>
    &nbsp;&nbsp; {status_icon} {status_text}
    &nbsp;&nbsp; <span style="color: #888;">{time_ago}</span>
    <br>
    <small style="color: #666;">{message}</small>
</div>
""",
            unsafe_allow_html=True,
        )

    st.subheader("Recent Activity Log")
    if health:
        log_df = pd.DataFrame(health)[
            ["created_at", "component", "status", "message"]
        ]
        st.dataframe(log_df, use_container_width=True, hide_index=True)
    else:
        st.info("No health logs yet.")

    cutoff = datetime.now() - timedelta(days=7)
    errors = []
    for h in health:
        if h.get("status", "").upper() in ("OK", "SUCCESS"):
            continue
        ts = h.get("created_at")
        if not ts:
            continue
        try:
            if _parse_ts(ts.replace("Z", "+00:00").split("+")[0]) >= cutoff:
                errors.append(h)
        except ValueError:
            errors.append(h)
    st.metric("Error count (last 7 days)", len(errors))


def tab_daily_reviews() -> None:
    """Daily AI session reviews from Supabase."""
    st.header("📓 Daily Trade Reviews")
    st.caption("AI analysis of each trading session")

    try:
        reviews = get_sync().get_daily_reviews()
    except Exception as exc:
        st.error(f"Could not load reviews: {exc}")
        return

    if not reviews:
        st.info(
            "No reviews yet. First review appears after "
            "the first trading session."
        )
        return

    dates = [r["review_date"] for r in reviews]
    selected = st.selectbox("Select date:", dates)
    review = next(r for r in reviews if r["review_date"] == selected)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Entered", review.get("entered_count", 0))
    with col2:
        st.metric("Skipped", review.get("skipped_count", 0))
    with col3:
        pnl = float(review.get("pnl", 0) or 0)
        st.metric("P&L", f"${pnl:+.2f}", delta_color="normal")
    with col4:
        st.metric("Win Rate", f"{float(review.get('win_rate', 0) or 0):.0%}")

    st.divider()
    st.markdown(review.get("full_markdown", "Review not available"))

    suggestion = review.get("improvement_suggestion", "")
    if suggestion:
        st.info(f"💡 **Tomorrow's Improvement:**\n{suggestion}")


def render_footer() -> None:
    et = pytz.timezone("America/New_York")
    now_et = datetime.now(et)
    st.divider()
    col1, col2, col3 = st.columns([2, 2, 1])
    with col1:
        st.caption(
            f"🔄 Auto-refreshes every 5 min | "
            f"Last updated: {now_et.strftime('%H:%M:%S ET')}"
        )
    with col2:
        st.caption(
            f"v{SYSTEM_VERSION} | "
            f"Running {DAYS_RUNNING} days | "
            f"© Q-ALPHA 2026"
        )
    with col3:
        if st.button("🔄 Refresh Now"):
            st.rerun()


def main() -> None:
    trades, pool_history, scans, health = _safe_load()
    render_header()

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📊 Live Status",
        "📋 Trade Log",
        "📈 Performance",
        "🔧 System Health",
        "📓 Daily Reviews",
    ])

    with tab1:
        tab_live_status(trades, scans, pool_history)
    with tab2:
        tab_trade_log(trades)
    with tab3:
        tab_performance(trades, pool_history)
    with tab4:
        tab_system_health(health)
    with tab5:
        tab_daily_reviews()

    render_footer()


if __name__ == "__main__":
    main()
