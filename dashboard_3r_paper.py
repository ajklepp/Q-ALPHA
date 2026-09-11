"""
Dashboard tab — Peak Hour 3R multi-target shadow paper book.

Live Status remains the IBKR 4T keep-profit book.
This tab shows the parallel software 3-target ladder on the same fills.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
CANDIDATES = ROOT / "candidates"
if str(CANDIDATES) not in sys.path:
    sys.path.insert(0, str(CANDIDATES))

from dashboard_theme import MUTED, section_header  # noqa: E402


def _load_scoreboard() -> dict[str, Any]:
    from tsd_scan_pipeline.tsd_shadow_multi_target import scoreboard

    marks: dict[str, float] = {}
    # Prefer open TSD marks from Live Status path when available
    try:
        from state_paths import state_path
        import json

        book_path = state_path("tsd_book_state.json")
        if book_path.is_file():
            live = json.loads(book_path.read_text(encoding="utf-8"))
            for pos in live.get("positions") or []:
                if str(pos.get("status") or "").upper() != "OPEN":
                    continue
                sym = str(pos.get("symbol") or "").upper()
                for leg in pos.get("legs") or []:
                    if str(leg.get("status") or "").upper() != "OPEN":
                        continue
                    trail = leg.get("trail") or {}
                    for key in ("last_close", "peak_high"):
                        try:
                            v = float(trail.get(key) or 0)
                            if v > 0:
                                marks[sym] = v
                                break
                        except (TypeError, ValueError):
                            pass
                    if sym not in marks:
                        try:
                            v = float(leg.get("price") or 0)
                            if v > 0:
                                marks[sym] = v
                        except (TypeError, ValueError):
                            pass
    except Exception:
        pass
    return scoreboard(mark_by_symbol=marks)


def render_3r_paper_tab() -> None:
    """Streamlit tab: 3R shadow paper vs live 4T."""
    section_header("3R Paper (shadow)", "Same fills as Live 4T · software multi-target only")

    st.caption(
        "Live broker book stays on **4-tranche keep-profit**. "
        "This tab is a **parallel paper ledger**: identical entry fills, "
        "exits banked at **0.35R / 0.50R / 0.90R** (1.75% / 2.5% / 4.5%) "
        "weights **50/25/25**, residual kill **5%**. No second IBKR orders."
    )

    try:
        score = _load_scoreboard()
    except Exception as exc:
        st.error(f"Could not load 3R shadow book: {exc}")
        return

    ladder = score.get("ladder") or {}
    c1, c2, c3, c4, c5 = st.columns(5)
    total = float(score.get("total_pnl") or 0)
    c1.metric("Total P&L", f"${total:+,.2f}")
    c2.metric("Realized", f"${float(score.get('realized_pnl') or 0):+,.2f}")
    c3.metric("Open locked", f"${float(score.get('open_locked_pnl') or 0):+,.2f}")
    c4.metric("Open MTM", f"${float(score.get('open_mtm') or 0):+,.2f}")
    wr = score.get("win_rate")
    c5.metric(
        "Closed",
        f"{int(score.get('n_closed') or 0)}",
        None if wr is None else f"win {100 * wr:.0f}%",
    )

    st.markdown(
        f"<p style='color:{MUTED};font-size:0.85rem'>"
        f"Ladder <code>{ladder.get('id', 'mt3')}</code> · "
        f"targets R={list(ladder.get('targets_r') or [])} · "
        f"w={list(ladder.get('weights') or [])} · "
        f"open={int(score.get('n_open') or 0)} · "
        f"updated {score.get('updated_at') or '—'}"
        f"</p>",
        unsafe_allow_html=True,
    )

    st.subheader("Open 3R legs")
    open_rows = []
    for l in score.get("open_legs") or []:
        from tsd_scan_pipeline.tsd_multi_target import slice_caption

        open_rows.append({
            "Symbol": l.get("symbol"),
            "Entry": l.get("entry_price"),
            "Shares": l.get("shares"),
            "Rem": l.get("remaining"),
            "Locked $": round(float(l.get("realized_gross") or 0), 2),
            "Slices": slice_caption(l),
            "Opened": str(l.get("opened_at") or "")[:19],
        })
    if open_rows:
        st.dataframe(pd.DataFrame(open_rows), use_container_width=True, hide_index=True)
    else:
        st.info("No open 3R shadow legs. New live fills will mirror here automatically.")

    st.subheader("Recent closed")
    closed = list(reversed(score.get("closed_legs") or []))
    closed_rows = []
    for l in closed[:40]:
        closed_rows.append({
            "Symbol": l.get("symbol"),
            "Entry": l.get("entry_price"),
            "Shares": l.get("shares"),
            "P&L": l.get("pnl"),
            "Exit": l.get("closed_at"),
            "Banks": ", ".join(
                f"{e.get('id')}:{e.get('reason')}"
                for e in (l.get("exits") or [])
            ),
        })
    if closed_rows:
        st.dataframe(pd.DataFrame(closed_rows), use_container_width=True, hide_index=True)
    else:
        st.caption("No closed 3R legs yet.")

    with st.expander("How to read this bakeoff"):
        st.markdown(
            """
- **Live Status** = real paper IBKR + 4T ratchet (what the trail monitor sells).
- **3R Paper** = same fill price/size; slice banks at 1.75% / 2.5% / 4.5%; leftover stops at −5%.
- Compare week P&L here vs Live Status to decide which exit to promote.
- Shadow state file: `candidates/tsd_shadow_mt3_book.json` (local; not capacity).
"""
        )
