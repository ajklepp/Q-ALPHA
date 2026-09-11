"""
Q-ALPHA UTS v2 — LAUNCH phase scoring + continuation ranker (EXP-0021).

Low scan_score (~25-45) + buy_signal / early_bull = LAUNCH.
High scan_score (65+) = EXTENSION — soft penalty in ranker; hard-block only >=75.

Chat A bar-state bakeoff: drop hard red veto; yellow best, red ok, green ok,
orange admit-but-deprioritize.

Live slot pick: continuation_score (v1.6 same-day extension objective).
"""
from __future__ import annotations

import math
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

# Hour soft demote (do not hard-block 07/13) — used by v0 only
HOUR_SCORE_MULT = {7: 0.90, 11: 1.0, 12: 1.0, 13: 0.85}
DEFAULT_HOUR_MULT = 1.0

# Soft scan preference (earlier = better)
SCAN_EARLY_WEIGHT = 0.2
GUIDANCE_CUT_PENALTY = 25.0

# EXP-0021 continuation ranker — LIVE Peak Hour objective (v1.6+)
# Optimize same-day extension after the signal bar, NOT multi-day "setup beauty".
# Ablation (week of 2026-09-08): score vs MFE r≈0.06 (noise); hour vs win_proxy r≈-0.34;
# launch_score is the only legacy term with useful positive MFE corr (~0.14).
PEAK_HOUR_BONUS_HOURS = frozenset({7, 11, 12, 13})  # legacy telemetry only
# Same-day MFE is stronger in the morning session on the live week — prefer early hours.
EARLY_SESSION_BONUS_HOURS = frozenset({5, 6, 7, 8, 9})
EARLY_SESSION_BONUS_PTS = 12.0
LATE_SESSION_HOURS = frozenset({11, 12, 13, 14, 15})
LATE_SESSION_PENALTY_PTS = 10.0
BAR_STATE_PTS_V1 = {
    "yellow": 12.0,
    "red": 8.0,
    "green": 5.0,
    "orange": 0.0,
    "extended": -20.0,
}
RANKER_LIST_LAUNCH_FLOOR = 40.0  # admit if launch>=40 OR scan<=55
CONTINUATION_SCORE_VERSION = "v1.6"  # first-principles same-day objective reweight
# Blind-spot #2: soft-skip extreme overnight gaps
EXTREME_GAP_PCT = 0.05
EXTREME_GAP_SOFT_PENALTY = 20.0
# Blind-spot #4: soft RS overlays (study winners; peer hard filters not shipped)
RS_SPY_LEAD_STRONG = 0.05
RS_SPY_LEAD_MILD = 0.03
RS_SPY_LAG = -0.05
RS_SPY_LEAD_STRONG_PTS = 10.0
RS_SPY_LEAD_MILD_PTS = 5.0
RS_SPY_LAG_PENALTY = 10.0
RS_SECTOR_LEAD = 0.04
RS_SECTOR_LAG = -0.04
RS_SECTOR_LEAD_PTS = 8.0
RS_SECTOR_LAG_PENALTY = 8.0
# Blind-spot #5: soft boost when news tags earnings (thin but ship-gate PASS)
CATALYST_EARNINGS_SOFT_PTS = 6.0

# v1.6 hist/room — keep as soft context only (EXP-0021 upweighted these for a
# different label; they inflate #1–2 without predicting same-day MFE).
HIST_HIT_WEIGHT = 6.0
HIST_MFE_WEIGHT = 6.0
ROOM_WEIGHT = 10.0
BOUNCE_WEIGHT = 8.0
LAUNCH_TERM_WEIGHT = 0.28  # ablation: launch_score best positive legacy predictor
HTF_TERM_WEIGHT = 0.08


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
    Color preference for slot pick lives in continuation_score_v0/v1.
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
    Legacy Peak Hour slot-pick rank (kept for telemetry / bakeoff baseline).

    base launch + bar_state_bonus + early-scan soft + HTF + hour_mult − guidance_cut.
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


