"""
Peak Hour keep-profit + hybrid structure kill (post-autopsy).

Autopsy (2026-08-31..09-10):
  - 11/20 trades went green then lost; losers avg MFE +3.2% then died into ~5–8% MAE
  - Blanket structure kill LOSES money (hurts runners like CLYM)
  - Half-bank at +2% is the large positive lift
  - Root cause: fallback triggers 3/5/8/10% with 4% trail means T1 cannot
    trail-exit until ~+7% — so early open profit is never kept

Rules (Peak Hour only):
  1) T1 hard-banks at its trigger (default +2%) — no trail on T1
  2) After T1 banks green, raise shared kill to breakeven (−0.3% buffer)
  3) T2–T4 trail as before, with earlier triggers + tighter trail
  4) Hybrid kill at entry: if structure risk in [1%, 2.5%], use structure+0.5%
     (floor 2%); else MAE/fallback 5%. Wide structure keeps 5% (Chat A lesson).
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from strategy_a import (
    SimState,
    TrancheState,
    process_bar,
    split_tranches,
    triggers_for_n,
)

from tsd_scan_pipeline.tsd_kill import (
    FALLBACK_KILL_PCT,
    resolve_kill_pct,
    structure_risk_pct,
)
from tsd_scan_pipeline.tsd_trail import (
    sim_state_from_dict,
    sim_state_to_dict,
)

# Earlier bank / runner ladder (Peak Hour)
PHP_TRIGGERS = (0.02, 0.035, 0.06, 0.10)
PHP_TRAIL_PCT = 0.025
PHP_T1_TRIGGER = 0.02
BE_LOCK_BUFFER = 0.003
STRUCTURE_KILL_MIN = 0.01
STRUCTURE_KILL_MAX = 0.025
STRUCTURE_KILL_BUFFER = 0.005
STRUCTURE_KILL_FLOOR = 0.02


def resolve_php_kill_pct(
    *,
    entry: float,
    structure_level: float | None,
    raw: float | None = None,
    profile: dict[str, Any] | None = None,
) -> tuple[float, str, float | None]:
    """
    Hybrid kill for Peak Hour.

    Near-turn (tight structure risk): stop just under structure.
    Otherwise: profile MAE / 5% fallback (never naked area-low on wide bases).
    Returns (kill_pct, source, kill_price).
    """
    base, src = resolve_kill_pct(raw, profile=profile)
    risk = structure_risk_pct(entry, structure_level)
    if (
        risk is not None
        and STRUCTURE_KILL_MIN <= risk <= STRUCTURE_KILL_MAX
        and structure_level
        and structure_level > 0
        and entry > 0
    ):
        # Just under structure (0.5% buffer below area low, expressed as entry risk)
        struct_pct = min(base, max(STRUCTURE_KILL_FLOOR, risk + STRUCTURE_KILL_BUFFER))
        kill_px = float(entry) * (1.0 - struct_pct)
        # Prefer actual structure-0.5% if still inside band
        under = float(structure_level) * (1.0 - STRUCTURE_KILL_BUFFER)
        if under < entry:
            alt = (entry - under) / entry
            if STRUCTURE_KILL_FLOOR <= alt <= base:
                struct_pct = alt
                kill_px = under
        return round(struct_pct, 6), "structure_near_turn", round(kill_px, 4)

    kill_px = float(entry) * (1.0 - base)
    return base, src, round(kill_px, 4)


def init_php_trail_state(
    entry_price: float,
    n_shares: int,
    *,
    kill_pct: float,
    kill_price: float | None = None,
    triggers: tuple[float, ...] = PHP_TRIGGERS,
    trail_pct: float = PHP_TRAIL_PCT,
) -> dict[str, Any]:
    """Build trail doc with Peak Hour keep-profit levels."""
    alloc = split_tranches(n_shares)
    trigs = triggers_for_n(list(triggers), len(alloc))
    tranches: list[TrancheState] = []
    for (tid, sh, w), trig in zip(alloc, trigs):
        tranches.append(
            TrancheState(
                id=tid,
                shares=sh,
                weight=w,
                trigger_pct=float(trig),
                trigger_price=entry_price * (1.0 + float(trig)),
                trail_pct=float(trail_pct),
                run_high=0.0,
            )
        )
    kp = float(kill_price) if kill_price is not None else entry_price * (1.0 - kill_pct)
    state = SimState(
        entry_price=float(entry_price),
        kill_price=float(kp),
        kill_pct=float(kill_pct),
        trail_pct=float(trail_pct),
        tranches=tranches,
        peak_high=float(entry_price),
        trading_day=1,
    )
    doc = sim_state_to_dict(state)
    doc["php_keep_profit"] = True
    doc["kill_stop_cancelled"] = False
    doc["breakeven_locked"] = False
    doc["levels_source"] = "php_keep_profit_v1"
    return doc


def _close_tranche_at(t: TrancheState, px: float, when: str, reason: str) -> None:
    t.closed = True
    t.trailing = False
    t.exit_price = float(px)
    t.exit_time = when
    t.exit_reason = reason


def php_process_bar(
    trail_doc: dict[str, Any],
    *,
    high: float,
    low: float,
    close: float,
    when: str,
    force_time_cap: bool = False,
    be_lock_after_t1: bool = False,
    kill_tighten_after_t1: float | None = 0.025,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    Peak Hour bar step.

    T1 hard-banks at trigger (+2%). Remaining T2–T4 keep trailing.
    Default: do NOT full BE-lock after T1 (that caps runners). Optionally
    tighten kill to kill_tighten_after_t1 (e.g. 2.5%) to cut give-back risk.
    """
    before = sim_state_from_dict(trail_doc)
    prior_closed = {t.id: t.closed for t in before.tranches}
    state = deepcopy(before)

    if all(t.closed for t in state.tranches):
        return trail_doc, []

    if low <= state.kill_price:
        for t in state.tranches:
            if not t.closed:
                _close_tranche_at(t, state.kill_price, when, "kill")
    else:
        t1 = next((t for t in state.tranches if t.id == "T1" and not t.closed), None)
        t1_banked_now = False
        if t1 is not None and high >= t1.trigger_price:
            _close_tranche_at(t1, t1.trigger_price, when, "t1_bank")
            t1_banked_now = True
            if be_lock_after_t1:
                be = state.entry_price * (1.0 - BE_LOCK_BUFFER)
                if be > state.kill_price:
                    state.kill_price = be
            elif kill_tighten_after_t1 is not None:
                tightened = state.entry_price * (1.0 - float(kill_tighten_after_t1))
                if tightened > state.kill_price:
                    state.kill_price = tightened
                    state.kill_pct = float(kill_tighten_after_t1)

        if not all(t.closed for t in state.tranches):
            if low <= state.kill_price:
                for t in state.tranches:
                    if not t.closed:
                        reason = "kill_be" if be_lock_after_t1 else (
                            "kill_tight" if t1_banked_now else "kill"
                        )
                        _close_tranche_at(t, state.kill_price, when, reason)
            else:
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
        if not prior_closed.get(t.id, False) and t.closed:
            exits.append({
                "tranche_id": t.id,
                "shares": int(t.shares),
                "exit_price": float(t.exit_price or close),
                "reason": t.exit_reason or "trail",
                "when": t.exit_time or when,
            })

    updated = sim_state_to_dict(state)
    for k in (
        "php_keep_profit", "kill_stop_cancelled", "opened_at",
        "last_session_date", "structure_stop", "rth_armed", "levels_source",
    ):
        if k in trail_doc:
            updated[k] = trail_doc[k]
    updated["breakeven_locked"] = bool(be_lock_after_t1 and any(
        t.id == "T1" and t.closed for t in state.tranches
    ))
    updated["php_keep_profit"] = True
    return updated, exits
