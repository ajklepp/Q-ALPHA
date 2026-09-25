"""
Shared reader for Streamlit Cloud strategy paper books.

Same contract as ``dashboard_track100``: Q-ALPHA displays JSON another
process may write. This module never places broker orders and never reads
laptop-only absolute paths. Cloud reads a repo-relative file; an env path
may override it.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz

ROOT = Path(__file__).resolve().parent
ET = pytz.timezone("America/New_York")

# Seykota risk card used by PRO MIX and the SEYKOTA daily book (paper only).
PAPER_STARTING_EQUITY_USD = 5_000.0  # paper account size, separate from Peak Hour
PAPER_RISK_PER_TRADE_PCT = 0.01  # fraction of equity risked on each new long
PAPER_MAX_HEAT_PCT = 0.20  # open risk / equity cap

PEAK_HOUR_NOTE = (
    "Live Peak Hour on the Live Status tab is unchanged. "
    "This tab is paper display only and does not send TWS orders."
)

SCHEMA_VERSION = 1
TABLE_ROW_LIMIT = 40  # recent paper legs shown in each table
HEAT_ALREADY_PERCENT_ABOVE = 1.5  # writer stored 20 instead of 0.20


def strategy_book_candidates(env_var: str, repo_relative: Path) -> list[Path]:
    """Env file first, then the committed repo-relative scaffold."""
    out: list[Path] = []
    env_file = (os.environ.get(env_var) or "").strip()
    if env_file:
        out.append(Path(env_file).expanduser())
    out.append(ROOT / repo_relative)
    return _dedupe_paths(out)


def empty_strategy_book(
    *,
    strategy: str,
    alias: str,
    reason: str,
    path: str | None = None,
) -> dict[str, Any]:
    """Valid empty ledger when the paper file is missing or unreadable."""
    return {
        "version": SCHEMA_VERSION,
        "strategy": strategy,
        "alias": alias,
        "mode": "PAPER",
        "side": "long_only",
        "scaffold": True,
        "ok": False,
        "reason": reason,
        "updated_et": None,
        "path": path,
        "risk_card": _default_risk_card(),
        "rules": {},
        "equity_curve": [],
        "open": [],
        "closed": [],
        "totals": _default_totals(),
        "looked_for": [],
    }


def load_strategy_paper_book(
    *,
    env_var: str,
    repo_relative: Path,
    strategy: str,
    alias: str,
) -> dict[str, Any]:
    """
    Load one strategy paper book.

    A missing file is an empty PAPER state, not a crashed tab. The first
    existing candidate wins (explicit env path, then the repo scaffold).
    """
    candidates = strategy_book_candidates(env_var, repo_relative)
    path = next((p for p in candidates if p.is_file()), None)
    looked = [str(p) for p in candidates]
    if path is None:
        book = empty_strategy_book(strategy=strategy, alias=alias, reason="missing_paper_book")
        book["looked_for"] = looked
        return book
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        book = empty_strategy_book(
            strategy=strategy,
            alias=alias,
            reason=f"unreadable:{exc}",
            path=str(path),
        )
        book["looked_for"] = looked
        return book
    if not isinstance(doc, dict):
        book = empty_strategy_book(
            strategy=strategy,
            alias=alias,
            reason="invalid_json",
            path=str(path),
        )
        book["looked_for"] = looked
        return book
    book = normalize_strategy_book(doc, path=str(path), strategy=strategy, alias=alias)
    book["looked_for"] = looked
    return book


def normalize_strategy_book(
    doc: dict[str, Any],
    *,
    path: str,
    strategy: str,
    alias: str,
) -> dict[str, Any]:
    """Coerce a writer document into the display shape. Ignores unknown keys."""
    open_legs = _as_rows(doc.get("open") if doc.get("open") is not None else doc.get("open_legs"))
    closed = _as_rows(doc.get("closed") if doc.get("closed") is not None else doc.get("closed_legs"))
    curve = _as_rows(doc.get("equity_curve"))
    totals_in = doc.get("totals") if isinstance(doc.get("totals"), dict) else {}
    risk = doc.get("risk_card") if isinstance(doc.get("risk_card"), dict) else {}
    rules = doc.get("rules") if isinstance(doc.get("rules"), dict) else {}

    equity = _f(totals_in.get("equity_usd"))
    if equity is None:
        equity = _f(risk.get("starting_equity_usd"), PAPER_STARTING_EQUITY_USD)
    starting = _f(totals_in.get("starting_cash"))
    if starting is None:
        starting = _f(risk.get("starting_equity_usd"), PAPER_STARTING_EQUITY_USD)
    realized = _f(totals_in.get("realized_pnl_usd"), 0.0)
    heat = _heat_fraction(totals_in, open_legs, equity)

    totals = {
        "starting_cash": starting,
        "equity_usd": equity,
        "cash_usd": _f(totals_in.get("cash_usd")),
        "open_risk_usd": _f(totals_in.get("open_risk_usd")),
        "heat_pct": heat,
        "realized_pnl_usd": realized,
        "realized_pnl_pct": _f(totals_in.get("realized_pnl_pct")),
        "n_open": len(open_legs),
        "n_closed": len(closed),
    }
    return {
        "version": doc.get("version") or SCHEMA_VERSION,
        "strategy": doc.get("strategy") or strategy,
        "alias": doc.get("alias") or alias,
        "mode": "PAPER",
        "side": doc.get("side") or "long_only",
        "scaffold": bool(doc.get("scaffold")),
        "ok": True,
        "reason": "ok",
        "updated_et": doc.get("updated_et") or doc.get("updated_at"),
        "path": path,
        "risk_card": {**_default_risk_card(), **{k: risk[k] for k in risk}},
        "rules": rules,
        "equity_curve": curve,
        "open": open_legs,
        "closed": closed,
        "universe": universe_rows(doc),
        "totals": totals,
        "source_project": doc.get("source_project"),
        "notes": doc.get("notes"),
    }


def universe_rows(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Heat-list rows the PRO MIX writer stored on the paper book.

    Q-ALPHA does not build this list. An explicit empty list stays empty.
    """
    for key in ("universe", "shortlist", "heat_universe"):
        if key not in doc:
            continue
        val = doc.get(key)
        if not isinstance(val, list) or not val:
            return []
        if isinstance(val[0], str):
            return [{"symbol": s} for s in val if str(s).strip()]
        return [r for r in val if isinstance(r, dict)]
    return []


