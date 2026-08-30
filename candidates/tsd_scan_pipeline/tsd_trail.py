"""
Q-ALPHA TSD pipeline — strategy_a 4-tranche software trail engine.

Adapts TSD profiler output to strategy_lab.strategy_a levels and state machine.
Used by tsd_trail_monitor.py for live IBKR exits (no Polygon for trail monitoring).
"""
from __future__ import annotations

import sys
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytz

CANDIDATES_DIR = Path(__file__).resolve().parent.parent
ROOT = CANDIDATES_DIR.parent
LAB = ROOT / "strategy_lab"
for p in (str(CANDIDATES_DIR), str(LAB)):
    if p not in sys.path:
        sys.path.insert(0, p)

from strategy_a import (  # noqa: E402
    MAX_HOLD_TRADING_DAYS,
    SimState,
    TrancheState,
    extract_levels,
    process_bar,
    split_tranches,
    triggers_for_n,
)

ET = pytz.timezone("America/New_York")
PROFILES_DIR = Path(__file__).resolve().parent / "profiles"
FALLBACK_KILL_PCT = 0.07


def tsd_profile_to_strategy_profile(tsd_profile: dict[str, Any]) -> dict[str, Any]:
    """Map TSD profiler JSON to strategy_a extract_levels() input shape."""
    status = str(tsd_profile.get("status") or "INSUFFICIENT").upper()
    meaningful = status == "OK" and int(tsd_profile.get("analog_count") or 0) >= 30
    mae = tsd_profile.get("mae") or {}
    mfe = tsd_profile.get("mfe") or {}
    kill = float(tsd_profile.get("kill_pct") or mae.get("p75") or FALLBACK_KILL_PCT)
    return {
        "confidence": "OK" if meaningful else "INSUFFICIENT",
        "stats_meaningful": meaningful,
        "percentiles": {"mae": mae, "mfe": mfe},
        "bracket": {"safe_max_stop_pct": kill},
    }


def load_tsd_profile(symbol: str) -> dict[str, Any] | None:
    """Load saved TSD profile for symbol, if present."""
    path = PROFILES_DIR / f"{symbol.upper()}_tsd_profile.json"
    if not path.exists():
        return None
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def _tranche_to_dict(t: TrancheState) -> dict[str, Any]:
    return {
        "id": t.id,
        "shares": t.shares,
        "weight": t.weight,
        "trigger_pct": t.trigger_pct,
        "trigger_price": t.trigger_price,
        "trail_pct": t.trail_pct,
        "trailing": t.trailing,
        "run_high": t.run_high,
        "activated_at": t.activated_at,
        "activation_high": t.activation_high,
        "closed": t.closed,
        "exit_price": t.exit_price,
        "exit_time": t.exit_time,
        "exit_reason": t.exit_reason,
        "trail_stop_at_exit": t.trail_stop_at_exit,
    }


def _tranche_from_dict(d: dict[str, Any]) -> TrancheState:
    return TrancheState(
        id=str(d["id"]),
        shares=int(d["shares"]),
        weight=float(d.get("weight") or 0.0),
        trigger_pct=float(d["trigger_pct"]),
        trigger_price=float(d["trigger_price"]),
        trail_pct=float(d["trail_pct"]),
        trailing=bool(d.get("trailing")),
        run_high=float(d.get("run_high") or 0.0),
        activated_at=d.get("activated_at"),
        activation_high=d.get("activation_high"),
        closed=bool(d.get("closed")),
        exit_price=d.get("exit_price"),
        exit_time=d.get("exit_time"),
        exit_reason=d.get("exit_reason"),
        trail_stop_at_exit=d.get("trail_stop_at_exit"),
    )


def sim_state_to_dict(state: SimState) -> dict[str, Any]:
    return {
        "entry_price": state.entry_price,
        "kill_price": state.kill_price,
        "kill_pct": state.kill_pct,
        "trail_pct": state.trail_pct,
        "peak_high": state.peak_high,
        "trading_day": state.trading_day,
        "last_bar_time": state.last_bar_time,
        "last_close": state.last_close,
        "tranches": [_tranche_to_dict(t) for t in state.tranches],
    }


