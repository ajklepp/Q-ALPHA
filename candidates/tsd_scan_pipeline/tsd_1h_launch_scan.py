"""
Q-ALPHA UTS v2.6 — hourly 1H LAUNCH scan.

HTF-pass names → last completed 1H launch eval → rank by continuation_score →
Attention Pool (score + gainers + buzz) → Case Review (ENTER/WAIT/REJECT) →
queue / enter at most 2 NEW names if slots free.

Bar source: Polygon 1H aggs (see tsd_1h_signal.BAR_SOURCE).
Hours: 05–15 ET (scan at :15 after bar close; hitch study + RTH).
Rank nominates; case decides. Cap: 2 new names per scan.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

from tsd_scan_pipeline.php_scan_funnel import (  # noqa: E402
    build_reject_summary,
    build_scan_funnel_doc,
    write_scan_funnel,
)
from tsd_scan_pipeline.tsd_1h_signal import (
    ALLOWED_HOURS,
    BAR_SOURCE,
    evaluate_1h_buy_signal,
    is_launch_hour_window,
)
from tsd_scan_pipeline.tsd_capacity import (
    MAX_NEW_ENTRIES_PER_SCAN,
    load_state,
    reset_scan_counter,
    save_state,
)
from tsd_scan_pipeline.tsd_htf_gates import compute_htf_rank_score
from tsd_scan_pipeline.tsd_htf_universe import build_htf_universe, htf_pass_symbols
from tsd_scan_pipeline.tsd_launch_score import (
    enrich_launch_fields,
    equal_signal_mode_label,
    is_hard_extension_block,
    live_ranker_version_label,
)
from tsd_scan_pipeline.tsd_stage_log import StageTimer  # noqa: E402
from tsd_scan_pipeline.tsd_watch_queue import (  # noqa: E402
    add_to_watch_queue,
    confirmed_symbols,
    process_micro_confirm_queue,
)
from tsd_scan_pipeline.universe_tsd import load_polygon_key

ET = pytz.timezone("America/New_York")

QUEUE_ADMIT_STATUSES = {"ADDED", "UPDATED", "WATCHING"}
# Queue outcomes that must NOT consume a new-risk take slot (autopsy P0).
QUEUE_NON_NEW_REASONS = frozenset({"already_confirmed"})
QUEUE_NON_NEW_STATUSES = frozenset({"UNCHANGED"})
LAUNCH_CACHE_PATH = PIPELINE_DIR / "results" / "last_1h_launch.json"
# Overlap Polygon 1H RTT across the HTF universe. polygon_get still 0.12s-paces
# and retries 429; 8 workers cut ~147 serial fetches from ~20 min toward ~3 min.
EVAL_WORKERS = 8


def _non_new_risk_symbols(book: dict[str, Any] | None = None) -> set[str]:
    """OPEN book + already CONFIRMED queue — exclude from take-slot accounting."""
    from tsd_scan_pipeline.tsd_capacity import open_symbols

    skip = set(confirmed_symbols())
    try:
        skip |= {str(s).upper() for s in open_symbols(book if book is not None else load_state())}
    except Exception:
        pass
    return {s for s in skip if s}


def _queue_consumed_new_slot(qr: dict[str, Any]) -> bool:
    """True when queue result represents a NEW risk attempt against the hour cap."""
    st = str(qr.get("status") or "").upper()
    reason = str(qr.get("reason") or "").lower()
    if st in QUEUE_NON_NEW_STATUSES or reason in QUEUE_NON_NEW_REASONS:
        return False
    return st in QUEUE_ADMIT_STATUSES or st in {"SKIPPED", "REJECTED"}


def _evaluate_universe(
    symbols: list[str],
    *,
    htf_rows: dict[str, dict[str, Any]],
    polygon_key: str,
    now_et: datetime,
) -> list[dict[str, Any]]:
    """1H launch eval for HTF-pass names, overlapping Polygon RTT."""
    n = len(symbols)
    if n == 0:
        return []
    workers = max(1, min(EVAL_WORKERS, n))
    print(f"  Evaluating {n} names with {workers} workers...", flush=True)
    rows: list[dict[str, Any] | None] = [None] * n

    def _one(item: tuple[int, str]) -> tuple[int, dict[str, Any]]:
        i, sym = item
        try:
            row = evaluate_1h_symbol(
                sym, htf_row=htf_rows.get(sym), polygon_key=polygon_key, now=now_et,
            )
        except Exception as exc:
            row = {
                "symbol": str(sym).upper(),
                "pass": False,
                "reject_reason": f"fetch_err:{exc}",
            }
        return i, row

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_one, (i, s)) for i, s in enumerate(symbols)]
        done = 0
        for fut in as_completed(futs):
            i, row = fut.result()
            rows[i] = row
            done += 1
            if row.get("pass") or done % 25 == 0 or done <= 5:
                print(
                    f"  [{done:>3}/{n}] {str(row.get('symbol') or ''):<6} "
                    f"1H_buy={row.get('buy_signal')} hour={row.get('htf_1h_bar_hour')} "
                    f"phase3h={row.get('phase_3h')} -> "
                    f"{'LAUNCH' if row.get('pass') else row.get('reject_reason')}",
                    flush=True,
                )
    return [r for r in rows if r is not None]


def _emit_live_scan_telegram(
    *,
    now_et: datetime,
    htf_pass_count: int,
    all_rows: list[dict[str, Any]],
    ranked: list[dict[str, Any]],
    take: list[dict[str, Any]],
    entered_n: int | None = None,
) -> None:
    """SCAN Telegram as soon as HTF/launches/take/rejects are known (not after sync)."""
    from tsd_scan_pipeline.tsd_notify import format_scan_summary, notify_tsd

    reject_summary, _ = build_reject_summary(all_rows)
    notify_tsd(
        format_scan_summary(
            hour=now_et.hour,
            htf_pass=htf_pass_count,
            launches_n=len(ranked),
            take_n=len(take),
            entered_n=entered_n,
            reject_summary=reject_summary if reject_summary else None,
            take_symbols=[str(r.get("symbol") or "").upper() for r in take],
        )
    )
    print(
        f"  Telegram sent: Peak Hour SCAN hour={now_et.hour} "
        f"(early; entered pending={entered_n is None})",
        flush=True,
    )


def _promote_next_enter_takes(
    attention: list[dict[str, Any]],
    *,
    already_tried: set[str],
    need: int,
    book: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Pick next CASE ENTER names not yet tried (fill-fail / already_confirmed refill)."""
    from tsd_scan_pipeline.tsd_case_review import select_enter_rows

    if need <= 0:
        return []
    exclude = _non_new_risk_symbols(book) | {str(s).upper() for s in already_tried}
    return select_enter_rows(attention, max_n=need, exclude_symbols=exclude)