def equity_points(book: dict[str, Any]) -> list[dict[str, Any]]:
    """Plottable equity points. Drops rows that lack a date or a number."""
    points: list[dict[str, Any]] = []
    for row in book.get("equity_curve") or []:
        if not isinstance(row, dict):
            continue
        equity = _f(row.get("equity_usd") if row.get("equity_usd") is not None else row.get("equity"))
        stamp = row.get("date") or row.get("ts") or row.get("updated_et")
        if equity is None or not stamp:
            continue
        points.append({"date": str(stamp), "equity_usd": equity})
    return points


def table_rows(
    rows: list[dict[str, Any]],
    fields: list[tuple[str, tuple[str, ...]]],
    *,
    limit: int = TABLE_ROW_LIMIT,
) -> list[dict[str, Any]]:
    """Project writer rows onto display columns. Missing keys become None."""
    show: list[dict[str, Any]] = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        show.append({label: _first(row, keys) for label, keys in fields})
    return show


def last_update_label(book: dict[str, Any]) -> str:
    """Writer stamp when present; otherwise the file's mtime in ET."""
    stamp = book.get("updated_et")
    if stamp:
        return str(stamp)
    mtime = file_mtime_et(book.get("path"))
    if mtime:
        return f"no writer stamp · file {mtime}"
    return "—"


