#!/usr/bin/env python3
"""
PAPER-ONLY Peak Hour exit engine — early trail + kill ratchet.

HARD CONSTRAINTS
----------------
This module is research / paper-shadow only. It does NOT change live trail /
kill / T1 / gate behavior. Do not import this from tsd_keep_profit, tsd_kill,
tsd_trail (live evaluate_trail_tick), tsd_exit, or live entry gates.

`php_arm_after_mfe_shadow` may import this engine to log B_arm4_lock on the
same open book (software path only — no broker orders). Trail monitor may
import the shadow wrapper, never this module, and only after live save_state.

Live php_process_bar (T1 hard-bank at +2%, PHP_TRAIL_PCT=2.5% off run-high)
stays untouched. Mode A callers may import tsd_keep_profit read-only as a
baseline. Arm-after-MFE / trail-from-entry modes use the paper fork below.

Language: all levels are % of entry or % off high.
If older notes say "+1R", treat that as +kill_pct of entry (live fallback
kill is 5%, so +1R ≈ +5% of entry — not a separate R-multiple system).
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

# Live keep-profit defaults (copied as named constants for paper contrast only).
LIVE_T1_BANK_PCT = 0.02  # php_keep_profit PHP_T1_TRIGGER — % of entry
LIVE_TRAIL_PCT_OFF_HIGH = 0.025  # php_keep_profit PHP_TRAIL_PCT
LIVE_KILL_TIGHTEN_AFTER_T1 = 0.025  # % of entry below entry after T1 bank
FALLBACK_KILL_PCT = 0.05  # % of entry; same as live fallback / EXP-0021 1R
COST_PER_TRADE = 0.0015  # round-trip fraction of entry notional

# Prior PR #16 Mode B: trail-everything arms immediately — no hard bank.
TRAIL_EVERYTHING_ARM_MFE_PCT = 0.0

# Arm-after-MFE study grid (primary candidate). Trail stays off until MFE.
ARM_AFTER_MFE_GRID = (0.03, 0.04)
# Live paper shadow mode (PR follow-up to the study): arm at +4% of entry.
B_ARM4_LOCK_MODE = "B_arm4_lock"
B_ARM4_LOCK_ARM_MFE_PCT = 0.04  # +4% of entry — same as study B_arm4 / B_arm4_lock

# Default grid leftover from the schedule study (estimator may pick inside).
TRAIL_OFF_HIGH_GRID = (0.010, 0.015, 0.020, 0.025, 0.030)
ARM_MFE_GRID = (0.015, 0.020, 0.030, 0.040)

# Tighter "lock" width once armed: fade fail-safe, not a hard take-profit.
LOCK_WIDTH_FRAC = 0.70  # 70% of ticker MAE-p50
LOCK_WIDTH_FLOOR = 0.010  # never tighter than 1.0% off high

# Kill ratchet milestones: after these MFE % of entry, kill may tighten UP.
KILL_MILESTONES_MFE = (0.00, 0.02, 0.03, 0.04)

# Green-then-lost: was open ≥ this MFE % of entry, then finished red.
GREEN_MFE_FLOOR = 0.01

# Ripper slice: path MFE ≥ this % of entry (PR #16 failure mode for from-entry).
RIPPER_MFE_PCT = 0.04


def clamp(value: float, lo: float, hi: float) -> float:
    """Bound value to [lo, hi]."""
    return max(lo, min(hi, float(value)))


def percentile(values: list[float], pct: float) -> float | None:
    """Linear-interpolation percentile; pct in 0..100."""
    if not values:
        return None
    s = sorted(float(v) for v in values)
    k = (len(s) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def lock_trail_width(mae_p50: float | None) -> float:
    """
    Slightly tighter trail width once armed (lock-profit fail-safe).

    Width is % off high from ticker MAE-p50, not a global +2/+3.5/+6/+10 bank.
    """
    base = float(mae_p50) if mae_p50 is not None and mae_p50 > 0 else LIVE_TRAIL_PCT_OFF_HIGH
    return clamp(base * LOCK_WIDTH_FRAC, LOCK_WIDTH_FLOOR, base)


def b_arm4_lock_paper_kwargs(
    mae_p50: float | None,
    *,
    emergency_kill_pct: float | None = None,
) -> dict[str, Any]:
    """
    Study Mode B_arm4_lock knobs — single source of truth for the paper shadow.

    Arm trail only after MFE reaches +4% of entry. Width is the lock fail-safe
    (70% of MAE-p50, floor 1% off high). Kill ratchets UP with the trail.
    No T1 hard bank. No broker orders.
    """
    lock = lock_trail_width(mae_p50)
    return {
        "trail_pct_off_high": lock,
        "trail_arm_mfe_pct": B_ARM4_LOCK_ARM_MFE_PCT,
        "kill_schedule": emergency_kill_schedule(emergency_kill_pct),
        "ratchet_kill_with_trail": True,
        "mode": B_ARM4_LOCK_MODE,
    }


def resolve_mae_trail_width(mae_p50: float | None) -> float:
    """MAE-p50 % off high, clamped to a tradable band; live 2.5% fallback."""
    if mae_p50 is None or mae_p50 <= 0:
        return LIVE_TRAIL_PCT_OFF_HIGH
    return clamp(float(mae_p50), 0.010, 0.050)


def _bar_hlc(bar: dict[str, Any]) -> tuple[float, float, float] | None:
    """Extract high/low/close; None if unusable."""
    try:
        high = float(bar.get("high") or bar.get("h") or 0)
        low = float(bar.get("low") or bar.get("l") or 0)
        close = float(bar.get("close") or bar.get("c") or 0)
    except (TypeError, ValueError):
        return None
    if high <= 0 or low <= 0:
        return None
    if close <= 0:
        close = (high + low) / 2.0
    return high, low, close


def normalize_kill_schedule(
    schedule: list[dict[str, Any]] | None,
    *,
    fallback_kill: float = FALLBACK_KILL_PCT,
) -> list[dict[str, float]]:
    """
    Sort kill ratchets by after_mfe_pct and force kill % to tighten (never loosen).

    kill_pct_below_entry is % of entry (0.05 = stop 5% under entry).
    After a green MFE milestone the stop price ratchets UP (smaller %).
    """
    raw = list(schedule or [])
    if not raw:
        raw = [{"after_mfe_pct": 0.0, "kill_pct_below_entry": fallback_kill}]
    rows: list[dict[str, float]] = []
    for step in raw:
        try:
            rows.append({
                "after_mfe_pct": float(step.get("after_mfe_pct") or 0.0),
                "kill_pct_below_entry": float(
                    step.get("kill_pct_below_entry")
                    if step.get("kill_pct_below_entry") is not None
                    else fallback_kill
                ),
            })
        except (TypeError, ValueError):
            continue
    if not rows:
        rows = [{"after_mfe_pct": 0.0, "kill_pct_below_entry": fallback_kill}]
    rows.sort(key=lambda s: s["after_mfe_pct"])
    tightest = max(rows[0]["kill_pct_below_entry"], 0.0)
    out: list[dict[str, float]] = []
    for step in rows:
        # Ratchet UP: kill % may only shrink (stop closer to / above prior).
        tightest = min(tightest, max(0.0, step["kill_pct_below_entry"]))
        out.append({
            "after_mfe_pct": round(step["after_mfe_pct"], 6),
            "kill_pct_below_entry": round(tightest, 6),
        })
    return out


def kill_pct_for_mfe(
    schedule: list[dict[str, float]],
    mfe_pct: float,
) -> float:
    """Active kill % of entry given peak MFE so far."""
    active = schedule[0]["kill_pct_below_entry"] if schedule else FALLBACK_KILL_PCT
    for step in schedule:
        if mfe_pct + 1e-12 >= step["after_mfe_pct"]:
            active = step["kill_pct_below_entry"]
    return float(active)


def emergency_kill_schedule(kill_pct: float | None = None) -> list[dict[str, float]]:
    """Single-step emergency floor (no intermediate tighten until trail arms)."""
    pct = FALLBACK_KILL_PCT if kill_pct is None else float(kill_pct)
    return normalize_kill_schedule([
        {"after_mfe_pct": 0.0, "kill_pct_below_entry": clamp(pct, 0.02, 0.06)},
    ])


def init_paper_state(
    entry_price: float,
    n_shares: int,
    *,
    trail_pct_off_high: float,
    trail_arm_mfe_pct: float = TRAIL_EVERYTHING_ARM_MFE_PCT,
    kill_schedule: list[dict[str, Any]] | None = None,
    fallback_kill: float = FALLBACK_KILL_PCT,
    mode: str = "C",
    ratchet_kill_with_trail: bool = False,
) -> dict[str, Any]:
    """
    Build a paper full-position trail + kill-ratchet state.

    One slice (all shares). No T1 hard bank. Trail width is % off run-high.
    Trail arms only after MFE (% of entry) reaches trail_arm_mfe_pct.
    When ratchet_kill_with_trail is set, the emergency kill floor rises to
    the trail stop after arm (never a fixed % full exit).
    """
    entry = float(entry_price)
    shares = int(n_shares)
    sched = normalize_kill_schedule(kill_schedule, fallback_kill=fallback_kill)
    kill_pct = kill_pct_for_mfe(sched, 0.0)
    return {
        "paper_only": True,
        "mode": str(mode),
        "entry_price": entry,
        "shares": shares,
        "remaining": shares,
        "peak_high": entry,
        "run_high": entry,
        "mfe_peak_pct": 0.0,
        "mae_peak_pct": 0.0,
        "trail_pct_off_high": float(trail_pct_off_high),
        "trail_arm_mfe_pct": float(trail_arm_mfe_pct),
        "trail_armed": float(trail_arm_mfe_pct) <= 0.0,
        "ratchet_kill_with_trail": bool(ratchet_kill_with_trail),
        "kill_schedule": sched,
        "kill_pct": kill_pct,
        "kill_price": round(entry * (1.0 - kill_pct), 6),
        "closed": shares <= 0,
        "exits": [],
        "realized_gross": 0.0,
        "last_close": None,
        "last_when": None,
    }


def paper_process_bar(
    state: dict[str, Any],
    *,
    high: float,
    low: float,
    close: float,
    when: str,
    force_time_cap: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    PAPER fork of a Peak Hour bar step (full-position trail + kill ratchet).

    Intrabar order (long-only, path-first):
      1) Advance peak / MFE from HIGH, then ratchet emergency kill UP if a
         green milestone printed (same convention as php_multi_target_study:
         MFE-before-stop when both print on one bar).
      2) If already armed from a prior bar and ratchet_kill_with_trail, raise
         kill to the trail stop (kill only ratchets UP).
      3) Kill on LOW at the (possibly tightened) kill_price.
      4) Arm trail if peak MFE ≥ trail_arm_mfe_pct.
      5) Trail-exit on LOW at run_high × (1 − trail_pct_off_high),
         skipping the bar the trail first armed (never exit below the
         arm print on the same bar).
      6) Optional time-cap at CLOSE.

    Does not call or mutate live php_process_bar.
    """
    doc = deepcopy(state)
    if doc.get("closed") or int(doc.get("remaining") or 0) <= 0:
        return doc, []

    entry = float(doc["entry_price"])
    hi = float(high)
    lo = float(low)
    cl = float(close)
    new_exits: list[dict[str, Any]] = []
    already_armed = bool(doc.get("trail_armed"))

    doc["last_close"] = cl
    doc["last_when"] = when
    if hi > float(doc["peak_high"]):
        doc["peak_high"] = hi
    if hi > float(doc["run_high"]):
        doc["run_high"] = hi

    mfe = max(0.0, (float(doc["peak_high"]) - entry) / entry) if entry > 0 else 0.0
    mae = max(0.0, (entry - lo) / entry) if entry > 0 else 0.0
    doc["mfe_peak_pct"] = max(float(doc.get("mfe_peak_pct") or 0.0), mfe)
    doc["mae_peak_pct"] = max(float(doc.get("mae_peak_pct") or 0.0), mae)

    sched = normalize_kill_schedule(doc.get("kill_schedule"))
    doc["kill_schedule"] = sched
    new_kill_pct = kill_pct_for_mfe(sched, float(doc["mfe_peak_pct"]))
    new_kill_px = entry * (1.0 - new_kill_pct)
    # Kill only ratchets UP (higher price / tighter %).
    if new_kill_px > float(doc.get("kill_price") or 0):
        doc["kill_pct"] = new_kill_pct
        doc["kill_price"] = round(new_kill_px, 6)
    else:
        doc["kill_pct"] = new_kill_pct

    trail_pct = float(doc.get("trail_pct_off_high") or 0.0)
    run_high = float(doc.get("run_high") or entry)
    trail_stop = run_high * (1.0 - trail_pct)

    if already_armed and doc.get("ratchet_kill_with_trail"):
        if trail_stop > float(doc.get("kill_price") or 0):
            doc["kill_price"] = round(trail_stop, 6)
            if entry > 0:
                doc["kill_pct"] = (entry - trail_stop) / entry

    kill_px = float(doc["kill_price"])
    if lo <= kill_px:
        reason = "trail" if (
            already_armed
            and doc.get("ratchet_kill_with_trail")
            and trail_stop + 1e-12 >= kill_px
        ) else "kill"
        _paper_flat(doc, kill_px, when, reason, new_exits)
        return doc, new_exits

    newly_armed = False
    arm_need = float(doc.get("trail_arm_mfe_pct") or 0.0)
    if not doc.get("trail_armed") and float(doc["mfe_peak_pct"]) + 1e-12 >= arm_need:
        doc["trail_armed"] = True
        newly_armed = True

    if doc.get("trail_armed") and not newly_armed and not doc.get("closed"):
        if lo <= trail_stop:
            _paper_flat(doc, trail_stop, when, "trail", new_exits)
            return doc, new_exits

    if force_time_cap and not doc.get("closed"):
        _paper_flat(doc, cl, when, "time_cap", new_exits)
    return doc, new_exits


