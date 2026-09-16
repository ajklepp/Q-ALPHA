"""
RESEARCH ONLY — equal-signal Peak Hour overlay.

Do NOT import this from scheduler / tsd_1h_launch_scan / live tick.
Live Peak Hour still grades LAUNCH vs EXTENSION in continuation_score v1.6.

Locked test philosophy
----------------------
Every valid 1H signal candle is equal. Do not rank or demote by stage
(LAUNCH / EXTENSION / NEUTRAL, scan sweet-spot, soft phase penalty).
After equal admission, choose with popularity, momentum/RS/vol, room,
tape, and case review.

Hard-extension (primary): keep scan >= EXTENSION_SCAN_AUTO (75).
Live maps phase=EXTENSION → bar_state=extended, which then hard-blocks.
That leak is neutralized here; OHLC bar_state is used for tape quality only.
"""
from __future__ import annotations

import math
from typing import Any

from tsd_scan_pipeline.tsd_attention import (
    ATTENTION_POOL_MAX,
    ATTENTION_TOP_K,
    HARD_EXT_SCAN,
    build_attention_pool,
)
from tsd_scan_pipeline.tsd_case_review import (
    _case,
    build_case_dossier,
    classify_room_class,
    deterministic_case_verdict,
    select_enter_rows,
)
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
    compute_continuation_score_v1_1,
    compute_launch_phase,
    compute_launch_score,
    enrich_launch_fields,
)

EQUAL_SIGNAL_VERSION = "research_v1"
# Mirror live tsd_capacity.MAX_NEW_ENTRIES_PER_SCAN without importing tsd_entry / ib_insync.
MAX_NEW_ENTRIES_PER_SCAN = 2
# Live v1.6 stage knobs this overlay zeros (documented for the report).
LIVE_PHASE_EXTENSION_PENALTY = 15.0
LIVE_SCAN_OVER_MAX_PENALTY = 10.0
LIVE_SCAN_TERM_HIGH = -5.0
LIVE_SCAN_TERM_EARLY = 5.0
LIVE_SCAN_TERM_MID = 2.0


