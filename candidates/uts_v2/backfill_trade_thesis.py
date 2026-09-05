#!/usr/bin/env python3
"""
Holiday dry-run: backfill thesis onto open/closed book legs + missed ledger.

Usage:
  .\\venv\\Scripts\\python.exe candidates\\uts_v2\\backfill_trade_thesis.py
  .\\venv\\Scripts\\python.exe candidates\\uts_v2\\backfill_trade_thesis.py --push
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = ROOT / "candidates"
sys.path.insert(0, str(CANDIDATES))

from state_paths import state_path  # noqa: E402
from tsd_scan_pipeline.php_missed_ledger import (  # noqa: E402
    LEDGER_PATH,
    _load_ledger,
    _save_ledger,
    mark_ran_up,
)
from tsd_scan_pipeline.trade_thesis import build_trade_thesis  # noqa: E402


def _backfill_book() -> int:
    path = state_path("tsd_book_state.json")
    if not path.exists():
        print(f"No book at {path}")
        return 0
    book = json.loads(path.read_text(encoding="utf-8"))
    launch_by_sym: dict[str, dict] = {}
    cache = CANDIDATES / "tsd_scan_pipeline" / "results" / "last_1h_launch.json"
    if cache.exists():
        try:
            payload = json.loads(cache.read_text(encoding="utf-8"))
            for r in payload.get("rows") or []:
                sym = str(r.get("symbol") or "").upper()
                if sym:
                    launch_by_sym[sym] = r
        except Exception:
            pass
    n = 0
    for pos in book.get("positions") or []:
        sym = str(pos.get("symbol") or "").upper()
        for leg in pos.get("legs") or []:
            status = str(leg.get("status") or "").upper()
            outcome = "TAKEN"
            extra = launch_by_sym.get(sym) or {}
            row = {
                **extra,
                **leg,
                "symbol": sym,
                "htf_1h_close": leg.get("price") or extra.get("htf_1h_close"),
                "entry_price": (leg.get("trail") or {}).get("entry_price") or leg.get("price"),
                "bar_state": leg.get("bar_state") or extra.get("bar_state"),
                "buy_signal": extra.get("buy_signal", True),
                "phase": leg.get("phase") or extra.get("phase"),
                "print": leg.get("print") if leg.get("print") is not None else extra.get("print"),
                "outlook": leg.get("outlook") if leg.get("outlook") is not None else extra.get("outlook"),
            }
            leg["thesis"] = build_trade_thesis(row, outcome=outcome)
            n += 1
            print(f"  book {sym} {status}: {leg['thesis'].get('headline')}")
    path.write_text(json.dumps(book, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {n} thesis cards -> {path}")
    return n


def _backfill_missed() -> int:
    # Re-seed from last launch cache if ledger thin
    cache = CANDIDATES / "tsd_scan_pipeline" / "results" / "last_1h_launch.json"
    if cache.exists():
        from datetime import datetime
        import pytz
        from tsd_scan_pipeline.php_missed_ledger import record_scan_outcomes

        ET = pytz.timezone("America/New_York")
        payload = json.loads(cache.read_text(encoding="utf-8"))
        updated = payload.get("updated_at") or datetime.now(ET).isoformat()
        now = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
        if now.tzinfo is None:
            now = ET.localize(now)
        ranked = []
        taken = set()
        for r in payload.get("rows") or []:
            ranked.append(r)
            st = str(r.get("status") or "").upper()
            if st in ("ENTERED", "FILLED", "OPEN"):
                taken.add(str(r.get("symbol") or "").upper())
        record_scan_outcomes(now_et=now, ranked=ranked, taken_symbols=taken)
        mark_ran_up(days=14, only_missed=True)

    doc = _load_ledger()
    launch_by_sym: dict[str, dict] = {}
    if cache.exists():
        try:
            for r in (json.loads(cache.read_text(encoding="utf-8")).get("rows") or []):
                sym = str(r.get("symbol") or "").upper()
                if sym:
                    launch_by_sym[sym] = r
        except Exception:
            pass
    n = 0
    for row in doc.get("rows") or []:
        outcome = str(row.get("outcome") or "MISSED").upper()
        extra = launch_by_sym.get(str(row.get("symbol") or "").upper()) or {}
        thesis = build_trade_thesis(
            {
                **extra,
                **row,
                "htf_1h_close": row.get("ref_price") or extra.get("htf_1h_close"),
                "entry_price": row.get("ref_price"),
                "bar_state": extra.get("bar_state") or row.get("bar_state"),
                "buy_signal": extra.get("buy_signal", True),
                "phase": extra.get("phase") or row.get("phase"),
            },
            outcome=outcome,
        )
        row["thesis"] = thesis
        n += 1
        print(f"  missed/ledger {row.get('symbol')} {outcome}: {thesis.get('headline')}")
    _save_ledger(doc)
    print(f"Wrote {n} thesis cards -> {LEDGER_PATH}")
    return n


def _push() -> None:
    from tsd_supabase_sync import push_dashboard_best_effort

    summary = push_dashboard_best_effort(telegram_on_fail=False)
    print("push:", summary)


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill trade thesis cards")
    ap.add_argument("--push", action="store_true", help="Push book + missed to Supabase")
    args = ap.parse_args()
    _backfill_book()
    _backfill_missed()
    if args.push:
        _push()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
