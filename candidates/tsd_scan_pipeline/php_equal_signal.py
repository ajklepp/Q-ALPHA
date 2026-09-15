"""
Peak Hour equal-signal overlay (LIVE when PHP_EQUAL_SIGNAL is ON, default).

Every valid 1H signal candle is equal. Do not rank or demote by stage
(LAUNCH / EXTENSION / NEUTRAL, scan sweet-spot, soft phase penalty).
After equal admission, live still chooses with popularity, momentum/RS/vol,
room, tape, and case review.

Hard-extension stays ON: scan >= EXTENSION_SCAN_AUTO (75).
The pre-change leak (phase=EXTENSION → bar_state=extended → extension_hard
at scan ~65–74) is neutralized when the flag is ON.

Set PHP_EQUAL_SIGNAL=0 to restore pre-2026-09-15 stage grading.
See candidates/tsd_scan_pipeline/REVERT.md.
"""
from __future__ import annotations

import math
from typing import Any

from tsd_scan_pipeline.tsd_launch_score import (
    BAR_STATE_PTS_V1,
    BOUNCE_WEIGHT,
    CATALYST_EARNINGS_SOFT_PTS,
    EARLY_SESSION_BONUS_HOURS,
    EARLY_SESSION_BONUS_PTS,
    EXTENSION_SCAN_AUTO,
    EXTENSION_SCAN_MIN,
    EXTREME_GAP_PCT,
    EXTREME_GAP_SOFT_PENALTY,
    GUIDANCE_CUT_PENALTY,
    HIST_HIT_WEIGHT,
    HIST_MFE_WEIGHT,
    HTF_TERM_WEIGHT,
    LAUNCH_SCAN_MAX,
    LAUNCH_TERM_WEIGHT,
    LATE_SESSION_HOURS,
    LATE_SESSION_PENALTY_PTS,
    PTS_BUY_SIGNAL,
    PTS_EARLY_BULL,
    PTS_HIGH_SCAN_PENALTY,
    RANKER_LIST_LAUNCH_FLOOR,
    ROOM_WEIGHT,
    RS_SECTOR_LAG,
    RS_SECTOR_LAG_PENALTY,
    RS_SECTOR_LEAD,
    RS_SECTOR_LEAD_PTS,
    RS_SPY_LAG,
    RS_SPY_LAG_PENALTY,
    RS_SPY_LEAD_MILD,
    RS_SPY_LEAD_MILD_PTS,
    RS_SPY_LEAD_STRONG,
    RS_SPY_LEAD_STRONG_PTS,
    _clip01,
    _row_hour,
    classify_bar_state,
    compute_launch_phase,
    compute_launch_score,
)

EQUAL_SIGNAL_VERSION = "live_v1"
# Live v1.6 stage knobs this overlay zeros (documented for REVERT.md / tests).
LIVE_PHASE_EXTENSION_PENALTY = 15.0
LIVE_SCAN_OVER_MAX_PENALTY = 10.0
LIVE_SCAN_TERM_HIGH = -5.0
LIVE_SCAN_TERM_EARLY = 5.0
LIVE_SCAN_TERM_MID = 2.0


def ohlc_bar_state(row: dict[str, Any]) -> str:
    """
    Tape color from OHLC only — ignore phase / scan>=75 extended leak.

    Live classify_bar_state() (flag OFF) marks phase=EXTENSION as 'extended',
    which then fails extension_hard. That is a soft-stage → hard-block leak.
    """
    probe = dict(row)
    probe["phase"] = "NEUTRAL"
    try:
        scan = float(probe.get("scan_score") or 0.0)
    except (TypeError, ValueError):
        scan = 0.0
    if scan >= EXTENSION_SCAN_AUTO:
        probe["scan_score"] = LAUNCH_SCAN_MAX  # keep OHLC path, not auto-extended
    return classify_bar_state(probe, equal_signal=True)