def ohlc_bar_state(row: dict[str, Any]) -> str:
    """
    Tape color from OHLC only — ignore phase / scan>=75 extended leak.

    Live classify_bar_state() marks phase=EXTENSION as 'extended', which then
    fails extension_hard. That is a soft-stage → hard-block leak.
    """
    probe = dict(row)
    probe["phase"] = "NEUTRAL"
    try:
        scan = float(probe.get("scan_score") or 0.0)
    except (TypeError, ValueError):
        scan = 0.0
    if scan >= EXTENSION_SCAN_AUTO:
        probe["scan_score"] = LAUNCH_SCAN_MAX  # keep OHLC path, not auto-extended
    return classify_bar_state(probe)


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
    True when live would reject as extension_hard because phase mapped to
    bar_state=extended even though scan < 75.
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
    live_bs = classify_bar_state(enriched)
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

    Positive restored = how much equal-signal adds back vs live.
    """
    scan = float(row.get("scan_score") or 55.0)
    phase = str(row.get("phase") or row.get("phase_3h") or compute_launch_phase(row))
    live_launch = float(row.get("launch_score") or compute_launch_score(row))
    trigger_launch = trigger_only_launch_score(row)
    from tsd_scan_pipeline.tsd_launch_score import LAUNCH_TERM_WEIGHT

    scan_term = live_scan_term(scan)
    over_max = LIVE_SCAN_OVER_MAX_PENALTY if scan > LAUNCH_SCAN_MAX else 0.0
    phase_pen = LIVE_PHASE_EXTENSION_PENALTY if phase == "EXTENSION" else 0.0
    launch_delta = LAUNCH_TERM_WEIGHT * (live_launch - trigger_launch)
    live_bs = classify_bar_state(row if "phase" in row else {**row, "phase": phase})
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

    from tsd_scan_pipeline.tsd_launch_score import LAUNCH_TERM_WEIGHT

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


def enrich_equal_signal_fields(
    row: dict[str, Any],
    *,
    keep_hard_extension: bool = True,
) -> dict[str, Any]:
    """Attach live + equal-signal scores; does not mutate live defaults."""
    out = enrich_launch_fields(dict(row))
    out["ohlc_bar_state"] = ohlc_bar_state(out)
    out["bar_state_live"] = out.get("bar_state")
    out["continuation_score_live"] = float(out.get("continuation_score") or 0.0)
    out["continuation_score_equal"] = compute_continuation_score_equal_signal(out)
    out["equal_signal_version"] = EQUAL_SIGNAL_VERSION
    out["hard_extended"] = is_hard_extended(out, keep_hard=keep_hard_extension)
    out["live_soft_stage_leak_hard_block"] = live_soft_stage_leak_hard_block(out)
    out["stage_demotion"] = stage_demotion_breakdown(out)
    out["equal_signal_list_ok"] = is_equal_signal_list_candidate(
        out, keep_hard_extension=keep_hard_extension,
    )
    # Live list gate (telemetry) — use live bar_state before we overwrite it.
    scan = float(out.get("scan_score") or 99.0)
    launch = float(out.get("launch_score") or 0.0)
    live_list = (
        (bool(out.get("buy_signal")) or bool(out.get("early_bull")))
        and scan < EXTENSION_SCAN_AUTO
        and str(out.get("bar_state_live") or "") != "extended"
        and not (launch < RANKER_LIST_LAUNCH_FLOOR and scan > LAUNCH_SCAN_MAX)
    )
    out["live_list_ok"] = live_list
    # Attention pool hard-drops bar_state==extended; neutralize the phase leak.
    if not is_hard_extended(out, keep_hard=True) or not keep_hard_extension:
        out["bar_state"] = out["ohlc_bar_state"]
    return out


def rerank_equal_signal(
    rows: list[dict[str, Any]],
    *,
    keep_hard_extension: bool = True,
    require_trigger: bool = True,
) -> list[dict[str, Any]]:
    """Enrich and sort passers by equal-signal score (hard-ext filtered)."""
    out: list[dict[str, Any]] = []
    for row in rows:
        enriched = enrich_equal_signal_fields(
            row, keep_hard_extension=keep_hard_extension,
        )
        if require_trigger and not (
            bool(enriched.get("buy_signal")) or bool(enriched.get("early_bull"))
        ):
            continue
        if not is_equal_signal_list_candidate(
            enriched, keep_hard_extension=keep_hard_extension,
        ):
            continue
        # Rank / attention read continuation_score
        ranked = dict(enriched)
        ranked["continuation_score"] = ranked["continuation_score_equal"]
        ranked["combined_rank_score"] = ranked["continuation_score_equal"]
        out.append(ranked)
    out.sort(
        key=lambda r: (
            -(float(r.get("continuation_score_equal") or 0.0)),
            float(r.get("scan_score") or 99.0),
        )
    )
    return out


def build_attention_pool_equal_signal(
    ranked: list[dict[str, Any]],
    *,
    popularity_ctx: dict[str, Any] | None = None,
    gainers: set[str] | None = None,
    top_k: int = ATTENTION_TOP_K,
    pool_max: int = ATTENTION_POOL_MAX,
    keep_hard_extension: bool = True,
) -> list[dict[str, Any]]:
    """
    Same union as live attention, but ranked already uses equal-signal scores.

    Hard-ext rows are dropped before the live pool builder. Soft-extension
    *admission* lane is kept (that is a hook, not a demotion).
    """
    if keep_hard_extension:
        ranked = [
            r for r in ranked
            if not is_hard_extended(r, keep_hard=True)
        ]
    prepared: list[dict[str, Any]] = []
    for r in ranked:
        q = dict(r)
        q["bar_state"] = ohlc_bar_state(q)
        if not keep_hard_extension:
            # Live pool builder also drops scan>=75; peel that only for sensitivity.
            try:
                if float(q.get("scan_score") or 0.0) >= HARD_EXT_SCAN:
                    q["_scan_score_real"] = q.get("scan_score")
                    q["scan_score"] = HARD_EXT_SCAN - 0.01
            except (TypeError, ValueError):
                pass
        prepared.append(q)
    return build_attention_pool(
        prepared,
        popularity_ctx=popularity_ctx,
        gainers=gainers,
        top_k=top_k,
        pool_max=pool_max,
        include_tws=False,
    )


def deterministic_case_verdict_equal_signal(dossier: dict[str, Any]) -> dict[str, Any]:
    """
    Rules-only case with the scan<=55 stage gate removed.

    Wreckage / toxic / contradiction / no-momentum WAIT stay.
    ENTER when constructive room + momentum + popular, any scan < 75.
    Else fail-closed WAIT — never invent LLM ENTER.
    """
    ruled = deterministic_case_verdict(dossier)
    if ruled is not None:
        # Live ENTER already required scan<=55; if it ENTER'd, keep it.
        return dict(ruled, source=str(ruled.get("source") or "rules") + "+equal_signal")

    scan = float(dossier.get("scan_score") or 99.0)
    popular = bool(
        dossier.get("tradable_popular")
        or dossier.get("recent_leaderboard")
        or dossier.get("tws_popular")
        or dossier.get("on_gainers")
        or dossier.get("buzz_accel")
    )
    if (
        dossier.get("room_class") == "CONSTRUCTIVE_ROOM"
        and dossier.get("momentum_context")
        and popular
        and scan < HARD_EXT_SCAN
    ):
        evidence = ["constructive_room+momentum+popular+equal_signal_no_scan_band"]
        return _case(
            str(dossier.get("symbol") or "").upper(),
            "ENTER",
            0.72,
            dossier,
            structure_note=(
                "Equal-signal rules ENTER: constructive room + popularity/momentum "
                "(scan band not used)"
            ),
            sentiment_note=(
                f"popular={dossier.get('tradable_popular')} "
                f"scan={scan:.1f} (stage ignored)"
            ),
            risks=[],
            evidence=evidence,
            source="rules_equal_signal",
        )

    return _case(
        str(dossier.get("symbol") or "").upper(),
        "WAIT",
        0.55,
        dossier,
        structure_note=(
            "Equal-signal rules-only: no ENTER (need constructive room + "
            "momentum + tradable popularity). LLM not used."
        ),
        sentiment_note="",
        risks=["case_incomplete_rules_only"],
        evidence=["llm_not_replayed"],
        source="rules_equal_signal_fail_closed",
    )


def review_case_equal_signal(row: dict[str, Any]) -> dict[str, Any]:
    """Rules-only equal-signal case. Never calls OpenRouter."""
    dossier = build_case_dossier(row)
    return deterministic_case_verdict_equal_signal(dossier)


def review_attention_pool_equal_signal(
    pool: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach rules-only equal-signal case; sort ENTER-first."""
    out: list[dict[str, Any]] = []
    for row in pool:
        case = review_case_equal_signal(row)
        r = dict(row)
        r["case_review"] = case
        r["case_verdict"] = case.get("verdict")
        r["case_confidence"] = case.get("confidence")
        r["case_source"] = case.get("source")
        out.append(r)
    order = {"ENTER": 0, "WAIT": 1, "REJECT": 2}
    out.sort(
        key=lambda r: (
            order.get(str(r.get("case_verdict") or ""), 9),
            -(float(r.get("case_confidence") or 0)),
            -(float(r.get("continuation_score") or 0)),
        )
    )
    return out


