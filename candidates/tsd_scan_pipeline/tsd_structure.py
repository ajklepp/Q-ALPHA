"""
Q-ALPHA TSD — structure stop (Layer 2) — UTS v2 Phase 2.5 kill-until-1R.

Layer 1: broker kill stop (MAE p75) — never cancelled while shares remain.
Layer 2: BE lock after +1R — **gated**. Live Peak Hour default is OFF
         (`PHP_STRUCTURE_STOP_EXITS=0`): no hard structure_stop /
         be_lock_1r / breakeven_ratchet dumps. Protective path is the
         single broker kill/trail stop, ratcheting UP only, plus a tighter
         software trail after ~+3–4% MFE. Set PHP_STRUCTURE_STOP_EXITS=1
         to restore Phase 2.5 hard BE sells (see REVERT.md).
Layer 3: strategy_a 4-tranche trail (T1–T4). T1 keep-profit bank is unchanged.

MT3 / 3R multi-target banks stay paper/shadow only (tsd_shadow_multi_target).
"""
from __future__ import annotations

import os
from datetime import date, datetime, time
from typing import Any, TYPE_CHECKING

import pytz

from tsd_scan_pipeline.tsd_entry import SessionKind, classify_session
from tsd_scan_pipeline.tsd_trail import (
    any_tranche_trailing,
    sim_state_from_dict,
)

if TYPE_CHECKING:
    from ib_insync import IB

ET = pytz.timezone("America/New_York")

BE_LOCK_PCT = 0.003  # structure_stop = entry * (1 - BE_LOCK_PCT) after +1R
THESIS_FAIL_DAY = 6  # idle_no_1r: never-+1R by day 6 (not day 2–5)
IDLE_NO_1R_DAY = 6
STRUCTURE_MAX_PCT = 0.03
STRUCTURE_MIN_PCT = 0.015
STRUCTURE_LOW_RVOL_MAX_PCT = 0.02
BREAKEVEN_BUFFER_PCT = 0.003
ORB_BUFFER_PCT = 0.01
RTH_OPEN = time(9, 30)
RTH_BOOTSTRAP_AFTER = time(9, 35)
ORB_END = time(9, 44)
RTH_CLOSE = time(16, 0)
RTH_MINUTES = 390

# Live Peak Hour: hard structure BE sells default OFF. Set env=1 to restore.
PHP_STRUCTURE_STOP_EXITS_ENV = "PHP_STRUCTURE_STOP_EXITS"
_ENV_TRUE = {"1", "true", "yes", "on"}

# "Lock profit" replacement when structure BE dumps are off: tighter trail
# after ~+3–4% MFE, using ticker MAE/MFE priors when present.
LOCK_PROFIT_MFE_PCT = 0.035  # default MFE to arm tighter trail (~3.5%)
LOCK_PROFIT_MFE_MIN = 0.03
LOCK_PROFIT_MFE_MAX = 0.04
LOCK_PROFIT_TRAIL_PCT = 0.02  # default tighter software-trail width
LOCK_PROFIT_TRAIL_MIN = 0.01
LOCK_PROFIT_TRAIL_MAX = 0.03
# Never raise kill into last (would dump the long). 50 bp buffer under last.
LOCK_PROFIT_LAST_BUFFER_PCT = 0.005


def structure_stop_exits_enabled() -> bool:
    """
    True when live hard structure BE sells (structure_stop dumps) are allowed.

    Default OFF. Set PHP_STRUCTURE_STOP_EXITS=1 to restore Phase 2.5
    be_lock_1r / breakeven_ratchet exits. Unset, empty, 0, false, off → disabled.
    """
    raw = (os.environ.get(PHP_STRUCTURE_STOP_EXITS_ENV) or "0").strip().lower()
    return raw in _ENV_TRUE


def lock_profit_mfe_threshold(profile: dict[str, Any] | None = None) -> float:
    """MFE fraction that arms tighter trail; clamped to ~3–4%."""
    mfe = (profile or {}).get("mfe") or {}
    raw = mfe.get("p25")
    try:
        value = float(raw) if raw is not None else LOCK_PROFIT_MFE_PCT
    except (TypeError, ValueError):
        value = LOCK_PROFIT_MFE_PCT
    if value <= 0:
        value = LOCK_PROFIT_MFE_PCT
    return min(LOCK_PROFIT_MFE_MAX, max(LOCK_PROFIT_MFE_MIN, value))


