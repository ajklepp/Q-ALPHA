"""
Q-ALPHA UTS v2 — LAUNCH phase scoring (user edge: early move, not extended).

Low scan_score (~25-45) + buy_signal / early_bull = LAUNCH.
High scan_score (65+) = EXTENSION — reject new longs.

Chat A bar-state bakeoff: drop hard red veto; yellow best, red ok, green ok,
orange admit-but-deprioritize. Rank via continuation_score_v0.
"""
from __future__ import annotations

from typing import Any, Literal

LaunchPhase = Literal["LAUNCH", "EXTENSION", "NEUTRAL"]
BarState = Literal["extended", "orange", "red", "yellow", "green"]

# Gate constants (Lane B / LAUNCH)
LAUNCH_SCORE_MIN = 50.0
LAUNCH_SCAN_MAX = 55.0
EXTENSION_SCAN_MIN = 65.0
EXTENSION_TREND_MIN = 0.7
EXTENSION_SCAN_AUTO = 75.0  # clearly extended regardless of trend

# Scoring table — red monopoly hard-reduced so yellow/green can compete
PTS_BUY_SIGNAL = 25
PTS_EARLY_BULL = 20
PTS_SIGNAL_BAR_RED = 0  # color preference is bar_state_bonus only (Chat A)
PTS_SWEET_SPOT_MAX = 25  # peak at scan_score 35 (25-45 band)
PTS_SCAN_LE_MAX = 10     # scan_score <= LAUNCH_SCAN_MAX
PTS_RED_EARLY_COMBO = 0  # removed: was starving green/yellow
PTS_HIGH_SCAN_PENALTY = 20  # scan > 65

# Bar-state rank bonuses (bakeoff order: yellow > red > green > orange)
BAR_STATE_BONUS = {
    "yellow": 12.0,
    "red": 8.0,
    "green": 5.0,
    "orange": 0.0,
    "extended": -50.0,
}
BODY_DOJI = 0.25
BODY_WEAK_GREEN = 0.50

# Hour soft demote (do not hard-block 07/13)
HOUR_SCORE_MULT = {7: 0.90, 11: 1.0, 12: 1.0, 13: 0.85}
DEFAULT_HOUR_MULT = 1.0

# Soft scan preference (earlier = better)
SCAN_EARLY_WEIGHT = 0.2
GUIDANCE_CUT_PENALTY = 25.0


def signal_bar_red(row: dict[str, Any]) -> bool:
    """True when signal bar closed red (close < open). Soft signal only."""
    o = row.get("open")
    c = row.get("close")
    if o is None or c is None:
        return bool(row.get("signal_bar_red", False))
    return float(c) < float(o)


def classify_bar_state(row: dict[str, Any]) -> BarState:
    """
    Classify signal 1H bar OHLC into bar_state.

    orange = doji/coiled (body < 25% of range)
    red = close < open
    yellow = weak green (body < 50%)
    green = strong non-extended green
    extended = already gated via phase; marked when phase says so
    """
    if str(row.get("phase") or "") == "EXTENSION" or float(row.get("scan_score") or 0) >= EXTENSION_SCAN_AUTO:
        return "extended"

    o = row.get("open")
    h = row.get("high")
    low = row.get("low")
    c = row.get("close")
    if o is None or c is None:
        existing = row.get("bar_state")
        if existing in BAR_STATE_BONUS:
            return existing  # type: ignore[return-value]
        return "red" if signal_bar_red(row) else "green"

    o_f, c_f = float(o), float(c)
    hi = float(h) if h is not None else max(o_f, c_f)
    lo = float(low) if low is not None else min(o_f, c_f)
    rng = max(hi - lo, 1e-9)
    body_ratio = abs(c_f - o_f) / rng

    if body_ratio < BODY_DOJI:
        return "orange"
    if c_f < o_f:
        return "red"
    if body_ratio < BODY_WEAK_GREEN:
        return "yellow"
    return "green"


def hour_score_mult(hour: int | None) -> float:
    """Soft hour multiplier; 11/12 full, 07×0.90, 13×0.85."""
    if hour is None:
        return DEFAULT_HOUR_MULT
    return float(HOUR_SCORE_MULT.get(int(hour), DEFAULT_HOUR_MULT))