def _write_launch_artifact(
    *,
    now_et: datetime,
    ranked: list[dict[str, Any]],
    take: list[dict[str, Any]],
    queue_results: list[dict[str, Any]] | None = None,
    entry_results: list[dict[str, Any]] | None = None,
    attention: list[dict[str, Any]] | None = None,
) -> Path:
    """Persist today's Peak Hour 1H board for dashboard / Supabase watchlist SoT."""
    q_by_sym = {
        str(r.get("symbol", "")).upper(): r for r in (queue_results or [])
    }
    e_by_sym = {
        str(r.get("symbol", "")).upper(): r for r in (entry_results or [])
    }
    att_by_sym = {
        str(r.get("symbol", "")).upper(): r for r in (attention or [])
    }
    take_syms = {str(t.get("symbol", "")).upper() for t in take}
    rows_out: list[dict[str, Any]] = []
    for i, r in enumerate(ranked, 1):
        sym = str(r.get("symbol", "")).upper()
        qr = q_by_sym.get(sym) or {}
        er = e_by_sym.get(sym) or {}
        ar = att_by_sym.get(sym) or r
        if er.get("status") == "FILLED":
            status = "ENTERED"
        elif str(qr.get("status", "")).upper() in QUEUE_ADMIT_STATUSES:
            status = "QUEUED"
        elif qr:
            st = str(qr.get("status") or "SKIP").upper()
            status = "SKIP" if st == "SKIPPED" else st
        elif sym in take_syms:
            status = "TAKE"
        elif sym in att_by_sym:
            cv = str((ar.get("case_review") or {}).get("verdict") or ar.get("case_verdict") or "")
            status = f"CASE_{cv}" if cv else "ATTENTION"
        else:
            status = "RANKED"
        rows_out.append({
            "rank": i,
            "symbol": sym,
            "htf_1h_bar_hour": r.get("htf_1h_bar_hour"),
            "htf_score": r.get("htf_score") or r.get("htf_rank_score"),
            "launch_score": r.get("launch_score"),
            "continuation_score": r.get("continuation_score") or r.get("combined_rank_score"),
            "combined_rank_score": r.get("combined_rank_score"),
            "bar_state": r.get("bar_state"),
            "hour_mult": r.get("hour_mult"),
            "phase": r.get("phase_3h") or r.get("phase"),
            "buy_signal": bool(r.get("buy_signal") or r.get("htf_1h_buy_signal")),
            "htf_1h_close": r.get("htf_1h_close") or r.get("close"),
            "status": status,
            "queue_reason": qr.get("reason"),
            "structure_mode": r.get("structure_mode"),
            "print": ar.get("print") or r.get("print"),
            "outlook": ar.get("outlook") or r.get("outlook"),
            "case_verdict": (ar.get("case_review") or {}).get("verdict") or ar.get("case_verdict"),
            "case_confidence": (ar.get("case_review") or {}).get("confidence")
            or ar.get("case_confidence"),
            "momentum_context": ar.get("momentum_context"),
            "on_gainers": ar.get("on_gainers"),
            "attention_reasons": ar.get("attention_reasons"),
            "room_class": (ar.get("case_review") or {}).get("room_class"),
            "rs_spy_1h": ar.get("rs_spy_1h"),
            "rs_spy_5d": ar.get("rs_spy_5d"),
            "dollar_vol_1h": ar.get("dollar_vol_1h"),
            "dollar_vol_1h_vs_20d": ar.get("dollar_vol_1h_vs_20d"),
            "float_shares": ar.get("float_shares"),
            "micro_dead_tape": ar.get("micro_dead_tape"),
            "options_call_share": ar.get("options_call_share"),
            "options_score_lite": ar.get("options_score_lite"),
        })
    payload = {
        "updated_at": now_et.isoformat(),
        "strategy": "Peak Hour Performers",
        "version": "3.2-case-review",
        "bar_source": BAR_SOURCE,
        "hours": sorted(ALLOWED_HOURS),
        "continuation_score_version": live_ranker_version_label(),
        "equal_signal": equal_signal_mode_label(),
        "slots_per_scan": MAX_NEW_ENTRIES_PER_SCAN,
        "ranked_count": len(ranked),
        "attention_count": len(attention) if attention is not None else None,
        "take_count": len(take),
        "entered_count": sum(
            1 for e in (entry_results or [])
            if str(e.get("status") or "").upper() == "FILLED"
        ),
        "take_to_entered_rate": (
            round(
                sum(
                    1 for e in (entry_results or [])
                    if str(e.get("status") or "").upper() == "FILLED"
                ) / len(take),
                4,
            )
            if take
            else None
        ),
        "case_enter_count": sum(
            1 for r in (attention or [])
            if str((r.get("case_review") or {}).get("verdict") or "").upper() == "ENTER"
        ) if attention is not None else None,
        "rows": rows_out,
    }
    LAUNCH_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAUNCH_CACHE_PATH.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"  Wrote {LAUNCH_CACHE_PATH.relative_to(CANDIDATES_DIR.parent)}")
    return LAUNCH_CACHE_PATH