def lock_profit_trail_pct(profile: dict[str, Any] | None = None) -> float:
    """Tighter trail width after lock-profit MFE; MAE p25/p50 when available."""
    mae = (profile or {}).get("mae") or {}
    raw = mae.get("p25") if mae.get("p25") is not None else mae.get("p50")
    try:
        value = float(raw) if raw is not None else LOCK_PROFIT_TRAIL_PCT
    except (TypeError, ValueError):
        value = LOCK_PROFIT_TRAIL_PCT
    if value <= 0:
        value = LOCK_PROFIT_TRAIL_PCT
    return min(LOCK_PROFIT_TRAIL_MAX, max(LOCK_PROFIT_TRAIL_MIN, value))


def one_r_price(entry: float, kill_pct: float) -> float:
    """+1R target: entry * (1 + kill_pct)."""
    return float(entry) * (1.0 + float(kill_pct))


def be_lock_price(entry: float) -> float:
    """Breakeven lock stop after +1R touched."""
    return round(float(entry) * (1.0 - BE_LOCK_PCT), 2)


def compute_structure_stop(
    entry: float,
    orb_low: float,
    kill_price: float,
    trail_pct: float,
    *,
    max_pct: float = STRUCTURE_MAX_PCT,
) -> tuple[float, str]:
    """
    Legacy ORB structure formula (kept for tests / reference).

    Phase 2.5 runtime does NOT arm structure from ORB at RTH open.
    """
    if entry <= 0 or orb_low <= 0:
        raise ValueError("entry and orb_low must be positive")

    candidates = [
        entry * (1.0 - max_pct),
        orb_low * (1.0 - ORB_BUFFER_PCT),
        entry * (1.0 - float(trail_pct)),
    ]
    raw = min(candidates)
    floor = max(float(kill_price) + 0.01, entry * (1.0 - STRUCTURE_MIN_PCT))
    stop = max(raw, floor)
    return round(stop, 2), "orb_structure"


def is_rth_bootstrap_ready(now: datetime | None = None) -> bool:
    """True when ET is weekday RTH and time >= 09:35."""
    dt = _as_et(now)
    if dt.weekday() >= 5:
        return False
    t = dt.time()
    return RTH_OPEN <= t < RTH_CLOSE and t >= RTH_BOOTSTRAP_AFTER


def poll_interval_sec(now: datetime | None = None) -> int:
    """RTH: 30s trail loop; extended/overnight: 5 min (kill backstop only)."""
    return 30 if classify_session(now) == "RTH" else 300


def _as_et(now: datetime | None) -> datetime:
    dt = now or datetime.now(ET)
    if dt.tzinfo is None:
        return ET.localize(dt)
    return dt.astimezone(ET)


def _bar_et(bar) -> datetime:
    ts = bar.date
    if ts.tzinfo is None:
        return ET.localize(ts)
    return ts.astimezone(ET)


def fetch_orb_bars(ib: "IB", symbol: str, *, day: date | None = None) -> list:
    """1-min RTH bars for ORB window (09:30–09:44 ET)."""
    from ib_insync import Stock

    contract = Stock(symbol.upper(), "SMART", "USD")
    ib.qualifyContracts(contract)
    bars = ib.reqHistoricalData(
        contract,
        endDateTime="",
        durationStr="1 D",
        barSizeSetting="1 min",
        whatToShow="TRADES",
        useRTH=True,
        formatDate=1,
        keepUpToDate=False,
    )
    target = day or _as_et().date()
    orb: list = []
    for bar in bars or []:
        bt = _bar_et(bar).time()
        bd = _bar_et(bar).date()
        if bd != target:
            continue
        if RTH_OPEN <= bt <= ORB_END:
            orb.append(bar)
    return orb


