"""
Q-ALPHA UTS v2 — 3H base detection and breakdown exit.

Sideways base on prior bars; exit when close breaks below base_low.
"""
from __future__ import annotations

from typing import Any

BASE_LOOKBACK = 6
BASE_MAX_DEPTH_PCT = 0.08  # range / mid — tighter = more sideways


def detect_3h_base(
    bars: list[dict[str, Any]] | list[Any],
    *,
    lookback: int = BASE_LOOKBACK,
) -> dict[str, float] | None:
    """
    Detect sideways base from prior *lookback* 3H bars (excludes latest bar).

    Returns {base_high, base_low, depth_pct} or None if not a tight base.
    """
    if len(bars) < lookback + 1:
        return None

    window = bars[-(lookback + 1):-1]

    def _hl(b) -> tuple[float, float]:
        if isinstance(b, dict):
            return float(b["high"]), float(b["low"])
        return float(b.high), float(b.low)

    highs, lows = zip(*[_hl(b) for b in window])
    base_high = max(highs)
    base_low = min(lows)
    mid = (base_high + base_low) / 2.0
    if mid <= 0:
        return None

    depth_pct = (base_high - base_low) / mid
    if depth_pct > BASE_MAX_DEPTH_PCT:
        return None

    return {
        "base_high": round(base_high, 4),
        "base_low": round(base_low, 4),
        "depth_pct": round(depth_pct, 4),
    }


def base_break_exit(close: float, base_low: float | None) -> bool:
    """True when price closes below base support."""
    if base_low is None or base_low <= 0:
        return False
    return float(close) < float(base_low)


def check_base_break(
    bars: list[Any],
    close: float,
    *,
    lookback: int = BASE_LOOKBACK,
) -> tuple[bool, dict[str, float] | None]:
    """Return (should_exit, base_info)."""
    base = detect_3h_base(bars, lookback=lookback)
    if base is None:
        return False, None
    return base_break_exit(close, base["base_low"]), base
