"""
Dashboard Weekly Review — public scoreboard only (no strategy docs).
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


def tab_weekly_research() -> None:
    """Public weekly scoreboard — taken / skipped / outcomes only."""
    st.subheader("Weekly Review")
    st.caption("What ran · what we took · wins vs losses — no strategy detail.")

    days = st.selectbox("Window (days)", [7, 14, 30], index=0)
    rebuild = st.button("Refresh", type="primary")

    card: dict[str, Any] | None = None
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
            st.success("Updated.")
            json_path = out_js
        except Exception as exc:
            st.error(f"Refresh failed: {exc}")

    if card is None and json_path and json_path.exists():
        try:
            card = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            card = None
    if card is None and not rebuild:
        try:
            from uts_v2.php_weekly_funnel import build_weekly_funnel

            card = build_weekly_funnel(days=int(days))
        except Exception as exc:
            st.warning(f"No weekly data yet. ({exc})")

    if not card:
        st.info("No weekly data yet.")
        return

    entered = list(card.get("symbols_entered") or [])
    skipped_n = int(card.get("total_skipped") or 0)
    if not skipped_n:
        # Derive from launches − entered when funnel omits explicit skip count
        launches = int(card.get("total_launches") or 0)
        skipped_n = max(0, launches - len(entered))

    outcomes = list(card.get("outcomes") or [])
    wins = sum(1 for o in outcomes if float(o.get("pnl_dollars") or o.get("pnl") or 0) > 0)
    losses = sum(1 for o in outcomes if float(o.get("pnl_dollars") or o.get("pnl") or 0) <= 0)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Taken", len(entered) or int(card.get("total_entered") or 0))
    m2.metric("Skipped", skipped_n)
    m3.metric("Wins", wins if outcomes else "—")
    m4.metric("Losses", losses if outcomes else "—")

    if entered:
        st.write("**Taken:** " + ", ".join(entered))

    if outcomes:
        st.write("**Results**")
        rows = []
        for o in outcomes:
            pnl = float(o.get("pnl_dollars") or o.get("pnl") or 0)
            peak = o.get("mfe_pct") or o.get("ran_up_pct") or o.get("peak_pct")
            rows.append({
                "Symbol": o.get("symbol") or o.get("ticker") or "—",
                "Result": "Win" if pnl > 0 else "Loss",
                "P&L": f"${pnl:+.2f}",
                "Ran up": f"{float(peak):+.1f}%" if peak is not None else "—",
            })
        st.dataframe(rows, hide_index=True, use_container_width=True)