def is_low_rvol_day(ib: "IB", symbol: str, *, now: datetime | None = None) -> bool:
    """True when today's session volume is below 50% of expected pace vs 20d avg."""
    from ib_insync import Stock

    dt = _as_et(now)
    if dt.time() < RTH_BOOTSTRAP_AFTER:
        return False

    contract = Stock(symbol.upper(), "SMART", "USD")
    ib.qualifyContracts(contract)
    daily = ib.reqHistoricalData(
        contract,
        endDateTime="",
        durationStr="25 D",
        barSizeSetting="1 day",
        whatToShow="TRADES",
        useRTH=True,
        formatDate=1,
        keepUpToDate=False,
    )
    if not daily or len(daily) < 5:
        return False

    hist = list(daily)[:-1]
    if not hist:
        return False
    avg_daily = sum(float(b.volume or 0) for b in hist[-20:]) / min(20, len(hist))

    intraday = fetch_orb_bars(ib, symbol, day=dt.date())
    today_vol = sum(float(b.volume or 0) for b in intraday)
    minutes_elapsed = max(1, (dt.hour - 9) * 60 + dt.minute - 30)
    expected = avg_daily * (minutes_elapsed / RTH_MINUTES)
    if expected <= 0:
        return False
    return today_vol < 0.5 * expected


def orb_high_low(orb_bars: list) -> tuple[float, float] | None:
    if not orb_bars:
        return None
    lows = [float(b.low) for b in orb_bars if float(b.low) > 0]
    highs = [float(b.high) for b in orb_bars if float(b.high) > 0]
    if not lows or not highs:
        return None
    return max(highs), min(lows)


def maybe_arm_be_lock_on_1r(
    leg: dict[str, Any],
    trail_doc: dict[str, Any],
    *,
    quote_high: float,
) -> bool:
    """
    Record +1R and optionally arm a hard BE structure stop.

    Until +1R: structure_stop stays None (kill is the only stop).
    After +1R: always sets one_r_locked (idle_no_1r / day-1 base-break).
    Hard BE dump (structure_stop = entry * 0.997) only when
    PHP_STRUCTURE_STOP_EXITS=1. Default OFF — kill remains the sole
    broker protective; lock-profit uses maybe_lock_profit_via_trail.
    """
    if leg.get("one_r_locked"):
        return False

    entry = float(leg.get("price") or trail_doc.get("entry_price") or 0)
    kill_pct = float(trail_doc.get("kill_pct") or leg.get("kill_pct") or 0)
    if entry <= 0 or kill_pct <= 0:
        return False

    if float(quote_high) < one_r_price(entry, kill_pct):
        return False

    # Always mark +1R — used by idle_no_1r and day-1 base-break deferral.
    leg["one_r_locked"] = True
    trail_doc["one_r_locked"] = True

    if not structure_stop_exits_enabled():
        # Do not arm a second hard BE sell. Kill stays the broker protective.
        leg["trail"] = trail_doc
        return True

    be_stop = be_lock_price(entry)
    leg["structure_stop"] = be_stop
    leg["structure_stop_reason"] = "be_lock_1r"
    leg["breakeven_locked"] = True
    trail_doc["structure_stop"] = be_stop
    trail_doc["breakeven_locked"] = True
    leg["trail"] = trail_doc
    return True


def maybe_ratchet_breakeven(
    leg: dict[str, Any],
    trail_doc: dict[str, Any],
    *,
    quote_high: float,
) -> bool:
    """
    Ratchet structure stop higher after BE lock (+1R already touched).

    No-op when PHP_STRUCTURE_STOP_EXITS is off (default). Only runs once
    one_r_locked; uses +0.5R / T1 triggers for further ratchet.
    """
    if not structure_stop_exits_enabled():
        return False
    if not leg.get("one_r_locked"):
        return False
    if leg.get("breakeven_locked") and leg.get("structure_stop_reason") != "be_lock_1r":
        return False

    entry = float(leg.get("price") or trail_doc.get("entry_price") or 0)
    kill = float(trail_doc.get("kill_price") or 0)
    if entry <= 0 or kill <= 0:
        return False

    risk = entry - kill
    half_r = entry + 0.5 * risk

    state = sim_state_from_dict(trail_doc)
    t1_trigger = None
    for t in state.tranches:
        if t.id == "T1":
            t1_trigger = float(t.trigger_price)
            break

    triggered = quote_high >= half_r
    if t1_trigger is not None and quote_high >= t1_trigger:
        triggered = True

    if not triggered:
        return False

    be_stop = round(entry * (1.0 - BREAKEVEN_BUFFER_PCT), 2)
    current = float(leg.get("structure_stop") or 0)
    if be_stop <= current:
        return False

    leg["structure_stop"] = be_stop
    leg["breakeven_locked"] = True
    leg["structure_stop_reason"] = "breakeven_ratchet"
    trail_doc["structure_stop"] = be_stop
    trail_doc["breakeven_locked"] = True
    return True