def compute_launch_phase(row: dict[str, Any]) -> LaunchPhase:
    """
    Classify LAUNCH | EXTENSION | NEUTRAL from scan row / bar summary.

    EXTENSION: move already extended (high scan_score + trend, or score >= 75).
    LAUNCH: early-phase candidate (low score, trigger present).
    """
    score = float(row.get("scan_score") or 0)
    trend = float(row.get("trend_strength") or 0)

    if score >= EXTENSION_SCAN_AUTO:
        return "EXTENSION"
    if score >= EXTENSION_SCAN_MIN and trend >= EXTENSION_TREND_MIN:
        return "EXTENSION"

    launch_score = compute_launch_score(row)
    trigger = bool(row.get("buy_signal")) or bool(row.get("early_bull"))
    if (
        launch_score >= LAUNCH_SCORE_MIN
        and score <= LAUNCH_SCAN_MAX
        and trigger
    ):
        return "LAUNCH"

    return "NEUTRAL"


def _sweet_spot_points(scan_score: float) -> float:
    """Peak points at 35; tapers 25-45 band."""
    if scan_score < 20 or scan_score > 50:
        return 0.0
    if 25 <= scan_score <= 45:
        dist = abs(scan_score - 35) / 10.0
        return PTS_SWEET_SPOT_MAX * max(0.0, 1.0 - dist)
    return PTS_SWEET_SPOT_MAX * 0.25


def compute_launch_score(row: dict[str, Any]) -> float:
    """
    Launch quality 0-100. Red is soft (+4), not a monopoly.
    Color preference for slot pick lives in continuation_score_v0.
    """
    score = float(row.get("scan_score") or 0)
    pts = 0.0

    if row.get("buy_signal"):
        pts += PTS_BUY_SIGNAL
    if row.get("early_bull"):
        pts += PTS_EARLY_BULL
    if signal_bar_red(row):
        pts += PTS_SIGNAL_BAR_RED
    pts += _sweet_spot_points(score)
    if score <= LAUNCH_SCAN_MAX:
        pts += PTS_SCAN_LE_MAX
    if row.get("early_bull") and signal_bar_red(row) and PTS_RED_EARLY_COMBO:
        pts += PTS_RED_EARLY_COMBO
    if score > EXTENSION_SCAN_MIN:
        pts -= PTS_HIGH_SCAN_PENALTY

    return round(max(0.0, min(100.0, pts)), 1)


def compute_continuation_score_v0(row: dict[str, Any]) -> float:
    """
    Slot-pick rank among admitted list (not a hard gate).

    base launch (red monopoly removed) + bar_state_bonus + early-scan soft
    + HTF + hour_mult − guidance_cut penalty.
    """
    launch = float(row.get("launch_score") or compute_launch_score(row))
    bar_state = str(row.get("bar_state") or classify_bar_state(row))
    bonus = float(BAR_STATE_BONUS.get(bar_state, 0.0))
    scan = float(row.get("scan_score") or 55.0)
    early = max(-5.0, min(11.0, (LAUNCH_SCAN_MAX - scan) * SCAN_EARLY_WEIGHT))
    htf = float(row.get("htf_score") or 0)
    hour = row.get("htf_1h_bar_hour")
    if hour is None:
        hour = row.get("bar_hour")
    mult = hour_score_mult(int(hour) if hour is not None else None)
    raw = launch + bonus + early + htf
    if str(row.get("outlook") or "").lower() in ("lowered", "withdrawn"):
        raw -= GUIDANCE_CUT_PENALTY
    return round(raw * mult, 1)


def enrich_launch_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Add launch_score, phase, bar_state, continuation/combined rank to a row."""
    out = dict(row)
    out["signal_bar_red"] = signal_bar_red(out)
    out["launch_score"] = compute_launch_score(out)
    out["phase"] = compute_launch_phase(out)
    out["bar_state"] = classify_bar_state(out)
    hour = out.get("htf_1h_bar_hour")
    out["hour_mult"] = hour_score_mult(int(hour) if hour is not None else None)
    htf = float(out.get("htf_score") or 0)
    out["htf_score"] = htf
    out["continuation_score"] = compute_continuation_score_v0(out)
    out["combined_rank_score"] = out["continuation_score"]
    out["entry_score"] = out["combined_rank_score"]
    return out


def is_launch_candidate(row: dict[str, Any]) -> bool:
    """True when row is LAUNCH phase and passes launch score floor. Color does NOT veto."""
    enriched = enrich_launch_fields(row) if "phase" not in row else row
    return (
        enriched.get("phase") == "LAUNCH"
        and float(enriched.get("launch_score") or 0) >= LAUNCH_SCORE_MIN
        and float(enriched.get("scan_score") or 99) <= LAUNCH_SCAN_MAX
        and (bool(enriched.get("buy_signal")) or bool(enriched.get("early_bull")))
        and str(enriched.get("bar_state") or "") != "extended"
    )
