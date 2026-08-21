"""
Q-ALPHA — Ticker Profiles (Setup Analysis)

Separate from Live Status. Reads precomputed profiles/<TICKER>_profile.json.
On-demand "Refresh profile" runs the expensive Polygon MAE/MFE profiler —
NEVER auto-runs on page load / autorefresh.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard_shared import (
    SYSTEM_VERSION,
    compute_and_save_profile,
    et_today,
    list_cached_profile_tickers,
    load_profile,
    load_todays_watchlist,
    profile_path,
)

st.set_page_config(
    page_title="Q-ALPHA Ticker Profiles",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("# 🔬 Ticker Profiles")
st.caption(
    f"Setup analysis from analog MAE/MFE · informational only · v{SYSTEM_VERSION} · "
    "nav via sidebar (Home = Live Status)"
)
st.info(
    "Profiles are **precomputed** JSON files. This page does **not** call Polygon "
    "on load. Use **Refresh profile** only when you need a new compute "
    "(~13+ minute-bar pulls per ticker)."
)

# ── Ticker picker (watchlist first, then cached profiles) ───────────────────
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
# Preserve watchlist order, then append cached-only names
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
    manual = st.text_input("Ticker symbol", value="JOBY").strip().upper()
    if manual:
        options = [manual]

col_sel, col_meta = st.columns([2, 3])
with col_sel:
    ticker = st.selectbox(
        "Select ticker",
        options=options or ["JOBY"],
        index=0,
        help="Today's watchlist preferred; cached profiles also listed.",
    )
with col_meta:
    path = profile_path(ticker)
    if path.exists():
        mtime = path.stat().st_mtime
        from datetime import datetime
        st.caption(
            f"Cache: `{path.relative_to(ROOT)}` · "
            f"updated {datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')}"
        )
    else:
        st.caption(f"No cache at `{path.relative_to(ROOT)}`")

# ── On-demand compute (explicit only) ───────────────────────────────────────
c1, c2, _ = st.columns([1, 1, 2])
with c1:
    do_refresh = st.button(
        f"🔄 Refresh profile — {ticker}",
        type="primary",
        help="Runs build_ticker_profile (Polygon 1-min). Slow. Not auto.",
    )
with c2:
    st.caption("Requires POLYGON_API_KEY in env or Streamlit secrets.")

if do_refresh:
    with st.spinner(
        f"Computing profile for {ticker} (Polygon daily + 1-min per analog)…"
    ):
        try:
            profile = compute_and_save_profile(ticker)
            st.success(
                f"Saved {ticker} profile "
                f"({profile.get('n_analogs_measured', '?')} analogs, "
                f"{profile.get('confidence', '?')})"
            )
            st.rerun()
        except Exception as exc:
            st.error(f"Profile compute failed: {exc}")

profile = load_profile(ticker)

if profile is None:
    st.warning(
        f"No precomputed profile for **{ticker}**. "
        f"Click **Refresh profile** to generate one (expensive)."
    )
    st.stop()

# ── Summary strip ───────────────────────────────────────────────────────────
st.divider()
conf = profile.get("confidence", "?")
n_m = profile.get("n_analogs_measured") or profile.get("n_analogs_finder") or 0
as_of = profile.get("as_of_date", "—")
weighting = profile.get("weighting", "equal")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Analogs measured", n_m)
m2.metric("Confidence", conf)
m3.metric("As of", as_of)
m4.metric("Weighting", weighting)

if profile.get("informational_only", True):
    st.caption("INFORMATIONAL ONLY — not wired into order / entry logic.")

# ── Outcomes + R:R ──────────────────────────────────────────────────────────
outcomes = profile.get("outcomes") or {}
bracket = profile.get("bracket") or {}
tiers = bracket.get("tiers") or {}
pct = profile.get("percentiles") or {}
mae = pct.get("mae") or {}
mfe = pct.get("mfe") or {}

rr_warn = outcomes.get("rr_warning")
if rr_warn:
    st.error(f"⚠️ R:R warning: {rr_warn}")
elif outcomes.get("reward_risk") is not None:
    st.success(
        f"Reward:Risk = {outcomes.get('reward_risk')} "
        f"(target / safe-max stop)"
    )

o1, o2, o3 = st.columns(3)
o1.metric(
    "Win rate",
    f"{outcomes.get('win_rate_pct_display', '—')}%",
    help=outcomes.get("win_definition", "held close > entry"),
)
o2.metric(
    "Winner MFE p50",
    f"{outcomes.get('winner_mfe_p50_display', '—')}%",
    help="Median MFE among days that closed above entry",
)
o3.metric(
    "Failure MAE p50",
    f"{outcomes.get('failure_mae_p50_display', '—')}%",
    help="Median MAE among days that closed below entry",
)

# ── Percentiles ─────────────────────────────────────────────────────────────
st.subheader("MAE / MFE percentiles (equal-weight)")
pct_rows = []
for key in ("p50", "p75", "p90"):
    pct_rows.append({
        "Percentile": key,
        "MAE %": round((mae.get(key) or 0) * 100, 2),
        "MFE %": round((mfe.get(key) or 0) * 100, 2),
    })
st.dataframe(pd.DataFrame(pct_rows), hide_index=True, use_container_width=True)

# ── Derived bracket ─────────────────────────────────────────────────────────
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

# ── Expandable per-analog detail ────────────────────────────────────────────
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
