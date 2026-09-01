"""
Q-ALPHA TSD pipeline — capacity gates and book state.

Separate from Strategy Lab SIM and morning agent pool_state.json.
Tracks slot usage (T4-only frees a slot) and per-ticker add-on limits.

Rules (locked):
  - MAX 3 new entries per scan
  - MAX 10 full slots (T1/T2/T3 working; T4-only frees slot)
  - MAX 2 add-ons per ticker (3 total entries over trend life)
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz

from state_paths import state_path
from tsd_scan_pipeline.tsd_entry import SessionKind, classify_session
from tsd_scan_pipeline.tsd_structure import init_leg_session_fields

ET = pytz.timezone("America/New_York")

MAX_NEW_ENTRIES_PER_SCAN = 3
MAX_FULL_SLOTS = 10
MAX_ENTRIES_PER_TICKER = 3
MAX_ADDONS_PER_TICKER = 2

TSD_STATE_FILE = "tsd_book_state.json"


def _default_state() -> dict[str, Any]:
    return {
        "positions": [],
        "entries_this_scan": 0,
        "last_scan_at": None,
    }


def load_state(path: Path | None = None) -> dict[str, Any]:
    """Load TSD book state; empty structure if missing."""
    p = path or state_path(TSD_STATE_FILE)
    if not p.exists():
        return _default_state()
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc.setdefault("positions", [])
    doc.setdefault("entries_this_scan", 0)
    return doc


def save_state(state: dict[str, Any], path: Path | None = None) -> None:
    p = path or state_path(TSD_STATE_FILE)
    p.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def reset_scan_counter(state: dict[str, Any]) -> None:
    """Call at start of each TWS :03 scan."""
    state["entries_this_scan"] = 0
    state["last_scan_at"] = datetime.now(ET).isoformat()


def _position(state: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    sym = symbol.upper()
    for pos in state.get("positions") or []:
        if str(pos.get("symbol", "")).upper() == sym:
            return pos
    return None


def full_slots_used(state: dict[str, Any]) -> int:
    """Count positions occupying a full slot (not T4-only runner)."""
    n = 0
    for pos in state.get("positions") or []:
        if str(pos.get("status", "OPEN")).upper() != "OPEN":
            continue
        if pos.get("t4_only"):
            continue
        n += 1
    return n


def open_symbols(state: dict[str, Any]) -> list[str]:
    return [
        str(p["symbol"]).upper()
        for p in state.get("positions") or []
        if str(p.get("status", "OPEN")).upper() == "OPEN"
    ]


def can_enter(
    state: dict[str, Any],
    symbol: str,
    *,
    is_addon: bool,
) -> tuple[bool, str]:
    """
    Check capacity before a new TSD entry.
    Returns (ok, reason).
    """
    sym = symbol.upper()
    entries_scan = int(state.get("entries_this_scan") or 0)
    if entries_scan >= MAX_NEW_ENTRIES_PER_SCAN:
        return False, "scan_cap_3"

    pos = _position(state, sym)
    if pos is None:
        if full_slots_used(state) >= MAX_FULL_SLOTS:
            return False, "slots_full_10"
        return True, "new"

    entry_count = int(pos.get("entry_count") or 1)
    if entry_count >= MAX_ENTRIES_PER_TICKER:
        return False, "ticker_cap_3"

    if is_addon:
        addons = entry_count - 1
        if addons >= MAX_ADDONS_PER_TICKER:
            return False, "addon_cap_2"
        return True, "addon"

    # Already long — no duplicate unless addon
    return False, "already_long"


def record_entry(
    state: dict[str, Any],
    symbol: str,
    *,
    entry_price: float,
    shares: int,
    scan_score: float,
    is_addon: bool,
    order_id: int | None = None,
    kill_order_id: int | None = None,
    kill_pct: float | None = None,
    tsd_profile: dict[str, Any] | None = None,
    session_at_entry: SessionKind | str | None = None,
) -> dict[str, Any]:
    """Book a filled entry into TSD state."""
    from tsd_scan_pipeline.tsd_trail import init_trail_state

    sym = symbol.upper()
    pos = _position(state, sym)
    now = datetime.now(ET).isoformat()
    session = session_at_entry or classify_session()
    trail = init_trail_state(entry_price, shares, tsd_profile)
    leg = {
        "time": now,
        "price": entry_price,
        "shares": shares,
        "scan_score": scan_score,
        "is_addon": is_addon,
        "order_id": order_id,
        "kill_order_id": kill_order_id,
        "kill_pct": kill_pct,
        "status": "OPEN",
        "trail": trail,
        "exits": [],
        **init_leg_session_fields(session),  # type: ignore[arg-type]
    }
    if pos is None:
        state.setdefault("positions", []).append(
            {
                "symbol": sym,
                "status": "OPEN",
                "entry_count": 1,
                "t4_only": False,
                "legs": [leg],
                "opened_at": now,
            }
        )
    else:
        pos["entry_count"] = int(pos.get("entry_count") or 1) + 1
        pos.setdefault("legs", []).append(leg)

    state["entries_this_scan"] = int(state.get("entries_this_scan") or 0) + 1
    return state


def mark_t4_only(state: dict[str, Any], symbol: str) -> None:
    """T4-only runner frees a full slot."""
    pos = _position(state, symbol)
    if pos:
        pos["t4_only"] = True


def record_leg_exit(
    pos: dict[str, Any],
    *,
    leg_index: int,
    shares: int,
    exit_price: float,
    reason: str,
    tranche_id: str,
    order_id: int | None = None,
) -> None:
    """Record a tranche exit on a position leg."""
    legs = pos.get("legs") or []
    if leg_index < 0 or leg_index >= len(legs):
        return
    leg = legs[leg_index]
    leg.setdefault("exits", []).append(
        {
            "time": datetime.now(ET).isoformat(),
            "shares": shares,
            "exit_price": exit_price,
            "reason": reason,
            "tranche_id": tranche_id,
            "order_id": order_id,
        }
    )
