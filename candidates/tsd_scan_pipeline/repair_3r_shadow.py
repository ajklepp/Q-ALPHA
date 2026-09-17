#!/usr/bin/env python3
"""
One-shot repair: mirror Peak Hour live fills into the 3R shadow paper book.

Does not place IBKR orders or change live 4T trails. Safe to re-run
(idempotent on order_id / opened_at).

Laptop:
  py -3 candidates\\tsd_scan_pipeline\\repair_3r_shadow.py
"""
from __future__ import annotations

import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

from tsd_scan_pipeline.tsd_shadow_multi_target import (  # noqa: E402
    scoreboard,
    shadow_book_path,
    sync_shadow_from_live_book,
)


def main() -> int:
    """Backfill the 3R book from tsd_book_state.json and print totals."""
    summary = sync_shadow_from_live_book()
    sc = scoreboard()
    print(
        f"3R repair: mirrored={summary['n_mirrored']} "
        f"replayed={summary['n_replayed']} "
        f"open={sc['n_open']} closed={sc['n_closed']} "
        f"total=${sc['total_pnl']:+.2f}"
    )
    print(f"  live_reason={summary['reason']} book={shadow_book_path()}")
    if summary["reason"] == "no_live_book":
        print(
            "  No tsd_book_state.json — if Trade Log shows Peak Hour fills, "
            "open the dashboard (it can reconstruct from Supabase) or copy "
            "the live book onto this machine."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