def _paper_flat(
    doc: dict[str, Any],
    px: float,
    when: str,
    reason: str,
    new_exits: list[dict[str, Any]],
) -> None:
    """Close all remaining paper shares at px."""
    rem = int(doc.get("remaining") or 0)
    if rem <= 0:
        doc["closed"] = True
        return
    entry = float(doc["entry_price"])
    exit_px = float(px)
    doc["realized_gross"] = float(doc.get("realized_gross") or 0.0) + (exit_px - entry) * rem
    ex = {
        "tranche_id": "ALL",
        "shares": rem,
        "exit_price": exit_px,
        "reason": reason,
        "when": when,
    }
    doc.setdefault("exits", []).append(ex)
    new_exits.append(ex)
    doc["remaining"] = 0
    doc["closed"] = True


def paper_mark_residual(
    state: dict[str, Any],
    *,
    mark: float,
    when: str,
    reason: str = "mark",
) -> dict[str, Any]:
    """Force-close leftover shares at mark (range EOD / hold-window end)."""
    doc = deepcopy(state)
    if not doc.get("closed") and int(doc.get("remaining") or 0) > 0:
        _paper_flat(doc, float(mark), when, reason, [])
    return doc


def replay_paper_path(
    entry_price: float,
    bars: list[dict[str, Any]],
    *,
    shares: int,
    trail_pct_off_high: float,
    trail_arm_mfe_pct: float,
    kill_schedule: list[dict[str, Any]] | None,
    mode: str = "C",
    mark_reason: str = "hold_end",
    ratchet_kill_with_trail: bool = False,
) -> dict[str, Any]:
    """
    Walk a bar list through paper_process_bar and mark leftovers at last close.

    Returns book-style PnL in $ and % of entry (after COST_PER_TRADE).
    """
    state = init_paper_state(
        entry_price,
        shares,
        trail_pct_off_high=trail_pct_off_high,
        trail_arm_mfe_pct=trail_arm_mfe_pct,
        kill_schedule=kill_schedule,
        mode=mode,
        ratchet_kill_with_trail=ratchet_kill_with_trail,
    )
    for i, bar in enumerate(bars):
        hlc = _bar_hlc(bar)
        if hlc is None:
            continue
        high, low, close = hlc
        when = str(bar.get("when") or bar.get("date") or f"bar{i}")
        state, _ = paper_process_bar(
            state, high=high, low=low, close=close, when=when,
        )
        if state.get("closed"):
            break
    if not state.get("closed"):
        mark = float(state.get("last_close") or entry_price)
        when = str(state.get("last_when") or "hold_end")
        state = paper_mark_residual(state, mark=mark, when=when, reason=mark_reason)
    return summarize_closed_state(state, entry_price=entry_price, shares=shares)


