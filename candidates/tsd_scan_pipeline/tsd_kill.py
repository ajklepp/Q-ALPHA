"""
Q-ALPHA Peak Hour — kill % resolution (live broker stop).

Chat A bakeoff: winners' MAE p75 ≈ 4.6%; nearest 10-bar structure kill FAILED.
Live kill = profile MAE p75 ONLY when in [2%, 6%], else FALLBACK 5%.
Never place the broker stop at structure/area-low (research telemetry only).
"""
from __future__ import annotations

from typing import Any

# Chat A winners' path MAE p75 ≈ 4.62% — use 5% as primary fallback
FALLBACK_KILL_PCT = 0.05
PROFILE_KILL_MIN = 0.02
PROFILE_KILL_MAX = 0.06
# Research-only: skip entry when nearest area-low risk exceeds this (not used as stop)
STRUCTURE_RISK_MAX = 0.05
STRUCTURE_LOOKBACK_BARS = 10


def resolve_kill_pct(
    raw: float | None = None,
    *,
    profile: dict[str, Any] | None = None,
) -> tuple[float, str]:
    """
    Resolve live kill %.

    Prefer ticker profile kill ONLY if between PROFILE_KILL_MIN and PROFILE_KILL_MAX.
    Else FALLBACK_KILL_PCT. Returns (kill_pct, kill_source).
    """
    candidate: float | None = None
    source_try = "none"
    if raw is not None:
        try:
            candidate = float(raw)
            source_try = "raw"
        except (TypeError, ValueError):
            candidate = None
    if candidate is None and profile:
        mae = profile.get("mae") or {}
        for key in ("kill_pct",):
            if profile.get(key) is not None:
                try:
                    candidate = float(profile[key])
                    source_try = "profile.kill_pct"
                    break
                except (TypeError, ValueError):
                    pass
        if candidate is None and mae.get("p75") is not None:
            try:
                candidate = float(mae["p75"])
                source_try = "profile.mae.p75"
            except (TypeError, ValueError):
                candidate = None

    if candidate is not None and PROFILE_KILL_MIN <= candidate <= PROFILE_KILL_MAX:
        return round(candidate, 6), source_try

    return FALLBACK_KILL_PCT, "fallback_5pct"


def structure_area_low(lows: list[float], *, n: int = STRUCTURE_LOOKBACK_BARS) -> float | None:
    """Nearest N-bar area low (research telemetry — never used as broker kill)."""
    if not lows:
        return None
    window = [float(x) for x in lows[-n:] if x is not None and float(x) > 0]
    if not window:
        return None
    return min(window)


def structure_risk_pct(entry: float, area_low: float | None) -> float | None:
    """(entry - area_low) / entry; None if inputs invalid."""
    if entry is None or entry <= 0 or area_low is None or area_low <= 0:
        return None
    return round((float(entry) - float(area_low)) / float(entry), 6)


def structure_too_wide(entry: float, area_low: float | None) -> bool:
    """True when structure risk > STRUCTURE_RISK_MAX (soft skip, not a stop level)."""
    risk = structure_risk_pct(entry, area_low)
    if risk is None:
        return False
    return risk > STRUCTURE_RISK_MAX