def rank_1h_launches(
    rows: list[dict[str, Any]],
    *,
    polygon_key: str | None = None,
    now: datetime | None = None,
    attach_social: bool = True,
) -> list[dict[str, Any]]:
    """Rank by continuation_score (v1.6 same-day objective); peak hour is not a hard gate."""
    from tsd_scan_pipeline.universe_tsd import load_polygon_key
    from tsd_scan_pipeline.tsd_social import attach_social_to_rows
    from tsd_scan_pipeline.tsd_deep_features import attach_deep_features

    passed = [r for r in rows if r.get("pass")]
    if attach_social and passed:
        key = polygon_key or load_polygon_key()
        t_social = time.time()
        try:
            passed = attach_social_to_rows(
                passed,
                api_key=key,
                as_of=now,
                include_x=False,  # X API off — OpenRouter + Polygon/ST only
                include_st=True,
                include_tws=True,
            )
            n_news = sum(1 for r in passed if float(r.get("news_velocity_24h") or 0) > 0)
            n_st = sum(1 for r in passed if int(r.get("st_ok") or 0) == 1)
            n_tws = sum(1 for r in passed if int(r.get("tws_ok") or 0) == 1)
            print(
                f"  Social/news attached: {len(passed)} passers · "
                f"news>0={n_news} · ST_ok={n_st} · TWS_ok={n_tws} "
                f"({time.time() - t_social:.1f}s)",
                flush=True,
            )
        except Exception as exc:
            print(f"  social attach warn: {exc}", flush=True)

    if passed:
        t_deep = time.time()
        try:
            key = polygon_key or load_polygon_key()
            passed = attach_deep_features(passed, api_key=key, as_of=now)
            n_room = sum(1 for r in passed if abs(float(r.get("dist_20d_high_pct") or 0)) > 1e-6)
            n_prior = sum(1 for r in passed if float(r.get("ticker_prior_n") or 0) > 0)
            n_path = sum(1 for r in passed if float(r.get("ticker_prior_source") or 0) >= 2.0)
            print(
                f"  Deep features: room_filled={n_room} prior_filled={n_prior} "
                f"path_prior={n_path} ({time.time() - t_deep:.1f}s)",
                flush=True,
            )
        except Exception as exc:
            print(f"  deep features warn: {exc}", flush=True)

    # Decision-time RS_1h / $vol / float / options (autopsy gaps) — then re-rank.
    try:
        from tsd_scan_pipeline.tsd_decision_context import attach_decision_context

        t_ctx = time.time()
        # Preliminary score so options_top_n prefers strong names
        for row in passed:
            if row.get("continuation_score") is None:
                prelim = enrich_launch_fields(row)
                row["continuation_score"] = prelim.get("continuation_score")
        passed = attach_decision_context(
            passed, api_key=polygon_key or load_polygon_key(), now=now, options_top_n=12,
        )
        n_rs = sum(1 for r in passed if int(r.get("rs_spy_1h_ok") or 0) == 1)
        n_opt = sum(1 for r in passed if r.get("options_call_share") is not None)
        n_dead = sum(1 for r in passed if int(r.get("micro_dead_tape") or 0) == 1)
        print(
            f"  Decision context: rs_1h_ok={n_rs}/{len(passed)} "
            f"options={n_opt} dead_tape={n_dead} ({time.time() - t_ctx:.1f}s)",
            flush=True,
        )
    except Exception as exc:
        print(f"  decision context warn: {exc}", flush=True)

    for row in passed:
        enriched = enrich_launch_fields(row)
        if row.get("htf_range_20d_pct") is not None:
            row["htf_score"] = compute_htf_rank_score(row)
        elif row.get("htf_score") is None:
            row["htf_score"] = 0.0
        merged = {**enriched, **{k: row.get(k) for k in (
            "news_velocity_24h", "news_velocity_72h", "news_headline_count_48h",
            "dilution_flag", "distress_flag", "unresolved", "catalyst_type",
            "st_msg_24h", "st_bull_ratio", "st_ok",
            "x_posts_24h", "x_sent_lex", "x_ok", "social_missing",
            "guidance_cut", "print", "outlook",
            "tws_ok", "tws_headline_count",
            "dist_20d_high_pct", "dist_20d_low_bounce", "dist_20d_low_pct",
            "vol_ratio_20", "ticker_prior_hit1r_rate", "ticker_prior_mfe_p50",
            "ticker_prior_n", "ticker_prior_source",
            "gap_pct", "prior_close", "day_open",
            "rs_spy_5d", "rs_sector_5d", "rs_ok", "sector_etf", "sic_code",
            "rs_spy_1h", "rs_spy_1h_ok", "dollar_vol_1h", "dollar_vol_1h_vs_20d",
            "float_shares", "micro_dead_tape", "options_call_share",
            "options_score_lite", "decision_context_ok",
        ) if row.get(k) is not None}, "htf_score": row["htf_score"]}
        enriched2 = enrich_launch_fields(merged)
        row["launch_score"] = enriched2.get("launch_score")
        row["bar_state"] = enriched2.get("bar_state")
        row["hour_mult"] = enriched2.get("hour_mult")
        row["continuation_score"] = enriched2.get("continuation_score")
        row["continuation_score_v0"] = enriched2.get("continuation_score_v0")
        row["combined_rank_score"] = enriched2.get("combined_rank_score")
        for k in (
            "news_velocity_24h", "news_velocity_72h", "news_headline_count_48h",
            "dilution_flag", "distress_flag", "catalyst_type",
            "st_msg_24h", "st_bull_ratio", "social_missing",
            "x_posts_24h", "x_sent_lex",
            "tws_ok", "tws_headline_count",
            "dist_20d_high_pct", "dist_20d_low_bounce", "vol_ratio_20",
            "ticker_prior_hit1r_rate", "ticker_prior_mfe_p50", "ticker_prior_n",
            "ticker_prior_source",
            "gap_pct", "prior_close", "day_open",
            "rs_spy_5d", "rs_sector_5d", "rs_ok", "sector_etf", "sic_code",
            "rs_spy_1h", "rs_spy_1h_ok", "dollar_vol_1h", "dollar_vol_1h_vs_20d",
            "float_shares", "micro_dead_tape", "options_call_share",
            "options_score_lite", "decision_context_ok",
        ):
            if k in merged:
                row[k] = merged.get(k)
    passed.sort(
        key=lambda r: (-(r.get("combined_rank_score") or 0), r.get("scan_score") or 99),
    )
    return passed


