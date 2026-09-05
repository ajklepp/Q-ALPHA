"""
Dashboard Weekly Review — results + missed runners (on-the-go highlights).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import pytz
import streamlit as st

from dashboard_theme import section_header
from dashboard_thesis import render_thesis_expander

ET = pytz.timezone("America/New_York")


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        v = float(x)
        return v if v == v else default
    except (TypeError, ValueError):
        return default


def _parse_et_date(val: Any) -> datetime | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    try:
        cleaned = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned[:26] if "T" in cleaned else cleaned)
        if dt.tzinfo is None:
            dt = ET.localize(dt)
        return dt.astimezone(ET)
    except (ValueError, TypeError):
        try:
            return ET.localize(datetime.strptime(s[:10], "%Y-%m-%d"))
        except (ValueError, TypeError):
            return None


def _ran_up_pct(entry: float, peak: float) -> float | None:
    if entry <= 0 or peak <= 0:
        return None
    return (peak / entry - 1.0) * 100.0


def _in_window(dt: datetime | None, start: datetime) -> bool:
    return dt is not None and dt >= start


def _local_missed(days: int) -> list[dict[str, Any]]:
    try:
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parent
        cand = root / "candidates"
        if str(cand) not in sys.path:
            sys.path.insert(0, str(cand))
        from tsd_scan_pipeline.php_missed_ledger import rows_since

        return rows_since(days, outcome="MISSED")
    except Exception:
        return []


def _local_book_thesis_map() -> dict[str, dict]:
    try:
        import json
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parent
        cand = root / "candidates"
        if str(cand) not in sys.path:
            sys.path.insert(0, str(cand))
        from state_paths import state_path

        book = json.loads(state_path("tsd_book_state.json").read_text(encoding="utf-8"))
        out: dict[str, dict] = {}
        for pos in book.get("positions") or []:
            sym = str(pos.get("symbol") or "").upper()
            for leg in pos.get("legs") or []:
                th = leg.get("thesis")
                if sym and isinstance(th, dict):
                    out[sym] = th
                    break
        return out
    except Exception:
        return {}


def tab_weekly_research(get_sync: Callable[..., Any]) -> None:
    """Weekly scoreboard + missed runners for on-the-go review."""
    days = st.selectbox("Last", [7, 14, 30], index=0, format_func=lambda d: f"{d} days")
    now = datetime.now(ET)
    start = now - timedelta(days=int(days))

    closed: list[dict] = []
    opens: list[dict] = []
    pool: dict = {}
    missed: list[dict] = []
    err: str | None = None
    try:
        sync = get_sync()
        closed = list(sync.get_tsd_closed_legs() or [])
        opens = list(sync.get_tsd_positions(status="OPEN") or [])
        pool = sync.get_latest_tsd_pool() or {}
        missed = list(sync.get_tsd_missed_moves(days=int(days)) or [])
    except Exception as exc:
        err = str(exc)

    if err:
        st.warning("Could not load book data.")
        st.caption(err)
        return

    if not missed:
        missed = _local_missed(int(days))

    local_thesis = _local_book_thesis_map()
    for r in opens:
        if not isinstance(r.get("thesis"), dict):
            th = local_thesis.get(str(r.get("symbol") or "").upper())
            if th:
                r["thesis"] = th
    for r in closed:
        if not isinstance(r.get("thesis"), dict):
            th = local_thesis.get(str(r.get("symbol") or "").upper())
            if th:
                r["thesis"] = th

    closed_w: list[dict] = []
    for r in closed:
        dt = _parse_et_date(r.get("closed_at")) or _parse_et_date(r.get("entry_date"))
        if _in_window(dt, start):
            closed_w.append(r)

    wins = [r for r in closed_w if _safe_float(r.get("pnl_dollars")) > 0]
    losses = [r for r in closed_w if _safe_float(r.get("pnl_dollars")) <= 0]
    pnl_closed = sum(_safe_float(r.get("pnl_dollars")) for r in closed_w)
    pnl_open = sum(_safe_float(r.get("pnl_dollars")) for r in opens)
    wr = (len(wins) / len(closed_w)) if closed_w else None

    cash = _safe_float(pool.get("pool"), 3000.0)
    starting = _safe_float(pool.get("starting_pool"), 3000.0)
    in_mkt = sum(
        _safe_float(r.get("current_price")) * int(_safe_float(r.get("shares")))
        for r in opens
    )
    equity = cash + in_mkt
    total_pnl = equity - starting

    # Highlight: biggest missed runs
    hot_misses = [
        m for m in missed
        if _safe_float(m.get("ran_up_pct"), float("-inf")) > 0
    ]
    best_miss = hot_misses[0] if hot_misses else None

    with st.container(border=True):
        section_header("Week", f"Last {int(days)} days")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("P&L (closed)", f"${pnl_closed:+,.2f}")
        c2.metric("Wins", str(len(wins)))
        c3.metric("Losses", str(len(losses)))
        c4.metric("Win rate", f"{wr:.0%}" if wr is not None else "—")

        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Taken", str(len(closed_w) + len(opens)))
        d2.metric("Still open", str(len(opens)))
        d3.metric("Missed", str(len(missed)))
        if best_miss:
            d4.metric(
                "Biggest miss",
                f"{best_miss.get('symbol')} {_safe_float(best_miss.get('ran_up_pct')):+.1f}%",
            )
        else:
            d4.metric("Biggest miss", "—")
        st.caption(
            f"Book ${equity:,.2f} · P&L ${total_pnl:+,.2f}"
            + (f" · open MTM ${pnl_open:+,.2f}" if opens else "")
        )

    # --- Missed first (the point of weekly review) ---
    with st.container(border=True):
        section_header("Missed", "")
        if not missed:
            st.info("No missed names in this window yet.")
        else:
            rows = []
            for m in missed:
                ran = m.get("ran_up_pct")
                rows.append({
                    "Symbol": str(m.get("symbol") or "").upper(),
                    "Day": str(m.get("signal_day") or "")[:10],
                    "Ref": f"${_safe_float(m.get('ref_price')):.2f}",
                    "High": (
                        f"${_safe_float(m.get('peak_price')):.2f}"
                        if m.get("peak_price") is not None
                        else "—"
                    ),
                    "Ran up": f"{_safe_float(ran):+.1f}%" if ran is not None else "—",
                })
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
            for m in missed:
                sym = str(m.get("symbol") or "").upper()
                day = str(m.get("signal_day") or "")[:10]
                if m.get("thesis"):
                    render_thesis_expander(
                        m.get("thesis"),
                        label=f"Thesis · {sym}" + (f" · {day}" if day else ""),
                    )

    # --- Taken ---
    taken_cards: list[dict[str, Any]] = []
    for r in opens:
        entry = _safe_float(r.get("entry_price"))
        peak = _safe_float(r.get("peak_high"), float("nan"))
        ran = _ran_up_pct(entry, peak)
        taken_cards.append({
            "Symbol": str(r.get("symbol") or "").upper(),
            "Status": "Open",
            "P&L": f"${_safe_float(r.get('pnl_dollars')):+.2f}",
            "Ran up": f"{ran:+.1f}%" if ran is not None else "—",
            "thesis": r.get("thesis"),
        })
    for r in sorted(
        closed_w,
        key=lambda x: str(x.get("closed_at") or x.get("entry_date") or ""),
        reverse=True,
    ):
        entry = _safe_float(r.get("entry_price"))
        peak = _safe_float(r.get("peak_high"), float("nan"))
        ran = _ran_up_pct(entry, peak)
        if r.get("mfe_pct") is not None:
            ran = _safe_float(r.get("mfe_pct"))
        pnl = _safe_float(r.get("pnl_dollars"))
        taken_cards.append({
            "Symbol": str(r.get("symbol") or "").upper(),
            "Status": "Win" if pnl > 0 else "Loss",
            "P&L": f"${pnl:+.2f}",
            "Ran up": f"{ran:+.1f}%" if ran is not None else "—",
            "thesis": r.get("thesis"),
        })

    with st.container(border=True):
        section_header("Taken", "")
        if not taken_cards:
            st.info("No taken trades in this window.")
        else:
            show = [
                {k: v for k, v in card.items() if k != "thesis"}
                for card in taken_cards
            ]
            st.dataframe(pd.DataFrame(show), hide_index=True, use_container_width=True)
            for card in taken_cards:
                if card.get("thesis"):
                    render_thesis_expander(
                        card.get("thesis"),
                        label=f"Thesis · {card.get('Symbol')} · {card.get('Status')}",
                    )
