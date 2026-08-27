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
# Supabase ticker_profiles (anon) then profiles/*.json; "Refresh profile" is
# on-demand only — never on load/autorefresh. Strategy Lab prefers Supabase
# strategy_lab_state (anon); local fallback is strategy_lab/results/forward_state.json
# (SIM paper only). Cadence: strategy_lab/DASHBOARD_FRESHNESS.md (marks ~30m;
# settle ~16:20 ET).
# =============================================================================
from __future__ import annotations

import json
import math
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
    list_profile_option_tickers,
    load_profile,
    load_todays_watchlist,
    profile_path,
    profile_source_label,
)
from dashboard_theme import (
    ACCENT,
    BG,
    BORDER,
    MUTED,
    NEGATIVE,
    POSITIVE,
    TEXT,
    WARN,
    brand_block,
    footer_rule,
    glossary_scope_marker,
    inject_theme,
    lab_ahead_banner,
    lab_sim_banner,
    regime_banner,
    section_header,
    status_panel,
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
inject_theme()

# Autorefresh so Live Status + Strategy Lab pick up Supabase marks without
# manual rerun / redeploy. 90s sits in the 60–120s band; Lab marks push ~30m.
st_autorefresh(interval=90 * 1000, key="main_refresh")


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
    if "status" in df.columns:
        df["status"] = df["status"].astype(str).str.upper().str.strip()
    for col in (
        "entry_price", "stop_price", "target_2r", "pnl_dollars", "pnl_pct",
        "current_price", "r_multiple", "dist_to_stop", "dist_to_target",
    ):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _safe_float(x, default: float = 0.0) -> float:
    """
    Coerce to float; None / NaN / Inf / blank → default.

    Needed because pandas NaN is truthy, so `x or default` keeps NaN and
    f-strings then render '$nan' or raise on non-str last_updated.
    """
    try:
        if x is None:
            return default
        if isinstance(x, str) and not x.strip():
            return default
        # pd.isna covers None, NaN, NaT; skip non-scalars.
        try:
            if pd.isna(x):
                return default
        except (TypeError, ValueError):
            pass
        v = float(x)
        if not math.isfinite(v):
            return default
        return v
    except (TypeError, ValueError):
        return default


def _updated_hhmm_et(updated) -> str | None:
    """HH:MM from ISO-ish last_updated, or None if not a usable string."""
    if not isinstance(updated, str):
        return None
    s = updated.strip()
    if len(s) < 16:
        return None
    return s[11:16]


def _open_mark_notional(trade) -> float:
    """
    Display market value of one open: mark×shares when current_price is
    valid, else entry notional (position_size / position_value / entry×shares).
    Display-only — does not change pool_state accounting.
    """
    entry = _safe_float(trade.get("entry_price"), 0.0)
    shares = int(_safe_float(trade.get("shares_total"), 0.0))
    entry_notional = _safe_float(trade.get("position_size"), float("nan"))
    if not math.isfinite(entry_notional) or entry_notional <= 0:
        entry_notional = _safe_float(trade.get("position_value"), float("nan"))
    if not math.isfinite(entry_notional) or entry_notional <= 0:
        entry_notional = entry * shares if entry > 0 and shares > 0 else 0.0

    mark = _safe_float(trade.get("current_price"), float("nan"))
    if math.isfinite(mark) and mark > 0 and shares > 0:
        return mark * shares

    pnl = _safe_float(trade.get("pnl_dollars"), float("nan"))
    if math.isfinite(pnl) and entry_notional > 0:
        return entry_notional + pnl
    return float(entry_notional)


def _open_entry_notional(trade) -> float:
    """Cost-book notional for one open (entry×shares / position_size). Never mark."""
    entry = _safe_float(trade.get("entry_price"), 0.0)
    shares = int(_safe_float(trade.get("shares_total"), 0.0))
    notional = _safe_float(trade.get("position_size"), float("nan"))
    if not math.isfinite(notional) or notional <= 0:
        notional = _safe_float(trade.get("position_value"), float("nan"))
    if not math.isfinite(notional) or notional <= 0:
        notional = entry * shares if entry > 0 and shares > 0 else 0.0
    return float(notional) if math.isfinite(notional) else 0.0


def _display_pool_kpis(
    pool_history: list,
    open_df: pd.DataFrame,
) -> tuple[float, float, float, float, float]:
    """
    Live Status KPIs — display only; never mutates pool_state / snapshots.

    Formulas
    --------
    Pool (cost book) = cash + deployed
      cash, deployed from latest pool_snapshots row when trustworthy.
    P&L = (cash + Σ open mark notional) − starting_pool
      mark notional = current_price×shares when valid, else entry notional.

    Stale-snapshot guard
    --------------------
    Mid-day fills may not have upserted pool_snapshots yet, so Cloud can show
    cash≈$3000 / deployed≈0 while Open Positions already lists fills. In that
    case treating snap.pool as residual cash and adding open marks double-counts
    (fake +29% Pool Value). When deployed≈0 but opens exist:
      deployed := Σ open entry notionals
      cash     := starting − deployed   (cost book; ignores unrealized)
      Pool     := cash + deployed = starting (until realized PnL exists)
      P&L      := cash + mark_notional − starting
               = mark_notional − deployed  (true MTM vs cost)

    Returns (pool_book, pnl_dollar, cash, deployed_book, starting).
    """
    starting = STARTING_POOL
    cash = STARTING_POOL
    deployed_book = 0.0
    if pool_history:
        snap = pool_history[-1] or {}
        cash = _safe_float(snap.get("pool"), STARTING_POOL)
        deployed_book = _safe_float(snap.get("deployed"), 0.0)
        starting = _safe_float(snap.get("starting_pool"), STARTING_POOL)

    has_opens = open_df is not None and not open_df.empty
    opens_entry = (
        sum(_open_entry_notional(row) for _, row in open_df.iterrows())
        if has_opens else 0.0
    )
    opens_mark = (
        sum(_open_mark_notional(row) for _, row in open_df.iterrows())
        if has_opens else 0.0
    )

    # Snap undeployed but ledger shows opens → reconstruct cost book from opens.
    snap_undeployed = deployed_book < 1.0 and has_opens and opens_entry > 0
    if snap_undeployed:
        deployed_book = opens_entry
        cash = max(0.0, starting - deployed_book)

    pool_book = cash + deployed_book
    # MTM equity for P&L only — never shown as "Pool".
    mtm_equity = cash + (opens_mark if has_opens else deployed_book)
    pnl_dollar = mtm_equity - starting
    return pool_book, pnl_dollar, cash, deployed_book, starting


def _oneshot_polygon_mark(ticker: str) -> float | None:
    """
    Fail-soft live mark when Supabase current_price is null (until next monitor).
    One attempt per ticker per Streamlit session.
    """
    t = str(ticker or "").upper().strip()
    if not t:
        return None
    cache_key = f"_oneshot_mark_{t}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]
    price: float | None = None
    try:
        import requests

        api_key = ensure_polygon_key_from_secrets()
        if not api_key:
            st.session_state[cache_key] = None
            return None
        url = (
            f"https://api.polygon.io/v2/snapshot/"
            f"locale/us/markets/stocks/tickers/{t}"
        )
        resp = requests.get(url, params={"apiKey": api_key}, timeout=5)
        resp.raise_for_status()
        data = resp.json().get("ticker") or {}
        last = (data.get("lastTrade") or {}).get("p")
        day_c = (data.get("day") or {}).get("c")
        prev_c = (data.get("prevDay") or {}).get("c")
        for raw in (last, day_c, prev_c):
            px = _safe_float(raw, 0.0)
            if px > 0:
                price = px
                break
    except Exception as exc:
        print(f"  oneshot mark failed ({t}): {exc}")
        price = None
    st.session_state[cache_key] = price
    return price


# Statuses that must never appear in Live Status Open Positions / open KPI.
_NON_OPEN_STATUSES = frozenset({
    "NEVER_FILLED", "REJECTED_INELIGIBLE", "REJECTED_NO_FILL",
    "SKIPPED", "CLOSED",
})


def _open_positions_df(df: pd.DataFrame) -> pd.DataFrame:
    """Only real bracket opens — NEVER_FILLED / rejects excluded."""
    if df.empty or "status" not in df.columns:
        return pd.DataFrame()
    mask = df["status"].isin(OPEN_STATUSES) & ~df["status"].isin(_NON_OPEN_STATUSES)
    return df.loc[mask].copy()


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
    color = POSITIVE if val >= 0 else NEGATIVE
    return f"color: {color}; font-weight: 600"


def _color_gap(val: str) -> str:
    try:
        pct = float(str(val).replace("%", "").replace("+", "").strip())
        if pct > 0:
            return f"color: {POSITIVE}; font-weight: 600"
        if pct < 0:
            return f"color: {NEGATIVE}; font-weight: 600"
        return f"color: {MUTED}"
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
    if status in {"NEVER_FILLED", "REJECTED_INELIGIBLE", "REJECTED_NO_FILL"}:
        return "🚫 Never filled"
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
        v = _safe_float(val, float("nan"))
        if not math.isfinite(v):
            return "—"
        return f"${v:.2f}"

    entry = trade.get("entry_price")
    stop = trade.get("stop_price")
    target = trade.get("target_2r")
    pnl_f = _safe_float(trade.get("pnl_dollars"), float("nan"))
    pct_f = _safe_float(trade.get("pnl_pct"), float("nan"))

    pnl_str = "—"
    if math.isfinite(pnl_f):
        pct_part = ""
        if math.isfinite(pct_f):
            # Trades store fraction (0.012) or already-percent; tolerate both.
            disp = pct_f * 100.0 if abs(pct_f) <= 1.0 else pct_f
            pct_part = f" ({disp:+.1f}%)"
        pnl_str = f"${pnl_f:+.2f}{pct_part}"

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
            return f"color: {MUTED}"
        if text.startswith("$-") or "-$" in text or text.startswith("-$"):
            return f"color: {NEGATIVE}; font-weight: 600"
        if "+$" in text or text.startswith("$+"):
            return f"color: {POSITIVE}; font-weight: 600"
        if text.startswith("$") and not text.startswith("$-"):
            rest = text[1:].lstrip("+")
            if rest and rest[0].isdigit():
                return f"color: {POSITIVE}; font-weight: 600"
        return ""

    def _rr_cell(val: str) -> str:
        text = str(val)
        if text in {"", "—", "None"}:
            return f"color: {MUTED}"
        if text == "n/a":
            return f"color: {MUTED}"
        if "⚠️" in text:
            return f"color: {WARN}; font-weight: 600"
        return f"color: {POSITIVE}"

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
        live_et = ""
        try:
            last_intraday = get_sync().get_last_health("intraday_monitor")
            if last_intraday and last_intraday.get("last_run"):
                live_et = last_intraday["last_run"][11:16]
        except Exception:
            pass
        brand_block(live_et)
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

    open_df = _open_positions_df(df)
    pool_book, pnl_dollar, cash, deployed_book, starting = _display_pool_kpis(
        pool_history, open_df,
    )
    pnl_pct = (pnl_dollar / starting) * 100 if starting else 0.0
    open_pos = len(open_df)
    t3_count = len(open_df[open_df["status"] == "T3_TRAIL"]) if not open_df.empty else 0

    closed = df[df["status"] == "CLOSED"] if not df.empty else pd.DataFrame()
    # Total Trades = closed exits only (win rate denominator); NEVER_FILLED excluded.
    total_trades = len(closed)
    winning_trades = len(closed[closed["pnl_dollars"] > 0]) if not closed.empty else 0
    losing_trades = total_trades - winning_trades
    win_rate = winning_trades / total_trades if total_trades else 0

    with st.container(border=True):
        section_header("Session KPIs", "Pool, slots, and hit rate")
        # Pool = cost book (cash+deployed). P&L = MTM vs $3000 — never merge into one metric.
        col1, col2, col3, col4, col5, col6 = st.columns(6)
        with col1:
            st.metric("Pool", f"${pool_book:,.2f}")
            st.caption(
                f"Cash ${cash:,.2f} · Deployed ${deployed_book:,.2f}"
            )
        with col2:
            st.metric(
                "P&L",
                f"${pnl_dollar:+,.2f}",
                f"{pnl_pct:+.1f}% vs ${starting:,.0f}",
                delta_color="normal",
            )
        with col3:
            st.metric(
                "Open Positions",
                f"{open_pos}/{MAX_SLOTS} slots",
                f"{MAX_SLOTS - open_pos} available",
            )
        with col4:
            st.metric("T3 Trailing", f"{t3_count} free-running", "slots released")
        with col5:
            st.metric(
                "Total Trades",
                str(total_trades),
                f"{winning_trades}W / {losing_trades}L",
            )
        with col6:
            st.metric("Win Rate", f"{win_rate:.0%}", "Base rate: ~39%")

    # Regime from today's watchlist (not legacy daily_scans).
    spy_regime = (watch_rows[0].get("regime") if watch_rows else None) or "UNKNOWN"
    vix_regime = "NORMAL"
    sizing_pct = "100%" if vix_regime == "NORMAL" else "50%"
    regime_banner(spy_regime, vix_regime, sizing_pct)

    with st.container(border=True):
        section_header("Open Positions", "Live marks vs stop / 2R target")
        if open_df.empty:
            st.info("No open positions.")
        else:
            for _, trade in open_df.iterrows():
                ticker = str(trade.get("ticker") or "")
                entry_price = _safe_float(trade.get("entry_price"), 0.0)
                stop_price = _safe_float(trade.get("stop_price"), 0.0)
                target_2r = _safe_float(trade.get("target_2r"), 0.0)
                shares = int(_safe_float(trade.get("shares_total"), 0.0))

                raw_mark = _safe_float(trade.get("current_price"), float("nan"))
                if not math.isfinite(raw_mark) or raw_mark <= 0:
                    fetched = _oneshot_polygon_mark(ticker)
                    if fetched is not None and fetched > 0:
                        raw_mark = fetched
                current_price = (
                    raw_mark
                    if math.isfinite(raw_mark) and raw_mark > 0
                    else entry_price
                )

                pnl_dollars = _safe_float(trade.get("pnl_dollars"), float("nan"))
                pnl_pct_val = _safe_float(trade.get("pnl_pct"), float("nan"))
                r_mult = _safe_float(trade.get("r_multiple"), float("nan"))
                dist_stop = _safe_float(trade.get("dist_to_stop"), float("nan"))

                # Recompute display P&L when marks were missing/NaN but price known.
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
                    if not math.isfinite(to_go):
                        to_go = 0.0
                    st.metric(
                        "Target",
                        f"${target_2r:.2f}",
                        f"{to_go:.1%} to go",
                    )

                with col5:
                    price_ok = (
                        math.isfinite(current_price)
                        and current_price > 0
                        and math.isfinite(stop_price)
                        and math.isfinite(target_2r)
                        and target_2r > stop_price
                    )
                    if price_ok:
                        progress = (
                            (current_price - stop_price) / (target_2r - stop_price)
                        )
                        if math.isfinite(progress):
                            progress = max(0.0, min(1.0, progress))
                            st.progress(
                                progress,
                                text=(
                                    f"Stop ${stop_price:.2f} ──── "
                                    f"${current_price:.2f} ──── "
                                    f"Target ${target_2r:.2f}"
                                ),
                            )
                    hhmm = _updated_hhmm_et(trade.get("last_updated"))
                    if hhmm:
                        st.caption(f"Updated: {hhmm} ET")
                    else:
                        st.caption("Updated: —")

                st.divider()

    with st.container(border=True):
        section_header("Today's Watchlist", "Agent candidates for the session")
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
    never = (
        df[df["status"].isin(["NEVER_FILLED", "REJECTED_INELIGIBLE", "REJECTED_NO_FILL"])]
        if not df.empty else pd.DataFrame()
    )

    with st.container(border=True):
        section_header("Closed Trades", "Full exit log")
        if closed.empty:
            st.info("No closed trades yet.")
        else:
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

    if not never.empty:
        with st.container(border=True):
            section_header(
                "Never filled / IB rejected",
                "Not opens — no pool capital; not counted as trades",
            )
            show = never.rename(columns={
                "entry_date": "Date",
                "ticker": "Ticker",
                "status": "Status",
                "exit_reason": "Reason",
                "skip_reason": "Detail",
            })
            cols = [c for c in ["Date", "Ticker", "Status", "Reason", "Detail"] if c in show.columns]
            st.dataframe(show[cols], use_container_width=True, hide_index=True)

    if closed.empty:
        return

    winners = closed[closed["pnl_dollars"] > 0]
    losers = closed[closed["pnl_dollars"] <= 0]
    with st.container(border=True):
        section_header("Summary Stats")
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
    with st.container(border=True):
        section_header("Equity Curve", "Pool value over time")
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
                line=dict(color=ACCENT, width=2.5),
                fill="tozeroy",
                fillcolor="rgba(45, 212, 191, 0.12)",
            ))
            fig.add_hline(
                y=STARTING_POOL,
                line_dash="dash",
                line_color=MUTED,
                annotation_text="Starting Capital $3,000",
            )
            fig.update_layout(
                title="Portfolio Equity Curve",
                xaxis_title="Date",
                yaxis_title="Portfolio Value ($)",
                plot_bgcolor=BG,
                paper_bgcolor=BG,
                font=dict(color=TEXT, family="Sora"),
                yaxis=dict(gridcolor=BORDER),
                xaxis=dict(gridcolor=BORDER),
                hovermode="x unified",
                height=450,
                margin=dict(l=40, r=20, t=50, b=40),
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No pool history yet.")

    df = _trades_df(trades)
    closed = df[df["status"] == "CLOSED"] if not df.empty else pd.DataFrame()

    with st.container(border=True):
        section_header("P&L Per Trade")
        if not closed.empty:
            closed_plot = closed.copy()
            closed_plot["label"] = closed_plot["entry_date"] + " " + closed_plot["ticker"]
            fig2 = px.bar(
                closed_plot,
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

    with st.container(border=True):
        section_header("Monthly Returns")
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
    with st.container(border=True):
        section_header("Component Status", "Last heartbeat per job")
        components = {
            "morning_scan": {"icon": "🔍", "name": "Morning Scanner"},
            "eod_monitor": {"icon": "📊", "name": "EOD Monitor"},
            "approval_processor": {"icon": "✅", "name": "Approval Processor"},
        }

        for key, info in components.items():
            last = get_last_health(key, health)
            if last:
                time_ago = get_time_ago(last.get("created_at") or last.get("last_run", ""))
                status_text = last.get("status", "UNKNOWN")
                message = last.get("message", "—")
                ok = last.get("status") == "OK"
                tone = "up" if ok else "down"
                status_icon = "🟢" if ok else "🔴"
            else:
                time_ago = "Never"
                status_text = "Never run"
                message = "—"
                tone = "muted"
                status_icon = "⚫"

            status_panel(
                info["name"],
                status_text,
                time_ago,
                message,
                tone=tone,
                icon=info["icon"],
                status_icon=status_icon,
            )

    with st.container(border=True):
        section_header("Recent Activity Log")
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
    with st.container(border=True):
        section_header("Daily Trade Reviews", "AI analysis of each trading session")

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
    with st.container(border=True):
        section_header(
            "Ticker Profiles",
            "Analog MAE/MFE · informational only · Supabase-first (no auto Polygon)",
        )
        st.info(
            "Profiles are **precomputed** at the 9:20 scan (and via Refresh). "
            "This tab does **not** call Polygon on load. Cloud reads "
            "`ticker_profiles` via anon key; local JSON is fallback."
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

    options = list_profile_option_tickers(watch_tickers)

    if watch_err:
        st.warning(f"Watchlist unavailable: {watch_err}")

    if not options:
        st.warning(
            "No watchlist tickers and no cached/remote profiles yet. "
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
            help="Watchlist ∪ Supabase ticker_profiles ∪ local JSON.",
        )
    with col_meta:
        path = profile_path(ticker)
        peek = load_profile(ticker)
        src = profile_source_label(peek, ticker)
        st.caption(f"Source: **{src}**")
        if path.exists():
            mtime = path.stat().st_mtime
            st.caption(
                f"Local file: `{path.relative_to(ROOT)}` · "
                f"updated {datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')}"
            )
        elif src == "supabase":
            st.caption("No local JSON — using Supabase row (Cloud path).")
        else:
            st.caption(f"No cache at `{path.relative_to(ROOT)}`")

    do_refresh = False
    if ensure_polygon_key_from_secrets():
        # On-demand Polygon refresh — only when a key is available (local / secrets).
        # Streamlit Cloud usually has no key; profiles come from Supabase.
        if st.button(
            f"🔄 Refresh profile — {ticker}",
            type="primary",
            key="profile_refresh_btn",
            help="Runs build_ticker_profile (Polygon 1-min). Slow. Writes local + Supabase.",
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
                    f"{', flag=' + repr(hf) if hf else ''}) · local + Supabase"
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Profile compute failed: {exc}")
                return

    profile = load_profile(ticker)
    if profile is None:
        st.warning(
            f"No precomputed profile for **{ticker}** (source: missing). "
            f"Click **Refresh profile** to generate one (expensive), or wait for "
            f"the 9:20 agent upsert."
        )
        return

    st.caption(
        f"Loaded from **{profile_source_label(profile, ticker)}**"
        + (
            f" · updated `{profile.get('_profile_updated_at')}`"
            if profile.get("_profile_updated_at")
            else ""
        )
    )
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

    with st.container(border=True):
        section_header(display_name, f"Weighting: {weighting}")
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


def _lab_fmt_money(x) -> str:
    """Format a dollar amount or em dash."""
    if x is None:
        return "—"
    try:
        return f"${float(x):,.4f}"
    except (TypeError, ValueError):
        return "—"


def _lab_unrealized_cell(pos: dict | None) -> str:
    """
    Unrealized $ and % from mark_price vs entry for one Lab pool leg.
    Missing mark → 'no mark' (not a Live Status T3 KPI).
    """
    if not pos:
        return "—"
    entry = pos.get("entry_price")
    mark = pos.get("mark_price")
    shares = pos.get("shares")
    if entry is None or mark is None:
        return "no mark"
    try:
        e = float(entry)
        m = float(mark)
        sh = float(shares) if shares is not None else 0.0
    except (TypeError, ValueError):
        return "no mark"
    if e <= 0:
        return "no mark"
    pnl = (m - e) * sh
    pct = (m / e - 1.0) * 100.0
    return f"${pnl:+,.2f} ({pct:+.1f}%)"


def _lab_residuals_cell(pos: dict | None) -> str:
    """residual_tranche_ids + FULL vs T4-only slot tag."""
    if not pos:
        return "—"
    ids = pos.get("residual_tranche_ids")
    if isinstance(ids, list) and ids:
        tag = "+".join(str(x) for x in ids)
    else:
        tag = "—"
    full = pos.get("counts_as_full_slot")
    if full is True:
        slot = "FULL"
    elif full is False:
        slot = "T4-only"
    else:
        slot = "?"
    return f"{tag} · {slot}"


_LAB_FULL_TRANCHE_IDS = frozenset({"T1", "T2", "T3"})


def _lab_is_t4_only_runner(pos: dict | None) -> bool:
    """
    Lab trailing runner: residual is T4-only (no T1/T2/T3 still working).

    Uses residual_tranche_ids when present; else counts_as_full_slot is False.
    Lab semantics only — not Live agent T3 trailing.
    """
    if not pos:
        return False
    ids = pos.get("residual_tranche_ids")
    if isinstance(ids, list) and ids:
        residual = {str(x) for x in ids}
        return "T4" in residual and not (residual & _LAB_FULL_TRANCHE_IDS)
    return pos.get("counts_as_full_slot") is False


def _lab_trail_runner_stats(pool: dict) -> tuple[int, int]:
    """
    Strategy A display: (t4_only_runner_count, full_slots_used).

    full_slots_used prefers counts_as_full_slot; falls back to residual ∩ {T1,T2,T3}.
    """
    opens = pool.get("open_positions") or {}
    t4_only = 0
    full_slots = 0
    for pos in opens.values():
        if not isinstance(pos, dict):
            continue
        if _lab_is_t4_only_runner(pos):
            t4_only += 1
        full = pos.get("counts_as_full_slot")
        if full is True:
            full_slots += 1
        elif full is False:
            continue
        else:
            ids = pos.get("residual_tranche_ids")
            if isinstance(ids, list) and ids:
                residual = {str(x) for x in ids}
                if residual & _LAB_FULL_TRANCHE_IDS:
                    full_slots += 1
            else:
                # Pre-settle open with no residuals yet → full slot.
                full_slots += 1
    return t4_only, full_slots


def _lab_closed_side_cells(rec: dict | None) -> dict:
    """Display cells for one pool's closed_trades record (or empty)."""
    if not rec or not rec.get("taken", True):
        return {
            "exits": "—",
            "reason": "—",
            "ret%": "—",
            "pnl": "—",
        }
    pnl = rec.get("pnl_usd")
    try:
        pnl_s = f"${float(pnl):+,.2f}" if pnl is not None else "—"
    except (TypeError, ValueError):
        pnl_s = "—"
    ret = rec.get("return_pct")
    try:
        ret_s = f"{float(ret):+.2f}%" if ret is not None else "—"
    except (TypeError, ValueError):
        ret_s = "—"
    return {
        "exits": _lab_tranche_exits(rec.get("tranches")),
        "reason": _lab_exit_summary(rec.get("exit_reason_counts")),
        "ret%": ret_s,
        "pnl": pnl_s,
    }


def _lab_align_closed_trades(pool_a: dict, pool_b: dict) -> list[dict]:
    """
    Align A/B closed_trades by (ticker, flag_date).

    Never includes tickers that are still open in that pool for the same
    flag_date. Does not use report.per_ticker (which mixes day entries / opens).
    """
    open_a = pool_a.get("open_positions") or {}
    open_b = pool_b.get("open_positions") or {}

    def _index(pool: dict, opens: dict) -> dict[tuple[str, str], dict]:
        out: dict[tuple[str, str], dict] = {}
        for rec in pool.get("closed_trades") or []:
            if not rec.get("taken", True):
                continue
            ticker = str(rec.get("ticker") or "").upper()
            if not ticker:
                continue
            fd = str(rec.get("flag_date") or "")[:10]
            # Skip if still open on same flag_date in this pool.
            op = opens.get(ticker) or {}
            if op and str(op.get("flag_date") or "")[:10] == fd:
                continue
            if ticker in opens and not op.get("flag_date") and not fd:
                continue
            out[(ticker, fd)] = rec
        return out

    by_a = _index(pool_a, open_a)
    by_b = _index(pool_b, open_b)
    still_open = set(open_a) | set(open_b)
    keys = sorted(set(by_a) | set(by_b), key=lambda k: (k[1], k[0]))
    rows: list[dict] = []
    for ticker, fd in keys:
        # Never list a name that still has any Lab residual open.
        if ticker in still_open:
            continue
        ar = by_a.get((ticker, fd))
        br = by_b.get((ticker, fd))
        entry = None
        if ar and ar.get("entry_price") is not None:
            entry = ar.get("entry_price")
        elif br and br.get("entry_price") is not None:
            entry = br.get("entry_price")
        ac = _lab_closed_side_cells(ar)
        bc = _lab_closed_side_cells(br)
        settled = (
            (ar or {}).get("settled_at")
            or (br or {}).get("settled_at")
            or ""
        )
        rows.append({
            "Date": fd or "—",
            "Ticker": ticker,
            "Entry": _lab_fmt_money(entry),
            "A exits": ac["exits"],
            "A reason": ac["reason"],
            "A ret%": ac["ret%"],
            "A pnl": ac["pnl"],
            "B exits": bc["exits"],
            "B reason": bc["reason"],
            "B ret%": bc["ret%"],
            "B pnl": bc["pnl"],
            "sweep": (
                (ar or {}).get("sweep_reclaim")
                or (br or {}).get("sweep_reclaim")
                or "—"
            ),
            "_settled_at": settled,
        })
    return rows


def _lab_chrono_closed_rows(pool_a: dict, pool_b: dict) -> list[dict]:
    """Flat chronological closed list (A and B as separate rows) for Lab Trade Log."""
    rows: list[dict] = []
    for pool_label, pool in (("A", pool_a), ("B", pool_b)):
        opens = pool.get("open_positions") or {}
        for rec in pool.get("closed_trades") or []:
            if not rec.get("taken", True):
                continue
            ticker = str(rec.get("ticker") or "").upper()
            fd = str(rec.get("flag_date") or "")[:10]
            op = opens.get(ticker) or {}
            if op and str(op.get("flag_date") or "")[:10] == fd:
                continue
            cells = _lab_closed_side_cells(rec)
            rows.append({
                "Date": fd or "—",
                "Pool": pool_label,
                "Ticker": ticker,
                "Entry": _lab_fmt_money(rec.get("entry_price")),
                "Reason": cells["reason"],
                "Ret%": cells["ret%"],
                "P&L": cells["pnl"],
                "Exits": cells["exits"],
                "Sweep": rec.get("sweep_reclaim") or "—",
                "_settled_at": str(rec.get("settled_at") or ""),
            })
    rows.sort(key=lambda r: (r.get("Date") or "", r.get("_settled_at") or "", r.get("Ticker") or ""))
    for r in rows:
        r.pop("_settled_at", None)
    return rows


def _lab_today_entry_rows(state: dict) -> list[dict]:
    """Today's Lab candidates/entries with open vs closed status (not agent book)."""
    flag = str(state.get("flag_date") or "")[:10]
    pool_a = state.get("pool_A_trailing") or {}
    pool_b = state.get("pool_B_target") or {}
    open_a = pool_a.get("open_positions") or {}
    open_b = pool_b.get("open_positions") or {}
    closed_a = {
        str(r.get("ticker") or "").upper()
        for r in (pool_a.get("closed_trades") or [])
        if str(r.get("flag_date") or "")[:10] == flag and r.get("taken", True)
    }
    closed_b = {
        str(r.get("ticker") or "").upper()
        for r in (pool_b.get("closed_trades") or [])
        if str(r.get("flag_date") or "")[:10] == flag and r.get("taken", True)
    }

    tickers: list[str] = []
    seen: set[str] = set()
    per = (state.get("report") or {}).get("per_ticker") or []
    for row in per:
        t = str(row.get("ticker") or "").upper()
        if t and t not in seen:
            tickers.append(t)
            seen.add(t)
    scan = state.get("scan") or {}
    for t in scan.get("tickers") or []:
        u = str(t or "").upper()
        if u and u not in seen:
            tickers.append(u)
            seen.add(u)
    for t in list(open_a) + list(open_b) + list(closed_a) + list(closed_b):
        u = str(t or "").upper()
        if u and u not in seen:
            tickers.append(u)
            seen.add(u)

    def _status(ticker: str, opens: dict, closed: set[str]) -> str:
        if ticker in opens:
            pos = opens[ticker] or {}
            residuals = pos.get("residual_tranche_ids") or []
            if residuals == ["T4"] or (
                isinstance(residuals, list)
                and len(residuals) == 1
                and residuals[0] == "T4"
            ):
                return "OPEN (T4)"
            return "OPEN"
        if ticker in closed:
            return "CLOSED"
        return "—"

    rows = []
    for t in tickers:
        per_row = next(
            (r for r in per if str(r.get("ticker") or "").upper() == t),
            None,
        )
        skipped = bool(per_row and per_row.get("skipped"))
        entry = None
        if per_row and per_row.get("entry_price") is not None:
            entry = per_row.get("entry_price")
        elif t in open_a:
            entry = (open_a[t] or {}).get("entry_price")
        elif t in open_b:
            entry = (open_b[t] or {}).get("entry_price")
        rows.append({
            "Date": flag or "—",
            "Ticker": t,
            "Entry": _lab_fmt_money(entry),
            "A status": "SKIP" if skipped else _status(t, open_a, closed_a),
            "B status": "SKIP" if skipped else _status(t, open_b, closed_b),
            "Sweep": (
                (per_row or {}).get("sweep_reclaim")
                or (open_a.get(t) or {}).get("sweep_reclaim")
                or (open_b.get(t) or {}).get("sweep_reclaim")
                or "—"
            ),
        })
    return rows


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
    section_header(
        "Out-of-Sample R²",
        "Negative R² = worse than predicting average MFE; small-N is noise",
    )
    st.caption(
        f"R² needs a meaningful sample (>={OOS_R2_MIN_N}) to be trustworthy. "
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
    lab_sim_banner()

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
    stamp_bits = []
    if state.get("updated_at"):
        stamp_bits.append(f"updated `{state['updated_at']}`")
    if state.get("_lab_state_updated_at"):
        stamp_bits.append(f"supabase `{state['_lab_state_updated_at']}`")
    last_mark = (state.get("last_mark") or {}).get("at")
    if last_mark:
        stamp_bits.append(f"last_mark `{last_mark}`")
    st.caption(" · ".join(meta_bits))
    if stamp_bits:
        st.caption(" · ".join(stamp_bits))
    st.caption(
        "Auto-refreshes ~90s · Lab marks Mon–Fri ~every 30m (10:00–16:00 ET) · "
        "EOD settle ~16:20 ET — see `strategy_lab/DASHBOARD_FRESHNESS.md`"
    )

    # --- Out-of-Sample R²: backtest (static) vs forward (live rolling) ---
    with st.container(border=True):
        _render_strategy_lab_r2_panel(state)

    # --- Who's ahead ---
    margin = abs(a_val - b_val)
    if a_val > b_val:
        ahead_label = pool_a.get("label") or "Strategy A (Trailing)"
    elif b_val > a_val:
        ahead_label = pool_b.get("label") or "Strategy B (Target)"
    else:
        ahead_label = "TIE"
    # Prefer eod winner label when present and pools still match
    w_pool = winner.get("pool")
    if w_pool == "A_trailing" and a_val >= b_val:
        ahead_label = pool_a.get("label") or "Strategy A (Trailing)"
        margin = float(winner.get("margin_usd") or margin)
    elif w_pool == "B_target" and b_val >= a_val:
        ahead_label = pool_b.get("label") or "Strategy B (Target)"
        margin = float(winner.get("margin_usd") or margin)
    elif w_pool == "tie":
        ahead_label = "TIE"
        margin = 0.0

    lab_ahead_banner(ahead_label, margin, a_val, b_val)

    # --- Side-by-side pool cards ---
    # Strategy A: Pool value | T4 trailing on top row (same height as B's pool-only top).
    col_a, col_b = st.columns(2)
    with col_a:
        with st.container(border=True):
            section_header(pool_a.get("label") or "Strategy A (Trailing)")
            t4_runners, _a_full_slots = _lab_trail_runner_stats(pool_a)
            a_top_l, a_top_r = st.columns(2)
            with a_top_l:
                st.metric("Pool value", f"${a_val:,.2f}", f"{a_ret:+.2f}%")
            with a_top_r:
                st.metric("T4 trailing", str(t4_runners))
            m2, m3 = st.columns(2)
            m2.metric("Realized P&L", f"${a_pnl:+,.2f}")
            m3.metric(
                "Win rate",
                f"{float(a_wr):.1f}%" if a_wr is not None else "—",
            )
            m4, m5 = st.columns(2)
            m4.metric("Open slots", f"{a_slots}/{LAB_MAX_SLOTS}")
            m5.metric("Closed trades", str(a_taken))
    with col_b:
        with st.container(border=True):
            section_header(pool_b.get("label") or "Strategy B (Target)")
            st.metric("Pool value", f"${b_val:,.2f}", f"{b_ret:+.2f}%")
            m2, m3 = st.columns(2)
            m2.metric("Realized P&L", f"${b_pnl:+,.2f}")
            m3.metric(
                "Win rate",
                f"{float(b_wr):.1f}%" if b_wr is not None else "—",
            )
            m4, m5 = st.columns(2)
            m4.metric("Open slots", f"{b_slots}/{LAB_MAX_SLOTS}")
            m5.metric("Closed trades", str(b_taken))

    # --- Equity curves (overlaid) ---
    with st.container(border=True):
        section_header("Equity curves")
        fig = go.Figure()
        for label, curve, color in (
            (
                pool_a.get("label") or "A Trailing",
                pool_a.get("equity_curve") or [],
                POSITIVE,
            ),
            (
                pool_b.get("label") or "B Target",
                pool_b.get("equity_curve") or [],
                ACCENT,
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
            plot_bgcolor=BG,
            paper_bgcolor=BG,
            font=dict(color=TEXT, family="Sora"),
            xaxis=dict(gridcolor=BORDER),
            yaxis=dict(gridcolor=BORDER),
        )
        if fig.data:
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("No equity curve points yet.")

    # --- Open positions (SIM Lab book — not IBKR Live Status) ---
    with st.container(border=True):
        section_header("Open Positions", "Lab A vs B marks (SIM / Polygon)")
        st.caption(
            "Lab **A** = trailing exits · Lab **B** = target exits · "
            "separate from the IBKR agent Live Status book. "
            "Lab JEM (if listed) is a SIM residual (e.g. T4 runner), "
            "not the agent NEVER_FILLED ghost."
        )
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
                # Prefer either pool's mark when only one side is open.
                mark_a = oa.get("mark_price") if oa else None
                mark_b = ob.get("mark_price") if ob else None
                mark_show = mark_a if mark_a is not None else mark_b
                open_rows.append({
                    "Ticker": t,
                    "Mark": _lab_fmt_money(mark_show),
                    "A entry": _lab_fmt_money(oa.get("entry_price") if oa else None),
                    "A shares": oa.get("shares") if oa else "—",
                    "A unrealized": _lab_unrealized_cell(oa if oa else None),
                    "A residual": _lab_residuals_cell(oa if oa else None),
                    "B entry": _lab_fmt_money(ob.get("entry_price") if ob else None),
                    "B shares": ob.get("shares") if ob else "—",
                    "B unrealized": _lab_unrealized_cell(ob if ob else None),
                    "B residual": _lab_residuals_cell(ob if ob else None),
                    "sweep_reclaim": (
                        (oa.get("sweep_reclaim") if oa else None)
                        or (ob.get("sweep_reclaim") if ob else None)
                        or "—"
                    ),
                })
            st.dataframe(
                pd.DataFrame(open_rows),
                use_container_width=True,
                hide_index=True,
            )

    # --- Closed trades (closed_trades only — never report.per_ticker / opens) ---
    with st.container(border=True):
        section_header("Closed Trades", "Fully flat A/B only · aligned by ticker + flag_date")
        st.caption(
            "SIM Lab ledger — not the IBKR agent Trade Log. "
            "Still-open names (incl. T4 runners) stay in Open Positions above."
        )
        closed_rows = _lab_align_closed_trades(pool_a, pool_b)
        if closed_rows:
            show = [
                {k: v for k, v in r.items() if not k.startswith("_")}
                for r in closed_rows
            ]
            st.dataframe(
                pd.DataFrame(show),
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


def tab_lab_trade_log() -> None:
    """
    Dedicated Strategy Lab trade review (SIM / Polygon).
    Never mixes agent paper_trades / IBKR fills.
    """
    state = _load_forward_state()
    with st.container(border=True):
        section_header(
            "Lab Trade Log",
            "SIM Polygon Lab review — not the IBKR agent Trade Log",
        )
        st.caption(
            "Lab **A** = trailing exits · Lab **B** = target exits. "
            "Agent Live Status / Trade Log is a separate book."
        )
        if state is None:
            st.info(
                "No forward-test data yet — start `live_forward.py` "
                "(or wait for Supabase `strategy_lab_state`)."
            )
            return

        pool_a = state.get("pool_A_trailing") or {}
        pool_b = state.get("pool_B_target") or {}
        flag = state.get("flag_date") or "—"
        st.caption(
            f"flag_date **{flag}** · source **{state.get('_lab_state_source') or '—'}** · "
            f"open A={len(pool_a.get('open_positions') or {})} "
            f"B={len(pool_b.get('open_positions') or {})}"
        )

    with st.container(border=True):
        section_header("Today's Lab entries", "Open vs closed for current flag_date")
        today_rows = _lab_today_entry_rows(state)
        if today_rows:
            st.dataframe(
                pd.DataFrame(today_rows),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption("No Lab candidates/entries for this flag_date yet.")

    with st.container(border=True):
        section_header(
            "Closed trades (chronological)",
            "Each pool row is a fully flat SIM exit",
        )
        chrono = _lab_chrono_closed_rows(pool_a, pool_b)
        if chrono:
            st.dataframe(
                pd.DataFrame(chrono),
                use_container_width=True,
                hide_index=True,
                height=min(520, 56 + 38 * max(len(chrono), 1)),
            )
        else:
            st.caption("No closed Lab trades yet.")

    with st.container(border=True):
        section_header("Closed trades (A vs B aligned)")
        aligned = _lab_align_closed_trades(pool_a, pool_b)
        if aligned:
            show = [
                {k: v for k, v in r.items() if not k.startswith("_")}
                for r in aligned
            ]
            st.dataframe(
                pd.DataFrame(show),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption("No aligned closed pairs yet.")


GLOSSARY_PATH = ROOT / "GLOSSARY.md"


def tab_glossary() -> None:
    """
    Render repo-root GLOSSARY.md — same content as the markdown file.
    Leave agent code untouched; best-effort if the file is missing.
    """
    with st.container(border=True):
        glossary_scope_marker()
        section_header(
            "Glossary",
            "Q-ALPHA / Strategy Lab terms · same content as GLOSSARY.md",
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
    footer_rule()
    col1, col2, col3 = st.columns([2, 2, 1])
    with col1:
        st.caption(
            f"Auto-refreshes every 90s · "
            f"Last updated: {now_et.strftime('%H:%M:%S ET')}"
        )
    with col2:
        st.caption(
            f"v{SYSTEM_VERSION} · "
            f"Running {DAYS_RUNNING} days · "
            f"Q-ALPHA 2026"
        )
    with col3:
        if st.button("Refresh now"):
            st.rerun()


def main() -> None:
    trades, pool_history, health = _safe_load()
    render_header()

    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9 = st.tabs([
        "📊 Live Status",
        "📋 Trade Log",
        "📈 Performance",
        "🔧 System Health",
        "📓 Daily Reviews",
        "🔬 Ticker Profiles",
        "🧪 Strategy Lab",
        "🧪 Lab Trade Log",
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
        tab_lab_trade_log()
    with tab9:
        tab_glossary()

    render_footer()


if __name__ == "__main__":
    main()