def sim_state_from_dict(doc: dict[str, Any]) -> SimState:
    tranches = [_tranche_from_dict(t) for t in doc.get("tranches") or []]
    return SimState(
        entry_price=float(doc["entry_price"]),
        kill_price=float(doc["kill_price"]),
        kill_pct=float(doc["kill_pct"]),
        trail_pct=float(doc["trail_pct"]),
        tranches=tranches,
        peak_high=float(doc.get("peak_high") or doc["entry_price"]),
        trading_day=int(doc.get("trading_day") or 1),
        last_bar_time=doc.get("last_bar_time"),
        last_close=doc.get("last_close"),
    )


def init_trail_state(
    entry_price: float,
    n_shares: int,
    profile: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Build serializable trail state for a new leg using strategy_a levels.
    """
    strat_profile = tsd_profile_to_strategy_profile(profile or {})
    levels = extract_levels(strat_profile)
    alloc = split_tranches(n_shares)
    trigs = triggers_for_n(levels["triggers_4"], len(alloc))
    tranches: list[TrancheState] = []
    for (tid, sh, w), trig in zip(alloc, trigs):
        tranches.append(
            TrancheState(
                id=tid,
                shares=sh,
                weight=w,
                trigger_pct=float(trig),
                trigger_price=entry_price * (1.0 + float(trig)),
                trail_pct=float(levels["trail_pct"]),
                run_high=0.0,
            )
        )
    state = SimState(
        entry_price=float(entry_price),
        kill_price=float(entry_price) * (1.0 - float(levels["kill_pct"])),
        kill_pct=float(levels["kill_pct"]),
        trail_pct=float(levels["trail_pct"]),
        tranches=tranches,
        peak_high=float(entry_price),
        trading_day=1,
    )
    doc = sim_state_to_dict(state)
    doc["levels_source"] = levels.get("source")
    doc["kill_stop_cancelled"] = False
    doc["opened_at"] = datetime.now(ET).isoformat()
    doc["last_session_date"] = datetime.now(ET).date().isoformat()
    return doc


def remaining_shares(trail_doc: dict[str, Any]) -> int:
    state = sim_state_from_dict(trail_doc)
    return sum(t.shares for t in state.tranches if not t.closed)


def is_t4_only(trail_doc: dict[str, Any]) -> bool:
    """True when only T4 tranche remains open."""
    state = sim_state_from_dict(trail_doc)
    open_tranches = [t for t in state.tranches if not t.closed]
    return len(open_tranches) == 1 and open_tranches[0].id == "T4"


def evaluate_trail_tick(
    trail_doc: dict[str, Any],
    *,
    high: float,
    low: float,
    close: float,
    when: str,
    force_time_cap: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    Advance trail state one price tick. Returns (updated_doc, new_exits).

    new_exits: [{tranche_id, shares, exit_price, reason, when}]
    """
    before = sim_state_from_dict(trail_doc)
    prior_closed = {t.id: t.closed for t in before.tranches}

    state = deepcopy(before)
    process_bar(
        state,
        high=float(high),
        low=float(low),
        close=float(close),
        when=when,
        force_time_cap=force_time_cap,
    )

    exits: list[dict[str, Any]] = []
    for t in state.tranches:
        was_closed = prior_closed.get(t.id, False)
        if not was_closed and t.closed:
            exits.append(
                {
                    "tranche_id": t.id,
                    "shares": int(t.shares),
                    "exit_price": float(t.exit_price or close),
                    "reason": t.exit_reason or "trail",
                    "when": t.exit_time or when,
                }
            )

    updated = sim_state_to_dict(state)
    updated["kill_stop_cancelled"] = trail_doc.get("kill_stop_cancelled", False)
    updated["levels_source"] = trail_doc.get("levels_source")
    updated["opened_at"] = trail_doc.get("opened_at")
    updated["last_session_date"] = trail_doc.get("last_session_date")
    return updated, exits


def maybe_roll_trading_day(trail_doc: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    """Increment trading_day on calendar roll; set force_time_cap when at max hold."""
    today_s = (today or datetime.now(ET).date()).isoformat()
    last = str(trail_doc.get("last_session_date") or today_s)
    if today_s <= last:
        return trail_doc
    doc = dict(trail_doc)
    doc["trading_day"] = int(doc.get("trading_day") or 1) + 1
    doc["last_session_date"] = today_s
    return doc


def at_time_cap(trail_doc: dict[str, Any]) -> bool:
    return int(trail_doc.get("trading_day") or 1) >= MAX_HOLD_TRADING_DAYS
