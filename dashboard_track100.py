"""
Dashboard tab — Track 100 paper-only ledger (no IBKR).

Q-ALPHA is display-only. Track 100 (sibling project) writes a durable
paper book; this tab reads it.

Read path contract (first existing file wins):
  1. TRACK100_PAPER_BOOK — explicit JSON file
  2. TRACK100_ROOT / results/paper_book.json
  3. Sibling folders next to this repo:
       ../Track 100/results/paper_book.json
       ../Track100/results/paper_book.json
       ../track-100/results/paper_book.json
  Optional dated scan artifacts (last_scan fallback only):
       {root}/results/scan_live_YYYY-MM-DD.json
       {root}/results/scan_YYYYMMDD.json

Writer schema (Track 100 v12 playbook — deepest OS → retest signal-low
→ next 1H open; exit book 5% stop / 15% target). No IBKR order fields.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz

# streamlit / pandas are only required to render the tab, not to load the book.

ROOT = Path(__file__).resolve().parent
ET = pytz.timezone("America/New_York")

# Sibling folder names Track 100 uses on Aaron's laptop / GitHub clone.
_SIBLING_DIR_NAMES = ("Track 100", "Track100", "track-100")
_PAPER_REL = Path("results") / "paper_book.json"

# Minimal schema the Track 100 writer should persist.
TRACK100_PAPER_SCHEMA_VERSION = 1
TRACK100_STRATEGY = "Track100_v12"


def track100_root_candidates() -> list[Path]:
    """Ordered Track 100 project roots to search (env, then siblings)."""
    out: list[Path] = []
    env_root = (os.environ.get("TRACK100_ROOT") or "").strip()
    if env_root:
        out.append(Path(env_root).expanduser())
    for name in _SIBLING_DIR_NAMES:
        out.append(ROOT.parent / name)
    # De-dupe while preserving order
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def paper_book_candidates() -> list[Path]:
    """
    Ordered paper_book.json paths.

    Prefer env override, then TRACK100_ROOT, then sibling clones.
    """
    out: list[Path] = []
    env_file = (os.environ.get("TRACK100_PAPER_BOOK") or "").strip()
    if env_file:
        out.append(Path(env_file).expanduser())
    # Streamlit Cloud: committed snapshot (laptop refreshes this file + pushes)
    out.append(ROOT / "candidates" / "track100_cloud" / "paper_book.json")
    for root in track100_root_candidates():
        out.append(root / _PAPER_REL)
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def resolve_paper_book_path() -> Path | None:
    """First existing paper_book.json on the contract path list."""
    for path in paper_book_candidates():
        if path.is_file():
            return path
    return None


def _latest_dated_scan(root: Path) -> dict[str, Any] | None:
    """Best-effort last_scan from dated Track 100 scan artifacts."""
    results = root / "results"
    if not results.is_dir():
        return None
    dated = sorted(
        list(results.glob("scan_live_*.json")) + list(results.glob("scan_2*.json")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in dated[:5]:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue
        return {
            "date_et": str(
                doc.get("date_et")
                or doc.get("scan_date")
                or path.stem.replace("scan_live_", "").replace("scan_", "")
            ),
            "ended": str(doc.get("ended") or doc.get("ended_et") or ""),
            "exit_code": doc.get("exit_code"),
            "artifact": str(path),
        }
    return None


def empty_track100_book(*, reason: str) -> dict[str, Any]:
    """Empty ledger shown when Track 100 has not written a paper book yet."""
    return {
        "version": TRACK100_PAPER_SCHEMA_VERSION,
        "strategy": TRACK100_STRATEGY,
        "updated_et": None,
        "last_scan": None,
        "waiting_retest": [],
        "armed": [],
        "open": [],
        "closed": [],
        "totals": {
            "realized_pnl_pct": 0.0,
            "realized_pnl_usd": 0.0,
            "n_closed": 0,
            "n_open": 0,
        },
        "path": None,
        "reason": reason,
        "ok": False,
    }


def _as_rows(val: Any) -> list[dict[str, Any]]:
    if not isinstance(val, list):
        return []
    return [r for r in val if isinstance(r, dict)]


def row_pnl_usd(row: dict[str, Any]) -> float | None:
    """Dollar P&L for one closed paper leg.

    Uses ``pnl_usd`` when the writer stored it. Otherwise qty × (exit − entry),
    then notional × pnl_pct / 100. Morning vetoes are $0. Does not invent a price.
    """
    if str(row.get("reason") or row.get("exit_reason") or "") == "morning_veto":
        return 0.0
    raw = row.get("pnl_usd")
    if raw is not None and raw != "":
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    try:
        qty = float(row.get("qty") if row.get("qty") is not None else row.get("shares") or 0.0)
        entry = float(row.get("entry") if row.get("entry") is not None else row.get("entry_price") or 0.0)
        exit_raw = row.get("exit") if row.get("exit") is not None else row.get("exit_price")
        exit_px = float(exit_raw) if exit_raw is not None else None
    except (TypeError, ValueError):
        qty, entry, exit_px = 0.0, 0.0, None
    if qty > 0 and entry > 0 and exit_px is not None:
        return qty * (exit_px - entry)
    notional = row.get("notional")
    pct = row.get("pnl_pct")
    if notional is not None and pct is not None:
        try:
            return float(notional) * float(pct) / 100.0
        except (TypeError, ValueError):
            return None
    return None


def _stamp_row_pnl_usd(closed: list[dict[str, Any]]) -> float | None:
    """Fill missing ``pnl_usd`` on closed rows. None when nothing can be priced."""
    if not closed:
        return 0.0
    total = 0.0
    priced = False
    for row in closed:
        usd = row_pnl_usd(row)
        if usd is None:
            continue
        priced = True
        if row.get("pnl_usd") is None:
            row["pnl_usd"] = round(usd, 4)
        total += usd
    if not priced:
        return None
    return total


def _totals_for_display(
    raw: dict[str, Any],
    *,
    realized_pct: float,
    realized_usd: float | None,
    n_closed: int,
    n_open: int,
    open_legs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Keep dollar fields the Track 100 writer stores. Do not drop them."""
    out: dict[str, Any] = {
        "realized_pnl_pct": realized_pct,
        "n_closed": n_closed,
        "n_open": n_open,
    }
    if realized_usd is not None:
        out["realized_pnl_usd"] = round(float(realized_usd), 4)
    for key in ("starting_cash", "equity_usd", "cash_usd", "open_notional_usd"):
        if raw.get(key) is None:
            continue
        try:
            out[key] = round(float(raw[key]), 4)
        except (TypeError, ValueError):
            continue
    if "open_notional_usd" not in out:
        notionals = []
        for row in open_legs:
            raw_n = row.get("notional")
            if raw_n is None:
                raw_n = row.get("target_notional")
            if raw_n is None:
                continue
            try:
                notionals.append(float(raw_n))
            except (TypeError, ValueError):
                continue
        if notionals:
            out["open_notional_usd"] = round(sum(notionals), 4)
    if "equity_usd" not in out and out.get("starting_cash") is not None and realized_usd is not None:
        equity = float(out["starting_cash"]) + float(realized_usd)
        out["equity_usd"] = round(equity, 4)
        if "cash_usd" not in out:
            out["cash_usd"] = round(equity - float(out.get("open_notional_usd") or 0.0), 4)
    return out


