#!/usr/bin/env python3
"""
One-off TSD pool reconcile — compare tsd_pool_state.json to book-derived accounting.

Usage (from repo root):
  python candidates/tsd_scan_pipeline/reconcile_pool.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = ROOT / "candidates"
sys.path.insert(0, str(CANDIDATES))

from state_paths import state_path  # noqa: E402
from tsd_scan_pipeline.tsd_pool import load_pool  # noqa: E402


def _closed_leg_pnl(leg: dict[str, Any]) -> float:
    """Sum exit P&L for one closed leg."""
    trail = leg.get("trail") or {}
    entry = _finite(trail.get("entry_price")) or _finite(leg.get("price"))
    if not entry or entry <= 0:
        return 0.0
    pnl = 0.0
    for ex in leg.get("exits") or []:
        sh = int(ex.get("shares") or 0)
        px = _finite(ex.get("exit_price"))
        if sh > 0 and px is not None:
            pnl += (px - entry) * sh
    return pnl


def _flatten_closed_pnl(book: dict[str, Any]) -> tuple[float, int]:
    total = 0.0
    count = 0
    for pos in book.get("positions") or []:
        for leg in pos.get("legs") or []:
            if str(leg.get("status", "")).upper() != "CLOSED":
                continue
            if not leg.get("exits"):
                continue
            total += _closed_leg_pnl(leg)
            count += 1
    return total, count


def _finite(x: Any) -> float | None:
    try:
        if x is None:
            return None
        v = float(x)
        return v if v == v else None  # NaN check
    except (TypeError, ValueError):
        return None


def _open_leg_remaining_shares(leg: dict[str, Any]) -> int:
    """Shares still open on a leg (full leg size minus recorded exits)."""
    leg_shares = int(leg.get("shares") or 0)
    exited = sum(int(ex.get("shares") or 0) for ex in leg.get("exits") or [])
    trail = leg.get("trail") or {}
    if str(leg.get("status", "")).upper() == "OPEN":
        from_tranches = sum(
            int(t.get("shares") or 0)
            for t in trail.get("tranches") or []
            if not t.get("closed")
        )
        if from_tranches > 0:
            return from_tranches
    return max(0, leg_shares - exited)


def _mark_price(leg: dict[str, Any]) -> float:
    trail = leg.get("trail") or {}
    for key in ("last_close", "peak_high"):
        px = _finite(trail.get(key))
        if px and px > 0:
            return px
    return _finite(leg.get("price")) or 0.0


def summarize_book(book: dict[str, Any]) -> dict[str, float]:
    """Derive realized, unrealized, open cost, and MTM from local book."""
    realized, closed_count = _flatten_closed_pnl(book)

    open_cost = 0.0
    open_mtm = 0.0
    for pos in book.get("positions") or []:
        for leg in pos.get("legs") or []:
            if str(leg.get("status", "")).upper() != "OPEN":
                continue
            sh = _open_leg_remaining_shares(leg)
            if sh <= 0:
                continue
            trail = leg.get("trail") or {}
            entry = _finite(trail.get("entry_price")) or _finite(leg.get("price")) or 0.0
            mark = _mark_price(leg)
            open_cost += entry * sh
            open_mtm += mark * sh

    unrealized = open_mtm - open_cost
    return {
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "open_cost_basis": open_cost,
        "open_mtm": open_mtm,
        "closed_legs": closed_count,
        "open_legs": sum(
            1
            for pos in book.get("positions") or []
            for leg in pos.get("legs") or []
            if str(leg.get("status", "")).upper() == "OPEN"
        ),
    }


def reconcile() -> dict[str, Any]:
    pool_path = state_path("tsd_pool_state.json")
    book_path = state_path("tsd_book_state.json")
    pool = load_pool(pool_path)
    book = json.loads(book_path.read_text(encoding="utf-8")) if book_path.exists() else {}

    book_sum = summarize_book(book)
    starting = float(pool.get("starting_pool") or 3000.0)
    cash = float(pool.get("pool") or 0.0)
    deployed = float(pool.get("deployed") or 0.0)

    equity_from_pool = cash + book_sum["open_mtm"]
    equity_from_pnl = starting + book_sum["realized_pnl"] + book_sum["unrealized_pnl"]

    expected_cash = starting + book_sum["realized_pnl"] - book_sum["open_cost_basis"]
    expected_deployed = book_sum["open_cost_basis"]
    phantom_deployed = deployed - expected_deployed

    return {
        "pool_file": str(pool_path),
        "book_file": str(book_path),
        "starting_pool": starting,
        "pool_cash": cash,
        "pool_deployed": deployed,
        "pool_plus_deployed": cash + deployed,
        **book_sum,
        "equity_mtm": equity_from_pool,
        "equity_pnl_identity": equity_from_pnl,
        "reconcile_gap": abs(equity_from_pool - equity_from_pnl),
        "expected_cash": expected_cash,
        "expected_deployed": expected_deployed,
        "phantom_deployed_estimate": phantom_deployed,
        "suggested_pool_correction": expected_cash - cash,
        "suggested_deployed_correction": expected_deployed - deployed,
    }


def main() -> None:
    r = reconcile()
    print("=== TSD pool reconcile ===")
    print(f"Pool file : {r['pool_file']}")
    print(f"Book file : {r['book_file']}")
    print()
    print(f"Starting pool     : ${r['starting_pool']:,.2f}")
    print(f"Pool cash (file)  : ${r['pool_cash']:,.2f}")
    print(f"Deployed (file)   : ${r['pool_deployed']:,.2f}")
    print(f"pool + deployed   : ${r['pool_plus_deployed']:,.2f}")
    print()
    print(f"Closed legs       : {r['closed_legs']}")
    print(f"Open legs         : {r['open_legs']}")
    print(f"Realized P&L      : ${r['realized_pnl']:+,.2f}")
    print(f"Open cost basis   : ${r['open_cost_basis']:,.2f}")
    print(f"Open MTM          : ${r['open_mtm']:,.2f}")
    print(f"Unrealized P&L    : ${r['unrealized_pnl']:+,.2f}")
    print()
    print(f"Equity (cash+MTM) : ${r['equity_mtm']:,.2f}")
    print(f"Equity (start+P&L): ${r['equity_pnl_identity']:,.2f}")
    print(f"Reconcile gap     : ${r['reconcile_gap']:,.2f}")
    print()
    print(f"Expected cash     : ${r['expected_cash']:,.2f}")
    print(f"Expected deployed : ${r['expected_deployed']:,.2f}")
    print(f"Phantom deployed  : ${r['phantom_deployed_estimate']:+,.2f}")
    print()
    print("Suggested corrections (add to file values):")
    print(f"  pool     : ${r['suggested_pool_correction']:+,.2f}")
    print(f"  deployed : ${r['suggested_deployed_correction']:+,.2f}")


if __name__ == "__main__":
    main()