def _clip01(x: float) -> float:
    return float(max(0.0, min(1.0, x)))


def _row_hour(row: dict[str, Any]) -> int | None:
    hour = row.get("htf_1h_bar_hour")
    if hour is None:
        hour = row.get("bar_hour")
    if hour is None:
        hour = row.get("hour")
    return int(hour) if hour is not None else None


def compute_continuation_score_v1(row: dict[str, Any]) -> float:
    """
    EXP-0021 original live ranker (kept for bakeoff / ablation).

    Missing MTF/social fields score as 0 (non-blocking).
    """
    hour = _row_hour(row)
    peak = 25.0 if hour is not None and hour in PEAK_HOUR_BONUS_HOURS else 0.0
    bs = str(row.get("bar_state") or classify_bar_state(row))
    bar_pts = float(BAR_STATE_PTS_V1.get(bs, 0.0))

    room = float(row.get("dist_20d_high_pct") or 0.0)
    if room < 0:
        room_term = -15.0 * _clip01((-room) / 0.05)
    else:
        room_term = 20.0 * _clip01(room / 0.15)

    bounce = float(row.get("dist_20d_low_bounce") or 0.0)
    bounce_term = 15.0 * _clip01(bounce)

    vr = float(row.get("vol_ratio_20") or row.get("vol_ratio") or 1.0)
    vol_term = 15.0 * _clip01(math.log1p(max(vr, 0.0)) / math.log1p(5.0))
    if vr < 0.5:
        vol_term -= 8.0

    prior_hit = float(row.get("ticker_prior_hit1r_rate") or 0.0)
    prior_mfe = float(row.get("ticker_prior_mfe_p50") or 0.0)
    hist_term = 10.0 * _clip01(prior_hit) + 12.0 * _clip01(prior_mfe / 0.05)

    news_v = float(
        row.get("news_velocity_24h")
        or row.get("news_headline_count_48h")
        or row.get("headline_count")
        or 0.0
    )
    news_term = 10.0 * _clip01(news_v / 5.0)

    st_msg = float(row.get("st_msg_24h") or 0.0)
    st_bull = float(row.get("st_bull_ratio") or 0.5)
    st_term = 8.0 * _clip01(st_bull) if st_msg > 0 else 0.0

    x_sent = float(row.get("x_sent_lex") or 0.0)
    x_term = (
        5.0 * _clip01((x_sent + 1.0) / 2.0) if not row.get("social_missing") else 0.0
    )

    launch = float(row.get("launch_score") or compute_launch_score(row))
    launch_term = 0.25 * launch
    htf = float(row.get("htf_score") or 0.0)
    htf_term = 0.15 * htf

    scan = float(row.get("scan_score") or 55.0)
    if 25.0 <= scan <= 45.0:
        scan_term = 10.0
    elif scan <= 55.0:
        scan_term = 4.0
    else:
        scan_term = -10.0

    score = (
        peak + bar_pts + room_term + bounce_term + vol_term + hist_term
        + news_term + st_term + x_term + launch_term + htf_term + scan_term
    )

    outlook = str(row.get("outlook") or "").lower()
    if row.get("guidance_cut") or outlook in ("lowered", "withdrawn"):
        score -= GUIDANCE_CUT_PENALTY
    if row.get("dilution_flag"):
        score -= 30.0
    if row.get("distress_flag"):
        score -= 40.0
    if scan > LAUNCH_SCAN_MAX:
        score -= 20.0
    if str(row.get("phase") or row.get("phase_3h") or "") == "EXTENSION":
        score -= 15.0

    return round(score, 2)


