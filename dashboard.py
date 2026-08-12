# =============================================================================
# Q-ALPHA STREAMLIT DASHBOARD
# =============================================================================
# DEPLOY TO STREAMLIT COMMUNITY CLOUD:
# 1. Push all changes to GitHub:
#    git add .
#    git commit -m "Add dashboard"
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
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from candidates.supabase_sync import SupabaseSync

STARTING_POOL = 3000.0
OPEN_STATUSES = {"OPEN", "T1_HIT", "T2_HIT", "T3_TRAIL", "PENDING_MOC"}

st.set_page_config(
    page_title="Q-ALPHA Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)


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
    for col in ("entry_price", "stop_price", "target_2r", "pnl_dollars", "pnl_pct"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _latest_scan(scans: list) -> dict | None:
    return scans[0] if scans else None


def _style_pnl(val):
    if pd.isna(val):
        return ""
    color = "#00FF88" if val >= 0 else "#FF4444"
    return f"color: {color}; font-weight: bold"


def tab_live_status(trades: list, scans: list, pool_history: list) -> None:
    df = _trades_df(trades)
    latest_scan = _latest_scan(scans)

    pool = STARTING_POOL
    if pool_history:
        pool = float(pool_history[-1].get("pool", STARTING_POOL))
    elif not df.empty and "pool" in df.columns:
        pool = float(df.iloc[-1].get("pool", STARTING_POOL))

    pnl_pct = ((pool - STARTING_POOL) / STARTING_POOL) * 100 if STARTING_POOL else 0
    open_df = df[df["status"].isin(OPEN_STATUSES)] if not df.empty else pd.DataFrame()
    open_pos = len(open_df)

    closed = df[df["status"] == "CLOSED"] if not df.empty else pd.DataFrame()
    total_trades = len(closed)
    wins = len(closed[closed["pnl_dollars"] > 0]) if not closed.empty else 0
    win_rate = wins / total_trades if total_trades else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Pool Value", f"${pool:,.2f}", f"{pnl_pct:+.1f}%")
    c2.metric("Open Positions", f"{open_pos}/10 slots")
    c3.metric("Total Trades", str(total_trades))
    c4.metric("Win Rate", f"{win_rate:.0%}")

    spy_regime = latest_scan.get("spy_regime", "UNKNOWN") if latest_scan else "UNKNOWN"
    vix_regime = latest_scan.get("vix_regime", "NORMAL") if latest_scan else "NORMAL"
    spy_price = float(latest_scan.get("spy_price", 0) or 0) if latest_scan else 0

    if spy_regime == "BULL":
        st.markdown(
            """
            <div style="background:#0d3d1f;border:2px solid #00FF88;
            padding:16px;border-radius:8px;text-align:center;
            font-size:1.4rem;font-weight:bold;color:#00FF88;">
            🐂 BULL MARKET
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            """
            <div style="background:#3d0d0d;border:2px solid #FF4444;
            padding:16px;border-radius:8px;text-align:center;
            font-size:1.4rem;font-weight:bold;color:#FF4444;">
            🐻 BEAR MARKET
            </div>
            """,
            unsafe_allow_html=True,
        )
    st.caption(f"VIX: {vix_regime} | SPY: ${spy_price:.2f}")

    st.subheader("Open Positions")
    if open_df.empty:
        st.info("No open positions.")
    else:
        display = open_df.copy()
        display = display.rename(columns={
            "ticker": "Ticker",
            "entry_price": "Entry",
            "stop_price": "Stop",
            "target_2r": "Target",
            "pnl_dollars": "Current P&L",
            "days_held": "Days Held",
            "status": "Status",
        })
        cols = ["Ticker", "Entry", "Stop", "Target", "Current P&L", "Days Held", "Status"]
        display = display[[c for c in cols if c in display.columns]]
        styled = display.style.map(
            lambda v: "color: #00FF88" if isinstance(v, (int, float)) and v >= 0
            else ("color: #FF4444" if isinstance(v, (int, float)) and v < 0 else ""),
            subset=[c for c in ["Current P&L"] if c in display.columns],
        )
        st.dataframe(styled, use_container_width=True, hide_index=True)

    st.subheader("Today's Scan Results")
    if not latest_scan:
        st.info("No scan data yet.")
        return

    candidates = json.loads(latest_scan.get("candidates_json") or "[]")
    if not candidates:
        st.info("No candidates in latest scan.")
        return

    approved_tickers = set(
        df[(df["entry_date"] == latest_scan.get("scan_date"))]["ticker"].tolist()
    ) if not df.empty else set()

    rows = []
    for c in candidates:
        rows.append({
            "Ticker": c.get("ticker"),
            "Gap%": f"{c.get('gap_estimate', 0) * 100:.1f}%",
            "Vol Ratio": c.get("pm_vol_ratio"),
            "News": "Y" if c.get("news_catalyst") else "N",
            "Approved?": "Yes" if c.get("ticker") in approved_tickers else "No",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


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
        hist_df["snapshot_date"] = pd.to_datetime(hist_df["snapshot_date"])
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=hist_df["snapshot_date"],
            y=hist_df["pool"],
            mode="lines+markers",
            line=dict(color="#00FF88", width=2),
            name="Pool Value",
        ))
        fig.add_hline(
            y=STARTING_POOL,
            line_dash="dash",
            line_color="#888888",
            annotation_text=f"Start ${STARTING_POOL:,.0f}",
        )
        fig.update_layout(
            template="plotly_dark",
            xaxis_title="Date",
            yaxis_title="Pool Value ($)",
            height=400,
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
        closed["color"] = closed["pnl_dollars"].apply(
            lambda x: "#00FF88" if x >= 0 else "#FF4444"
        )
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
    components = ["morning_scan", "approval_processor", "eod_monitor"]
    latest_by_component: dict[str, dict] = {}
    for row in health:
        comp = row.get("component", "")
        if comp not in latest_by_component:
            latest_by_component[comp] = row

    labels = {
        "morning_scan": "Morning Scanner",
        "approval_processor": "Approval Processor",
        "eod_monitor": "EOD Monitor",
    }
    for comp in components:
        row = latest_by_component.get(comp, {})
        last_run = row.get("last_run", "Never")
        status = row.get("status", "UNKNOWN")
        st.write(f"**{labels[comp]}:** {last_run} — {status}")

    st.subheader("Recent Activity Log")
    if health:
        log_df = pd.DataFrame(health)[
            ["created_at", "component", "status", "message"]
        ]
        st.dataframe(log_df, use_container_width=True, hide_index=True)
    else:
        st.info("No health logs yet.")

    cutoff = datetime.now() - timedelta(days=7)
    errors = [
        h for h in health
        if h.get("status", "").upper() not in ("OK", "SUCCESS")
        and h.get("created_at")
        and datetime.fromisoformat(h["created_at"].replace("Z", "+00:00").split("+")[0])
        >= cutoff
    ]
    st.metric("Error count (last 7 days)", len(errors))


def main() -> None:
    st.title("📈 Q-ALPHA Dashboard")
    trades, pool_history, scans, health = _safe_load()

    tab1, tab2, tab3, tab4 = st.tabs([
        "📊 Live Status",
        "📋 Trade Log",
        "📈 Performance",
        "🔧 System Health",
    ])

    with tab1:
        tab_live_status(trades, scans, pool_history)
    with tab2:
        tab_trade_log(trades)
    with tab3:
        tab_performance(trades, pool_history)
    with tab4:
        tab_system_health(health)


if __name__ == "__main__":
    main()