def load_track100_paper_book() -> dict[str, Any]:
    """
    Load the Track 100 paper ledger.

    Missing file is a valid empty state — not an error. Q-ALPHA never
    writes this file and never places Track 100 IBKR orders.
    """
    path = resolve_paper_book_path()
    if path is None:
        book = empty_track100_book(reason="missing_paper_book")
        for root in track100_root_candidates():
            scan = _latest_dated_scan(root)
            if scan:
                book["last_scan"] = scan
                book["reason"] = "scan_artifact_only"
                break
        return book
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        book = empty_track100_book(reason=f"unreadable:{exc}")
        book["path"] = str(path)
        return book
    if not isinstance(doc, dict):
        book = empty_track100_book(reason="invalid_json")
        book["path"] = str(path)
        return book

    waiting = _as_rows(doc.get("waiting_retest") or doc.get("waiting"))
    armed = _as_rows(doc.get("armed") or doc.get("retest_armed"))
    open_legs = _as_rows(doc.get("open") or doc.get("open_legs"))
    closed = _as_rows(doc.get("closed") or doc.get("closed_legs"))
    totals = doc.get("totals") if isinstance(doc.get("totals"), dict) else {}
    n_open = int(totals.get("n_open") or len(open_legs))
    n_closed = int(totals.get("n_closed") or len(closed))
    realized = totals.get("realized_pnl_pct")
    if realized is None:
        realized = sum(float(r.get("pnl_pct") or 0) for r in closed)
    realized_usd = totals.get("realized_pnl_usd")
    derived_usd = _stamp_row_pnl_usd(closed)
    if realized_usd is None:
        realized_usd = derived_usd
    last_scan = doc.get("last_scan")
    if not isinstance(last_scan, dict):
        last_scan = None
        for root in track100_root_candidates():
            last_scan = _latest_dated_scan(root)
            if last_scan:
                break

    return {
        "version": doc.get("version") or TRACK100_PAPER_SCHEMA_VERSION,
        "strategy": doc.get("strategy") or TRACK100_STRATEGY,
        "updated_et": doc.get("updated_et") or doc.get("updated_at"),
        "last_scan": last_scan,
        "waiting_retest": waiting,
        "armed": armed,
        "open": open_legs,
        "closed": closed,
        "totals": _totals_for_display(
            totals,
            realized_pct=float(realized or 0),
            realized_usd=None if realized_usd is None else float(realized_usd),
            n_closed=n_closed,
            n_open=n_open,
            open_legs=open_legs,
        ),
        "path": str(path),
        "reason": "ok",
        "ok": True,
        "notes": doc.get("notes"),
    }