def compute_continuation_score_v1_1(row: dict[str, Any]) -> float:
    """
    Live Peak Hour continuation ranker (v1.6 first-principles objective).

    Objective
    ---------
    Rank 1H launches by expected *same-day* extension after the signal bar:
    higher P(hit +2% MFE before -5% MAE) / higher expected same-day MFE.

    This is NOT the EXP-0021 multi-day "setup quality" objective. Ablation on the
    live week (2026-09-08..11, top-10/hour) showed continuation rank ≈ noise vs
    same-day MFE (r≈0.06) while clock hour was strongly anti-predictive for late
    session (r≈-0.34 vs win_proxy). launch_score was the best legacy positive
    correlator (~0.14). Skipping to rank #3–5 is a symptom workaround — fix the
    objective/weights instead.

    v1.6 changes vs v1.5
    --------------------
      - Early-session bonus (5–9) replaces flat peak-hour calendar boost (7/11–13)
      - Late-session soft penalty (11–15)
      - Slash hist prior + 20d room/bounce weights (they crowned hollow #1–2s)
      - Upweight launch_score / keep tape+RS_1h overlays from v1.5
    """
    hour = _row_hour(row)
    # Same-day path quality by clock (ablation), not EXP-0021 peak-hour calendar.
    if hour is not None and hour in EARLY_SESSION_BONUS_HOURS:
        session_pts = EARLY_SESSION_BONUS_PTS
    elif hour is not None and hour in LATE_SESSION_HOURS:
        session_pts = -LATE_SESSION_PENALTY_PTS
    else:
        session_pts = 0.0

    bs = str(row.get("bar_state") or classify_bar_state(row))
    bar_pts = float(BAR_STATE_PTS_V1.get(bs, 0.0))

    room = float(row.get("dist_20d_high_pct") or 0.0)
    if room < 0:
        room_term = -0.5 * ROOM_WEIGHT * _clip01((-room) / 0.05)
    else:
        room_term = ROOM_WEIGHT * _clip01(room / 0.15)

    bounce = float(row.get("dist_20d_low_bounce") or 0.0)
    bounce_term = BOUNCE_WEIGHT * _clip01(bounce)

    vr = float(row.get("vol_ratio_20") or row.get("vol_ratio") or 1.0)
    vol_term = 18.0 * _clip01(math.log1p(max(vr, 0.0)) / math.log1p(5.0))
    if vr < 0.5:
        vol_term -= 10.0

    # Soft context only — do not let ticker history dominate the slot pick.
    prior_hit = float(row.get("ticker_prior_hit1r_rate") or 0.0)
    prior_mfe = float(row.get("ticker_prior_mfe_p50") or 0.0)
    hist_term = (
        HIST_HIT_WEIGHT * _clip01(prior_hit)
        + HIST_MFE_WEIGHT * _clip01(prior_mfe / 0.05)
    )

    news_v = float(
        row.get("news_velocity_24h")
        or row.get("news_headline_count_48h")
        or row.get("headline_count")
        or 0.0
    )
    news_term = 10.0 * _clip01(news_v / 5.0)

    st_msg = float(row.get("st_msg_24h") or 0.0)
    st_bull = float(row.get("st_bull_ratio") or 0.5)
    st_term = 8.0 * _clip01(st_bull) if st_msg > 0 else 0.0

    x_sent = float(row.get("x_sent_lex") or 0.0)
    x_term = (
        5.0 * _clip01((x_sent + 1.0) / 2.0) if not row.get("social_missing") else 0.0
    )

    launch = float(row.get("launch_score") or compute_launch_score(row))
    launch_term = LAUNCH_TERM_WEIGHT * launch
    htf = float(row.get("htf_score") or 0.0)
    htf_term = HTF_TERM_WEIGHT * htf

    scan = float(row.get("scan_score") or 55.0)
    if 25.0 <= scan <= 45.0:
        scan_term = 5.0
    elif scan <= 55.0:
        scan_term = 2.0
    else:
        scan_term = -5.0

    score = (
        session_pts + bar_pts + room_term + bounce_term + vol_term + hist_term
        + news_term + st_term + x_term + launch_term + htf_term + scan_term
    )

    outlook = str(row.get("outlook") or "").lower()
    if row.get("guidance_cut") or outlook in ("lowered", "withdrawn"):
        score -= GUIDANCE_CUT_PENALTY
    if row.get("dilution_flag"):
        score -= 30.0
    if row.get("distress_flag"):
        score -= 40.0
    if scan > LAUNCH_SCAN_MAX:
        score -= 10.0
    if str(row.get("phase") or row.get("phase_3h") or "") == "EXTENSION":
        score -= 15.0

    if int(row.get("expectation_pending") or 0) == 1:
        score += 4.0  # was 8 — narrative soft only
    if int(row.get("stale_relevant") or 0) == 1 and int(row.get("fresh_catalyst") or 0) == 0:
        score += 3.0

    try:
        gap = float(row.get("gap_pct") or 0.0)
    except (TypeError, ValueError):
        gap = 0.0
    if gap >= EXTREME_GAP_PCT:
        score -= EXTREME_GAP_SOFT_PENALTY

    if int(row.get("rs_ok") or 0) == 1:
        try:
            rs_spy = float(row.get("rs_spy_5d"))
        except (TypeError, ValueError):
            rs_spy = None
        try:
            rs_sec = float(row.get("rs_sector_5d"))
        except (TypeError, ValueError):
            rs_sec = None
        if rs_spy is not None:
            if rs_spy >= RS_SPY_LEAD_STRONG:
                score += RS_SPY_LEAD_STRONG_PTS
            elif rs_spy >= RS_SPY_LEAD_MILD:
                score += RS_SPY_LEAD_MILD_PTS
            elif rs_spy <= RS_SPY_LAG:
                score -= RS_SPY_LAG_PENALTY
        if rs_sec is not None:
            if rs_sec >= RS_SECTOR_LEAD:
                score += RS_SECTOR_LEAD_PTS
            elif rs_sec <= RS_SECTOR_LAG:
                score -= RS_SECTOR_LAG_PENALTY

    if str(row.get("catalyst_type") or "").strip().lower() == "earnings":
        score += CATALYST_EARNINGS_SOFT_PTS

    # v1.5+ decision-time overlays (RS_1h, $vol_1h, float, options, dead tape)
    try:
        from tsd_scan_pipeline.tsd_decision_context import apply_decision_context_score_terms

        score = apply_decision_context_score_terms(score, row)
    except Exception:
        pass

    return round(score, 2)