def apply_day_structure_rules(
    leg: dict[str, Any],
    trail_doc: dict[str, Any],
    *,
    now: datetime | None = None,
) -> None:
    """DISABLED — Phase 2.5 removed day-2 tighten to entry*0.99."""
    return


def should_idle_no_1r(
    trail_doc: dict[str, Any],
    leg: dict[str, Any] | None = None,
) -> bool:
    """
    Flatten at close if trading_day >= 6, never hit +1R, and no tranche trailing.

    Do NOT exit merely underwater. one_r_locked means +1R was touched.
    """
    day = int(trail_doc.get("trading_day") or 1)
    if day < IDLE_NO_1R_DAY:
        return False
    locked = bool(trail_doc.get("one_r_locked"))
    if leg is not None:
        locked = locked or bool(leg.get("one_r_locked"))
    if locked:
        return False
    return not any_tranche_trailing(trail_doc)


def should_thesis_fail_exit(
    trail_doc: dict[str, Any],
    leg: dict[str, Any] | None = None,
) -> bool:
    """v2.6 alias for idle_no_1r (day 6, never +1R)."""
    return should_idle_no_1r(trail_doc, leg)


def should_day3_force_exit(trail_doc: dict[str, Any]) -> bool:
    """Disabled — day 2–5 no-1R cuts destroy P&L. Use idle_no_1r at day 6."""
    return False


def structure_stop_breached(quote_low: float, structure_stop: float | None) -> bool:
    """Pure price check — does not consult PHP_STRUCTURE_STOP_EXITS."""
    if structure_stop is None or structure_stop <= 0:
        return False
    return float(quote_low) <= float(structure_stop)


def should_exit_on_structure_stop(
    leg: dict[str, Any],
    trail_doc: dict[str, Any] | None,
    quote_low: float,
) -> bool:
    """
    True only when live structure BE dumps are enabled AND price breached.

    Flag off (default): leftover structure_stop on an open long does NOT close.
    Kill / trail remain the protective path.
    """
    if not structure_stop_exits_enabled():
        return False
    trail = trail_doc or {}
    stop = leg.get("structure_stop") or trail.get("structure_stop")
    return structure_stop_breached(quote_low, stop)