def is_hard_extended(row: dict[str, Any], *, keep_hard: bool = True) -> bool:
    """Primary safety ceiling: scan >= 75. Sensitivity pass sets keep_hard=False."""
    if not keep_hard:
        return False
    try:
        scan = float(row.get("scan_score") or 0.0)
    except (TypeError, ValueError):
        scan = 0.0
    return scan >= EXTENSION_SCAN_AUTO


def live_soft_stage_leak_hard_block(row: dict[str, Any]) -> bool:
    """
    True when *legacy* Peak Hour would reject as extension_hard because
    phase mapped to bar_state=extended even though scan < 75.

    Always uses pre-equal-signal classify (equal_signal=False), so the leak
    remains measurable after the live flag defaults ON.
    """
    try:
        scan = float(row.get("scan_score") or 0.0)
    except (TypeError, ValueError):
        scan = 0.0
    if scan >= EXTENSION_SCAN_AUTO:
        return False
    enriched = dict(row)
    if "phase" not in enriched:
        enriched["phase"] = compute_launch_phase(enriched)
    live_bs = classify_bar_state(enriched, equal_signal=False)
    return live_bs == "extended"


def trigger_only_launch_score(row: dict[str, Any]) -> float:
    """Launch points that are triggers, not scan-stage beauty."""
    pts = 0.0
    if row.get("buy_signal"):
        pts += PTS_BUY_SIGNAL
    if row.get("early_bull"):
        pts += PTS_EARLY_BULL
    return round(max(0.0, min(100.0, pts)), 1)


def live_scan_term(scan: float) -> float:
    """v1.6 scan_term — stage grading (early sweet-spot vs high-scan penalty)."""
    if 25.0 <= scan <= 45.0:
        return LIVE_SCAN_TERM_EARLY
    if scan <= LAUNCH_SCAN_MAX:
        return LIVE_SCAN_TERM_MID
    return LIVE_SCAN_TERM_HIGH


def stage_demotion_breakdown(row: dict[str, Any]) -> dict[str, Any]:
    """
    Points live v1.6 subtracts (or adds) purely for signal stage / scan band.

    Positive restored = how much equal-signal adds back vs legacy v1.6.
    """
    scan = float(row.get("scan_score") or 55.0)
    phase = str(row.get("phase") or row.get("phase_3h") or compute_launch_phase(row))
    live_launch = float(row.get("launch_score") or compute_launch_score(row))
    trigger_launch = trigger_only_launch_score(row)

    scan_term = live_scan_term(scan)
    over_max = LIVE_SCAN_OVER_MAX_PENALTY if scan > LAUNCH_SCAN_MAX else 0.0
    phase_pen = LIVE_PHASE_EXTENSION_PENALTY if phase == "EXTENSION" else 0.0
    launch_delta = LAUNCH_TERM_WEIGHT * (live_launch - trigger_launch)
    live_bs = classify_bar_state(
        row if "phase" in row else {**row, "phase": phase},
        equal_signal=False,
    )
    ohlc_bs = ohlc_bar_state(row)
    bar_delta = float(BAR_STATE_PTS_V1.get(live_bs, 0.0)) - float(
        BAR_STATE_PTS_V1.get(ohlc_bs, 0.0)
    )
    high_scan_launch_pen = PTS_HIGH_SCAN_PENALTY if scan > EXTENSION_SCAN_MIN else 0.0
    restored = round(
        -scan_term + over_max + phase_pen + max(0.0, launch_delta) + max(0.0, -bar_delta),
        2,
    )
    return {
        "phase": phase,
        "scan_score": scan,
        "live_bar_state": live_bs,
        "ohlc_bar_state": ohlc_bs,
        "scan_term": scan_term,
        "scan_over_max_penalty": over_max,
        "phase_extension_penalty": phase_pen,
        "live_launch_score": live_launch,
        "trigger_launch_score": trigger_launch,
        "launch_term_stage_drag": round(launch_delta, 2),
        "bar_state_stage_drag": round(bar_delta, 2),
        "high_scan_launch_penalty": high_scan_launch_pen,
        "live_soft_stage_leak_hard_block": live_soft_stage_leak_hard_block(row),
        "approx_pts_restored_vs_live": restored,
    }