def file_mtime_et(path: str | None) -> str | None:
    """Local file mtime formatted in America/New_York."""
    if not path:
        return None
    try:
        ts = Path(path).stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(ts, tz=ET).strftime("%Y-%m-%d %H:%M ET")


def display_path(path: str | None) -> str:
    """Repo-relative path when the file lives in this checkout."""
    if not path:
        return "—"
    p = Path(path)
    try:
        return str(p.resolve().relative_to(ROOT))
    except (OSError, ValueError):
        return str(p)


def fmt_usd(val: Any) -> str:
    """Signed dollars. Blank inputs stay an em dash."""
    num = _f(val)
    if num is None:
        return "—"
    if num < 0:
        return f"-${abs(num):,.2f}"
    return f"${num:,.2f}"


def fmt_heat(val: Any) -> str:
    """Heat as a percent. Values above 1.5 are already in percentage points."""
    num = _f(val)
    if num is None:
        return "—"
    if abs(num) <= HEAT_ALREADY_PERCENT_ABOVE:
        return f"{num:.1%}"
    return f"{num:.1f}%"


def render_paper_strategy_tab(
    *,
    title: str,
    subtitle: str,
    rule: str,
    book: dict[str, Any],
    open_fields: list[tuple[str, tuple[str, ...]]],
    schema_md: str,
    source_note: str = "",
    after_open_book: Any = None,
) -> None:
    """Draw one PAPER strategy tab. Swallows render errors so other tabs stay up."""
    import pandas as pd
    import streamlit as st

    from dashboard_theme import MUTED, section_header

    try:
        _render_paper_strategy_tab(
            title=title,
            subtitle=subtitle,
            rule=rule,
            book=book if isinstance(book, dict) else {},
            open_fields=open_fields,
            schema_md=schema_md,
            source_note=source_note,
            after_open_book=after_open_book,
            pd=pd,
            st=st,
            muted=MUTED,
            section_header=section_header,
        )
    except Exception as exc:
        st.error(f"{title} tab could not render: {exc}")


def _render_paper_strategy_tab(
    *,
    title: str,
    subtitle: str,
    rule: str,
    book: dict[str, Any],
    open_fields: list[tuple[str, tuple[str, ...]]],
    schema_md: str,
    source_note: str,
    after_open_book: Any,
    pd: Any,
    st: Any,
    muted: str,
    section_header: Any,
) -> None:
    """Draw the PAPER rule card, totals, equity, and open/closed tables."""
    section_header(title, subtitle)
    with st.container(border=True):
        st.markdown("**Status: PAPER**")
        st.markdown(rule)

    totals = book.get("totals") if isinstance(book.get("totals"), dict) else {}
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Status", "PAPER")
    c2.metric("Equity", fmt_usd(totals.get("equity_usd")))
    c3.metric("Open", str(int(totals.get("n_open") or 0)))
    c4.metric("Heat", fmt_heat(totals.get("heat_pct")))
    c5.metric("Realized", fmt_usd(totals.get("realized_pnl_usd")))
    c6.metric("Closed", str(int(totals.get("n_closed") or 0)))

    st.markdown(
        f"<p style='color:{muted};font-size:0.85rem'>"
        f"Strategy <code>{book.get('strategy') or title}</code> · "
        f"schema v{book.get('version') or SCHEMA_VERSION} · "
        f"last update {last_update_label(book)}"
        f"</p>",
        unsafe_allow_html=True,
    )
    st.caption(
        f"Reading `{display_path(book.get('path'))}` · {PEAK_HOUR_NOTE}"
    )
    if source_note:
        st.caption(source_note)

    if not book.get("ok"):
        st.warning(f"Paper book not loaded ({book.get('reason') or 'unknown'}).")
        looked = book.get("looked_for") or []
        if looked:
            st.caption("Looked for: " + ", ".join(str(p) for p in looked[:4]))
    elif book.get("scaffold") or (
        int(totals.get("n_open") or 0) == 0 and int(totals.get("n_closed") or 0) == 0
    ):
        st.info(
            "Paper book is empty. The scaffold is in place and no paper fills "
            "have been written yet. This tab does not send orders."
        )

    st.subheader("Equity")
    points = equity_points(book)
    if len(points) >= 2:
        st.line_chart(pd.DataFrame(points), x="date", y="equity_usd")
    else:
        st.caption("No equity curve yet. A writer can append `equity_curve` points.")

    st.subheader("Open book")
    open_rows = table_rows(list(book.get("open") or []), open_fields)
    if open_rows:
        st.dataframe(pd.DataFrame(open_rows), use_container_width=True, hide_index=True)
    else:
        st.caption("No open paper longs.")

    st.subheader("Recent closed")
    closed_src = list(reversed(book.get("closed") or []))
    closed_rows = table_rows(closed_src, CLOSED_FIELDS)
    if closed_rows:
        st.dataframe(pd.DataFrame(closed_rows), use_container_width=True, hide_index=True)
    else:
        st.caption("No closed paper longs yet.")

    if after_open_book is not None:
        after_open_book()

    with st.expander("Paper book contract"):
        st.markdown(schema_md)
        st.caption(PEAK_HOUR_NOTE)


