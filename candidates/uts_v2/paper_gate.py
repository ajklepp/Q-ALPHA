#!/usr/bin/env python3
"""
UTS v2 Phase 2.5 paper gate — track closed-leg WR before sizing up.

Target: 20 closed legs, WR >= 45%.

Usage:
  python candidates/uts_v2/paper_gate.py
  python candidates/uts_v2/paper_gate.py --min-trades 20 --min-wr 0.45
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


def _closed_leg_pnls(book: dict) -> list[float]:
    pnls: list[float] = []
    for pos in book.get("positions") or []:
        for leg in pos.get("legs") or []:
            if str(leg.get("status", "")).upper() != "CLOSED":
                continue
            trail = leg.get("trail") or {}
            entry = float(trail.get("entry_price") or leg.get("price") or 0)
            if entry <= 0:
                continue
            for ex in leg.get("exits") or []:
                sh = int(ex.get("shares") or 0)
                px = float(ex.get("exit_price") or 0)
                if sh > 0 and px > 0:
                    pnls.append((px - entry) * sh)
    return pnls


def main() -> None:
    parser = argparse.ArgumentParser(description="UTS v2 paper WR gate")
    parser.add_argument("--min-trades", type=int, default=20)
    parser.add_argument("--min-wr", type=float, default=0.45)
    args = parser.parse_args()

    book_path = state_path("tsd_book_state.json")
    if not book_path.exists():
        print(f"No book at {book_path}")
        sys.exit(1)

    book = json.loads(book_path.read_text(encoding="utf-8"))
    pnls = _closed_leg_pnls(book)
    n = len(pnls)
    winners = sum(1 for p in pnls if p > 0)
    wr = winners / n if n else 0.0
    total = sum(pnls)

    print("=== UTS v2 Phase 2.5 Paper Gate ===")
    print(f"Closed leg exits : {n}")
    print(f"Winners          : {winners}")
    print(f"Win rate         : {wr:.1%}")
    print(f"Total P&L        : ${total:+,.2f}")
    print(f"Gate             : {n} >= {args.min_trades} trades AND WR >= {args.min_wr:.0%}")

    passed = n >= args.min_trades and wr >= args.min_wr
    print(f"Status           : {'PASS' if passed else 'FAIL — keep paper size'}")
    sys.exit(0 if passed else 2)


if __name__ == "__main__":
    main()
