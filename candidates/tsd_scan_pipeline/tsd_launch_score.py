"""
Q-ALPHA UTS v2 — LAUNCH phase scoring (user edge: early move, not extended).

Low scan_score (~25-45) + buy_signal on red bar / early_bull = LAUNCH.
High scan_score (65+) = EXTENSION — reject new longs.
"""
from __future__ import annotations

from typing import Any, Literal

LaunchPhase = Literal["LAUNCH", "EXTENSION", "NEUTRAL"]

# Gate constants (Lane B / LAUNCH)
LAUNCH_SCORE_MIN = 50.0
LAUNCH_SCAN_MAX = 55.0
EXTENSION_SCAN_MIN = 65.0
EXTENSION_TREND_MIN = 0.7
EXTENSION_SCAN_AUTO = 75.0  # clearly extended regardless of trend

# Scoring table (Chat A / user chart rules)
PTS_BUY_SIGNAL = 25
PTS_EARLY_BULL = 20
PTS_SIGNAL_BAR_RED = 15
PTS_SWEET_SPOT_MAX = 25  # peak at scan_score 35 (25-45 band)
PTS_SCAN_LE_MAX = 10     # scan_score <= LAUNCH_SCAN_MAX
PTS_RED_EARLY_COMBO = 10
PTS_HIGH_SCAN_PENALTY = 20  # scan > 65


def signal_bar_red(row: dict[str, Any]) -> bool:
    """True when signal 3H bar closed red (close < open)."""
    o = row.get("open")
    c = row.get("close")
    if o is None or c is None:
        return bool(row.get("signal_bar_red", False))
    return float(c) < float(o)


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
    Launch quality 0-100 from user chart rules.

    | Factor              | Pts |
    |---------------------|-----|
    | buy_signal          | 25  |
    | early_bull          | 20  |
    | signal bar red      | 15  |
    | score 25-45 sweet   | 25  |
    | score <= 55         | 10  |
    | red + early_bull    | 10  |
    | scan > 65           | -20 |
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
    if row.get("early_bull") and signal_bar_red(row):
        pts += PTS_RED_EARLY_COMBO
    if score > EXTENSION_SCAN_MIN:
        pts -= PTS_HIGH_SCAN_PENALTY

    return round(max(0.0, min(100.0, pts)), 1)


def enrich_launch_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Add launch_score, phase, signal_bar_red to a scan/queue row."""
    out = dict(row)
    out["signal_bar_red"] = signal_bar_red(out)
    out["launch_score"] = compute_launch_score(out)
    out["phase"] = compute_launch_phase(out)
    out["entry_score"] = out["launch_score"]
    return out


def is_launch_candidate(row: dict[str, Any]) -> bool:
    """True when row is LAUNCH phase and passes launch score floor."""
    enriched = enrich_launch_fields(row) if "phase" not in row else row
    return (
        enriched.get("phase") == "LAUNCH"
        and float(enriched.get("launch_score") or 0) >= LAUNCH_SCORE_MIN
        and float(enriched.get("scan_score") or 99) <= LAUNCH_SCAN_MAX
        and (bool(enriched.get("buy_signal")) or bool(enriched.get("early_bull")))
    )