def select_would_take(
    reviewed: list[dict[str, Any]],
    *,
    max_n: int = MAX_NEW_ENTRIES_PER_SCAN,
    exclude_symbols: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Live take cap + tradable-popularity filter (select_enter_rows)."""
    return select_enter_rows(
        reviewed, max_n=max_n, exclude_symbols=exclude_symbols,
    )


def counterfactual_hour(
    passers: list[dict[str, Any]],
    *,
    popularity_ctx: dict[str, Any] | None = None,
    gainers: set[str] | None = None,
    keep_hard_extension: bool = True,
    open_symbols: set[str] | None = None,
    max_new: int = MAX_NEW_ENTRIES_PER_SCAN,
    top_k: int = ATTENTION_TOP_K,
    pool_max: int = ATTENTION_POOL_MAX,
) -> dict[str, Any]:
    """
    One hour: equal-signal rank → attention → rules case → cap.

    passers should already be 1H signal-valid rows (buy/early_bull). Rows
    that fail hard-extension are dropped in the primary path.
    """
    occupied = {str(s).upper() for s in (open_symbols or set())}
    ranked = rerank_equal_signal(
        passers, keep_hard_extension=keep_hard_extension,
    )
    ranked = [
        r for r in ranked
        if str(r.get("symbol") or "").upper() not in occupied
    ]
    attention = build_attention_pool_equal_signal(
        ranked,
        popularity_ctx=popularity_ctx,
        gainers=gainers,
        top_k=top_k,
        pool_max=pool_max,
        keep_hard_extension=keep_hard_extension,
    )
    reviewed = review_attention_pool_equal_signal(attention)
    take = select_would_take(
        reviewed, max_n=max_new, exclude_symbols=occupied,
    )
    return {
        "keep_hard_extension": keep_hard_extension,
        "max_new": max_new,
        "ranked_n": len(ranked),
        "attention_n": len(reviewed),
        "ranked": ranked,
        "attention": reviewed,
        "would_take": take,
        "would_take_symbols": [
            str(r.get("symbol") or "").upper() for r in take
        ],
        "case_enter_symbols": [
            str(r.get("symbol") or "").upper()
            for r in reviewed
            if str(r.get("case_verdict") or "").upper() == "ENTER"
        ],
    }


def live_vs_equal_pair(row: dict[str, Any]) -> dict[str, Any]:
    """Compact live vs equal comparison for one constructed or scanned row."""
    live = enrich_launch_fields(dict(row))
    eq = enrich_equal_signal_fields(live)
    return {
        "symbol": eq.get("symbol"),
        "scan_score": eq.get("scan_score"),
        "phase": eq.get("phase"),
        "live_bar_state": eq.get("bar_state"),
        "ohlc_bar_state": eq.get("ohlc_bar_state"),
        "live_continuation": eq.get("continuation_score_live"),
        "equal_continuation": eq.get("continuation_score_equal"),
        "live_list_ok": eq.get("live_list_ok"),
        "equal_list_ok": eq.get("equal_signal_list_ok"),
        "live_soft_stage_leak_hard_block": eq.get("live_soft_stage_leak_hard_block"),
        "stage_demotion": eq.get("stage_demotion"),
        "live_score_raw": compute_continuation_score_v1_1(live),
    }
