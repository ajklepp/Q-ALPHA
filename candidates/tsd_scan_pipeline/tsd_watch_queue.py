"""
Q-ALPHA UTS v2 — Peak Hour watch queue.

Live Paper: 1H LAUNCH scan admits names here, then execute_live_entries places
BUY orders (sole live entry authority via TSD Scheduler --tick --live).

setup_watch_agent.py is research/legacy — not on the Live Paper schedule.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import pytz

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

from state_paths import state_path
from tsd_scan_pipeline.tsd_capacity import can_enter, open_symbols, record_entry
from tsd_scan_pipeline.tsd_entry import place_tsd_entry
from tsd_scan_pipeline.tsd_entry_gates import (
    evaluate_entry_gates,
    fetch_regime_bull,
    infer_signal_lane,
)
from tsd_scan_pipeline.quality_history_gate import enrich_queue_row
from tsd_scan_pipeline.tsd_launch_score import enrich_launch_fields

ET = pytz.timezone("America/New_York")

WatchStatus = Literal["WATCHING", "CONFIRMED", "SKIPPED"]
QUEUE_PATH = state_path("tsd_watch_queue.json")


def load_queue() -> dict[str, Any]:
    """Load watch queue state from disk."""
    if not QUEUE_PATH.exists():
        return {"queue": [], "last_updated": None}
    return json.loads(QUEUE_PATH.read_text(encoding="utf-8"))


def save_queue(state: dict[str, Any]) -> None:
    """Persist watch queue state."""
    state["last_updated"] = datetime.now(ET).isoformat()
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_PATH.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def _queue_index(state: dict[str, Any], symbol: str) -> int | None:
    sym = symbol.upper()
    for i, row in enumerate(state.get("queue") or []):
        if str(row.get("symbol", "")).upper() == sym:
            return i
    return None


def add_to_watch_queue(
    trade_candidates: list[dict[str, Any]],
    *,
    scan_at: str | None = None,
    polygon_key: str | None = None,
) -> list[dict[str, Any]]:
    """
    Admit Peak Hour launch picks to the watch queue, then live path enters.

    Applies LAUNCH gates then quality_history_gate (analogs soft-only).
    News is context only. Caller (1H launch) runs execute_live_entries on admits.
    """
    state = load_queue()
    when = scan_at or datetime.now(ET).isoformat()
    try:
        gate_now = datetime.fromisoformat(when)
        if gate_now.tzinfo is None:
            gate_now = ET.localize(gate_now)
        else:
            gate_now = gate_now.astimezone(ET)
    except (TypeError, ValueError):
        gate_now = datetime.now(ET)
    bull, regime_label, regime_detail = fetch_regime_bull(polygon_key=polygon_key)
    results: list[dict[str, Any]] = []

    print(f"  Regime: {regime_label} (bull={bull})")

    for cand in trade_candidates:
        sym = str(cand.get("symbol", "")).upper()
        enriched = enrich_launch_fields({**cand, "symbol": sym})
        passed, gates, reasons = evaluate_entry_gates(
            enriched,
            regime_bull=bull,
            require_rth_window=False,
            now=gate_now,
            polygon_key=polygon_key,
        )

        if not passed:
            results.append({
                "symbol": sym,
                "status": "SKIPPED",
                "reason": ";".join(reasons) or "gate_fail",
                "gates": gates,
                "phase": enriched.get("phase"),
            })
            print(f"  QUEUE SKIP {sym}: {reasons} phase={enriched.get('phase')}")
            continue

        qh_row, qh_pass, qh_gates, qh_reasons = enrich_queue_row(
            {**enriched, **cand, "symbol": sym},
            polygon_key=polygon_key,
            fetch_news=True,
        )
        if not qh_pass:
            results.append({
                "symbol": sym,
                "status": "SKIPPED",
                "reason": ";".join(qh_reasons) or "quality_gate_fail",
                "gates": {**gates, **qh_gates},
                "phase": enriched.get("phase"),
            })
            print(f"  QUEUE SKIP {sym}: {qh_reasons} (quality/history)")
            continue

        row = {
            "symbol": sym,
            "signal_lane": infer_signal_lane(qh_row),
            "entry_score": float(qh_row.get("launch_score_display") or qh_row.get("launch_score") or 0),
            "launch_score": float(qh_row.get("launch_score") or 0),
            "htf_score": float(qh_row.get("htf_score") or gates.get("htf_score") or 0),
            "combined_rank_score": float(
                qh_row.get("combined_rank_score")
                or enriched.get("combined_rank_score")
                or qh_row.get("launch_score")
                or 0
            ),
            "launch_score_display": float(qh_row.get("launch_score_display") or qh_row.get("launch_score") or 0),
            "phase": qh_row.get("phase"),
            "signal_bar_red": bool(qh_row.get("signal_bar_red")),
            "bar_state": qh_row.get("bar_state") or cand.get("bar_state"),
            "hour_mult": qh_row.get("hour_mult") or cand.get("hour_mult"),
            "continuation_score": float(
                qh_row.get("continuation_score")
                or qh_row.get("combined_rank_score")
                or 0
            ),
            "early_bull": bool(qh_row.get("early_bull")),
            "buy_signal": bool(qh_row.get("buy_signal")),
            "print": qh_row.get("print") or "unknown",
            "outlook": qh_row.get("outlook") or "unknown",
            "guidance_cut": bool(qh_row.get("guidance_cut")),
            "structure_level": qh_row.get("structure_level") or cand.get("structure_level"),
            "structure_risk_pct": qh_row.get("structure_risk_pct") or cand.get("structure_risk_pct"),
            "cross_level": round(float(qh_row.get("close") or 0), 4),
            "scan_score": float(qh_row.get("scan_score") or 0),
            "wt_gap": float(qh_row.get("wt_gap") or 0),
            "htf_1h_bar_time": qh_row.get("htf_1h_bar_time") or cand.get("htf_1h_bar_time"),
            "htf_1h_bar_hour": qh_row.get("htf_1h_bar_hour") or cand.get("htf_1h_bar_hour"),
            "htf_1h_close": qh_row.get("htf_1h_close") or qh_row.get("close"),
            "htf_1h_buy_signal": qh_row.get("htf_1h_buy_signal", cand.get("htf_1h_buy_signal")),
            "phase_3h": qh_row.get("phase_3h") or qh_row.get("phase"),
            "structure_mode": "KILL ONLY until +1R",
            "htf_range_20d_pct": qh_row.get("htf_range_20d_pct") or cand.get("htf_range_20d_pct"),
            "htf_close_above_sma50": qh_row.get("htf_close_above_sma50", cand.get("htf_close_above_sma50")),
            "htf_sma20_rising": qh_row.get("htf_sma20_rising", cand.get("htf_sma20_rising")),
            "added_at": when,
            "status": "WATCHING",
            "gates": {**gates, "quality": qh_gates},
            "quality_gates": qh_gates,
            "regime": regime_label,
            "kill_pct": qh_row.get("kill_pct") or cand.get("kill_pct"),
            "close": qh_row.get("close"),
            "tsd_profile": qh_row.get("tsd_profile") or cand.get("tsd_profile"),
            "scan_at": when,
            "analog_count": qh_row.get("analog_count"),
            "analog_win_rate": qh_row.get("analog_win_rate"),
            "tags": qh_row.get("tags") or [],
            "size_mult": qh_row.get("size_mult", 1.0),
            "pre_catalyst": bool(qh_row.get("pre_catalyst", True)),
            "news_summary": qh_row.get("news_summary") or "",
            "catalyst_tier": int(qh_row.get("catalyst_tier") or 0),
            "sentiment_score": float(qh_row.get("sentiment_score") or 0),
            # Live social/news velocity (tsd_social) — soft rank + thesis only
            "news_velocity_24h": float(
                qh_row.get("news_velocity_24h") or cand.get("news_velocity_24h") or 0
            ),
            "news_velocity_72h": float(
                qh_row.get("news_velocity_72h") or cand.get("news_velocity_72h") or 0
            ),
            "news_headline_count_48h": float(
                qh_row.get("news_headline_count_48h")
                or cand.get("news_headline_count_48h")
                or 0
            ),
            "dilution_flag": int(qh_row.get("dilution_flag") or cand.get("dilution_flag") or 0),
            "distress_flag": int(qh_row.get("distress_flag") or cand.get("distress_flag") or 0),
            "catalyst_type": qh_row.get("catalyst_type") or cand.get("catalyst_type") or "none",
            "st_msg_24h": float(qh_row.get("st_msg_24h") or cand.get("st_msg_24h") or 0),
            "st_bull_ratio": float(
                qh_row.get("st_bull_ratio")
                if qh_row.get("st_bull_ratio") is not None
                else (cand.get("st_bull_ratio") if cand.get("st_bull_ratio") is not None else 0.5)
            ),
            "social_missing": int(
                qh_row.get("social_missing")
                if qh_row.get("social_missing") is not None
                else (cand.get("social_missing") if cand.get("social_missing") is not None else 1)
            ),
            "x_posts_24h": float(qh_row.get("x_posts_24h") or cand.get("x_posts_24h") or 0),
            "x_sent_lex": float(qh_row.get("x_sent_lex") or cand.get("x_sent_lex") or 0),
            "tws_ok": int(qh_row.get("tws_ok") or cand.get("tws_ok") or 0),
            "tws_headline_count": float(
                qh_row.get("tws_headline_count") or cand.get("tws_headline_count") or 0
            ),
            "dist_20d_high_pct": qh_row.get("dist_20d_high_pct", cand.get("dist_20d_high_pct")),
            "dist_20d_low_bounce": qh_row.get("dist_20d_low_bounce", cand.get("dist_20d_low_bounce")),
            "vol_ratio_20": qh_row.get("vol_ratio_20", cand.get("vol_ratio_20")),
            "ticker_prior_hit1r_rate": qh_row.get(
                "ticker_prior_hit1r_rate", cand.get("ticker_prior_hit1r_rate")
            ),
            "ticker_prior_mfe_p50": qh_row.get(
                "ticker_prior_mfe_p50", cand.get("ticker_prior_mfe_p50")
            ),
            "ticker_prior_n": qh_row.get("ticker_prior_n", cand.get("ticker_prior_n")),
            "ticker_prior_source": qh_row.get(
                "ticker_prior_source", cand.get("ticker_prior_source")
            ),
        }

        idx = _queue_index(state, sym)
        if idx is not None:
            existing = state["queue"][idx]
            if existing.get("status") == "CONFIRMED":
                results.append({
                    "symbol": sym,
                    "status": "UNCHANGED",
                    "reason": "already_confirmed",
                })
                print(f"  QUEUE KEEP {sym}: CONFIRMED (not overwritten)")
                continue
            state["queue"][idx] = row
            action = "UPDATED"
        else:
            state["queue"].append(row)
            action = "ADDED"

        results.append({"symbol": sym, "status": action, "lane": row["signal_lane"], "gates": gates})
        print(
            f"  QUEUE {action} {sym} lane={row['signal_lane']} "
            f"phase={row['phase']} launch={row['launch_score']:.0f} "
            f"scan={row['scan_score']:.1f} wt_gap={row['wt_gap']:.1f} "
            f"analogs={row.get('analog_count')} wr={row.get('analog_win_rate')}% "
            f"pre_cat={row.get('pre_catalyst')} tags={row.get('tags')} "
            f"cross={row['cross_level']}"
        )

    save_queue(state)
    return results


def execute_live_entries(
    ib,
    trade_candidates: list[dict[str, Any]],
    book_state: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Place session-aware BUY orders for Peak Hour / setup-watch.

    On FILLED: telegram + best-effort Supabase push (dashboard within seconds).
    """
    from tsd_scan_pipeline.tsd_capacity import save_state
    from tsd_scan_pipeline.tsd_notify import format_entered, notify_tsd

    results: list[dict[str, Any]] = []
    any_fill = False
    for cand in trade_candidates:
        sym = cand["symbol"]
        has_open = sym in open_symbols(book_state)
        is_addon = has_open
        ok, reason = can_enter(book_state, sym, is_addon=is_addon)
        if not ok:
            results.append({
                "symbol": sym, "status": "SKIPPED", "reason": reason,
                "kind": "SKIP",
            })
            print(f"  SKIP {sym}: {reason} (open={has_open}, addon={is_addon})")
            continue

        entry_kind = "ADDON" if reason == "addon" else "NEW"
        print(f"  {entry_kind} {sym}: capacity_ok reason={reason}")

        kill_pct = cand.get("kill_pct")
        from tsd_scan_pipeline.tsd_kill import resolve_kill_pct

        kill_pct, kill_source = resolve_kill_pct(
            kill_pct, profile=cand.get("tsd_profile"),
        )
        fill = place_tsd_entry(
            ib, sym,
            entry_price=cand.get("htf_1h_close") or cand.get("close"),
            kill_pct=kill_pct,
        )
        if fill.get("status") != "FILLED":
            results.append({**fill, "kind": entry_kind})
            print(f"  ENTRY FAIL {sym} ({entry_kind}): {fill.get('reason')}")
            continue

        fill_kill_source = fill.get("kill_source") or kill_source
        record_entry(
            book_state,
            sym,
            entry_price=float(fill["fill_price"]),
            shares=int(fill["shares"]),
            scan_score=float(cand.get("scan_score") or 0),
            is_addon=is_addon,
            order_id=fill.get("order_id"),
            kill_order_id=fill.get("kill_order_id"),
            kill_pct=fill.get("kill_pct"),
            kill_source=fill_kill_source,
            tsd_profile=cand.get("tsd_profile"),
            session_at_entry=fill.get("session"),
            bar_state=cand.get("bar_state"),
            bar_hour=cand.get("htf_1h_bar_hour"),
            continuation_score=cand.get("continuation_score") or cand.get("combined_rank_score"),
            print_tag=cand.get("print"),
            outlook=cand.get("outlook"),
            structure_level=cand.get("structure_level"),
            launch_score=float(cand.get("launch_score") or 0) or None,
            phase=cand.get("phase") or cand.get("phase_3h"),
            pre_catalyst=cand.get("pre_catalyst"),
            buy_signal=cand.get("buy_signal") or cand.get("htf_1h_buy_signal"),
            early_bull=cand.get("early_bull"),
            htf_close_above_sma50=cand.get("htf_close_above_sma50"),
            htf_sma20_rising=cand.get("htf_sma20_rising"),
            htf_range_20d_pct=cand.get("htf_range_20d_pct"),
            analog_win_rate=cand.get("analog_win_rate"),
            analog_count=cand.get("analog_count"),
            news_headline_count_48h=cand.get("news_headline_count_48h"),
            dollar_vol_1h=cand.get("dollar_vol_1h"),
            news_velocity_24h=cand.get("news_velocity_24h"),
            news_velocity_72h=cand.get("news_velocity_72h"),
            dilution_flag=cand.get("dilution_flag"),
            distress_flag=cand.get("distress_flag"),
            catalyst_type=cand.get("catalyst_type"),
            st_msg_24h=cand.get("st_msg_24h"),
            st_bull_ratio=cand.get("st_bull_ratio"),
            social_missing=cand.get("social_missing"),
            x_posts_24h=cand.get("x_posts_24h"),
            x_sent_lex=cand.get("x_sent_lex"),
            tws_ok=cand.get("tws_ok"),
            tws_headline_count=cand.get("tws_headline_count"),
            dist_20d_high_pct=cand.get("dist_20d_high_pct"),
            dist_20d_low_bounce=cand.get("dist_20d_low_bounce"),
            vol_ratio_20=cand.get("vol_ratio_20"),
            ticker_prior_hit1r_rate=cand.get("ticker_prior_hit1r_rate"),
            ticker_prior_mfe_p50=cand.get("ticker_prior_mfe_p50"),
            ticker_prior_n=cand.get("ticker_prior_n"),
            ticker_prior_source=cand.get("ticker_prior_source"),
        )
        any_fill = True
        results.append({**fill, "kind": entry_kind, "kill_source": fill_kill_source})
        print(
            f"  ENTRY FILLED {sym} ({entry_kind}): {fill['shares']} @ {fill['fill_price']:.2f} "
            f"session={fill.get('session')} kill={fill.get('kill_pct'):.1%} "
            f"src={fill_kill_source} bar={cand.get('bar_state')} "
            f"kill_oid={fill.get('kill_order_id')}"
        )
        notify_tsd(
            format_entered(
                sym,
                shares=int(fill["shares"]),
                fill_price=float(fill["fill_price"]),
                kill_pct=fill.get("kill_pct"),
                kill_source=fill_kill_source,
                bar_hour=cand.get("htf_1h_bar_hour"),
                bar_state=cand.get("bar_state"),
                rank=cand.get("combined_rank_score") or cand.get("launch_score"),
                continuation_score=cand.get("continuation_score"),
                print_tag=cand.get("print"),
                outlook=cand.get("outlook"),
                kind=entry_kind,
            )
        )

    if any_fill:
        try:
            save_state(book_state)
        except Exception as exc:
            print(f"  book save warn after fill: {exc}")
        try:
            from tsd_supabase_sync import push_dashboard_best_effort

            push_dashboard_best_effort(book=book_state, telegram_on_fail=True)
        except Exception as exc:
            print(f"  on-fill dashboard sync warn: {exc}")

    return results


