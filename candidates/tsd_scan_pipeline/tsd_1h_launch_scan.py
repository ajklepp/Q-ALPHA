"""
Q-ALPHA UTS v2.6 — hourly 1H LAUNCH scan.

HTF-pass names → last completed 1H launch eval → rank by HTF+launch →
queue / enter at most 2 NEW names if slots free.

Bar source: Polygon 1H aggs (see tsd_1h_signal.BAR_SOURCE).
Hours: 05–15 ET (scan at :15 after bar close; hitch study + RTH).
Rank: continuation_score_v1.1 (EXP-0021 ship gate). Cap: 2 new names per scan.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

from tsd_scan_pipeline.php_scan_funnel import (  # noqa: E402
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
    CONTINUATION_SCORE_VERSION,
    EXTENSION_SCAN_AUTO,
    enrich_launch_fields,
)
from tsd_scan_pipeline.tsd_watch_queue import add_to_watch_queue, execute_live_entries
from tsd_scan_pipeline.universe_tsd import load_polygon_key

ET = pytz.timezone("America/New_York")

QUEUE_ADMIT_STATUSES = {"ADDED", "UPDATED", "WATCHING"}
LAUNCH_CACHE_PATH = PIPELINE_DIR / "results" / "last_1h_launch.json"


def _write_launch_artifact(
    *,
    now_et: datetime,
    ranked: list[dict[str, Any]],
    take: list[dict[str, Any]],
    queue_results: list[dict[str, Any]] | None = None,
    entry_results: list[dict[str, Any]] | None = None,
) -> Path:
    """Persist today's Peak Hour 1H board for dashboard / Supabase watchlist SoT."""
    q_by_sym = {
        str(r.get("symbol", "")).upper(): r for r in (queue_results or [])
    }
    e_by_sym = {
        str(r.get("symbol", "")).upper(): r for r in (entry_results or [])
    }
    take_syms = {str(t.get("symbol", "")).upper() for t in take}
    rows_out: list[dict[str, Any]] = []
    for i, r in enumerate(ranked, 1):
        sym = str(r.get("symbol", "")).upper()
        qr = q_by_sym.get(sym) or {}
        er = e_by_sym.get(sym) or {}
        if er.get("status") == "FILLED":
            status = "ENTERED"
        elif str(qr.get("status", "")).upper() in QUEUE_ADMIT_STATUSES:
            status = "QUEUED"
        elif qr:
            st = str(qr.get("status") or "SKIP").upper()
            status = "SKIP" if st == "SKIPPED" else st
        elif sym in take_syms:
            status = "TAKE"
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
            "print": r.get("print"),
            "outlook": r.get("outlook"),
        })
    payload = {
        "updated_at": now_et.isoformat(),
        "strategy": "Peak Hour Performers",
        "version": "3.1-continuation-ranker",
        "bar_source": BAR_SOURCE,
        "hours": sorted(ALLOWED_HOURS),
        "continuation_score_version": CONTINUATION_SCORE_VERSION,
        "slots_per_scan": MAX_NEW_ENTRIES_PER_SCAN,
        "ranked_count": len(ranked),
        "take_count": len(take),
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
    """Rank by continuation_score_v1.1 (EXP-0021); peak hour is bonus only."""
    from tsd_scan_pipeline.universe_tsd import load_polygon_key
    from tsd_scan_pipeline.tsd_social import attach_social_to_rows
    from tsd_scan_pipeline.tsd_deep_features import attach_deep_features

    passed = [r for r in rows if r.get("pass")]
    if attach_social and passed:
        key = polygon_key or load_polygon_key()
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
                f"news>0={n_news} · ST_ok={n_st} · TWS_ok={n_tws}"
            )
        except Exception as exc:
            print(f"  social attach warn: {exc}")

    if passed:
        try:
            key = polygon_key or load_polygon_key()
            passed = attach_deep_features(passed, api_key=key, as_of=now)
            n_room = sum(1 for r in passed if abs(float(r.get("dist_20d_high_pct") or 0)) > 1e-6)
            n_prior = sum(1 for r in passed if float(r.get("ticker_prior_n") or 0) > 0)
            n_path = sum(1 for r in passed if float(r.get("ticker_prior_source") or 0) >= 2.0)
            print(
                f"  Deep features: room_filled={n_room} prior_filled={n_prior} "
                f"path_prior={n_path}"
            )
        except Exception as exc:
            print(f"  deep features warn: {exc}")

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
        base.update({k: v for k, v in htf_row.items() if k.startswith("htf_") or k == "close"})
    ok, launch_row = evaluate_1h_buy_signal(base, polygon_key=polygon_key, now=now)
    out = {**base, **launch_row, "symbol": symbol.upper()}
    scan = float(out.get("scan_score") or out.get("htf_1h_scan_score") or 0)
    # Hard-block only auto-extended; softer EXTENSION demoted in continuation_score_v1.1
    if scan >= EXTENSION_SCAN_AUTO or str(out.get("bar_state") or "") == "extended":
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
    now_et = now or datetime.now(ET)
    if now_et.tzinfo is None:
        now_et = ET.localize(now_et)
    else:
        now_et = now_et.astimezone(ET)

    print("=" * 64)
    print("1H LAUNCH v3.1 continuation-ranker")
    print(f"ET={now_et.strftime('%Y-%m-%d %H:%M:%S')} hours={sorted(ALLOWED_HOURS)}")
    print(f"Bar source: {BAR_SOURCE}  score={CONTINUATION_SCORE_VERSION}  slots={MAX_NEW_ENTRIES_PER_SCAN}")
    print("Structure: KILL ONLY until +1R")
    print("=" * 64)

    if not is_launch_hour_window(now_et):
        print(f"  SKIP: hour {now_et.hour} not in {sorted(ALLOWED_HOURS)}")
        return 0

    key = load_polygon_key()
    htf_doc = build_htf_universe(refresh=False, polygon_key=key)
    htf_rows = {str(r["symbol"]).upper(): r for r in htf_doc.get("rows") or []}
    symbols = htf_pass_symbols()
    if max_symbols:
        symbols = symbols[:max_symbols]
    htf_pass_count = len(symbols)
    print(f"HTF-pass universe: {htf_pass_count}")

    book = load_state()
    reset_scan_counter(book)
    save_state(book)

    rows: list[dict[str, Any]] = []
    for i, sym in enumerate(symbols, 1):
        row = evaluate_1h_symbol(
            sym, htf_row=htf_rows.get(sym), polygon_key=key, now=now_et,
        )
        rows.append(row)
        if row.get("pass") or i % 25 == 0 or i <= 5:
            print(
                f"  [{i:>3}/{len(symbols)}] {sym:<6} "
                f"1H_buy={row.get('buy_signal')} hour={row.get('htf_1h_bar_hour')} "
                f"phase3h={row.get('phase_3h')} -> "
                f"{'LAUNCH' if row.get('pass') else row.get('reject_reason')}"
            )

    ranked = rank_1h_launches(rows, polygon_key=key, now=now_et)
    take = ranked[:MAX_NEW_ENTRIES_PER_SCAN]
    print(f"\n1H launches: {len(ranked)}  taking top {len(take)} (cap {MAX_NEW_ENTRIES_PER_SCAN}/hour)")
    for r in take:
        print(
            f"  {r['symbol']:<6} 1H_close={r.get('htf_1h_close')} "
            f"hour={r.get('htf_1h_bar_hour')} bar={r.get('bar_state')} "
            f"HTF={r.get('htf_score')} launch={r.get('launch_score')} "
            f"cont={r.get('continuation_score')} mult={r.get('hour_mult')} "
            f"mode={r.get('structure_mode')}"
        )

    queue_results: list[dict[str, Any]] = []
    entry_results: list[dict[str, Any]] = []
    exit_code = 0

    if live and take:
        print("\n--- WATCH QUEUE / ENTER (queue-admitted only) ---")
        queue_results = add_to_watch_queue(take, scan_at=now_et.isoformat(), polygon_key=key)
        admitted: set[str] = set()
        skipped: list[dict[str, Any]] = []
        for qr in queue_results:
            sym = str(qr.get("symbol", "")).upper()
            st = str(qr.get("status", "")).upper()
            if st in QUEUE_ADMIT_STATUSES:
                admitted.add(sym)
            else:
                skipped.append(qr)
                print(f"  LIVE SKIP {sym}: status={st} reason={qr.get('reason', '')}")

        enter_rows = [r for r in take if str(r.get("symbol", "")).upper() in admitted]
        if not enter_rows:
            print("  No queue-admitted names to enter")
            if take and skipped:
                try:
                    from tsd_scan_pipeline.tsd_notify import (
                        format_queue_skip_summary,
                        notify_tsd,
                    )

                    notify_tsd(format_queue_skip_summary(len(take), skipped))
                except Exception:
                    pass
        else:
            from ib_insync import IB, util

            util.startLoop()
            ib = IB()
            try:
                ib.connect("127.0.0.1", 7497, clientId=93, timeout=12)
            except Exception as exc:
                print(f"CONNECT FAILED: {exc}")
                exit_code = 1
                ib = None
            if ib is not None:
                book = load_state()
                reset_scan_counter(book)
                entry_results = execute_live_entries(ib, enter_rows, book)
                save_state(book)
                try:
                    ib.disconnect()
                except Exception:
                    pass
                for fill in entry_results:
                    print(f"  {fill.get('symbol')} {fill.get('status')} {fill.get('reason', '')}")

    _write_launch_artifact(
        now_et=now_et,
        ranked=ranked,
        take=take,
        queue_results=queue_results,
        entry_results=entry_results,
    )
    funnel_path = _persist_funnel(
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

    # Missed-move ledger: ranked launches not filled (Weekly Review highlights)
    try:
        from tsd_scan_pipeline.php_missed_ledger import mark_ran_up, record_scan_outcomes

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
        n_mark = mark_ran_up(days=14, only_missed=True) if live else 0
        print(f"  Missed ledger upserted={n_led} marked={n_mark}")
    except Exception as exc:
        print(f"  missed ledger warn: {exc}")

    # Always refresh dashboard + Telegram after every live scan (incl. 0 launches).
    if live:
        try:
            from tsd_supabase_sync import push_dashboard_best_effort

            push_dashboard_best_effort(telegram_on_fail=True)
        except Exception as exc:
            print(f"  post-scan dashboard sync warn: {exc}")
        try:
            from tsd_scan_pipeline.tsd_notify import format_scan_summary, notify_tsd

            reject_summary: dict[str, Any] = {}
            try:
                import json as _json
                from pathlib import Path as _Path

                if funnel_path and _Path(str(funnel_path)).exists():
                    reject_summary = (
                        _json.loads(_Path(str(funnel_path)).read_text(encoding="utf-8")).get(
                            "reject_summary"
                        )
                        or {}
                    )
            except Exception:
                reject_summary = {}
            entered_n = sum(
                1 for e in entry_results if str(e.get("status") or "").upper() == "FILLED"
            )
            notify_tsd(
                format_scan_summary(
                    hour=now_et.hour,
                    htf_pass=htf_pass_count,
                    launches_n=len(ranked),
                    take_n=len(take),
                    entered_n=entered_n,
                    reject_summary=reject_summary if isinstance(reject_summary, dict) else None,
                    take_symbols=[str(r.get("symbol") or "").upper() for r in take],
                )
            )
        except Exception as exc:
            print(f"  post-scan telegram warn: {exc}")

    print("=" * 64)
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description="UTS v2.6 1H LAUNCH hourly scan")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-symbols", type=int, default=None)
    args = parser.parse_args()
    return run_1h_launch_scan(live=args.live, max_symbols=args.max_symbols)


if __name__ == "__main__":
    sys.exit(main())