def _finite_price(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed > 0 else 0.0


def maybe_lock_profit_via_trail(
    leg: dict[str, Any],
    trail_doc: dict[str, Any],
    *,
    quote_high: float,
    quote_last: float | None = None,
    profile: dict[str, Any] | None = None,
    force: bool = False,
) -> bool:
    """
    After ~+3–4% MFE, tighten software trail and ratchet kill UP only.

    Replacement for hard structure BE sells when PHP_STRUCTURE_STOP_EXITS is off.
    Never lowers kill. Never removes kill. Never arms a second broker stop.
    Kill raise is capped under last (when known) so we do not dump into the tape.

    force=True: used by the offline migration helper even if the flag is on.
    """
    if structure_stop_exits_enabled() and not force:
        return False

    entry = _finite_price(leg.get("price") or trail_doc.get("entry_price"))
    kill = _finite_price(trail_doc.get("kill_price") or leg.get("kill_price"))
    if entry <= 0 or kill <= 0:
        return False

    peak = max(
        _finite_price(quote_high),
        _finite_price(trail_doc.get("peak_high")),
        _finite_price(leg.get("peak_high")),
        entry,
    )
    mfe = (peak - entry) / entry
    if mfe < lock_profit_mfe_threshold(profile):
        return False

    trail_pct = lock_profit_trail_pct(profile)
    changed = False

    for tranche in trail_doc.get("tranches") or []:
        if tranche.get("closed"):
            continue
        current_width = _finite_price(tranche.get("trail_pct"))
        if current_width <= 0 or current_width > trail_pct + 1e-12:
            tranche["trail_pct"] = trail_pct
            changed = True
    current_doc_width = _finite_price(trail_doc.get("trail_pct"))
    if current_doc_width <= 0 or current_doc_width > trail_pct + 1e-12:
        trail_doc["trail_pct"] = trail_pct
        changed = True

    # Move the old BE lock onto the single kill stop (UP only).
    be_px = be_lock_price(entry)
    candidate = max(kill, be_px)
    last = _finite_price(quote_last)
    if last > 0:
        cap = last * (1.0 - LOCK_PROFIT_LAST_BUFFER_PCT)
        if candidate > cap:
            candidate = max(kill, min(candidate, cap))

    if candidate > kill + 1e-9:
        trail_doc["kill_price"] = round(candidate, 4)
        trail_doc["kill_pct"] = round((entry - candidate) / entry, 6)
        leg["kill_price"] = trail_doc["kill_price"]
        changed = True

    trail_doc["lock_profit_armed"] = True
    trail_doc["lock_profit_mfe"] = round(mfe, 6)
    trail_doc["lock_profit_trail_pct"] = trail_pct
    if changed:
        leg["trail"] = trail_doc
    return changed


def clear_structure_stop_fields(
    leg: dict[str, Any],
    trail_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Drop hard structure BE fields so the monitor cannot dump on them.

    Kill price / kill_order_id are left untouched (never naked). Caller may
    then run maybe_lock_profit_via_trail to ratchet kill UP.
    """
    trail = dict(trail_doc if trail_doc is not None else (leg.get("trail") or {}))
    leg["structure_stop"] = None
    leg["structure_stop_reason"] = None
    leg["breakeven_locked"] = False
    trail["structure_stop"] = None
    trail["breakeven_locked"] = False
    leg["trail"] = trail
    return trail


def ensure_rth_monitoring(
    leg: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Mark leg as RTH-monitored without arming ORB structure.

    Phase 2.5: kill-only until +1R. Hard BE structure_stop is armed only when
    PHP_STRUCTURE_STOP_EXITS=1; default live path leaves it None.
    """
    if leg.get("rth_armed"):
        return {"armed": True, "leg": leg, "reason": "already_armed"}

    if not is_rth_bootstrap_ready(now):
        return {"armed": False, "leg": leg, "reason": "before_rth_window"}

    when = _as_et(now).isoformat()
    leg["rth_armed"] = True
    leg["rth_armed_at"] = when
    if leg.get("structure_stop") is None and not leg.get("one_r_locked"):
        leg["structure_stop"] = None
        leg["structure_stop_reason"] = "kill_only_until_1r"

    trail = dict(leg.get("trail") or {})
    trail["rth_armed"] = True
    if not trail.get("one_r_locked"):
        trail["structure_stop"] = None
    leg["trail"] = trail

    if not leg.get("session_at_entry"):
        leg["session_at_entry"] = classify_session(now)

    return {"armed": True, "leg": leg, "reason": "kill_only_until_1r"}


def bootstrap_rth_structure(
    ib: "IB",
    leg: dict[str, Any],
    symbol: str,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Phase 2.5: no ORB arm — delegate to ensure_rth_monitoring."""
    _ = ib, symbol, dry_run
    return ensure_rth_monitoring(leg, now=now)


def init_leg_session_fields(session: SessionKind) -> dict[str, Any]:
    """Default per-leg structure fields at entry."""
    return {
        "session_at_entry": session,
        "rth_armed": False,
        "rth_armed_at": None,
        "structure_stop": None,
        "structure_stop_reason": None,
        "one_r_locked": False,
        "breakeven_locked": False,
        "orb_low": None,
        "orb_high": None,
    }