def get_watching_rows() -> list[dict[str, Any]]:
    """Return WATCHING rows sorted by combined HTF+launch rank (highest first)."""
    rows = [
        dict(r)
        for r in load_queue().get("queue") or []
        if str(r.get("status", "")).upper() == "WATCHING"
    ]
    rows.sort(
        key=lambda r: -float(
            r.get("combined_rank_score") or r.get("launch_score") or 0
        ),
    )
    return rows


def queue_row_as_candidate(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a queue row to execute_live_entries candidate dict."""
    return {
        "symbol": str(row["symbol"]).upper(),
        "signal_lane": row.get("signal_lane"),
        "scan_score": float(row.get("scan_score") or 0),
        "launch_score": float(row.get("launch_score") or row.get("entry_score") or 0),
        "phase": row.get("phase"),
        "buy_signal": bool(row.get("buy_signal") or row.get("htf_1h_buy_signal")),
        "early_bull": bool(row.get("early_bull")),
        "signal_bar_red": bool(row.get("signal_bar_red")),
        "bar_state": row.get("bar_state"),
        "continuation_score": row.get("continuation_score") or row.get("combined_rank_score"),
        "print": row.get("print"),
        "outlook": row.get("outlook"),
        "structure_level": row.get("structure_level"),
        "wt_gap": float(row.get("wt_gap") or 0),
        "close": float(
            row.get("htf_1h_close") or row.get("entry_price") or row.get("close") or row.get("cross_level") or 0
        ),
        "htf_1h_buy_signal": row.get("htf_1h_buy_signal"),
        "htf_1h_bar_hour": row.get("htf_1h_bar_hour"),
        "htf_1h_bar_time": row.get("htf_1h_bar_time"),
        "htf_1h_close": row.get("htf_1h_close"),
        "phase_3h": row.get("phase_3h") or row.get("phase"),
        "htf_range_20d_pct": row.get("htf_range_20d_pct"),
        "htf_close_above_sma50": row.get("htf_close_above_sma50"),
        "htf_sma20_rising": row.get("htf_sma20_rising"),
        "kill_pct": row.get("kill_pct"),
        "tsd_profile": row.get("tsd_profile"),
    }


def update_queue_row(
    symbol: str,
    *,
    status: WatchStatus | None = None,
    reason: str | None = None,
    extra: dict[str, Any] | None = None,
) -> bool:
    """Update a queue row by symbol. Returns True if found."""
    state = load_queue()
    idx = _queue_index(state, symbol)
    if idx is None:
        return False
    row = state["queue"][idx]
    if status is not None:
        row["status"] = status
    if reason is not None:
        row["skip_reason"] = reason
    if status == "CONFIRMED":
        row["confirmed_at"] = datetime.now(ET).isoformat()
    if status == "SKIPPED":
        row["skipped_at"] = datetime.now(ET).isoformat()
    if extra:
        row.update(extra)
    state["queue"][idx] = row
    save_queue(state)
    return True


def watching_symbols() -> list[str]:
    """Symbols currently WATCHING in the queue."""
    state = load_queue()
    return [
        str(r["symbol"]).upper()
        for r in state.get("queue") or []
        if str(r.get("status", "")).upper() == "WATCHING"
    ]