def evaluate_1h_symbol(
    symbol: str,
    *,
    htf_row: dict[str, Any] | None = None,
    polygon_key: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """1H LAUNCH trigger. 3H buy_signal is NOT required. Soft EXTENSION via score."""
    base = {"symbol": symbol.upper(), "pass": False, "reject_reason": None}
    if htf_row:
        base.update({
            k: v for k, v in htf_row.items()
            if k.startswith("htf_")
            or k in ("close", "market_cap", "dollar_vol_20d_avg", "dollar_vol_20d")
        })
        if htf_row.get("dollar_vol_20d_avg") is not None and base.get("dollar_vol_20d") is None:
            base["dollar_vol_20d"] = htf_row.get("dollar_vol_20d_avg")
    ok, launch_row = evaluate_1h_buy_signal(base, polygon_key=polygon_key, now=now)
    out = {**base, **launch_row, "symbol": symbol.upper()}
    # Hard-block auto-extended. Equal-signal ON: scan>=75 only (no phase leak).
    if is_hard_extension_block(out):
        out["pass"] = False
        out["reject_reason"] = "extension_hard"
        return out
    if not ok:
        out["pass"] = False
        out["reject_reason"] = out.get("source") or out.get("reject_reason") or "not_1h_launch"
        if out.get("hour_allowed") is False:
            out["reject_reason"] = f"hour_not_allowed:{out.get('htf_1h_bar_hour')}"
        return out
    out["pass"] = True
    out["signal"] = "1H_LAUNCH"
    out["structure_mode"] = "KILL ONLY until +1R"
    return out


def _persist_funnel(
    *,
    now_et: datetime,
    htf_pass_count: int,
    symbols_scanned: int,
    all_rows: list[dict[str, Any]],
    ranked: list[dict[str, Any]],
    take: list[dict[str, Any]],
    queue_results: list[dict[str, Any]] | None,
    entry_results: list[dict[str, Any]] | None,
    t0: float,
    live: bool,
) -> Path:
    """Write research funnel artifact (dashboard board stays passers-only)."""
    doc = build_scan_funnel_doc(
        now_et=now_et,
        bar_source=BAR_SOURCE,
        hours=sorted(ALLOWED_HOURS),
        htf_pass_count=htf_pass_count,
        symbols_scanned=symbols_scanned,
        all_rows=all_rows,
        ranked=ranked,
        take=take,
        queue_results=queue_results,
        entry_results=entry_results,
        runtime_sec=time.time() - t0,
        live=live,
    )
    return write_scan_funnel(doc, now_et=now_et)


def run_1h_launch_scan(
    *,
    live: bool = False,
    max_symbols: int | None = None,
    now: datetime | None = None,
) -> int:
    """Hourly 1H LAUNCH scan on today's HTF-pass universe."""
    t0 = time.time()
    timer = StageTimer()
    now_et = now or datetime.now(ET)
    if now_et.tzinfo is None:
        now_et = ET.localize(now_et)
    else:
        now_et = now_et.astimezone(ET)

    print("=" * 64, flush=True)
    print("1H LAUNCH v3.1 continuation-ranker", flush=True)
    print(f"ET={now_et.strftime('%Y-%m-%d %H:%M:%S')} hours={sorted(ALLOWED_HOURS)}", flush=True)
    print(
        f"Bar source: {BAR_SOURCE}  score={live_ranker_version_label()}  "
        f"equal_signal={equal_signal_mode_label()}  "
        f"slots={MAX_NEW_ENTRIES_PER_SCAN}",
        flush=True,
    )
    print("Structure: KILL ONLY until +1R", flush=True)
    print("=" * 64, flush=True)

    if not is_launch_hour_window(now_et):
        print(f"  SKIP: hour {now_et.hour} not in {sorted(ALLOWED_HOURS)}", flush=True)
        return 0

    key = load_polygon_key()
    htf_doc = build_htf_universe(refresh=False, polygon_key=key)
    htf_rows = {str(r["symbol"]).upper(): r for r in htf_doc.get("rows") or []}
    symbols = htf_pass_symbols()
    if max_symbols:
        symbols = symbols[:max_symbols]
    htf_pass_count = len(symbols)
    print(f"HTF-pass universe: {htf_pass_count}", flush=True)
    timer.stage("htf")

    book = load_state()
    reset_scan_counter(book)
    save_state(book)

    rows = _evaluate_universe(
        symbols, htf_rows=htf_rows, polygon_key=key, now_et=now_et,
    )
    timer.stage("bars_eval")

    ranked = rank_1h_launches(rows, polygon_key=key, now=now_et)
    print(f"\n1H launches: {len(ranked)} ranked passers", flush=True)
    timer.stage("score")

    from tsd_scan_pipeline.tsd_attention import build_attention_pool
    from tsd_scan_pipeline.tsd_case_review import (
        alert_case_rank_disagreement,
        review_attention_pool,
        select_enter_rows,
    )

    attention_raw = build_attention_pool(ranked, polygon_key=key)
    timer.stage("attention")
    # Dry scans: rules-only case (no LLM spend). Live: full fusion + web search.
    attention = review_attention_pool(
        attention_raw,
        use_web_search=bool(live),
        allow_llm=bool(live),
        enrich_catalyst=bool(live),
        polygon_key=key,
    )
    alert_case_rank_disagreement(attention, notify=bool(live))
    timer.stage("case_review")

    # Autopsy P0: only NEW risk consumes the 2/hour take cap (skip already_confirmed / OPEN).
    exclude_take = _non_new_risk_symbols(book)
    if exclude_take:
        print(f"  Cap exclude (OPEN/CONFIRMED): {sorted(exclude_take)}", flush=True)
    take = select_enter_rows(
        attention, max_n=MAX_NEW_ENTRIES_PER_SCAN, exclude_symbols=exclude_take,
    )
    print(
        f"\nCase review: attention={len(attention)} "
        f"ENTER={sum(1 for r in attention if str(r.get('case_verdict') or '').upper()=='ENTER')} "
        f"taking {len(take)} NEW (cap {MAX_NEW_ENTRIES_PER_SCAN}/hour)",
        flush=True,
    )
    for r in take:
        case = r.get("case_review") or {}
        print(
            f"  {r['symbol']:<6} 1H_close={r.get('htf_1h_close')} "
            f"hour={r.get('htf_1h_bar_hour')} bar={r.get('bar_state')} "
            f"HTF={r.get('htf_score')} cont={r.get('continuation_score')} "
            f"CASE={case.get('verdict')} conf={case.get('confidence')} "
            f"mom={int(bool(r.get('momentum_context')))} "
            f"reasons={','.join(r.get('attention_reasons') or [])}",
            flush=True,
        )
    if not take and attention:
        print("  No case-ENTER names this scan (score alone cannot buy)", flush=True)

    queue_results: list[dict[str, Any]] = []
    entry_results: list[dict[str, Any]] = []
    exit_code = 0
    tried_syms: set[str] = {
        str(r.get("symbol") or "").upper() for r in take if r.get("symbol")
    }

    # SCAN TG as soon as HTF / launches / take / rejects are known — before
    # enter (micro-confirm) and before non-critical dashboard / missed_moves sync.
    if live:
        try:
            _emit_live_scan_telegram(
                now_et=now_et,
                htf_pass_count=htf_pass_count,
                all_rows=rows,
                ranked=ranked,
                take=take,
                entered_n=None,
            )
        except Exception as exc:
            print(f"  early scan telegram warn: {exc}", flush=True)
        timer.stage("telegram")
    else:
        timer.stage("telegram")

    def _live_queue_and_enter(ib, candidates: list[dict[str, Any]]) -> None:
        """Admit candidates to queue and micro-confirm → BUY; mutate outer results."""
        nonlocal queue_results, entry_results, book
        if not candidates:
            return
        print("\n--- WATCH QUEUE / ENTER (queue-admitted only) ---", flush=True)
        qrs = add_to_watch_queue(candidates, scan_at=now_et.isoformat(), polygon_key=key)
        queue_results.extend(qrs)
        admitted: set[str] = set()
        skipped: list[dict[str, Any]] = []
        for qr in qrs:
            sym = str(qr.get("symbol", "")).upper()
            st = str(qr.get("status", "")).upper()
            if st in QUEUE_ADMIT_STATUSES:
                admitted.add(sym)
            else:
                skipped.append(qr)
                print(
                    f"  LIVE SKIP {sym}: status={st} reason={qr.get('reason', '')}",
                    flush=True,
                )

        enter_rows = [r for r in candidates if str(r.get("symbol", "")).upper() in admitted]
        if not enter_rows:
            print("  No queue-admitted names to micro-confirm", flush=True)
            if candidates and skipped:
                try:
                    from tsd_scan_pipeline.tsd_notify import (
                        format_queue_skip_summary,
                        notify_tsd,
                    )

                    notify_tsd(format_queue_skip_summary(len(candidates), skipped))
                except Exception:
                    pass
            return

        book = load_state()
        reset_scan_counter(book)
        print("\n--- MICRO-CONFIRM (1-min tape since 1H close) ---", flush=True)
        fills = process_micro_confirm_queue(
            ib, book_state=book, live=True, polygon_key=key,
        )
        save_state(book)
        entry_results.extend(fills)
        for fill in fills:
            print(
                f"  {fill.get('symbol')} {fill.get('status')} "
                f"{fill.get('reason', '')}",
                flush=True,
            )

    if live and take:
        from ib_insync import IB, util

        util.startLoop()
        ib = IB()
        try:
            ib.connect("127.0.0.1", 7497, clientId=93, timeout=12)
        except Exception as exc:
            print(f"CONNECT FAILED: {exc}", flush=True)
            exit_code = 1
            ib = None
        if ib is not None:
            try:
                _live_queue_and_enter(ib, take)

                # Autopsy P0: already_confirmed / UNCHANGED must not burn the hour;
                # no_fill_timeout frees the slot for the next CASE ENTER.
                def _slots_remaining() -> int:
                    filled = sum(
                        1
                        for e in entry_results
                        if str(e.get("status") or "").upper() == "FILLED"
                    )
                    in_flight = sum(
                        1
                        for e in entry_results
                        if str(e.get("status") or "").upper()
                        in ("PENDING", "CONFIRM", "DRY_CONFIRM")
                    )
                    # Successful NEW queue admits still watching count as in-flight
                    watching_now = 0
                    try:
                        from tsd_scan_pipeline.tsd_watch_queue import watching_symbols

                        watching_now = sum(
                            1 for s in watching_symbols() if s in tried_syms
                        )
                    except Exception:
                        watching_now = 0
                    used = filled + in_flight + watching_now
                    return max(0, MAX_NEW_ENTRIES_PER_SCAN - used)

                non_new_skips = sum(
                    1 for qr in queue_results if not _queue_consumed_new_slot(qr)
                )
                no_fill_n = sum(
                    1
                    for e in entry_results
                    if str(e.get("reason") or "") == "no_fill_timeout"
                )
                need = _slots_remaining()
                if need > 0 and (non_new_skips or no_fill_n or need > 0):
                    # Always refill free NEW slots when ENTER backlog remains
                    promote = _promote_next_enter_takes(
                        attention,
                        already_tried=tried_syms,
                        need=need,
                        book=book,
                    )
                    if promote:
                        print(
                            f"\n--- PROMOTE NEXT ENTER "
                            f"(need={need} non_new_skips={non_new_skips} "
                            f"no_fill={no_fill_n}) ---",
                            flush=True,
                        )
                        for r in promote:
                            tried_syms.add(str(r.get("symbol") or "").upper())
                            take.append(r)
                            print(
                                f"  PROMOTE {r.get('symbol')} "
                                f"cont={r.get('continuation_score')} "
                                f"CASE={(r.get('case_review') or {}).get('verdict')}",
                                flush=True,
                            )
                        _live_queue_and_enter(ib, promote)
            finally:
                try:
                    ib.disconnect()
                except Exception:
                    pass
    timer.stage("enter")

    _write_launch_artifact(
        now_et=now_et,
        ranked=ranked,
        take=take,
        queue_results=queue_results,
        entry_results=entry_results,
        attention=attention,
    )
    _persist_funnel(
        now_et=now_et,
        htf_pass_count=htf_pass_count,
        symbols_scanned=len(rows),
        all_rows=rows,
        ranked=ranked,
        take=take,
        queue_results=queue_results,
        entry_results=entry_results,
        t0=t0,
        live=live,
    )
    timer.stage("funnel")

    # Missed-move ledger: ranked launches not filled (Weekly Review highlights).
    # Peak-price Polygon refresh is deferred off this hot path (dashboard sync
    # also skips mark_ran_up when refresh_missed_peaks=False).
    try:
        from tsd_scan_pipeline.php_missed_ledger import record_scan_outcomes

        taken_syms = {
            str(e.get("symbol") or "").upper()
            for e in entry_results
            if str(e.get("status") or "").upper() == "FILLED"
        }
        # Also treat OPEN book names from this scan's take as taken if already entered
        if not taken_syms and take:
            try:
                book_now = load_state()
                open_syms = {
                    str(p.get("symbol") or "").upper()
                    for p in (book_now.get("positions") or [])
                    if str(p.get("status") or "").upper() == "OPEN"
                }
                for r in take:
                    sym = str(r.get("symbol") or "").upper()
                    if sym in open_syms:
                        taken_syms.add(sym)
            except Exception:
                pass
        n_led = record_scan_outcomes(
            now_et=now_et, ranked=ranked, taken_symbols=taken_syms,
        )
        print(f"  Missed ledger upserted={n_led} (ran-up refresh deferred)", flush=True)
    except Exception as exc:
        print(f"  missed ledger warn: {exc}", flush=True)
    timer.stage("ledger")

    # Dashboard / book sync after SCAN TG + enter. Skip Polygon missed-peak
    # hammer (was ~400+ daily fetches, several minutes) on this tick.
    if live:
        try:
            from tsd_supabase_sync import push_dashboard_best_effort

            push_dashboard_best_effort(
                telegram_on_fail=True,
                refresh_missed_peaks=False,
            )
        except Exception as exc:
            print(f"  post-scan dashboard sync warn: {exc}", flush=True)
    timer.stage("sync")

    print(f"  {timer.summary()}", flush=True)
    print("=" * 64, flush=True)
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description="UTS v2.6 1H LAUNCH hourly scan")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-symbols", type=int, default=None)
    args = parser.parse_args()
    return run_1h_launch_scan(live=args.live, max_symbols=args.max_symbols)


if __name__ == "__main__":
    sys.exit(main())
