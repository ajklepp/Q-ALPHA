# =============================================================================
# Q-ALPHA STREAMLIT DASHBOARD
# =============================================================================
# DEPLOY TO STREAMLIT COMMUNITY CLOUD:
# App URL: https://q-alpha-lshnrvza2radqpkjrkf52m.streamlit.app
# Repo:    ajklepp/Q-ALPHA  |  Branch: main  |  Main file: dashboard.py
#
# Streamlit Cloud DOES auto-redeploy on push to the linked branch IF the app
# is connected to GitHub. If the live app still shows old UI / AttributeError:
#   1. https://share.streamlit.io → open the Q-ALPHA app
#   2. Confirm Settings → General: repo ajklepp/Q-ALPHA, branch main,
#      Main file path dashboard.py
#   3. Manage app → Reboot app  (or Redeploy) to force a clean process
#      (clears @st.cache_resource holding a pre-watchlist SupabaseSync)
#   4. Settings → Secrets must contain:
#        SUPABASE_URL = "https://zabyiqhyliuvrwqbnxkq.supabase.co"
#        SUPABASE_SECRET_KEY = "<service_role key — same as local .env>"
#      Update secrets if keys were rotated; then reboot.
#
# LOCAL LAUNCH (DETACHED — never block Cursor / agents):
#   .\start_dashboard.ps1          # returns immediately; URL http://localhost:8501
#   .\stop_dashboard.ps1           # kills whatever owns port 8501
#
# Do NOT run `streamlit run dashboard.py` as a foreground command you wait on —
# Streamlit never exits and freezes the caller. Always use start_dashboard.ps1.
#
# MULTI-PAGE NOTE: single-file app. Tabs in st.tabs include Ticker Profiles,
# Strategy Lab, and Glossary (not pages/ sidebar routes). Profiler reads
# precomputed profiles/*.json; "Refresh profile" is on-demand only — never on
# load/autorefresh. Strategy Lab reads strategy_lab/results/forward_state.json
# (SIM paper only). Glossary tab renders repo-root GLOSSARY.md.
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
from dashboard_shared import SYSTEM_VERSION as SHARED_VERSION
from dashboard_shared import (
    compute_and_save_profile,
    ensure_polygon_key_from_secrets,
    et_today,
    format_profile_rr_cell,
    format_ticker_with_history,
    list_cached_profile_tickers,
    load_profile,
    load_todays_watchlist,
    profile_path,
)

STARTING_POOL = 3000.0
OPEN_STATUSES = {"OPEN", "T1_HIT", "T2_HIT", "T3_TRAIL", "PENDING_MOC"}
MAX_SLOTS = 10
SYSTEM_VERSION = SHARED_VERSION  # keep Home + Profiles pages in sync
SYSTEM_START_DATE = "2026-08-17"
SYSTEM_START = datetime.strptime(SYSTEM_START_DATE, "%Y-%m-%d").date()
DAYS_RUNNING = (datetime.now().date() - SYSTEM_START).days
# Bump when SupabaseSync gains/loses methods. Streamlit @st.cache_resource can
# otherwise keep a pre-redeploy class instance (no get_watchlist) forever.
_SUPABASE_SYNC_API = "watchlist-v2"
# Agent entry window closes at 11:00 ET — after that, no-trade = Skipped.
ENTRY_WINDOW_CLOSE = dtime(11, 0)

