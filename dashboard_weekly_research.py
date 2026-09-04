"""
Dashboard Weekly Research — Peak Hour funnel + EXP-0021 hitch notes.

Strategy Lab gap SIM is mothballed; this surface lists research Aaron reviews weekly.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz
import streamlit as st

ROOT = Path(__file__).resolve().parent
CANDIDATES = ROOT / "candidates"
EXP_0021 = ROOT / "experiments" / "EXP-0021"
if str(CANDIDATES) not in sys.path:
    sys.path.insert(0, str(CANDIDATES))

ET = pytz.timezone("America/New_York")
FUNNEL_DIR = CANDIDATES / "tsd_scan_pipeline" / "results" / "peak_hour_scans"


def _latest_weekly_files() -> tuple[Path | None, Path | None]:
    """Newest php_weekly_*.md / .json under peak_hour_scans."""
    if not FUNNEL_DIR.exists():
        return None, None
    mds = sorted(FUNNEL_DIR.glob("php_weekly_*.md"), reverse=True)
    js = sorted(FUNNEL_DIR.glob("php_weekly_*.json"), reverse=True)
    return (mds[0] if mds else None), (js[0] if js else None)


def _build_live_funnel(days: int = 7) -> dict[str, Any]:
    from uts_v2.php_weekly_funnel import build_weekly_funnel

    return build_weekly_funnel(days=days)


def tab_weekly_research() -> None:
    """Peak Hour weekly research hub (replaces Strategy Lab live tabs)."""
    st.subheader("Weekly Research")
    st.caption(
        "Peak Hour Performers · continuation ranker · hitch study. "
        "Strategy Lab gap SIM is **mothballed** (tasks disabled) — code kept under `strategy_lab/`."
    )

    col_a, col_b = st.columns([1, 1])
    with col_a:
        days = st.selectbox("Funnel window (days)", [7, 14, 30], index=0)
    with col_b:
        rebuild = st.button("Rebuild funnel from scans", type="primary")

    card: dict[str, Any] | None = None
    md_text = ""
    md_path, json_path = _latest_weekly_files()

    if rebuild:
        try:
            from uts_v2.php_weekly_funnel import build_weekly_funnel, format_weekly_md

            card = build_weekly_funnel(days=int(days))
            md_text = format_weekly_md(card)
            FUNNEL_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(ET).strftime("%Y%m%d")
            out_md = FUNNEL_DIR / f"php_weekly_{stamp}.md"
            out_js = FUNNEL_DIR / f"php_weekly_{stamp}.json"
            out_md.write_text(md_text, encoding="utf-8")
            out_js.write_text(json.dumps(card, indent=2, default=str), encoding="utf-8")
            st.success(f"Wrote `{out_md.relative_to(ROOT)}`")
            md_path, json_path = out_md, out_js
        except Exception as exc:
            st.error(f"Funnel rebuild failed: {exc}")

    if card is None and json_path and json_path.exists():
        try:
            card = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            card = None
    if not md_text and md_path and md_path.exists():
        md_text = md_path.read_text(encoding="utf-8")

    if card is None and not rebuild:
        try:
            card = _build_live_funnel(days=int(days))
            from uts_v2.php_weekly_funnel import format_weekly_md

            md_text = format_weekly_md(card)
        except Exception as exc:
            st.warning(f"No weekly funnel yet — run a few 1H scans, then Rebuild. ({exc})")

    st.markdown("### Peak Hour scan funnel")
    if card:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Scans", int(card.get("scans_run") or 0))
        m2.metric("1H launches", int(card.get("total_launches") or 0))
        m3.metric("Entered", int(card.get("total_entered") or 0))
        m4.metric(
            "Enter rate",
            f"{100 * float(card.get('enter_rate') or 0):.1f}%",
        )
        entered = card.get("symbols_entered") or []
        if entered:
            st.write("**Entered:** " + ", ".join(entered))
        hist = card.get("reject_histogram") or {}
        if hist:
            st.write("**Top reject reasons**")
            rows = [{"reason": k, "count": v} for k, v in list(hist.items())[:12]]
            st.dataframe(rows, hide_index=True, use_container_width=True)
        outcomes = card.get("outcomes") or []
        if outcomes:
            st.write("**Book outcomes (matched)**")
            st.dataframe(outcomes, hide_index=True, use_container_width=True)
        if card.get("scan_files"):
            with st.expander("Source scan files"):
                for name in card["scan_files"]:
                    st.code(name, language=None)
    else:
        st.info("No funnel data — wait for live scans or click Rebuild.")

    if md_text:
        with st.expander("Weekly funnel markdown", expanded=False):
            st.markdown(md_text)

    st.markdown("### Hitch / continuation research (EXP-0021)")
    hours_md = EXP_0021 / "HOURS_04_15_WINNERS.md"
    results_md = EXP_0021 / "results.md"
    if hours_md.exists():
        with st.expander("Winner existence by hour (04→15)", expanded=True):
            st.markdown(hours_md.read_text(encoding="utf-8"))
    else:
        st.caption("Missing `experiments/EXP-0021/HOURS_04_15_WINNERS.md`")
    if results_md.exists():
        with st.expander("Continuation ranker bakeoff results", expanded=False):
            # Truncate very long file for Streamlit comfort
            body = results_md.read_text(encoding="utf-8")
            st.markdown(body[:12000] + ("\n\n_…truncated_" if len(body) > 12000 else ""))

    st.markdown("### Archive")
    st.caption(
        "Gap Strategy Lab SIM code remains in `strategy_lab/` for exit A/B replay only — "
        "not Live Paper. Scheduled Entry / Mark / Settle tasks are **Disabled**."
    )