def walk_paper_bars(
    entry_price: float,
    bars: list[dict[str, Any]],
    *,
    shares: int,
    trail_pct_off_high: float,
    trail_arm_mfe_pct: float,
    kill_schedule: list[dict[str, Any]] | None,
    mode: str = B_ARM4_LOCK_MODE,
    ratchet_kill_with_trail: bool = True,
) -> dict[str, Any]:
    """
    Walk bars through paper_process_bar and leave leftovers OPEN.

    Used by the live paper shadow so an in-progress B_arm4_lock path is not
    force-marked at the last quote. Does not call live php_process_bar.
    """
    state = init_paper_state(
        entry_price,
        shares,
        trail_pct_off_high=trail_pct_off_high,
        trail_arm_mfe_pct=trail_arm_mfe_pct,
        kill_schedule=kill_schedule,
        mode=mode,
        ratchet_kill_with_trail=ratchet_kill_with_trail,
    )
    for i, bar in enumerate(bars):
        hlc = _bar_hlc(bar)
        if hlc is None:
            continue
        high, low, close = hlc
        when = str(bar.get("when") or bar.get("date") or f"bar{i}")
        state, _ = paper_process_bar(
            state, high=high, low=low, close=close, when=when,
        )
        if state.get("closed"):
            break
    return state