st.set_page_config(
    page_title="Q-ALPHA Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st_autorefresh(interval=5 * 60 * 1000, key="main_refresh")


@st.cache_resource
def get_sync(_api: str = _SUPABASE_SYNC_API) -> SupabaseSync:
    """Cached Supabase client. `_api` exists only to bust stale class caches."""
    return SupabaseSync()


def _et_today() -> str:
    """Today's calendar date in America/New_York as YYYY-MM-DD."""
    return datetime.now(pytz.timezone("America/New_York")).date().isoformat()


def _load_todays_watchlist(scan_date: str | None = None) -> list[dict]:
    """
    Load today's watchlist via a FRESH SupabaseSync (not get_sync cache).

    Cloud soft-reloads have previously kept a cached SupabaseSync class from
    before get_watchlist existed, which raised AttributeError while the rest
    of the new dashboard UI rendered. Instantiating here always binds the
    current module's class.
    """
    day = scan_date or _et_today()
    sync = SupabaseSync()
    if not hasattr(sync, "get_watchlist"):
        get_sync.clear()
        sync = SupabaseSync()
    return sync.get_watchlist(day)


def _safe_load() -> tuple[list, list, list]:
    try:
        sync = get_sync()
        return (
            sync.get_all_trades(),
            sync.get_pool_history(),
            sync.get_system_health(),
        )
    except Exception as exc:
        st.error(f"Could not connect to Supabase: {exc}")
        return [], [], []


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
    """Countdown to next weekday 9:20 AM ET scan (skips Sat/Sun)."""
    et = pytz.timezone("America/New_York")
    now_et = datetime.now(et)
    next_scan = now_et.replace(hour=9, minute=20, second=0, microsecond=0)
    if now_et >= next_scan:
        next_scan = next_scan + timedelta(days=1)
    # Scan only runs Mon–Fri — roll weekend targets forward to Monday 9:20.
    while next_scan.weekday() >= 5:  # 5=Sat, 6=Sun
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


def _format_watchlist_day(day: str) -> str:
    """ISO date → 'Aug 20, 2026' for the watchlist subheader."""
    try:
        return datetime.strptime(day, "%Y-%m-%d").strftime("%b %d, %Y")
    except ValueError:
        return day


def _trades_for_day(trades: list, day: str) -> dict[str, dict]:
    """
    Map ticker → trade for entry_date == day.
    Prefers the already-loaded trades list; falls back to get_todays_trades()
    and local paper_trades.json when that list is empty.
    """
    by_ticker: dict[str, dict] = {}
    for t in trades:
        if str(t.get("entry_date") or "") != day:
            continue
        ticker = str(t.get("ticker") or "").upper()
        if ticker:
            by_ticker[ticker] = t

    if not by_ticker:
        try:
            for t in get_sync().get_todays_trades(day):
                ticker = str(t.get("ticker") or "").upper()
                if ticker:
                    by_ticker[ticker] = t
        except Exception:
            pass

    if not by_ticker:
        local_path = ROOT / "candidates" / "paper_trades.json"
        if local_path.exists():
            try:
                import json
                payload = json.loads(local_path.read_text(encoding="utf-8"))
                for t in payload.get("trades") or []:
                    if str(t.get("entry_date") or "") != day:
                        continue
                    ticker = str(t.get("ticker") or "").upper()
                    if ticker:
                        by_ticker[ticker] = t
            except Exception:
                pass

    return by_ticker


def _candidate_status(trade: dict | None, now_et: datetime) -> str:
    """
    Lifecycle label for a watchlist ticker today.
    Watching / Skipped before|after 11:00 ET when no trade; otherwise
    In Trade / Closed / Stopped from the trades row.
    """
    if trade is None:
        if now_et.time() < ENTRY_WINDOW_CLOSE:
            return "👀 Watching"
        return "— Skipped"

    status = str(trade.get("status") or "").upper()
    if status in OPEN_STATUSES or status in {"OPEN", "PENDING"}:
        return "🟢 In Trade"

    exit_reason = str(trade.get("exit_reason") or "").upper()
    pnl = float(trade.get("pnl_dollars") or 0)
    try:
        r_mult = float(trade["r_multiple"]) if trade.get("r_multiple") is not None else None
    except (TypeError, ValueError):
        r_mult = None

    is_stop = "STOP" in exit_reason
    is_target = "TARGET" in exit_reason
    if r_mult is None:
        if is_stop:
            r_mult = -1.0
        elif is_target:
            r_mult = 2.0
        else:
            r_mult = 1.0 if pnl > 0 else (-1.0 if pnl < 0 else 0.0)

    if is_stop or (not is_target and pnl < 0):
        return f"🔴 Stopped {r_mult:.0f}r"
    return f"✅ Closed {r_mult:+.0f}r"


def _trade_fill_columns(trade: dict | None) -> dict[str, str]:
    """
    Real fill levels from the trades table only (never scan-time estimates).
    Blank when no trade exists for this ticker today.
    """
    blank = {"Entry": "—", "Stop": "—", "Target": "—", "P&L": "—"}
    if not trade:
        return blank

    def _money(val) -> str:
        try:
            if val is None or val == "":
                return "—"
            return f"${float(val):.2f}"
        except (TypeError, ValueError):
            return "—"

    entry = trade.get("entry_price")
    stop = trade.get("stop_price")
    target = trade.get("target_2r")
    pnl = trade.get("pnl_dollars")
    pct = trade.get("pnl_pct")

    pnl_str = "—"
    try:
        if pnl is not None and pnl != "":
            pnl_f = float(pnl)
            pct_part = ""
            try:
                if pct is not None and pct != "":
                    pct_f = float(pct)
                    # Trades store fraction (0.012) or already-percent; tolerate both.
                    if abs(pct_f) <= 1.0:
                        pct_f *= 100.0
                    pct_part = f" ({pct_f:+.1f}%)"
            except (TypeError, ValueError):
                pct_part = ""
            pnl_str = f"${pnl_f:+.2f}{pct_part}"
    except (TypeError, ValueError):
        pnl_str = "—"

    return {
        "Entry": _money(entry),
        "Stop": _money(stop),
        "Target": _money(target),
        "P&L": pnl_str,
    }


def _style_watchlist(df: pd.DataFrame):
    """Gap%/P&L green/red; R:R warn amber; money columns right-aligned."""
    money_cols = [c for c in ("Gap %", "Vol Ratio", "Score", "Rank",
                              "Entry", "Stop", "Target", "P&L") if c in df.columns]

    def _pnl_cell(val: str) -> str:
        text = str(val)
        if text in {"—", "", "None"}:
            return "color: #666666"
        if text.startswith("$-") or "-$" in text or text.startswith("-$"):
            return "color: #FF4444; font-weight: bold"
        # "$+12.00" or positive without explicit +
        if "+$" in text or text.startswith("$+"):
            return "color: #00FF88; font-weight: bold"
        if text.startswith("$") and not text.startswith("$-"):
            # bare "$12.00" — treat leading digit after $ as positive if no minus
            rest = text[1:].lstrip("+")
            if rest and rest[0].isdigit():
                return "color: #00FF88; font-weight: bold"
        return ""

    def _rr_cell(val: str) -> str:
        text = str(val)
        if text in {"", "—", "None"}:
            return "color: #666666"
        if text == "n/a":
            return "color: #888888"
        if "⚠️" in text:
            return "color: #FFAA33; font-weight: bold"
        return "color: #00FF88"

    styled = df.style.map(_color_gap, subset=["Gap %"])
    if "P&L" in df.columns:
        styled = styled.map(_pnl_cell, subset=["P&L"])
    if "R:R" in df.columns:
        styled = styled.map(_rr_cell, subset=["R:R"])
    styled = styled.set_properties(
        subset=money_cols,
        **{"text-align": "right"},
    )
    styled = styled.set_properties(
        subset=[c for c in ("Ticker", "Status", "R:R") if c in df.columns],
        **{"text-align": "left"},
    )
    styled = styled.set_table_styles(
        [
            {
                "selector": "th",
                "props": [
                    ("background-color", "#1E2130"),
                    ("color", "#AAAAAA"),
                    ("font-weight", "600"),
                    ("text-align", "left"),
                    ("border-bottom", "1px solid #2A2F3A"),
                ],
            },
            {
                "selector": "td",
                "props": [
                    ("padding", "8px 12px"),
                    ("border-bottom", "1px solid #1A1D27"),
                ],
            },
        ],
        overwrite=False,
    )
    return styled


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


def tab_live_status(trades: list, pool_history: list) -> None:
    df = _trades_df(trades)
    today = _et_today()

    # Watchlist is the single source of truth for what the agent is watching.
    watch_rows: list[dict] = []
    try:
        watch_rows = _load_todays_watchlist(today)
    except Exception as exc:
        watch_load_err: str | None = str(exc)
    else:
        watch_load_err = None

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

    # Regime from today's watchlist (not legacy daily_scans).
    spy_regime = (watch_rows[0].get("regime") if watch_rows else None) or "UNKNOWN"
    vix_regime = "NORMAL"

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
            current_price = float(trade.get("current_price") or entry_price)
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

    st.subheader("Today's Watchlist")
    if watch_load_err:
        st.caption(f"Watchlist unavailable: {watch_load_err}")

    if watch_rows:
        regime_label = watch_rows[0].get("regime") or "—"
        et_now = datetime.now(pytz.timezone("America/New_York"))
        trades_today = _trades_for_day(trades, today)
        st.markdown(
            f"**{len(watch_rows)} candidates · {_format_watchlist_day(today)} "
            f"· {regime_label} regime**"
        )

        wl_rows = []
        for r in watch_rows:
            gap = r.get("gap_pct")
            try:
                gap_f = float(gap) if gap is not None else 0.0
            except (TypeError, ValueError):
                gap_f = 0.0
            gap_pct_display = gap_f * 100.0 if abs(gap_f) <= 1.0 else gap_f
            vol = r.get("pm_vol_ratio")
            try:
                vol_f = float(vol) if vol is not None else 0.0
            except (TypeError, ValueError):
                vol_f = 0.0
            score = r.get("score")
            try:
                score_f = float(score) if score is not None else 0.0
            except (TypeError, ValueError):
                score_f = 0.0
            ticker = str(r.get("ticker") or "").upper()
            trade = trades_today.get(ticker)
            fills = _trade_fill_columns(trade)
            wl_rows.append({
                "Rank": int(r.get("rank") or 0),
                "Ticker": format_ticker_with_history(ticker),
                "R:R": format_profile_rr_cell(ticker),
                "Gap %": f"+{gap_pct_display:.1f}%",
                "Vol Ratio": f"{vol_f:.1f}x",
                "Score": f"{score_f:.0f}",
                "Status": _candidate_status(trade, et_now),
                "Entry": fills["Entry"],
                "Stop": fills["Stop"],
                "Target": fills["Target"],
                "P&L": fills["P&L"],
            })

        wl_df = pd.DataFrame(wl_rows)
        st.dataframe(
            _style_watchlist(wl_df),
            column_config={
                "Rank": st.column_config.NumberColumn(
                    "Rank", width="small", format="%d",
                ),
                "Ticker": st.column_config.TextColumn(
                    "Ticker",
                    help=(
                        "Symbol + history_flag: none = reliable, "
                        "* = limited history/sample, "
                        "** = insufficient (informational only)."
                    ),
                    width="small",
                ),
                "R:R": st.column_config.TextColumn(
                    "R:R",
                    help=(
                        "target / safe-max-stop; <1.5 = reward may not "
                        "justify stop width. Shows number + ⚠️ when below "
                        "1.5; n/a when profile is insufficient (no R:R)."
                    ),
                    width="small",
                ),
                "Gap %": st.column_config.TextColumn(
                    "Gap %", help="Pre-market gap vs prior close", width="small",
                ),
                "Vol Ratio": st.column_config.TextColumn(
                    "Vol Ratio",
                    help="Pre-market volume vs expected baseline",
                    width="small",
                ),
                "Score": st.column_config.TextColumn(
                    "Score", help="Composite signal quality (0-100)", width="small",
                ),
                "Status": st.column_config.TextColumn(
                    "Status",
                    help="Trade lifecycle for today (watching → entered → closed)",
                    width="medium",
                ),
                "Entry": st.column_config.TextColumn(
                    "Entry",
                    help="Real fill from trades table (watch_and_enter) — not a scan estimate",
                    width="small",
                ),
                "Stop": st.column_config.TextColumn(
                    "Stop",
                    help="Real stop from trades table",
                    width="small",
                ),
                "Target": st.column_config.TextColumn(
                    "Target",
                    help="Real 2R target from trades table",
                    width="small",
                ),
                "P&L": st.column_config.TextColumn(
                    "P&L",
                    help="pnl_dollars from trades (live current_price when open)",
                    width="medium",
                ),
            },
            hide_index=True,
            use_container_width=True,
            height=min(520, 56 + 38 * max(len(wl_rows), 1)),
        )
    else:
        st.info(
            "No watchlist for today yet. It appears here as soon as the "
            "9:20 agent syncs candidates to Supabase (even with zero trades)."
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


def tab_ticker_profiles() -> None:
    """
    Setup-analysis tab: read precomputed profiles/<T>_profile.json.
    No Polygon calls on tab open — only the explicit Refresh button.
    """
    st.caption(
        "Analog MAE/MFE setup analysis · informational only · "
        "reads precomputed JSON (no auto Polygon calls)"
    )
    st.info(
        "Profiles are **precomputed** at the 9:20 scan (and via Refresh). "
        "This tab does **not** call Polygon on load."
    )
    st.caption(
        "History flags: `*` limited history/small sample or extended past 2yr · "
        "`**` insufficient — informational only"
    )

    today = et_today()
    watch_tickers: list[str] = []
    watch_err: str | None = None
    try:
        rows = load_todays_watchlist(today)
        watch_tickers = [
            str(r.get("ticker") or "").upper()
            for r in rows
            if r.get("ticker")
        ]
    except Exception as exc:
        watch_err = str(exc)

    cached = list_cached_profile_tickers()
    options: list[str] = []
    seen: set[str] = set()
    for t in watch_tickers + cached:
        if t and t not in seen:
            options.append(t)
            seen.add(t)

    if watch_err:
        st.warning(f"Watchlist unavailable: {watch_err}")

    if not options:
        st.warning(
            "No watchlist tickers and no cached profiles yet. "
            "After the morning scan, tickers appear here — or type a symbol below."
        )
        manual = st.text_input(
            "Ticker symbol", value="JOBY", key="profile_manual_ticker",
        ).strip().upper()
        if manual:
            options = [manual]

    col_sel, col_meta = st.columns([2, 3])
    with col_sel:
        ticker = st.selectbox(
            "Select ticker",
            options=options or ["JOBY"],
            index=0,
            key="profile_ticker_select",
            help="Today's watchlist preferred; cached profiles also listed.",
        )
    with col_meta:
        path = profile_path(ticker)
        if path.exists():
            mtime = path.stat().st_mtime
            st.caption(
                f"Cache: `{path.relative_to(ROOT)}` · "
                f"updated {datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')}"
            )
        else:
            st.caption(f"No cache at `{path.relative_to(ROOT)}`")

    do_refresh = False
    if ensure_polygon_key_from_secrets():
        # On-demand Polygon refresh — only when a key is available (local / secrets).
        # Streamlit Cloud usually has no key; profiles come from committed JSON.
        if st.button(
            f"🔄 Refresh profile — {ticker}",
            type="primary",
            key="profile_refresh_btn",
            help="Runs build_ticker_profile (Polygon 1-min). Slow. Not auto.",
        ):
            do_refresh = True

    if do_refresh:
        with st.spinner(
            f"Computing profile for {ticker} (Polygon daily + 1-min per analog)…"
        ):
            try:
                profile = compute_and_save_profile(ticker)
                hf = profile.get("history_flag") or ""
                st.success(
                    f"Saved {format_ticker_with_history(ticker)} profile "
                    f"({profile.get('analog_count', profile.get('n_analogs_measured', '?'))} "
                    f"analogs, {profile.get('confidence', '?')}"
                    f"{', lookback=' + str(profile.get('actual_lookback_days')) + 'd' if profile.get('actual_lookback_days') is not None else ''}"
                    f"{', flag=' + repr(hf) if hf else ''})"
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Profile compute failed: {exc}")
                return

    profile = load_profile(ticker)
    if profile is None:
        st.warning(
            f"No precomputed profile for **{ticker}**. "
            f"Click **Refresh profile** to generate one (expensive)."
        )
        return

    st.divider()
    conf = profile.get("confidence", "?")
    n_m = (
        profile.get("analog_count")
        or profile.get("n_analogs_measured")
        or profile.get("n_analogs_finder")
        or 0
    )
    as_of = profile.get("as_of_date", "—")
    weighting = profile.get("weighting", "equal")
    hist_flag = profile.get("history_flag") or ""
    lookback_d = profile.get("actual_lookback_days")
    display_name = format_ticker_with_history(ticker)

    st.subheader(display_name)
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Analogs", n_m)
    m2.metric("Confidence", conf)
    m3.metric(
        "History flag",
        {"*": "* limited", "**": "** insuff", "": "(none)"}.get(
            hist_flag, hist_flag or "(none)"
        ),
    )
    m4.metric("Lookback days", lookback_d if lookback_d is not None else "—")
    m5.metric("As of", as_of)

    # Avoid markdown **bold** wrapping tickers that already contain * / ** flags
    # (was rendering as: **USDE **** insufficient...).
    if hist_flag == "**":
        st.warning(
            f"⚠️ {ticker} — insufficient analogs; informational only."
        )
    elif hist_flag == "*":
        lb_note = profile.get("lookback_note")
        if lb_note:
            st.info(f"⚠️ {ticker} — {lb_note}")
        else:
            st.info(
                f"⚠️ {ticker} — limited history and/or small sample; "
                "usable but less certain."
            )

    if profile.get("informational_only", True):
        st.caption("INFORMATIONAL ONLY — not wired into order / entry logic.")

    outcomes = profile.get("outcomes") or {}
    bracket = profile.get("bracket") or {}
    tiers = bracket.get("tiers") or {}
    pct = profile.get("percentiles") or {}
    mae = pct.get("mae") or {}
    mfe = pct.get("mfe") or {}
    meaningful = profile.get("stats_meaningful", hist_flag != "**")

    if not meaningful:
        note = outcomes.get("note") or (
            f"n={n_m}, not meaningful — insufficient sample."
        )
        st.error(f"📊 Metrics suppressed: {note}")
        if profile.get("per_analog"):
            st.caption(
                "Raw per-analog audit rows may appear below; "
                "win-rate / R:R / percentiles are intentionally blank."
            )
        # Still show audit table if any; skip confident summary metrics
        per = profile.get("per_analog") or []
        if per:
            st.subheader("Per-analog audit (not for sizing)")
            st.dataframe(pd.DataFrame(per), hide_index=True, use_container_width=True)
        return

    rr_warn = outcomes.get("rr_warning")
    n_winners = ((outcomes.get("winner_mfe") or {}).get("n") or 0)
    if rr_warn and n_winners > 0:
        st.error(f"⚠️ R:R warning ({ticker}): {rr_warn}")
    elif outcomes.get("reward_risk") is not None:
        st.success(
            f"Reward:Risk = {outcomes.get('reward_risk')} "
            f"(target / safe-max stop) · {display_name}"
        )

    def _pct_metric(value) -> str:
        """Format a display-% metric; null → em dash (not 'None%')."""
        return "—" if value is None else f"{value}%"

    o1, o2, o3 = st.columns(3)
    o1.metric(
        "Win rate",
        _pct_metric(outcomes.get("win_rate_pct_display")),
        help=outcomes.get("win_definition", "held close > entry"),
    )
    o2.metric(
        "Winner MFE p50",
        _pct_metric(outcomes.get("winner_mfe_p50_display")),
        help="Median MFE among days that closed above entry",
    )
    o3.metric(
        "Failure MAE p50",
        _pct_metric(outcomes.get("failure_mae_p50_display")),
        help="Median MAE among days that closed below entry",
    )

    st.subheader("MAE / MFE percentiles (equal-weight)")
    pct_rows = []
    for key in ("p50", "p75", "p90"):
        pct_rows.append({
            "Percentile": key,
            "MAE %": round((mae.get(key) or 0) * 100, 2),
            "MFE %": round((mfe.get(key) or 0) * 100, 2),
        })
    st.dataframe(
        pd.DataFrame(pct_rows), hide_index=True, use_container_width=True,
    )

    st.subheader("Derived bracket (informational)")
    b1, b2 = st.columns(2)
    with b1:
        st.markdown(
            f"""
| Level | % below entry |
|---|---|
| **SAFE MAX STOP** | **{bracket.get('safe_max_stop_pct_display', '—')}%** |
| Tier 1 (≈ MAE p50) | {tiers.get('tier1_pct_display', '—')}% |
| Tier 2 (≈ MAE p75) | {tiers.get('tier2_pct_display', '—')}% |
| Tier 3 (≈ MAE p90) | {tiers.get('tier3_pct_display', '—')}% |
| Tier 4 (beyond p90) | {tiers.get('tier4_pct_display', '—')}% |
"""
        )
    with b2:
        st.metric(
            "TARGET (≈ MFE p50)",
            f"+{bracket.get('target_pct_display', '—')}%",
        )
        hit = profile.get("hit_rates") or {}
        if hit:
            st.markdown("**MFE hit-rates**")
            for _k, h in hit.items():
                thr = float(h.get("threshold_pct") or 0) * 100
                rate = h.get("equal_weight", h.get("unweighted", 0))
                st.caption(f"MFE ≥ +{thr:.0f}% → {float(rate) * 100:.1f}%")

    with st.expander("Per-analog day detail", expanded=False):
        rows = profile.get("per_analog") or []
        if not rows:
            st.write("No per-analog rows in this profile.")
        else:
            df = pd.DataFrame([
                {
                    "date": r.get("date"),
                    "entry": r.get("entry_proxy_price"),
                    "MAE%": round(float(r.get("mae_pct") or 0) * 100, 2),
                    "MFE%": round(float(r.get("mfe_pct") or 0) * 100, 2),
                    "held": "Y" if r.get("held") else "N",
                    "weight": r.get("weight_renorm", r.get("combined_weight")),
                }
                for r in rows
            ])
            st.dataframe(df, hide_index=True, use_container_width=True)

    san = profile.get("sanity") or {}
    with st.expander("Sanity checks", expanded=False):
        for c in san.get("checks") or []:
            st.write(f"- {c}")
        st.write(f"overall={'PASS' if san.get('ok') else 'FAIL'}")


# ---------------------------------------------------------------------------
# Strategy Lab — A vs B forward paper (reads strategy_lab/results/forward_state.json)
# ---------------------------------------------------------------------------

FORWARD_STATE_PATH = ROOT / "strategy_lab" / "results" / "forward_state.json"
LAB_MAX_SLOTS = 10


def _load_forward_state_local() -> dict | None:
    """Load local forward_state.json. None if missing/empty/unreadable."""
    if not FORWARD_STATE_PATH.exists():
        return None
    try:
        raw = FORWARD_STATE_PATH.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict) or not data:
            return None
        data = dict(data)
        data.setdefault("_lab_state_source", "local_file")
        return data
    except (OSError, json.JSONDecodeError):
        return None


def _load_forward_state() -> dict | None:
    """
    Prefer Supabase strategy_lab_state via ANON key (Cloud-safe), else local JSON.

    Same field names either way — tab binds to pool_A_trailing / pool_B_target / …
    Never uses SUPABASE_SECRET_KEY for this tab.
    """
    try:
        sys.path.insert(0, str(ROOT / "strategy_lab"))
        from lab_state_sync import fetch_latest_forward_state_anon

        remote = fetch_latest_forward_state_anon()
        if remote:
            return remote
    except Exception:
        pass
    return _load_forward_state_local()


def _lab_exit_summary(counts: dict | None) -> str:
    """Format exit_reason_counts like 'kill×4' (nonzero only)."""
    if not counts:
        return "—"
    parts = []
    for reason in ("trail", "target", "kill", "time_cap"):
        n = int((counts or {}).get(reason) or 0)
        if n > 0:
            parts.append(f"{reason}×{n}")
    # Any unexpected keys
    for k, v in (counts or {}).items():
        if k in ("trail", "target", "kill", "time_cap"):
            continue
        n = int(v or 0)
        if n > 0:
            parts.append(f"{k}×{n}")
    return ", ".join(parts) if parts else "—"


def _lab_tranche_exits(tranches: list | None) -> str:
    """Compact exit prices from tranches."""
    if not tranches:
        return "—"
    prices = []
    for t in tranches:
        px = t.get("exit_price")
        if px is None:
            continue
        try:
            prices.append(f"{float(px):.4f}")
        except (TypeError, ValueError):
            continue
    # Deduplicate while preserving order
    seen: set[str] = set()
    uniq = []
    for p in prices:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return ", ".join(uniq) if uniq else "—"


OOS_R2_BACKTEST_PATH = ROOT / "strategy_lab" / "results" / "oos_r2_backtest.json"
# Forward / gap R² is noise below this; still compute in runner, just don't show.
OOS_R2_MIN_N = 20


def _load_oos_r2_backtest() -> dict | None:
    """Static historical OOS R² from oos_r2.py (best-effort)."""
    if not OOS_R2_BACKTEST_PATH.exists():
        return None
    try:
        data = json.loads(OOS_R2_BACKTEST_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _r2_gap_interpretation(
    *,
    backtest_r2: float | None,
    forward_r2: float | None,
    backtest_n: int,
    forward_n: int,
    min_n: int = OOS_R2_MIN_N,
) -> str:
    """
    One-line gap read between backtest and live forward R².
    Only when BOTH sides have >= min_n completed pairs.
    """
    if backtest_n < min_n or forward_n < min_n:
        return "not enough forward data yet"
    if backtest_r2 is None or forward_r2 is None:
        return "not enough forward data yet"
    gap = float(forward_r2) - float(backtest_r2)
    if forward_r2 >= backtest_r2 and forward_r2 > 0:
        return "edge holding out-of-sample"
    if forward_r2 < 0 or gap < -0.15:
        return "possible overfit — predictions not holding live"
    if forward_r2 >= backtest_r2:
        return "forward ≥ backtest but still weak (near/below zero)"
    return "forward below backtest — watch as N grows"


def _render_strategy_lab_r2_panel(state: dict) -> None:
    """
    Side-by-side Backtest OOS R² vs Forward rolling OOS R².
    Never raises — missing/small-N → collecting-data UI.
    """
    st.subheader("Out-of-Sample R²")
    st.caption(
        "Negative R² means the profiler is worse than predicting the average MFE. "
        f"R² needs a meaningful sample (>={OOS_R2_MIN_N}) to be trustworthy; "
        "small-N values are noise. "
        "Forward R² is true OOS by construction (live / replay setups the strategy "
        "never trained on)."
    )

    bt = _load_oos_r2_backtest()
    bt_oos = (bt or {}).get("out_of_sample") or {}
    try:
        bt_r2 = float(bt_oos["r2"]) if bt_oos.get("r2") is not None else None
    except (TypeError, ValueError):
        bt_r2 = None
    try:
        bt_n = int(bt_oos.get("n") or 0)
    except (TypeError, ValueError):
        bt_n = 0

    fwd_stats = state.get("forward_oos_r2_stats") or {}
    try:
        fwd_n = int(
            state.get("forward_oos_r2_n")
            if state.get("forward_oos_r2_n") is not None
            else fwd_stats.get("n") or 0
        )
    except (TypeError, ValueError):
        fwd_n = 0
    fwd_r2_raw = state.get("forward_oos_r2")
    if fwd_r2_raw is None:
        fwd_r2_raw = fwd_stats.get("r2")
    try:
        fwd_r2 = float(fwd_r2_raw) if fwd_r2_raw is not None else None
    except (TypeError, ValueError):
        fwd_r2 = None

    fwd_ready = fwd_n >= OOS_R2_MIN_N and fwd_r2 is not None
    gap_ready = (
        bt_n >= OOS_R2_MIN_N
        and fwd_n >= OOS_R2_MIN_N
        and bt_r2 is not None
        and fwd_r2 is not None
    )

    col_bt, col_fwd = st.columns(2)
    with col_bt:
        st.markdown("**Backtest (historical, noisy)**")
        if bt_r2 is None:
            st.info("collecting data — backtest file missing")
        else:
            st.metric("OOS R²", f"{bt_r2:.4f}", help=f"N={bt_n} temporal holdout")
            st.caption(f"N={bt_n} · from `oos_r2_backtest.json`")

    with col_fwd:
        st.markdown(f"**Forward (live, N={fwd_n} trades)**")
        if not fwd_ready:
            st.info(
                f"Forward: collecting data "
                f"(N={fwd_n} / {OOS_R2_MIN_N} needed before R² is meaningful)"
            )
        else:
            st.metric("OOS R²", f"{fwd_r2:.4f}")
            msg = fwd_stats.get("message")
            if msg:
                st.caption(str(msg))

    if not gap_ready:
        st.markdown("**Gap:** not enough forward data yet")
    else:
        gap_txt = f"{fwd_r2 - bt_r2:+.4f}"
        interp = _r2_gap_interpretation(
            backtest_r2=bt_r2,
            forward_r2=fwd_r2,
            backtest_n=bt_n,
            forward_n=fwd_n,
        )
        st.markdown(f"**Gap** (forward − backtest): `{gap_txt}` — {interp}")


def tab_strategy_lab() -> None:
    """
    SIM / Polygon-paper A-vs-B forward test.

    Binds to real fields written by strategy_lab/live_forward.py →
    strategy_lab/results/forward_state.json. Does not touch live agent state.
    """
    state = _load_forward_state()
    if state is None:
        st.info(
            "No forward-test data yet — start `live_forward.py` "
            "(or run a replay dry-run). State syncs to Supabase for Cloud; "
            "local fallback is `strategy_lab/results/forward_state.json`."
        )
        return

    src = state.get("_lab_state_source") or "unknown"
    # --- Banner: never confuse with live IBKR agent ---
    st.markdown(
        """
<div style="
    background: #1a3a5c;
    border: 2px solid #3d8bfd;
    border-radius: 8px;
    padding: 14px 20px;
    margin-bottom: 12px;
">
  <div style="font-size: 18px; font-weight: 700; color: #7ec8ff;">
    SIM · Polygon paper · not IBKR / not real money
  </div>
  <div style="font-size: 13px; color: #b8d4f0; margin-top: 4px;">
    Strategy Lab forward test — dual pools from <code>live_forward.py</code>.
    Independent of the live agent / Supabase paper book.
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )

    pool_a = state.get("pool_A_trailing") or {}
    pool_b = state.get("pool_B_target") or {}
    eod = state.get("eod_summary") or {}
    winner = eod.get("winner") or (state.get("report") or {}).get("winner") or {}

    a_val = float(pool_a.get("value_usd") or pool_a.get("start_usd") or 0)
    b_val = float(pool_b.get("value_usd") or pool_b.get("start_usd") or 0)
    a_start = float(pool_a.get("start_usd") or 3000.0)
    b_start = float(pool_b.get("start_usd") or 3000.0)
    a_pnl = a_val - a_start
    b_pnl = b_val - b_start
    a_ret = (a_pnl / a_start * 100.0) if a_start else 0.0
    b_ret = (b_pnl / b_start * 100.0) if b_start else 0.0
    # Prefer stored win_rate_pct when present
    a_wr = pool_a.get("win_rate_pct")
    b_wr = pool_b.get("win_rate_pct")
    if a_wr is None and eod.get("pool_A"):
        a_wr = eod["pool_A"].get("win_rate_pct")
    if b_wr is None and eod.get("pool_B"):
        b_wr = eod["pool_B"].get("win_rate_pct")
    a_slots = int(pool_a.get("slots_open") or 0)
    b_slots = int(pool_b.get("slots_open") or 0)
    a_closed = len(pool_a.get("closed_trades") or [])
    b_closed = len(pool_b.get("closed_trades") or [])
    a_taken = int(pool_a.get("trades_taken") or a_closed)
    b_taken = int(pool_b.get("trades_taken") or b_closed)

    meta_bits = [
        f"date **{state.get('flag_date') or '—'}**",
        f"mode **{state.get('mode') or '—'}**",
        f"phase **{state.get('phase') or '—'}**",
        f"entry **{state.get('entry_model') or '—'}**",
        f"status **{state.get('status') or '—'}**",
        f"source **{src}**",
    ]
    if state.get("updated_at"):
        meta_bits.append(f"updated `{state['updated_at']}`")
    st.caption(" · ".join(meta_bits))

    # --- Out-of-Sample R²: backtest (static) vs forward (live rolling) ---
    _render_strategy_lab_r2_panel(state)

    # --- Who's ahead ---
    margin = abs(a_val - b_val)
    if a_val > b_val:
        ahead_label = pool_a.get("label") or "Strategy A (Trailing)"
        ahead_color = "#00AA44"
    elif b_val > a_val:
        ahead_label = pool_b.get("label") or "Strategy B (Target)"
        ahead_color = "#3d8bfd"
    else:
        ahead_label = "TIE"
        ahead_color = "#888888"
    # Prefer eod winner label when present and pools still match
    w_pool = winner.get("pool")
    if w_pool == "A_trailing" and a_val >= b_val:
        ahead_label = pool_a.get("label") or "Strategy A (Trailing)"
        margin = float(winner.get("margin_usd") or margin)
        ahead_color = "#00AA44"
    elif w_pool == "B_target" and b_val >= a_val:
        ahead_label = pool_b.get("label") or "Strategy B (Target)"
        margin = float(winner.get("margin_usd") or margin)
        ahead_color = "#3d8bfd"
    elif w_pool == "tie":
        ahead_label = "TIE"
        margin = 0.0
        ahead_color = "#888888"

    st.markdown(
        f"""
<div style="
    background: {ahead_color}18;
    border-left: 5px solid {ahead_color};
    border-radius: 6px;
    padding: 16px 20px;
    margin: 8px 0 16px 0;
">
  <div style="font-size: 26px; font-weight: 800; color: {ahead_color};">
    {"🤝 TIE" if ahead_label == "TIE" else f"🏆 {ahead_label} ahead by ${margin:,.2f}"}
  </div>
  <div style="font-size: 14px; color: #666; margin-top: 4px;">
    A ${a_val:,.2f} &nbsp;vs&nbsp; B ${b_val:,.2f}
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )

    # --- Side-by-side pool cards ---
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown(f"### {pool_a.get('label') or 'Strategy A (Trailing)'}")
        m1, m2, m3 = st.columns(3)
        m1.metric("Pool value", f"${a_val:,.2f}", f"{a_ret:+.2f}%")
        m2.metric("Realized P&L", f"${a_pnl:+,.2f}")
        m3.metric(
            "Win rate",
            f"{float(a_wr):.1f}%" if a_wr is not None else "—",
        )
        m4, m5 = st.columns(2)
        m4.metric("Open slots", f"{a_slots}/{LAB_MAX_SLOTS}")
        m5.metric("Closed trades", str(a_taken))
    with col_b:
        st.markdown(f"### {pool_b.get('label') or 'Strategy B (Target)'}")
        m1, m2, m3 = st.columns(3)
        m1.metric("Pool value", f"${b_val:,.2f}", f"{b_ret:+.2f}%")
        m2.metric("Realized P&L", f"${b_pnl:+,.2f}")
        m3.metric(
            "Win rate",
            f"{float(b_wr):.1f}%" if b_wr is not None else "—",
        )
        m4, m5 = st.columns(2)
        m4.metric("Open slots", f"{b_slots}/{LAB_MAX_SLOTS}")
        m5.metric("Closed trades", str(b_taken))

    # --- Equity curves (overlaid) ---
    st.markdown("### Equity curves")
    fig = go.Figure()
    for label, curve, color in (
        (
            pool_a.get("label") or "A Trailing",
            pool_a.get("equity_curve") or [],
            "#00AA44",
        ),
        (
            pool_b.get("label") or "B Target",
            pool_b.get("equity_curve") or [],
            "#3d8bfd",
        ),
    ):
        if not curve:
            continue
        xs = list(range(len(curve)))
        ys = [float(pt.get("value_usd") or 0) for pt in curve]
        hover = []
        for i, pt in enumerate(curve):
            ev = pt.get("event") or ""
            tk = pt.get("ticker") or ""
            hover.append(
                f"{label}<br>#{i} {ev}"
                + (f" {tk}" if tk else "")
                + f"<br>${float(pt.get('value_usd') or 0):,.2f}"
            )
        fig.add_trace(
            go.Scatter(
                x=xs,
                y=ys,
                mode="lines+markers",
                name=label,
                line=dict(color=color, width=2),
                hovertext=hover,
                hoverinfo="text",
            )
        )
    fig.update_layout(
        height=320,
        margin=dict(l=40, r=20, t=20, b=40),
        xaxis_title="Event #",
        yaxis_title="Pool value ($)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        hovermode="closest",
    )
    if fig.data:
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.caption("No equity curve points yet.")

    # --- Open positions ---
    st.markdown("### Open Positions")
    open_a = pool_a.get("open_positions") or {}
    open_b = pool_b.get("open_positions") or {}
    tickers_open = sorted(set(open_a.keys()) | set(open_b.keys()))
    if not tickers_open:
        st.caption("No open positions (all flat).")
    else:
        open_rows = []
        for t in tickers_open:
            oa = open_a.get(t) or {}
            ob = open_b.get(t) or {}
            open_rows.append({
                "Ticker": t,
                "A entry": (
                    f"${float(oa['entry_price']):.4f}"
                    if oa.get("entry_price") is not None
                    else "—"
                ),
                "A shares": oa.get("shares") if oa else "—",
                "A unrealized": "open (no mark)" if oa else "—",
                "B entry": (
                    f"${float(ob['entry_price']):.4f}"
                    if ob.get("entry_price") is not None
                    else "—"
                ),
                "B shares": ob.get("shares") if ob else "—",
                "B unrealized": "open (no mark)" if ob else "—",
                "sweep_reclaim": (
                    oa.get("sweep_reclaim")
                    or ob.get("sweep_reclaim")
                    or "—"
                ),
            })
        st.dataframe(
            pd.DataFrame(open_rows),
            use_container_width=True,
            hide_index=True,
        )

    # --- Closed trades (side-by-side A vs B) ---
    st.markdown("### Closed Trades")
    # Prefer report.per_ticker (aligned A/B); else zip closed_trades by ticker.
    per = (state.get("report") or {}).get("per_ticker") or []
    closed_rows = []
    if per:
        for row in per:
            if row.get("skipped"):
                continue
            ar = row.get("A") or {}
            br = row.get("B") or {}
            if not ar.get("taken") and not br.get("taken"):
                continue
            closed_rows.append({
                "Ticker": row.get("ticker"),
                "Entry": (
                    f"${float(row['entry_price']):.4f}"
                    if row.get("entry_price") is not None
                    else "—"
                ),
                "A exits": _lab_tranche_exits(ar.get("tranches")),
                "A reason": _lab_exit_summary(ar.get("exit_reason_counts")),
                "A ret%": (
                    f"{float(ar['return_pct']):+.2f}%"
                    if ar.get("return_pct") is not None
                    else "—"
                ),
                "B exits": _lab_tranche_exits(br.get("tranches")),
                "B reason": _lab_exit_summary(br.get("exit_reason_counts")),
                "B ret%": (
                    f"{float(br['return_pct']):+.2f}%"
                    if br.get("return_pct") is not None
                    else "—"
                ),
                "sweep_reclaim": row.get("sweep_reclaim") or "—",
            })
    else:
        # Fallback: index closed_trades by ticker
        by_a = {
            t.get("ticker"): t
            for t in (pool_a.get("closed_trades") or [])
            if t.get("taken")
        }
        by_b = {
            t.get("ticker"): t
            for t in (pool_b.get("closed_trades") or [])
            if t.get("taken")
        }
        for t in sorted(set(by_a) | set(by_b)):
            ar = by_a.get(t) or {}
            br = by_b.get(t) or {}
            entry = ar.get("entry_price", br.get("entry_price"))
            closed_rows.append({
                "Ticker": t,
                "Entry": f"${float(entry):.4f}" if entry is not None else "—",
                "A exits": _lab_tranche_exits(ar.get("tranches")),
                "A reason": _lab_exit_summary(ar.get("exit_reason_counts")),
                "A ret%": (
                    f"{float(ar['return_pct']):+.2f}%"
                    if ar.get("return_pct") is not None
                    else "—"
                ),
                "B exits": _lab_tranche_exits(br.get("tranches")),
                "B reason": _lab_exit_summary(br.get("exit_reason_counts")),
                "B ret%": (
                    f"{float(br['return_pct']):+.2f}%"
                    if br.get("return_pct") is not None
                    else "—"
                ),
                "sweep_reclaim": (
                    ar.get("sweep_reclaim") or br.get("sweep_reclaim") or "—"
                ),
            })

    if closed_rows:
        st.dataframe(
            pd.DataFrame(closed_rows),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("No closed trades yet.")

    # Candidates footer
    n_c = state.get("n_candidates")
    scan = state.get("scan") or {}
    if n_c or scan.get("tickers"):
        st.caption(
            f"Candidates: {n_c or len(scan.get('tickers') or [])} — "
            f"{', '.join(scan.get('tickers') or [])}"
        )


GLOSSARY_PATH = ROOT / "GLOSSARY.md"


def tab_glossary() -> None:
    """
    Render repo-root GLOSSARY.md — same content as the markdown file.
    Leave agent code untouched; best-effort if the file is missing.
    """
    st.header("📖 Glossary")
    st.caption(
        "Q-ALPHA / Strategy Lab terms · same content as `GLOSSARY.md` in the repo root"
    )
    if not GLOSSARY_PATH.exists():
        st.warning(
            "GLOSSARY.md not found in the repo root. "
            "Pull latest main or add the file locally."
        )
        return
    try:
        body = GLOSSARY_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        st.error(f"Could not read GLOSSARY.md: {exc}")
        return
    # Skip the duplicate H1 when the tab already has a header.
    lines = body.splitlines()
    if lines and lines[0].startswith("# "):
        body = "\n".join(lines[1:]).lstrip("\n")
    st.markdown(body)


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
    trades, pool_history, health = _safe_load()
    render_header()

    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
        "📊 Live Status",
        "📋 Trade Log",
        "📈 Performance",
        "🔧 System Health",
        "📓 Daily Reviews",
        "🔬 Ticker Profiles",
        "🧪 Strategy Lab",
        "📖 Glossary",
    ])

    with tab1:
        tab_live_status(trades, pool_history)
    with tab2:
        tab_trade_log(trades)
    with tab3:
        tab_performance(trades, pool_history)
    with tab4:
        tab_system_health(health)
    with tab5:
        tab_daily_reviews()
    with tab6:
        tab_ticker_profiles()
    with tab7:
        tab_strategy_lab()
    with tab8:
        tab_glossary()

    render_footer()


if __name__ == "__main__":
    main()
