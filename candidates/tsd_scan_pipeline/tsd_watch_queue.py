"""
Q-ALPHA UTS v2 — TSD watch queue (Phase 1).

Scan adds profiler-pass candidates to the queue instead of placing orders.
setup_watch_agent (Phase 3) will poll WATCHING rows and execute entries.
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
    occupied_symbols,
)

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
    Admit profiler-pass scan picks to the watch queue (no IBKR orders).

    Applies Phase 1 gates: scan_score>=70, wt_gap>=3, regime BULL, cross-book dedup.
    RTH 09:35-14:00 is recorded but does not block queue admission (Phase 3 executor).
    """
    state = load_queue()
    when = scan_at or datetime.now(ET).isoformat()
    bull, regime_label, regime_detail = fetch_regime_bull(polygon_key=polygon_key)
    results: list[dict[str, Any]] = []

    print(f"  Regime: {regime_label} (bull={bull})")

    for cand in trade_candidates:
        sym = str(cand.get("symbol", "")).upper()
        passed, gates, reasons = evaluate_entry_gates(
            cand, regime_bull=bull, require_rth_window=False,
        )

        if not passed:
            results.append({
                "symbol": sym,
                "status": "SKIPPED",
                "reason": ";".join(reasons) or "gate_fail",
                "gates": gates,
            })
            print(f"  QUEUE SKIP {sym}: {reasons}")
            continue

        row = {
            "symbol": sym,
            "signal_lane": infer_signal_lane(cand),
            "entry_score": float(cand.get("scan_score") or 0),
            "cross_level": round(float(cand.get("close") or 0), 4),
            "scan_score": float(cand.get("scan_score") or 0),
            "wt_gap": float(cand.get("wt_gap") or 0),
            "added_at": when,
            "status": "WATCHING",
            "gates": gates,
            "regime": regime_label,
            "kill_pct": cand.get("kill_pct"),
            "close": cand.get("close"),
            "tsd_profile": cand.get("tsd_profile"),
            "scan_at": when,
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
            f"score={row['scan_score']:.1f} wt_gap={row['wt_gap']:.1f} "
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
    Place session-aware BUY orders (Phase 3 setup_watch_agent only).

    NOT called from tsd_scan_ibkr in UTS v2 Phase 1+.
    """
    results: list[dict[str, Any]] = []
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
        fill = place_tsd_entry(ib, sym, entry_price=cand.get("close"), kill_pct=kill_pct)
        if fill.get("status") != "FILLED":
            results.append({**fill, "kind": entry_kind})
            print(f"  ENTRY FAIL {sym} ({entry_kind}): {fill.get('reason')}")
            continue

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
            tsd_profile=cand.get("tsd_profile"),
            session_at_entry=fill.get("session"),
        )
        results.append({**fill, "kind": entry_kind})
        print(
            f"  ENTRY FILLED {sym} ({entry_kind}): {fill['shares']} @ {fill['fill_price']:.2f} "
            f"session={fill.get('session')} kill={fill.get('kill_pct'):.1%} "
            f"kill_oid={fill.get('kill_order_id')}"
        )
    return results


def get_watching_rows() -> list[dict[str, Any]]:
    """Return queue rows with status WATCHING."""
    return [
        dict(r)
        for r in load_queue().get("queue") or []
        if str(r.get("status", "")).upper() == "WATCHING"
    ]


def queue_row_as_candidate(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a queue row to execute_live_entries candidate dict."""
    return {
        "symbol": str(row["symbol"]).upper(),
        "scan_score": float(row.get("scan_score") or row.get("entry_score") or 0),
        "wt_gap": float(row.get("wt_gap") or 0),
        "close": float(row.get("close") or row.get("cross_level") or 0),
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
