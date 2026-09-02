"""
Q-ALPHA TSD — structure stop (Layer 2) — UTS v2 Phase 2.5 kill-until-1R.

Layer 1: broker kill stop (MAE p75) — never cancelled while shares remain.
Layer 2: BE lock only after +1R (entry * (1 + kill_pct)) touched; no ORB arm.
Layer 3: strategy_a 4-tranche trail (T1–T4).
"""
from __future__ import annotations

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
THESIS_FAIL_DAY = 5
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
    Arm BE structure stop once price touches +1R.

    Until +1R: structure_stop stays None (kill is the only stop).
    After +1R: structure_stop = entry * 0.997 (BE lock).
  """
    if leg.get("one_r_locked"):
        return False

    entry = float(leg.get("price") or trail_doc.get("entry_price") or 0)
    kill_pct = float(trail_doc.get("kill_pct") or leg.get("kill_pct") or 0)
    if entry <= 0 or kill_pct <= 0:
        return False

    if float(quote_high) < one_r_price(entry, kill_pct):
        return False

    be_stop = be_lock_price(entry)
    leg["structure_stop"] = be_stop
    leg["structure_stop_reason"] = "be_lock_1r"
    leg["one_r_locked"] = True
    leg["breakeven_locked"] = True
    trail_doc["structure_stop"] = be_stop
    trail_doc["one_r_locked"] = True
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

    Only runs once one_r_locked; uses +0.5R / T1 triggers for further ratchet.
    """
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


def should_thesis_fail_exit(trail_doc: dict[str, Any]) -> bool:
    """Day 5+ with no tranche trailing → thesis failed."""
    day = int(trail_doc.get("trading_day") or 1)
    return day >= THESIS_FAIL_DAY and not any_tranche_trailing(trail_doc)


def should_day3_force_exit(trail_doc: dict[str, Any]) -> bool:
    """Backward-compatible alias — now uses day-5 thesis fail."""
    return should_thesis_fail_exit(trail_doc)


def structure_stop_breached(quote_low: float, structure_stop: float | None) -> bool:
    if structure_stop is None or structure_stop <= 0:
        return False
    return float(quote_low) <= float(structure_stop)


def ensure_rth_monitoring(
    leg: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Mark leg as RTH-monitored without arming ORB structure.

    Phase 2.5: kill-only until +1R; structure_stop remains None until BE lock.
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