def summarize_closed_state(
    state: dict[str, Any],
    *,
    entry_price: float,
    shares: int,
) -> dict[str, Any]:
    """Dollar + % of-entry metrics from a closed paper or live-baseline state."""
    entry = float(entry_price)
    sh = max(1, int(shares))
    realized = float(state.get("realized_gross") or 0.0)
    # Live keep-profit baseline stores exits only — derive gross if needed.
    if abs(realized) < 1e-12 and state.get("exits"):
        for ex in state["exits"]:
            realized += (float(ex["exit_price"]) - entry) * int(ex["shares"])
    cost = entry * sh * COST_PER_TRADE
    pnl = realized - cost
    pnl_pct = pnl / (entry * sh) if entry > 0 and sh > 0 else 0.0
    mfe = float(state.get("mfe_peak_pct") or 0.0)
    if mfe <= 1e-12 and entry > 0:
        peak = float(state.get("peak_high") or entry)
        mfe = max(0.0, (peak - entry) / entry)
    capture = 0.0 if mfe <= 1e-12 else pnl_pct / mfe
    left_on_table = mfe - pnl_pct
    green_then_lost = bool(mfe >= GREEN_MFE_FLOOR and pnl_pct < 0.0)
    reasons = [str(e.get("reason") or "") for e in (state.get("exits") or [])]
    return {
        "entry_price": entry,
        "shares": sh,
        "realized_gross": round(realized, 6),
        "cost": round(cost, 6),
        "pnl": round(pnl, 4),
        "pnl_pct_of_entry": round(pnl_pct, 6),
        "mfe_peak_pct": round(mfe, 6),
        "mae_peak_pct": round(float(state.get("mae_peak_pct") or 0.0), 6),
        "capture_frac_of_mfe": round(capture, 6),
        "left_on_table_pct_of_entry": round(left_on_table, 6),
        "green_then_lost": green_then_lost,
        "exit_reasons": reasons,
        "exits": list(state.get("exits") or []),
        "kill_pct_final": float(state.get("kill_pct") or 0.0),
        "trail_armed": bool(state.get("trail_armed")),
        "mode": state.get("mode"),
    }