def compute_continuation_score_equal_signal(row: dict[str, Any]) -> float:
    """
    v1.6 body with stage terms removed.

    Kept: session clock, OHLC bar tape, room, bounce, vol, hist, news/social,
    HTF (small), RS, gap, catalyst, decision-context tape overlays.
    Dropped: launch-score scan beauty, scan_term, scan>55 extra −10, phase −15,
    phase→extended bar penalty.
    """
    hour = _row_hour(row)
    if hour is not None and hour in EARLY_SESSION_BONUS_HOURS:
        session_pts = EARLY_SESSION_BONUS_PTS
    elif hour is not None and hour in LATE_SESSION_HOURS:
        session_pts = -LATE_SESSION_PENALTY_PTS
    else:
        session_pts = 0.0

    bs = ohlc_bar_state(row)
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

    launch_term = LAUNCH_TERM_WEIGHT * trigger_only_launch_score(row)
    htf = float(row.get("htf_score") or 0.0)
    htf_term = HTF_TERM_WEIGHT * htf

    score = (
        session_pts + bar_pts + room_term + bounce_term + vol_term + hist_term
        + news_term + st_term + x_term + launch_term + htf_term
    )

    outlook = str(row.get("outlook") or "").lower()
    if row.get("guidance_cut") or outlook in ("lowered", "withdrawn"):
        score -= GUIDANCE_CUT_PENALTY
    if row.get("dilution_flag"):
        score -= 30.0
    if row.get("distress_flag"):
        score -= 40.0

    if int(row.get("expectation_pending") or 0) == 1:
        score += 4.0
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

    try:
        from tsd_scan_pipeline.tsd_decision_context import apply_decision_context_score_terms

        score = apply_decision_context_score_terms(score, row)
    except Exception:
        pass

    return round(score, 2)


def is_equal_signal_list_candidate(
    row: dict[str, Any],
    *,
    keep_hard_extension: bool = True,
) -> bool:
    """
    Admit any valid 1H trigger that is not hard-extended.

    Drops live RANKER_LIST_LAUNCH_FLOOR ∧ scan>55 gate and phase→extended block.
    """
    if not (bool(row.get("buy_signal")) or bool(row.get("early_bull"))):
        return False
    if is_hard_extended(row, keep_hard=keep_hard_extension):
        return False
    return True


def live_vs_equal_pair(row: dict[str, Any]) -> dict[str, Any]:
    """Compact legacy-v1.6 vs equal-signal comparison for one row."""
    from tsd_scan_pipeline.tsd_launch_score import (
        compute_continuation_score_v1_1,
        equal_signal_enabled,
        enrich_launch_fields,
    )

    live = enrich_launch_fields(dict(row))
    live_bs = classify_bar_state(
        live if "phase" in live else {**live, "phase": compute_launch_phase(live)},
        equal_signal=False,
    )
    eq_score = compute_continuation_score_equal_signal(live)
    scan = float(live.get("scan_score") or 99.0)
    launch = float(live.get("launch_score") or 0.0)
    live_list = (
        (bool(live.get("buy_signal")) or bool(live.get("early_bull")))
        and scan < EXTENSION_SCAN_AUTO
        and str(live_bs or "") != "extended"
        and not (launch < RANKER_LIST_LAUNCH_FLOOR and scan > LAUNCH_SCAN_MAX)
    )
    return {
        "symbol": live.get("symbol"),
        "scan_score": live.get("scan_score"),
        "phase": live.get("phase"),
        "live_bar_state": live_bs,
        "ohlc_bar_state": ohlc_bar_state(live),
        "live_continuation": compute_continuation_score_v1_1({**live, "bar_state": live_bs}),
        "equal_continuation": eq_score,
        "live_list_ok": live_list,
        "equal_list_ok": is_equal_signal_list_candidate(live, keep_hard_extension=True),
        "live_soft_stage_leak_hard_block": live_soft_stage_leak_hard_block(live),
        "stage_demotion": stage_demotion_breakdown(live),
        "equal_signal_enabled": equal_signal_enabled(),
    }
