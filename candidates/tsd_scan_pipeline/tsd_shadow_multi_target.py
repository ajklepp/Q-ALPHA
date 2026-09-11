#!/usr/bin/env python3
"""
Shadow Peak Hour paper book — 3R multi-target exits alongside live 4T.

Live IBKR path stays on php_keep_profit 4-tranche ratchet.
This book mirrors the same fills and banks slices in software only
(no second broker SELLs, no capacity/pool impact).

State: candidates/tsd_shadow_mt3_book.json (via state_paths).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import pytz

from state_paths import state_path
from tsd_scan_pipeline.tsd_multi_target import (
    LADDER_MT3,
    advance_multi_target_leg,
    mark_flat_leg,
    open_multi_target_leg,
    slice_caption,
)

ET = pytz.timezone("America/New_York")
SHADOW_BOOK_FILE = "tsd_shadow_mt3_book.json"
COST_PER_TRADE = 0.0015


def shadow_book_path() -> Path:
    return state_path(SHADOW_BOOK_FILE)


def empty_shadow_book() -> dict[str, Any]:
    return {
        "version": "1",
        "exit_mode": "multi_target_3r",
        "ladder": dict(LADDER_MT3),
        "legs": [],
        "closed": [],
        "updated_at": None,
        "notes": (
            "Shadow paper: same live fills as 4T book; exits are 3R hard banks "
            "(0.35/0.50/0.90R @ 50/25/25) + 5% kill. No broker orders."
        ),
    }


def load_shadow_book() -> dict[str, Any]:
    path = shadow_book_path()
    if not path.is_file():
        return empty_shadow_book()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return empty_shadow_book()
    if not isinstance(doc, dict):
        return empty_shadow_book()
    doc.setdefault("legs", [])
    doc.setdefault("closed", [])
    doc.setdefault("ladder", dict(LADDER_MT3))
    doc.setdefault("exit_mode", "multi_target_3r")
    return doc


def save_shadow_book(doc: dict[str, Any]) -> Path:
    doc["updated_at"] = datetime.now(ET).isoformat()
    path = shadow_book_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
    return path


def _live_leg_key(symbol: str, opened_at: str | None, order_id: Any = None) -> str:
    oid = order_id if order_id is not None else ""
    return f"{str(symbol).upper()}|{opened_at or ''}|{oid}"


def mirror_live_fill(
    *,
    symbol: str,
    fill_price: float,
    shares: int,
    opened_at: str | None = None,
    order_id: Any = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Mirror a live FILLED entry into the 3R shadow book.

    Idempotent on live_leg_key. Never touches IBKR or live capacity.
    """
    if shares <= 0 or fill_price <= 0:
        return None
    when = opened_at or datetime.now(ET).isoformat()
    key = _live_leg_key(symbol, when, order_id)
    book = load_shadow_book()
    for leg in book.get("legs") or []:
        if leg.get("live_leg_key") == key and str(leg.get("status")) == "OPEN":
            return leg
    for leg in book.get("closed") or []:
        if leg.get("live_leg_key") == key:
            return leg

    leg = open_multi_target_leg(
        symbol=symbol,
        entry_price=float(fill_price),
        shares=int(shares),
        ladder=book.get("ladder") or LADDER_MT3,
        opened_at=when,
        live_leg_key=key,
        meta=meta,
    )
    book.setdefault("legs", []).append(leg)
    save_shadow_book(book)
    print(
        f"  SHADOW MT3 mirror {symbol}: {shares} @ {fill_price:.2f} "
        f"ladder={leg.get('ladder_id')} key={key}"
    )
    return leg


