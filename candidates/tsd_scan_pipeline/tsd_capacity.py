"""
Q-ALPHA TSD pipeline — capacity gates and book state.

UTS v2.6:
  - MAX 2 new entries per hourly 1H scan
  - Dynamic 2..10 full slots (slot-then-size; see slot_ladder)
  - NO daily entry cap
  - MAX 2 add-ons per ticker (3 total entries over trend life)
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz

from state_paths import state_path
from tsd_scan_pipeline.tsd_entry import SessionKind, classify_session
from tsd_scan_pipeline.tsd_structure import init_leg_session_fields

ET = pytz.timezone("America/New_York")

MAX_NEW_ENTRIES_PER_SCAN = 2
MAX_FULL_SLOTS = 10  # ceiling; live cap is slot_ladder N in 2..10
MIN_FULL_SLOTS = 2
MAX_NEW_ENTRIES_PER_DAY = 999  # v2.6: no 2/day cap
MAX_ENTRIES_PER_TICKER = 3
MAX_ADDONS_PER_TICKER = 2
UNIT_S_START = 300.0  # frozen until N==10
SHARE_LOT = 4

TSD_STATE_FILE = "tsd_book_state.json"


def slot_ladder(equity: float) -> tuple[int, float]:
    """
    Slot-then-size: grow N first, then S.

    S starts at $300 frozen until N==10.
    N = min(10, max(2, floor(equity / S_start)))
    When N==10: S = equity / 10.
    """
    eq = max(0.0, float(equity))
    n = min(MAX_FULL_SLOTS, max(MIN_FULL_SLOTS, math.floor(eq / UNIT_S_START)))
    if n >= MAX_FULL_SLOTS:
        s = eq / MAX_FULL_SLOTS if eq > 0 else UNIT_S_START
        return MAX_FULL_SLOTS, s
    return n, UNIT_S_START


def deploy_budget(equity: float, cash: float, open_count: int) -> float:
    """Per-entry notional: min(S, cash / remaining slots). Never full-pool/2."""
    n, s = slot_ladder(equity)
    remaining = max(1, n - int(open_count))
    cash_f = max(0.0, float(cash))
    return min(s, cash_f / remaining)


def shares_for_budget(budget: float, price: float) -> int:
    """Largest SHARE_LOT multiple that fits in budget."""
    if budget <= 0 or price <= 0:
        return 0
    raw = math.floor(float(budget) / float(price))
    shares = (raw // SHARE_LOT) * SHARE_LOT
    return int(shares) if shares >= SHARE_LOT else 0


def _equity_and_cash() -> tuple[float, float]:
    from tsd_scan_pipeline.tsd_pool import load_pool

    doc = load_pool()
    cash = float(doc.get("pool") or 0.0)
    deployed = float(doc.get("deployed") or 0.0)
    return cash + deployed, cash


def current_slot_cap(state: dict[str, Any] | None = None) -> int:
    """Live concurrent slot ceiling from equity ladder."""
    equity, _ = _equity_and_cash()
    n, _ = slot_ladder(equity)
    return n


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
    """Call at start of each hourly 1H launch scan."""
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


def entries_opened_today(state: dict[str, Any], *, when: datetime | None = None) -> int:
    """Count legs opened on the current ET calendar day (informational)."""
    today = (when or datetime.now(ET)).astimezone(ET).date().isoformat()
    count = 0
    for pos in state.get("positions") or []:
        for leg in pos.get("legs") or []:
            leg_time = str(leg.get("time") or "")[:10]
            if leg_time == today:
                count += 1
    return count


def can_enter(
    state: dict[str, Any],
    symbol: str,
    *,
    is_addon: bool,
    slot_cap: int | None = None,
) -> tuple[bool, str]:
    """
    Check capacity before a new TSD entry.
    Returns (ok, reason).
    """
    sym = symbol.upper()
    entries_scan = int(state.get("entries_this_scan") or 0)
    if entries_scan >= MAX_NEW_ENTRIES_PER_SCAN:
        return False, "scan_cap_2"

    pos = _position(state, sym)
    cap = int(slot_cap) if slot_cap is not None else current_slot_cap(state)
    if pos is None:
        if full_slots_used(state) >= cap:
            return False, f"slots_full_{cap}"
        return True, "new"

    entry_count = int(pos.get("entry_count") or 1)
    if entry_count >= MAX_ENTRIES_PER_TICKER:
        return False, "ticker_cap_3"

    if is_addon:
        addons = entry_count - 1
        if addons >= MAX_ADDONS_PER_TICKER:
            return False, "addon_cap_2"
        return True, "addon"

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
    kill_source: str | None = None,
    tsd_profile: dict[str, Any] | None = None,
    session_at_entry: SessionKind | str | None = None,
    bar_state: str | None = None,
    bar_hour: Any = None,
    continuation_score: float | None = None,
    print_tag: str | None = None,
    outlook: str | None = None,
    structure_level: float | None = None,
) -> dict[str, Any]:
    """Book a filled entry into TSD state."""
    from tsd_scan_pipeline.tsd_kill import resolve_kill_pct
    from tsd_scan_pipeline.tsd_trail import init_trail_state

    sym = symbol.upper()
    pos = _position(state, sym)
    now = datetime.now(ET).isoformat()
    session = session_at_entry or classify_session()
    trail = init_trail_state(entry_price, shares, tsd_profile)
    resolved_kill, resolved_src = resolve_kill_pct(kill_pct, profile=tsd_profile)
    # Keep trail kill in sync with broker kill band
    trail["kill_pct"] = resolved_kill
    trail["kill_price"] = float(entry_price) * (1.0 - resolved_kill)
    trail["kill_source"] = kill_source or resolved_src or trail.get("kill_source")
    leg = {
        "time": now,
        "price": entry_price,
        "shares": shares,
        "scan_score": scan_score,
        "is_addon": is_addon,
        "order_id": order_id,
        "kill_order_id": kill_order_id,
        "kill_pct": resolved_kill,
        "kill_source": kill_source or resolved_src,
        "bar_state": bar_state,
        "bar_hour": bar_hour,
        "continuation_score": continuation_score,
        "print": print_tag,
        "outlook": outlook,
        "structure_level": structure_level,  # research only — not broker kill
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