def measure_path_giveback(
    entry_price: float,
    bars: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Signal-time-safe path stats: peak MFE % of entry, giveback % off high,
    and +3%/+4% fade flags. Uses only the supplied post-entry bars.
    """
    entry = float(entry_price)
    peak = entry
    mfe_peak = 0.0
    mae_peak = 0.0
    max_giveback_off_high = 0.0
    giveback_after_3 = 0.0
    giveback_after_4 = 0.0
    touched_3 = False
    touched_4 = False
    faded_after_3 = False
    faded_after_4 = False
    n_bars = 0
    last_close = entry

    for bar in bars:
        hlc = _bar_hlc(bar)
        if hlc is None:
            continue
        high, low, close = hlc
        n_bars += 1
        last_close = close
        if high > peak:
            peak = high
        if entry > 0:
            mfe_peak = max(mfe_peak, (peak - entry) / entry)
            mae_peak = max(mae_peak, (entry - low) / entry)
        if peak > 0:
            gb = max(0.0, (peak - low) / peak)
            max_giveback_off_high = max(max_giveback_off_high, gb)
            if mfe_peak + 1e-12 >= 0.03:
                touched_3 = True
                giveback_after_3 = max(giveback_after_3, gb)
            if mfe_peak + 1e-12 >= RIPPER_MFE_PCT:
                touched_4 = True
                giveback_after_4 = max(giveback_after_4, gb)
        if touched_3 and entry > 0 and (low - entry) / entry < 0.015:
            faded_after_3 = True
        if touched_4 and entry > 0 and (low - entry) / entry < 0.02:
            faded_after_4 = True

    final_pct = ((last_close - entry) / entry) if entry > 0 else 0.0
    return {
        "mfe_peak_pct": round(mfe_peak, 6),
        "mae_peak_pct": round(mae_peak, 6),
        "giveback_from_high_pct": round(max_giveback_off_high, 6),
        "giveback_after_3pct_off_high": round(giveback_after_3, 6),
        "giveback_after_4pct_off_high": round(giveback_after_4, 6),
        "touched_plus_3pct": touched_3,
        "touched_plus_4pct": touched_4,
        "faded_after_3pct": faded_after_3,
        "faded_after_4pct": faded_after_4,
        "final_pct_of_entry": round(final_pct, 6),
        "n_bars": n_bars,
        "is_ripper": mfe_peak + 1e-12 >= RIPPER_MFE_PCT,
        "is_grinder": mfe_peak + 1e-12 < RIPPER_MFE_PCT,
    }


def path_from_facts(
    entry_price: float,
    *,
    mfe_pct: float,
    mae_pct: float,
    killed: bool = False,
) -> list[dict[str, Any]]:
    """
    Two-bar path-first reconstruction when 1H bars are missing.

    Bar 1 prints MFE (high first). Bar 2 prints MAE / kill. Same convention
    as php_multi_target_study — not a substitute for real 1H when available.
    """
    entry = float(entry_price)
    mfe = max(0.0, float(mfe_pct))
    mae = max(0.0, float(mae_pct))
    peak = entry * (1.0 + mfe)
    trough = entry * (1.0 - mae)
    close2 = trough if killed else (entry + peak) / 2.0
    return [
        {"high": peak, "low": min(entry, peak), "close": peak, "when": "mfe"},
        {"high": peak, "low": trough, "close": close2, "when": "mae"},
    ]


def _median(values: list[float]) -> float | None:
    """Median of values, or None if empty."""
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def score_grid_result(
    rows: list[dict[str, Any]],
    *,
    green_then_lost_penalty: float = 0.01,
) -> dict[str, Any]:
    """
    Book score in % of entry: mean PnL − penalty × green-then-lost rate.

    Capture fraction of MFE is the tie-break (higher = more of the run kept).
    """
    n = len(rows)
    if n == 0:
        return {
            "n": 0,
            "mean_pnl_pct_of_entry": None,
            "median_pnl_pct_of_entry": None,
            "mean_capture_frac_of_mfe": None,
            "mean_capture_frac_when_mfe_ge_1pct": None,
            "mean_left_on_table_pct_of_entry": None,
            "green_then_lost_rate": None,
            "score": None,
        }
    pnls = [float(r["pnl_pct_of_entry"]) for r in rows]
    caps = [float(r.get("capture_frac_of_mfe") or 0.0) for r in rows]
    lefts = [float(r.get("left_on_table_pct_of_entry") or 0.0) for r in rows]
    gtl = sum(1 for r in rows if r.get("green_then_lost")) / n
    mean_pnl = sum(pnls) / n
    mean_cap = sum(caps) / n
    mean_left = sum(lefts) / n
    green_caps = [
        float(r["capture_frac_of_mfe"])
        for r in rows
        if float(r.get("mfe_peak_pct") or 0.0) >= GREEN_MFE_FLOOR
    ]
    score = mean_pnl - green_then_lost_penalty * gtl
    return {
        "n": n,
        "mean_pnl_pct_of_entry": round(mean_pnl, 6),
        "median_pnl_pct_of_entry": round(float(_median(pnls) or 0.0), 6),
        "mean_capture_frac_of_mfe": round(mean_cap, 6),
        "mean_capture_frac_when_mfe_ge_1pct": (
            round(sum(green_caps) / len(green_caps), 6) if green_caps else None
        ),
        "mean_left_on_table_pct_of_entry": round(mean_left, 6),
        "green_then_lost_rate": round(gtl, 6),
        "score": round(score, 6),
    }


def slice_trades(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """
    Partition replay rows: all / rippers / grinders / green-then-lost.

    Uses path MFE when present (measure_path_giveback), else sim MFE.
    2-bar path-fact rows stay in the slice — callers decide what is recommendable.
    """
    usable = [r for r in rows if r.get("status") != "no_bars"]
    rippers: list[dict[str, Any]] = []
    grinders: list[dict[str, Any]] = []
    gtl: list[dict[str, Any]] = []
    for r in usable:
        path = r.get("path") or {}
        mfe = float(path.get("mfe_peak_pct") or r.get("mfe_peak_pct") or 0.0)
        if mfe + 1e-12 >= RIPPER_MFE_PCT:
            rippers.append(r)
        else:
            grinders.append(r)
        if r.get("green_then_lost"):
            gtl.append(r)
    return {
        "all": usable,
        "rippers": rippers,
        "grinders": grinders,
        "green_then_lost": gtl,
    }


def summarize_slices(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Score each slice in % of entry."""
    parts = slice_trades(rows)
    return {name: score_grid_result(part) for name, part in parts.items()}