def compute_continuation_score(row: dict[str, Any]) -> float:
    """Live ranker entrypoint — currently v1.6 (same-day extension objective)."""
    return compute_continuation_score_v1_1(row)


def is_continuation_list_candidate(row: dict[str, Any]) -> bool:
    """
    EXP-0021 list gate: buy/early_bull + quality floors; peak hour NOT required.

    Hard-block only auto-extended (scan>=75 / bar_state extended).
    Soft EXTENSION (scan 55–75) may enter the list and be demoted by score.
    """
    enriched = enrich_launch_fields(row) if "launch_score" not in row else row
    if not (bool(enriched.get("buy_signal")) or bool(enriched.get("early_bull"))):
        return False
    scan = float(enriched.get("scan_score") or 99.0)
    if scan >= EXTENSION_SCAN_AUTO:
        return False
    if str(enriched.get("bar_state") or "") == "extended":
        return False
    launch = float(enriched.get("launch_score") or 0.0)
    if launch < RANKER_LIST_LAUNCH_FLOOR and scan > LAUNCH_SCAN_MAX:
        return False
    return True


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
    out["continuation_score_v0"] = compute_continuation_score_v0(out)
    out["continuation_score_v1"] = compute_continuation_score_v1(out)
    out["continuation_score"] = compute_continuation_score_v1_1(out)
    out["continuation_score_version"] = CONTINUATION_SCORE_VERSION
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
