"""
Q-ALPHA UTS v2 — setup watch confirmation logic (Phase 3).

Pure functions for Lane A / Lane B entry confirmation. Testable without IBKR.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

RVOL_MIN = 0.8
LANE_A_MIN_WAIT_MIN = 2


@dataclass
class SessionQuote:
    """Intraday session context for confirmation gates."""

    price: float
    low: float
    high: float
    session_open: float
    vwap: float
    orb_high: float
    orb_low: float
    rvol: float
    up_vol: float = 0.0
    dn_vol: float = 0.0
    first_candle_low: float = 0.0
    first_candle_high: float = 0.0
    minutes_since_open: int = 0
    prev_close: float | None = None
    was_below_vwap: bool = False


def compute_rvol(
    session_volume: float,
    avg_daily_volume: float,
    minutes_since_open: int,
    *,
    rth_minutes: int = 390,
) -> float:
    """Relative volume vs expected pace (today vol / expected-to-now)."""
    if avg_daily_volume <= 0 or minutes_since_open <= 0:
        return 0.0
    expected = avg_daily_volume * (minutes_since_open / rth_minutes)
    if expected <= 0:
        return 0.0
    return session_volume / expected


def confirm_lane_b(quote: SessionQuote, cross_level: float) -> tuple[bool, str]:
    """
    Lane B (TSD swing): hold above cross + (ORB break OR VWAP reclaim) + rvol>=0.8.
    """
    if cross_level <= 0:
        return False, "invalid_cross_level"
    if quote.price <= cross_level:
        return False, "below_cross_level"
    if quote.low <= cross_level * 0.998:
        return False, "lost_cross_level"

    orb_break = quote.orb_high > 0 and quote.price > quote.orb_high
    vwap_reclaim = quote.price > quote.vwap and (
        quote.was_below_vwap or quote.low < quote.vwap
    )
    if not orb_break and not vwap_reclaim:
        return False, "no_orb_or_vwap"

    if quote.rvol < RVOL_MIN:
        return False, f"rvol_{quote.rvol:.2f}<{RVOL_MIN}"

    trigger = "orb_break" if orb_break else "vwap_reclaim"
    return True, f"lane_b_{trigger}"


def confirm_lane_a(quote: SessionQuote, cross_level: float) -> tuple[bool, str]:
    """
    Lane A (gap-style): port of autonomous_agent watch_and_enter core gates.

    gap holding, above VWAP, volume confirming, not dumping, structure intact.
    """
    prev = quote.prev_close if quote.prev_close and quote.prev_close > 0 else cross_level
    if prev <= 0:
        return False, "invalid_prev_close"

    gap_holding = quote.price > prev * 1.015
    above_vwap = quote.price > quote.vwap
    vol_confirming = (
        quote.up_vol > quote.dn_vol * 1.1
        if quote.dn_vol > 0
        else quote.up_vol > 0
    )
    not_dumping = quote.price > quote.session_open * 0.97
    broke_structure = (
        quote.first_candle_low > 0
        and quote.price < quote.first_candle_low * 0.99
    )
    min_wait = quote.minutes_since_open >= LANE_A_MIN_WAIT_MIN

    if quote.price < prev * 1.005:
        return False, "gap_filled"
    if quote.price < quote.session_open * 0.95:
        return False, "hard_dump"
    if broke_structure and quote.minutes_since_open >= 5:
        return False, "broke_structure"

    if not all([gap_holding, above_vwap, vol_confirming, not_dumping, min_wait]):
        missing = []
        if not gap_holding:
            missing.append("gap")
        if not above_vwap:
            missing.append("vwap")
        if not vol_confirming:
            missing.append("vol")
        if not not_dumping:
            missing.append("dump")
        if not min_wait:
            missing.append("wait")
        return False, "lane_a_pending:" + ",".join(missing)

    return True, "lane_a_confirmed"


def confirm_setup(
    row: dict[str, Any],
    quote: SessionQuote,
) -> tuple[bool, str]:
    """Dispatch confirmation by signal_lane on queue row."""
    lane = str(row.get("signal_lane") or "B").upper()
    cross = float(row.get("cross_level") or row.get("close") or 0)
    if lane == "A":
        return confirm_lane_a(quote, cross)
    return confirm_lane_b(quote, cross)
