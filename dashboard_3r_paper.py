"""
Dashboard tab — Peak Hour 3R multi-target shadow paper book.

Live Status remains the IBKR 4T keep-profit book.
This tab shows the parallel software 3-target ladder on the same
Peak Hour fills — it is **not** Track 100.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
CANDIDATES = ROOT / "candidates"
if str(CANDIDATES) not in sys.path:
    sys.path.insert(0, str(CANDIDATES))

from dashboard_theme import MUTED, section_header  # noqa: E402


def _live_marks_from_book() -> dict[str, float]:
    """Open Peak Hour marks for 3R MTM (local book only)."""
    marks: dict[str, float] = {}
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
    return marks


def _sync_shadow(get_sync: Callable[..., Any] | None) -> dict[str, Any]:
    """
    Ensure the 3R book has every Peak Hour fill.

    Local tsd_book_state.json first; Supabase open/closed if still empty
    (Streamlit Cloud has no gitignored live book).
    """
    from tsd_scan_pipeline.tsd_shadow_multi_target import ensure_shadow_synced

    open_rows: list[dict[str, Any]] = []
    closed_rows: list[dict[str, Any]] = []
    if get_sync is not None:
        try:
            sync = get_sync()
            open_rows = list(sync.get_tsd_positions(status="OPEN") or [])
            closed_rows = list(sync.get_tsd_closed_legs() or [])
        except Exception:
            open_rows, closed_rows = [], []
    return ensure_shadow_synced(open_rows=open_rows, closed_rows=closed_rows)


def _load_scoreboard(get_sync: Callable[..., Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    from tsd_scan_pipeline.tsd_shadow_multi_target import scoreboard

    sync_info = _sync_shadow(get_sync)
    return scoreboard(mark_by_symbol=_live_marks_from_book()), sync_info


def render_3r_paper_tab(get_sync: Callable[..., Any] | None = None) -> None:
    """Streamlit tab: Peak Hour 3R shadow paper vs live 4T. Not Track 100."""
    section_header(
        "3R Paper — Peak Hour shadow",
        "Same Peak Hour fills · software multi-target only · not Track 100",
    )

    st.caption(
        "This is **Peak Hour's parallel paper ledger**, not Track 100. "
        "Live broker book stays on **4-tranche keep-profit**. "
        "Identical Peak Hour entry fills; exits banked at "
        "**0.35R / 0.50R / 0.90R** (1.75% / 2.5% / 4.5%) "
        "weights **50/25/25**, residual kill **5%**. No second IBKR orders."
    )

    try:
        score, sync_info = _load_scoreboard(get_sync)
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

    src = sync_info.get("source") or score.get("sync_source") or "—"
    st.markdown(
        f"<p style='color:{MUTED};font-size:0.85rem'>"
        f"Ladder <code>{ladder.get('id', 'mt3')}</code> · "
        f"targets R={list(ladder.get('targets_r') or [])} · "
        f"w={list(ladder.get('weights') or [])} · "
        f"open={int(score.get('n_open') or 0)} · "
        f"sync={src} mirrored={sync_info.get('n_mirrored', 0)} · "
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
        n_closed = int(score.get("n_closed") or 0)
        if n_closed:
            st.info("No open 3R shadow legs (closed history is below).")
        elif sync_info.get("reason") == "no_live_book" and not (
            sync_info.get("n_mirrored") or sync_info.get("n_closed")
        ):
            st.warning(
                "No Peak Hour live book on this machine and no Supabase fills "
                "to reconstruct. If Trade Log shows IRD-style takes, run "
                "`py -3 candidates\\tsd_scan_pipeline\\repair_3r_shadow.py` "
                "on the laptop that has `tsd_book_state.json`, or confirm "
                "Supabase `tsd_closed_legs` is reachable."
            )
        else:
            st.info(
                "No Peak Hour fills to mirror yet. New live Peak Hour BUYs "
                "open a 3R shadow leg automatically (record_entry)."
            )

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
            f"""
- **Live Status** = real paper IBKR + 4T ratchet (what the trail monitor sells).
- **3R Paper** = Peak Hour shadow only: same fill price/size; slice banks at
  1.75% / 2.5% / 4.5%; leftover stops at −5%. **Not Track 100**
  (that study has its own tab).
- Compare week P&L here vs Live Status to decide which exit to promote.
- Shadow state file: `{score.get('book_path') or 'candidates/tsd_shadow_mt3_book.json'}`
  (local; not capacity). One-shot repair:
  `py -3 candidates\\tsd_scan_pipeline\\repair_3r_shadow.py`
"""
        )