def advance_symbol(
    symbol: str,
    *,
    high: float,
    low: float,
    last: float | None = None,
    when: str | None = None,
) -> list[dict[str, Any]]:
    """Advance all open shadow legs for symbol. Returns exit events."""
    sym = str(symbol).upper()
    when_s = when or datetime.now(ET).isoformat()
    book = load_shadow_book()
    events: list[dict[str, Any]] = []
    still_open: list[dict[str, Any]] = []
    changed = False
    for leg in list(book.get("legs") or []):
        if str(leg.get("symbol") or "").upper() != sym:
            still_open.append(leg)
            continue
        if str(leg.get("status") or "").upper() != "OPEN":
            book.setdefault("closed", []).append(leg)
            changed = True
            continue
        leg2, exits, flat = advance_multi_target_leg(
            leg, high=float(high), low=float(low), when=when_s,
        )
        if exits:
            changed = True
            for ex in exits:
                events.append({"symbol": sym, **ex, "pnl_so_far": leg2.get("pnl")})
                print(
                    f"  SHADOW MT3 {sym} {ex['id']} {ex['reason']} "
                    f"{ex['shares']}@{ex['exit_price']:.4f}"
                )
        if flat:
            book.setdefault("closed", []).append(leg2)
            changed = True
        else:
            still_open.append(leg2)
            if exits:
                changed = True
    if changed:
        book["legs"] = still_open
        save_shadow_book(book)
    return events


def tick_open_shadows(
    fetch_quote: Callable[[str], dict[str, Any] | None],
    *,
    when: str | None = None,
) -> list[dict[str, Any]]:
    """
    Quote every open shadow symbol and advance ladders.

    fetch_quote(symbol) -> {high, low, last/close} or None.
    """
    book = load_shadow_book()
    open_syms = sorted({
        str(l.get("symbol") or "").upper()
        for l in (book.get("legs") or [])
        if str(l.get("status") or "").upper() == "OPEN"
    })
    events: list[dict[str, Any]] = []
    when_s = when or datetime.now(ET).isoformat()
    for sym in open_syms:
        q = fetch_quote(sym)
        if not q:
            continue
        try:
            high = float(q.get("high") or q.get("last") or q.get("close") or 0)
            low = float(q.get("low") or q.get("last") or q.get("close") or 0)
            last = float(q.get("last") or q.get("close") or 0)
        except (TypeError, ValueError):
            continue
        if high <= 0 or low <= 0:
            continue
        events.extend(
            advance_symbol(sym, high=high, low=low, last=last, when=when_s)
        )
    return events


def scoreboard(*, mark_by_symbol: dict[str, float] | None = None) -> dict[str, Any]:
    """Aggregate realized + MTM for dashboard."""
    book = load_shadow_book()
    marks = mark_by_symbol or {}
    open_legs = [
        l for l in (book.get("legs") or [])
        if str(l.get("status") or "").upper() == "OPEN"
    ]
    closed = list(book.get("closed") or [])
    realized = sum(float(c.get("pnl") or 0) for c in closed)
    # Partial banks already in realized_gross on open legs
    open_locked = sum(float(l.get("realized_gross") or 0) for l in open_legs)
    mtm = 0.0
    for l in open_legs:
        rem = int(l.get("remaining") or 0)
        if rem <= 0:
            continue
        entry = float(l.get("entry_price") or 0)
        sym = str(l.get("symbol") or "").upper()
        px = float(marks.get(sym) or entry)
        mtm += (px - entry) * rem
    # open locked banks already realized vs entry; include in equity
    equity_delta = realized + open_locked + mtm
    wins = sum(1 for c in closed if float(c.get("pnl") or 0) > 0)
    return {
        "ladder": book.get("ladder") or LADDER_MT3,
        "n_open": len(open_legs),
        "n_closed": len(closed),
        "n_wins": wins,
        "win_rate": round(wins / len(closed), 4) if closed else None,
        "realized_pnl": round(realized, 2),
        "open_locked_pnl": round(open_locked, 2),
        "open_mtm": round(mtm, 2),
        "total_pnl": round(equity_delta, 2),
        "updated_at": book.get("updated_at"),
        "open_legs": open_legs,
        "closed_legs": closed[-50:],  # recent
        "notes": book.get("notes"),
    }


def format_open_rows(score: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for l in score.get("open_legs") or []:
        rows.append({
            "symbol": l.get("symbol"),
            "entry": l.get("entry_price"),
            "shares": l.get("shares"),
            "remaining": l.get("remaining"),
            "locked_$": round(float(l.get("realized_gross") or 0), 2),
            "slices": slice_caption(l),
            "opened_at": l.get("opened_at"),
        })
    return rows
