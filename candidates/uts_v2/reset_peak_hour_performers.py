#!/usr/bin/env python3
"""
Peak Hour Performers v3.0 — archive + reset local paper state.

Archives runtime JSON under candidates/archive/pre_php_v3_YYYYMMDD_HHMM/,
then writes fresh book / pool / watch queue / scheduler last_runs.

Usage (repo root):
  py -3 candidates/uts_v2/reset_peak_hour_performers.py
  py -3 candidates/uts_v2/reset_peak_hour_performers.py --dry-run

NEVER git-commit archived or reset state files.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pytz

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = ROOT / "candidates"
sys.path.insert(0, str(CANDIDATES))

from state_paths import state_path  # noqa: E402
from tsd_scan_pipeline.tsd_pool import DEFAULT_STARTING_POOL  # noqa: E402

ET = pytz.timezone("America/New_York")
SCHEDULER_STATE = CANDIDATES / "tsd_scan_pipeline" / "results" / "tsd_scheduler_state.json"
ARCHIVE_ROOT = CANDIDATES / "archive"


def _default_book() -> dict:
    return {
        "positions": [],
        "entries_this_scan": 0,
        "last_scan_at": None,
        "reset_at": datetime.now(ET).isoformat(),
        "strategy": "Peak Hour Performers",
        "version": "3.0",
    }


def _default_pool() -> dict:
    return {
        "pool": DEFAULT_STARTING_POOL,
        "deployed": 0.0,
        "starting_pool": DEFAULT_STARTING_POOL,
        "reset_at": datetime.now(ET).isoformat(),
        "strategy": "Peak Hour Performers",
        "version": "3.0",
    }


def _default_queue() -> dict:
    return {
        "queue": [],
        "last_updated": datetime.now(ET).isoformat(),
        "reset_at": datetime.now(ET).isoformat(),
        "strategy": "Peak Hour Performers",
        "version": "3.0",
    }


def _default_scheduler() -> dict:
    return {
        "last_runs": {},
        "last_tick_at": None,
        "reset_at": datetime.now(ET).isoformat(),
        "strategy": "Peak Hour Performers",
        "version": "3.0",
    }


def archive_and_reset(*, dry_run: bool = False) -> Path:
    """Copy live state files to archive stamp dir, then write defaults."""
    stamp = datetime.now(ET).strftime("%Y%m%d_%H%M")
    archive_dir = ARCHIVE_ROOT / f"pre_php_v3_{stamp}"
    targets = {
        "tsd_book_state.json": state_path("tsd_book_state.json"),
        "tsd_pool_state.json": state_path("tsd_pool_state.json"),
        "tsd_watch_queue.json": state_path("tsd_watch_queue.json"),
        "tsd_scheduler_state.json": SCHEDULER_STATE,
    }
    writes = {
        targets["tsd_book_state.json"]: _default_book(),
        targets["tsd_pool_state.json"]: _default_pool(),
        targets["tsd_watch_queue.json"]: _default_queue(),
        targets["tsd_scheduler_state.json"]: _default_scheduler(),
    }

    print("=" * 64)
    print("Peak Hour Performers v3.0 — paper state reset")
    print(f"Archive -> {archive_dir}")
    print(f"dry_run={dry_run}")
    print("=" * 64)

    if not dry_run:
        archive_dir.mkdir(parents=True, exist_ok=True)

    for name, src in targets.items():
        exists = src.exists()
        print(f"  {'ARCHIVE' if exists else 'MISSING '} {src}")
        if dry_run or not exists:
            continue
        dest = archive_dir / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    for path, doc in writes.items():
        print(f"  WRITE   {path}")
        if dry_run:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")

    print("")
    print("Defaults: pool=$3000 deployed=$0 book=[] queue=[] last_runs={}")
    print("Aaron: cancel ALL TWS paper working orders + flatten any orphans before next live scan.")
    print("=" * 64)
    return archive_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="PHP v3.0 archive + reset local paper state")
    parser.add_argument("--dry-run", action="store_true", help="Print actions only")
    args = parser.parse_args()
    archive_and_reset(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
