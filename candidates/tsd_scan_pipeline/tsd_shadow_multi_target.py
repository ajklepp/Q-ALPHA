#!/usr/bin/env python3
"""
Shadow Peak Hour paper book — 3R multi-target exits alongside live 4T.

Live IBKR path stays on the php_keep_profit 4-tranche trail
(T1 hard bank off unless TSD_LIVE_T1_HARD_BANK=1).
This book mirrors the same fills and banks slices in software only
(no second broker SELLs, no capacity/pool impact).

State: candidates/tsd_shadow_mt3_book.json (via state_paths).

Dashboard / trail monitor must sync from the Peak Hour live book
(tsd_book_state.json) because the on-fill hook can miss historical
fills (process not yet pulled, swallowed import error, or Cloud
dashboard with no gitignored state file). Sync is idempotent.
"""
from __future__ import annotations

import argparse
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
LIVE_BOOK_FILE = "tsd_book_state.json"
COST_PER_TRADE = 0.0015


def shadow_book_path() -> Path:
    return state_path(SHADOW_BOOK_FILE)


def live_book_path() -> Path:
    return state_path(LIVE_BOOK_FILE)


def empty_shadow_book() -> dict[str, Any]:
    return {
        "version": "1",
        "exit_mode": "multi_target_3r",
        "ladder": dict(LADDER_MT3),
        "legs": [],
        "closed": [],
        "updated_at": None,
        "sync_source": None,
        "notes": (
            "Shadow paper: same live fills as Peak Hour 4T book; exits are 3R "
            "hard banks (0.35/0.50/0.90R @ 50/25/25) + 5% kill. No broker "
            "orders. Not Track 100."
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


def load_live_book() -> dict[str, Any] | None:
    """Load Peak Hour live book, or None if missing/invalid."""
    path = live_book_path()
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(doc, dict):
        return None
    return doc


def _live_leg_key(symbol: str, opened_at: str | None, order_id: Any = None) -> str:
    """Stable identity: prefer broker order_id, else book opened_at."""
    sym = str(symbol).upper()
    if order_id is not None and str(order_id) != "":
        return f"{sym}|oid:{order_id}"
    return f"{sym}|at:{opened_at or ''}"


def _order_id_of(leg: dict[str, Any]) -> str:
    if leg.get("order_id") not in (None, ""):
        return str(leg["order_id"])
    meta = leg.get("meta") or {}
    if isinstance(meta, dict) and meta.get("order_id") not in (None, ""):
        return str(meta["order_id"])
    key = str(leg.get("live_leg_key") or "")
    if "|oid:" in key:
        return key.split("|oid:", 1)[1]
    # Legacy key: SYMBOL|opened_at|order_id
    parts = key.split("|")
    if len(parts) >= 3 and parts[-1] and not parts[-1].startswith("at:"):
        return parts[-1]
    return ""


def _find_matching_leg(
    book: dict[str, Any],
    *,
    symbol: str,
    opened_at: str | None,
    order_id: Any = None,
) -> dict[str, Any] | None:
    """Idempotent match across open + closed, including legacy keys."""
    sym = str(symbol).upper()
    key = _live_leg_key(sym, opened_at, order_id)
    oid = str(order_id) if order_id not in (None, "") else ""
    opened = str(opened_at or "")
    pool = list(book.get("legs") or []) + list(book.get("closed") or [])
    for leg in pool:
        if str(leg.get("symbol") or "").upper() != sym:
            continue
        if str(leg.get("live_leg_key") or "") == key:
            return leg
        if oid and _order_id_of(leg) == oid:
            return leg
        if opened and str(leg.get("opened_at") or "") == opened:
            return leg
        # Legacy execute_live_entries key: SYMBOL|now|oid
        legacy = f"{sym}|{opened}|{oid}"
        if oid and str(leg.get("live_leg_key") or "") == legacy:
            return leg
    return None


def _append_open_leg(book: dict[str, Any], leg: dict[str, Any]) -> dict[str, Any]:
    book.setdefault("legs", []).append(leg)
    return leg


def mirror_live_fill(
    *,
    symbol: str,
    fill_price: float,
    shares: int,
    opened_at: str | None = None,
    order_id: Any = None,
    meta: dict[str, Any] | None = None,
    book: dict[str, Any] | None = None,
    persist: bool = True,
) -> dict[str, Any] | None:
    """
    Mirror a live FILLED entry into the 3R shadow book.

    Idempotent on live_leg_key / order_id / opened_at.
    Never touches IBKR or live capacity.
    """
    if shares <= 0 or fill_price <= 0:
        return None
    when = opened_at or datetime.now(ET).isoformat()
    key = _live_leg_key(symbol, when, order_id)
    own_book = book is None
    book = book if book is not None else load_shadow_book()
    existing = _find_matching_leg(
        book, symbol=symbol, opened_at=when, order_id=order_id,
    )
    if existing is not None:
        return existing

    extra = dict(meta or {})
    if order_id not in (None, ""):
        extra.setdefault("order_id", order_id)
    extra.setdefault("live_exit", "4t_keep_profit")
    extra.setdefault("shadow_exit", "mt3_035_050_090")
    extra.setdefault("not_track_100", True)

    leg = open_multi_target_leg(
        symbol=symbol,
        entry_price=float(fill_price),
        shares=int(shares),
        ladder=book.get("ladder") or LADDER_MT3,
        opened_at=when,
        live_leg_key=key,
        meta=extra,
    )
    if order_id not in (None, ""):
        leg["order_id"] = order_id
    _append_open_leg(book, leg)
    if persist or own_book:
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
    book: dict[str, Any] | None = None,
    persist: bool = True,
) -> list[dict[str, Any]]:
    """Advance all open shadow legs for symbol. Returns exit events."""
    del last  # accepted for quote compatibility; ladder uses high/low
    sym = str(symbol).upper()
    when_s = when or datetime.now(ET).isoformat()
    own_book = book is None
    book = book if book is not None else load_shadow_book()
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
        if persist or own_book:
            save_shadow_book(book)
    else:
        book["legs"] = still_open
    return events


def tick_open_shadows(
    fetch_quote: Callable[[str], dict[str, Any] | None],
    *,
    when: str | None = None,
) -> list[dict[str, Any]]:
    """
    Quote every open shadow symbol and advance ladders.

    fetch_quote(symbol) -> {high, low, last/close} or None.
    When the live snapshot includes session_high/session_low, paper uses
    that day's range. Live software stops do not.
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
            # Paper 3R still marks the session range. Live software stops use
            # last/interval low and must not read session_low (NUAI 2026-09-23).
            high = float(
                q.get("session_high") or q.get("high") or q.get("last") or q.get("close") or 0
            )
            low = float(
                q.get("session_low") or q.get("low") or q.get("last") or q.get("close") or 0
            )
            last = float(q.get("last") or q.get("close") or 0)
        except (TypeError, ValueError):
            continue
        if high <= 0 or low <= 0:
            continue
        events.extend(
            advance_symbol(sym, high=high, low=low, last=last, when=when_s)
        )
    return events


def _finite(val: Any) -> float | None:
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    if v != v or v == 0:  # NaN or zero — prices must be > 0
        return None
    return v


def iter_live_book_fills(book: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Extract Peak Hour entry fills from tsd_book_state.json.

    One row per booked leg (open or closed). Used to backfill the 3R book.
    """
    out: list[dict[str, Any]] = []
    for pos in book.get("positions") or []:
        sym = str(pos.get("symbol") or "").upper()
        if not sym:
            continue
        for leg in pos.get("legs") or []:
            trail = leg.get("trail") or {}
            px = _finite(leg.get("price")) or _finite(trail.get("entry_price"))
            try:
                sh = int(leg.get("shares") or 0)
            except (TypeError, ValueError):
                sh = 0
            if px is None or px <= 0 or sh <= 0:
                continue
            opened = str(
                leg.get("time")
                or trail.get("opened_at")
                or pos.get("opened_at")
                or ""
            )
            peak = (
                _finite(trail.get("peak_high"))
                or _finite(trail.get("run_high"))
                or _finite(leg.get("peak_high"))
            )
            last = (
                _finite(trail.get("last_close"))
                or _finite(trail.get("last_price"))
                or _finite(leg.get("price"))
            )
            exits = list(leg.get("exits") or [])
            closed_at = ""
            for ex in exits:
                t = str(ex.get("time") or "")
                if t >= closed_at:
                    closed_at = t
            out.append({
                "symbol": sym,
                "fill_price": px,
                "shares": sh,
                "opened_at": opened,
                "order_id": leg.get("order_id"),
                "status": str(leg.get("status") or "OPEN").upper(),
                "peak_high": peak,
                "last": last,
                "exits": exits,
                "closed_at": closed_at or None,
                "meta": {
                    "kind": "ADDON" if leg.get("is_addon") else "NEW",
                    "bar_state": leg.get("bar_state"),
                    "source": "live_book",
                },
            })
    return out


def replay_shadow_from_live_path(
    leg: dict[str, Any],
    *,
    peak_high: float | None,
    last: float | None,
    exits: list[dict[str, Any]] | None,
    live_status: str,
    closed_at: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """
    Reconstruct 3R banks from an already-observed Peak Hour path.

    Bar sequence is not stored on the live book, so we use a two-step
    approximation that does **not** look ahead of the live record:

    1. MFE tick (high=peak_high, low=entry) so targets that already printed
       can bank. Kill is not applied on this tick.
    2. Each live exit in time order as a high/low tick (path-first kill
       still applies on that tick).
    3. If the live twin is CLOSED and the shadow is still open, mark_flat
       at the last exit / last mark (live twin closed — software only).

    Returns (updated_leg, changed).
    """
    if str(leg.get("status") or "").upper() == "CLOSED":
        return leg, False
    entry = float(leg.get("entry_price") or 0)
    if entry <= 0:
        return leg, False
    changed = False
    opened = str(leg.get("opened_at") or "")

    if peak_high and peak_high > entry:
        when = opened or datetime.now(ET).isoformat()
        leg, ev, _ = advance_multi_target_leg(
            leg, high=float(peak_high), low=entry, when=when,
        )
        if ev:
            changed = True

    for ex in exits or []:
        px = _finite(ex.get("exit_price"))
        if px is None:
            continue
        when = str(ex.get("time") or closed_at or datetime.now(ET).isoformat())
        high = max(px, entry)
        low = min(px, entry)
        if str(leg.get("status") or "").upper() == "CLOSED":
            break
        leg, ev, _ = advance_multi_target_leg(
            leg, high=high, low=low, when=when,
        )
        if ev:
            changed = True

    if (
        str(live_status).upper() == "CLOSED"
        and str(leg.get("status") or "").upper() == "OPEN"
    ):
        mark = None
        if exits:
            mark = _finite((exits or [])[-1].get("exit_price"))
        if mark is None:
            mark = last or entry
        when = closed_at or datetime.now(ET).isoformat()
        leg = mark_flat_leg(leg, mark=float(mark), when=when, reason="live_twin_closed")
        changed = True
    return leg, changed


def _relocate_flat_legs(book: dict[str, Any]) -> None:
    still: list[dict[str, Any]] = []
    for leg in list(book.get("legs") or []):
        if str(leg.get("status") or "").upper() == "CLOSED":
            book.setdefault("closed", []).append(leg)
        else:
            still.append(leg)
    book["legs"] = still


def sync_shadow_from_live_book(
    live_book: dict[str, Any] | None = None,
    *,
    persist: bool = True,
) -> dict[str, Any]:
    """
    Idempotent backfill: every Peak Hour booked fill becomes a 3R shadow leg.

    Safe to run on dashboard load and every trail tick. Does not change
    live 4T exits, pool, or IBKR orders.
    """
    live = live_book if live_book is not None else load_live_book()
    shadow = load_shadow_book()
    if live is None:
        return {
            "n_mirrored": 0,
            "n_replayed": 0,
            "n_open": sum(
                1 for l in (shadow.get("legs") or [])
                if str(l.get("status") or "").upper() == "OPEN"
            ),
            "n_closed": len(shadow.get("closed") or []),
            "source": None,
            "reason": "no_live_book",
        }

    n_mirrored = 0
    n_replayed = 0
    for fill in iter_live_book_fills(live):
        before = _find_matching_leg(
            shadow,
            symbol=fill["symbol"],
            opened_at=fill.get("opened_at"),
            order_id=fill.get("order_id"),
        )
        leg = mirror_live_fill(
            symbol=fill["symbol"],
            fill_price=float(fill["fill_price"]),
            shares=int(fill["shares"]),
            opened_at=fill.get("opened_at") or None,
            order_id=fill.get("order_id"),
            meta=fill.get("meta"),
            book=shadow,
            persist=False,
        )
        if leg is None:
            continue
        if before is None:
            n_mirrored += 1
        if str(leg.get("status") or "").upper() != "OPEN":
            continue
        # Replay against the in-memory open list entry (same object).
        updated, changed = replay_shadow_from_live_path(
            leg,
            peak_high=fill.get("peak_high"),
            last=fill.get("last"),
            exits=fill.get("exits") or [],
            live_status=str(fill.get("status") or "OPEN"),
            closed_at=fill.get("closed_at"),
        )
        if changed:
            n_replayed += 1
            # If replay closed it, relocate after loop.
            _ = updated

    _relocate_flat_legs(shadow)
    shadow["sync_source"] = "live_book"
    if persist:
        save_shadow_book(shadow)
    return {
        "n_mirrored": n_mirrored,
        "n_replayed": n_replayed,
        "n_open": sum(
            1 for l in (shadow.get("legs") or [])
            if str(l.get("status") or "").upper() == "OPEN"
        ),
        "n_closed": len(shadow.get("closed") or []),
        "source": "live_book",
        "reason": "ok",
    }


def sync_shadow_from_cloud_legs(
    open_rows: list[dict[str, Any]] | None,
    closed_rows: list[dict[str, Any]] | None,
    *,
    persist: bool = True,
) -> dict[str, Any]:
    """
    Reconstruct 3R legs from Supabase Peak Hour rows when the local live
    book is missing (Streamlit Cloud / laptop without tsd_book_state.json).

    Coarser than live-book replay: open rows have peak_high; closed rows
    usually have only entry/exit. Still enough to show IRD-style history.
    """
    shadow = load_shadow_book()
    n_mirrored = 0
    n_replayed = 0

    for row in list(open_rows or []) + list(closed_rows or []):
        sym = str(row.get("symbol") or "").upper()
        px = _finite(row.get("entry_price"))
        try:
            sh = int(row.get("shares") or 0)
        except (TypeError, ValueError):
            sh = 0
        if not sym or px is None or sh <= 0:
            continue
        opened = str(row.get("leg_opened_at") or row.get("opened_at") or "")
        status = str(row.get("status") or (
            "CLOSED" if row.get("exit_price") or row.get("closed_at") else "OPEN"
        )).upper()
        if status not in ("OPEN", "CLOSED"):
            status = "CLOSED" if row.get("exit_price") else "OPEN"
        before = _find_matching_leg(
            shadow, symbol=sym, opened_at=opened, order_id=row.get("order_id"),
        )
        leg = mirror_live_fill(
            symbol=sym,
            fill_price=px,
            shares=sh,
            opened_at=opened or None,
            order_id=row.get("order_id"),
            meta={"source": "supabase", "not_track_100": True},
            book=shadow,
            persist=False,
        )
        if leg is None:
            continue
        if before is None:
            n_mirrored += 1
        if str(leg.get("status") or "").upper() != "OPEN":
            continue
        exits = []
        exit_px = _finite(row.get("exit_price"))
        if exit_px is not None:
            exits.append({
                "exit_price": exit_px,
                "time": str(row.get("closed_at") or ""),
                "reason": row.get("exit_reason") or "CLOSED",
                "shares": sh,
            })
        updated, changed = replay_shadow_from_live_path(
            leg,
            peak_high=_finite(row.get("peak_high")),
            last=_finite(row.get("current_price")) or exit_px,
            exits=exits,
            live_status=status if exit_px is None else "CLOSED",
            closed_at=str(row.get("closed_at") or "") or None,
        )
        if changed:
            n_replayed += 1
            _ = updated

    _relocate_flat_legs(shadow)
    if n_mirrored or n_replayed:
        shadow["sync_source"] = "supabase"
        if persist:
            save_shadow_book(shadow)
    return {
        "n_mirrored": n_mirrored,
        "n_replayed": n_replayed,
        "n_open": sum(
            1 for l in (shadow.get("legs") or [])
            if str(l.get("status") or "").upper() == "OPEN"
        ),
        "n_closed": len(shadow.get("closed") or []),
        "source": "supabase" if (n_mirrored or n_replayed) else shadow.get("sync_source"),
        "reason": "ok" if (n_mirrored or n_replayed or (shadow.get("legs") or shadow.get("closed"))) else "no_cloud_legs",
    }


def ensure_shadow_synced(
    *,
    live_book: dict[str, Any] | None = None,
    open_rows: list[dict[str, Any]] | None = None,
    closed_rows: list[dict[str, Any]] | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """
    Dashboard/trail entry point: live book first, Supabase if still empty.
    """
    local = sync_shadow_from_live_book(live_book, persist=persist)
    if (local.get("n_open") or 0) + (local.get("n_closed") or 0) > 0:
        return local
    if local.get("n_mirrored"):
        return local
    if open_rows or closed_rows:
        return sync_shadow_from_cloud_legs(
            open_rows, closed_rows, persist=persist,
        )
    return local


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
        "sync_source": book.get("sync_source"),
        "book_path": str(shadow_book_path()),
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


def _cli() -> int:
    """One-shot repair / scoreboard. Shadow only — no IBKR."""
    parser = argparse.ArgumentParser(
        description="Sync Peak Hour live fills into the 3R shadow paper book.",
    )
    parser.add_argument(
        "--sync", action="store_true",
        help="Backfill from candidates/tsd_book_state.json (idempotent).",
    )
    parser.add_argument(
        "--score", action="store_true",
        help="Print the 3R scoreboard after optional sync.",
    )
    args = parser.parse_args()
    if args.sync:
        summary = sync_shadow_from_live_book()
        print(
            f"3R sync: mirrored={summary['n_mirrored']} "
            f"replayed={summary['n_replayed']} "
            f"open={summary['n_open']} closed={summary['n_closed']} "
            f"reason={summary['reason']}"
        )
        print(f"  book={shadow_book_path()}")
    if args.score or not args.sync:
        sc = scoreboard()
        print(
            f"3R scoreboard: open={sc['n_open']} closed={sc['n_closed']} "
            f"total=${sc['total_pnl']:+.2f} updated={sc.get('updated_at')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