def _fmt_px(val: Any) -> str:
    try:
        v = float(val)
    except (TypeError, ValueError):
        return "—"
    return f"{v:.4g}" if abs(v) < 1 else f"{v:.2f}"


def _fmt_usd(val: Any) -> str:
    """Signed dollars, e.g. +$89.84 or -$21.58."""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return "—"
    if v < 0:
        return f"-${abs(v):,.2f}"
    return f"+${v:,.2f}"


def _fmt_pct(val: Any) -> str:
    try:
        v = float(val)
    except (TypeError, ValueError):
        return "—"
    # Writer may store 15 as 15 or 0.15
    if abs(v) > 1.5:
        return f"{v:+.2f}%"
    return f"{v:+.2%}"


def render_track100_tab() -> None:
    """Streamlit tab: Track 100 paper log. No IBKR."""
    import pandas as pd
    import streamlit as st

    from dashboard_theme import MUTED, section_header

    section_header(
        "Track 100 (paper log)",
        "Sibling study · paper fills only · no IBKR",
    )
    st.caption(
        "This is **not** Peak Hour and **not** the 3R Paper tab. "
        "Track 100 is a **read-only paper ledger** written by the sibling "
        "project (`Documents\\Track 100` / `ajklepp/track-100`). "
        "Q-ALPHA **does not place Track 100 broker orders**."
    )

    book = load_track100_paper_book()
    totals = book.get("totals") or {}
    last = book.get("last_scan") if isinstance(book.get("last_scan"), dict) else {}

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Open paper", str(int(totals.get("n_open") or 0)))
    c2.metric("Closed paper", str(int(totals.get("n_closed") or 0)))
    usd = totals.get("realized_pnl_usd")
    if usd is not None:
        c3.metric("Realized $", _fmt_usd(usd))
    else:
        c3.metric("Realized", _fmt_pct(totals.get("realized_pnl_pct")))
    equity = totals.get("equity_usd")
    c4.metric("Equity $", _fmt_usd(equity) if equity is not None else "—")
    ended = str((last or {}).get("ended") or book.get("updated_et") or "—")
    c5.metric("Last scan", str((last or {}).get("date_et") or "—"))
    exit_code = (last or {}).get("exit_code")
    status = "ok" if exit_code in (0, "0", None) and book.get("ok") else (
        book.get("reason") or "—"
    )
    if exit_code not in (None, "") and book.get("ok"):
        status = f"exit {exit_code}"
    c6.metric("Status", str(status)[:18])

    st.markdown(
        f"<p style='color:{MUTED};font-size:0.85rem'>"
        f"Strategy <code>{book.get('strategy')}</code> · "
        f"schema v{book.get('version')} · "
        f"updated {book.get('updated_et') or '—'} · "
        f"scan ended {ended} · "
        f"realized {_fmt_usd(totals.get('realized_pnl_usd'))} · "
        f"equity {_fmt_usd(totals.get('equity_usd')) if totals.get('equity_usd') is not None else '—'}"
        f"</p>",
        unsafe_allow_html=True,
    )

    if book.get("reason") == "missing_paper_book":
        st.info(
            "No paper book yet — run Track 100 `scan_live` (sibling project). "
            "Expected file: `results/paper_book.json` under Track 100, or set "
            "`TRACK100_PAPER_BOOK` / `TRACK100_ROOT`."
        )
    elif book.get("reason") == "scan_artifact_only":
        st.info(
            "Found a dated Track 100 scan artifact, but no `paper_book.json` "
            "yet. Waiting/armed/fills will appear once the Track 100 writer "
            "persists the paper ledger."
        )
    elif not book.get("ok"):
        st.warning(f"Track 100 paper book not loaded: {book.get('reason')}")

    if book.get("path"):
        st.caption(f"Reading `{book['path']}`")
    else:
        tried = ", ".join(str(p) for p in paper_book_candidates()[:4])
        st.caption(f"Looked for paper book at: {tried}")

    def _table(title: str, rows: list[dict[str, Any]], cols: list[tuple[str, str]]) -> None:
        st.subheader(title)
        if not rows:
            st.caption(f"None.")
            return
        show = []
        for r in rows[:40]:
            show.append({label: r.get(key) for label, key in cols})
        st.dataframe(pd.DataFrame(show), use_container_width=True, hide_index=True)

    _table(
        "Waiting retest",
        book.get("waiting_retest") or [],
        [("Symbol", "symbol"), ("Signal low", "signal_low"), ("Notes", "notes")],
    )
    _table(
        "Retest-armed / recent fills",
        book.get("armed") or [],
        [
            ("Symbol", "symbol"),
            ("Signal low", "signal_low"),
            ("Armed", "armed_et"),
            ("Notes", "notes"),
        ],
    )
    open_rows = []
    for r in book.get("open") or []:
        open_rows.append({
            "Symbol": r.get("symbol"),
            "Entry": r.get("entry") if r.get("entry") is not None else r.get("entry_price"),
            "Qty": r.get("qty") if r.get("qty") is not None else r.get("shares"),
            "Notional $": r.get("notional") or r.get("target_notional"),
            "Stop": r.get("stop_pct") if r.get("stop_pct") is not None else 5,
            "Target": r.get("target_pct") if r.get("target_pct") is not None else 15,
            "Opened": r.get("opened_et") or r.get("opened_at"),
        })
    st.subheader("Open paper legs")
    if open_rows:
        st.dataframe(pd.DataFrame(open_rows), use_container_width=True, hide_index=True)
    else:
        st.info("No open Track 100 paper legs.")

    closed_rows = []
    for r in list(reversed(book.get("closed") or []))[:40]:
        closed_rows.append({
            "Symbol": r.get("symbol"),
            "Entry": r.get("entry") if r.get("entry") is not None else r.get("entry_price"),
            "Exit": r.get("exit") if r.get("exit") is not None else r.get("exit_price"),
            "P&L %": r.get("pnl_pct"),
            "P&L $": _fmt_usd(r.get("pnl_usd")) if r.get("pnl_usd") is not None else "—",
            "Reason": r.get("reason") or r.get("exit_reason"),
            "Closed": r.get("closed_et") or r.get("closed_at"),
        })
    st.subheader("Recent closed paper legs")
    if closed_rows:
        st.dataframe(pd.DataFrame(closed_rows), use_container_width=True, hide_index=True)
    else:
        st.caption("No closed Track 100 paper legs yet.")

    with st.expander("Paper book contract (for the Track 100 writer)"):
        st.markdown(
            """
Track 100 **writes**; Q-ALPHA **reads**. Paper log only — **no IBKR**.

Headline P&L is **dollars** (`totals.realized_pnl_usd`, `closed.pnl_usd`).
Percent stays on the row as `pnl_pct`. Equity is `totals.equity_usd`
($5,000 start + realized dollars, open legs at cost).

```json
{
  "version": 3,
  "strategy": "Track100_v12A",
  "updated_et": "2026-09-21T17:23:00-04:00",
  "last_scan": {"date_et": "2026-09-21", "ended": "...", "exit_code": 0, "sizing": "half_equity", "max_open_seats": 2},
  "open": [{"symbol": "TMUS", "entry": 166.6, "qty": 6.0, "notional": 1000.0, "opened_et": ""}],
  "closed": [{
    "symbol": "PEP", "entry": 134.5, "exit": 128.7,
    "pnl_pct": -4.32, "pnl_usd": -21.58,
    "reason": "target|stop|time|ratchet|morning_veto", "closed_et": ""
  }],
  "totals": {
    "realized_pnl_pct": 0, "realized_pnl_usd": 0,
    "equity_usd": 5000, "cash_usd": 5000,
    "n_closed": 0, "n_open": 0
  }
}
```
"""
        )
        now_et = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")
        st.caption(f"Contract checked {now_et} · Q-ALPHA display only")
