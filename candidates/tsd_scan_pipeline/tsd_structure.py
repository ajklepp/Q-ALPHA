"""
Q-ALPHA TSD — RTH structure stop (Layer 2) and ORB bootstrap.

Layer 1: broker kill stop (MAE p75) — never cancelled while shares remain.
Layer 2: software structure stop armed at RTH open from ORB.
Layer 3: strategy_a 4-tranche trail (T1–T4).
"""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

import pytz
from ib_insync import IB, Stock

from tsd_scan_pipeline.tsd_entry import SessionKind, classify_session
from tsd_scan_pipeline.tsd_trail import (
    any_tranche_trailing,
    remaining_shares,
    sim_state_from_dict,
)

ET = pytz.timezone("America/New_York")

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


def compute_structure_stop(
    entry: float,
    orb_low: float,
    kill_price: float,
    trail_pct: float,
    *,
    max_pct: float = STRUCTURE_MAX_PCT,
) -> tuple[float, str]:
    """
    Compute thesis structure stop between kill and entry.

    candidates = [entry*(1-max), orb_low*(1-buffer), entry*(1-trail_pct)]
    stop = max(min(candidates), kill+0.01, entry*0.985)
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


def fetch_orb_bars(ib: IB, symbol: str, *, day: date | None = None) -> list:
    """1-min RTH bars for ORB window (09:30–09:44 ET)."""
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


def is_low_rvol_day(ib: IB, symbol: str, *, now: datetime | None = None) -> bool:
    """
    True when today's session volume is below 50% of expected pace vs 20d avg.
    Used to tighten structure max from 3% to 2%.
    """
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
    minutes_elapsed = max(
        1,
        (dt.hour - 9) * 60 + dt.minute - 30,
    )
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


def maybe_ratchet_breakeven(
    leg: dict[str, Any],
    trail_doc: dict[str, Any],
    *,
    quote_high: float,
) -> bool:
    """
  Lock structure stop to breakeven when +0.5R or T1 trigger price touched.
    Returns True if ratchet applied.
    """
    if leg.get("breakeven_locked"):
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
    """Day-2 tighten when no tranche is trailing yet."""
    from tsd_scan_pipeline.tsd_entry_gates import leg_eligible_for_day2_tighten

    if not leg_eligible_for_day2_tighten(leg, trail_doc, now=now):
        return
    if any_tranche_trailing(trail_doc):
        return

    entry = float(leg.get("price") or trail_doc.get("entry_price") or 0)
    if entry <= 0:
        return

    tightened = round(entry * 0.99, 2)
    current = float(leg.get("structure_stop") or 0)
    if tightened > current:
        leg["structure_stop"] = tightened
        leg["structure_stop_reason"] = "day2_tighten"
        trail_doc["structure_stop"] = tightened


def should_day3_force_exit(trail_doc: dict[str, Any]) -> bool:
    """Day 3+ with no tranche trailing → thesis failed."""
    day = int(trail_doc.get("trading_day") or 1)
    return day >= 3 and not any_tranche_trailing(trail_doc)


def structure_stop_breached(quote_low: float, structure_stop: float | None) -> bool:
    if structure_stop is None or structure_stop <= 0:
        return False
    return float(quote_low) <= float(structure_stop)


def notify_rth_armed(symbol: str, structure_stop: float, kill_price: float) -> None:
    """Optional Telegram when RTH structure arms."""
    try:
        from autonomous_agent import send_telegram

        send_telegram(
            f"TSD {symbol.upper()} RTH armed | "
            f"Structure ${structure_stop:.2f} | "
            f"Kill ${kill_price:.2f} (backstop)"
        )
    except Exception:
        pass


def bootstrap_rth_structure(
    ib: IB,
    leg: dict[str, Any],
    symbol: str,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Arm Layer-2 structure stop from ORB on first RTH pass (>= 09:35 ET).
    Returns {armed: bool, leg: dict, reason?: str}.
    """
    if leg.get("rth_armed"):
        return {"armed": True, "leg": leg, "reason": "already_armed"}

    if not is_rth_bootstrap_ready(now):
        return {"armed": False, "leg": leg, "reason": "before_bootstrap_window"}

    orb_bars = fetch_orb_bars(ib, symbol, day=_as_et(now).date())
    hl = orb_high_low(orb_bars)
    if hl is None:
        return {"armed": False, "leg": leg, "reason": "orb_fetch_failed"}

    orb_high, orb_low = hl
    trail = dict(leg.get("trail") or {})
    entry = float(leg.get("price") or trail.get("entry_price") or 0)
    kill_price = float(trail.get("kill_price") or 0)
    trail_pct = float(trail.get("trail_pct") or 0.04)

    max_pct = STRUCTURE_MAX_PCT
    if is_low_rvol_day(ib, symbol, now=now):
        max_pct = STRUCTURE_LOW_RVOL_MAX_PCT

    stop, reason = compute_structure_stop(
        entry, orb_low, kill_price, trail_pct, max_pct=max_pct,
    )

    when = _as_et(now).isoformat()
    leg["rth_armed"] = True
    leg["rth_armed_at"] = when
    leg["structure_stop"] = stop
    leg["structure_stop_reason"] = reason
    leg["breakeven_locked"] = False
    leg["orb_low"] = round(orb_low, 4)
    leg["orb_high"] = round(orb_high, 4)

    trail["rth_armed"] = True
    trail["structure_stop"] = stop
    trail["breakeven_locked"] = False
    leg["trail"] = trail

    if not leg.get("session_at_entry"):
        leg["session_at_entry"] = classify_session(now)

    print(
        f"  {symbol.upper()} RTH armed structure=${stop:.2f} "
        f"ORB {orb_low:.2f}-{orb_high:.2f} kill=${kill_price:.2f}"
    )
    if not dry_run:
        notify_rth_armed(symbol, stop, kill_price)

    return {"armed": True, "leg": leg, "structure_stop": stop}


def init_leg_session_fields(session: SessionKind) -> dict[str, Any]:
    """Default per-leg RTH structure fields at entry."""
    return {
        "session_at_entry": session,
        "rth_armed": False,
        "rth_armed_at": None,
        "structure_stop": None,
        "structure_stop_reason": None,
        "breakeven_locked": False,
        "orb_low": None,
        "orb_high": None,
    }