CLOSED_FIELDS: list[tuple[str, tuple[str, ...]]] = [
    ("Symbol", ("symbol", "ticker")),
    ("Entry", ("entry", "entry_price")),
    ("Exit", ("exit", "exit_price")),
    ("P&L $", ("pnl_usd",)),
    ("P&L %", ("pnl_pct",)),
    ("Reason", ("reason", "exit_reason")),
    ("Closed", ("closed_et", "closed_at")),
]


def _default_risk_card() -> dict[str, float]:
    """Seykota card used when a writer omits ``risk_card``."""
    return {
        "starting_equity_usd": PAPER_STARTING_EQUITY_USD,
        "risk_per_trade_pct": PAPER_RISK_PER_TRADE_PCT,
        "max_heat_pct": PAPER_MAX_HEAT_PCT,
    }


def _default_totals() -> dict[str, Any]:
    """Flat $5k paper account with zero heat and zero P&L."""
    return {
        "starting_cash": PAPER_STARTING_EQUITY_USD,
        "equity_usd": PAPER_STARTING_EQUITY_USD,
        "cash_usd": PAPER_STARTING_EQUITY_USD,
        "open_risk_usd": 0.0,
        "heat_pct": 0.0,
        "realized_pnl_usd": 0.0,
        "realized_pnl_pct": 0.0,
        "n_open": 0,
        "n_closed": 0,
    }


def _as_rows(val: Any) -> list[dict[str, Any]]:
    """Keep dict rows only so a bad writer list cannot crash the table."""
    if not isinstance(val, list):
        return []
    return [r for r in val if isinstance(r, dict)]


def _first(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    """First present field among writer aliases (``entry`` or ``entry_price``)."""
    for key in keys:
        if key in row and row.get(key) is not None:
            return row.get(key)
    return None


def _f(val: Any, default: float | None = None) -> float | None:
    """Float coercion that treats blanks and junk as ``default``."""
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _heat_fraction(
    totals: dict[str, Any],
    open_legs: list[dict[str, Any]],
    equity: float | None,
) -> float | None:
    """Prefer the writer's heat. Otherwise sum open `risk_usd` / equity."""
    if totals.get("heat_pct") is not None:
        return _f(totals.get("heat_pct"))
    risks: list[float] = []
    for row in open_legs:
        risk = _f(row.get("risk_usd"))
        if risk is not None:
            risks.append(risk)
    if risks and equity:
        return sum(risks) / float(equity)
    if not open_legs:
        return 0.0
    return None


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    """Drop duplicate path strings, keeping the first occurrence."""
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out
