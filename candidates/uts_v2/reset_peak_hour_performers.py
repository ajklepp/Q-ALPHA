#!/usr/bin/env python3
"""
Peak Hour Performers v3.0 — archive + reset local paper state (+ optional cloud).

Archives runtime JSON under candidates/archive/pre_php_v3_YYYYMMDD_HHMM/,
then writes fresh book / pool / watch queue / scheduler last_runs.

Dashboard KPIs read Supabase (tsd_pool_snapshots / tsd_positions / tsd_closed_legs).
Use --also-reset-cloud so Session KPIs show a clean $3000 slate.

Usage (repo root):
  .\\venv\\Scripts\\python.exe candidates\\uts_v2\\reset_peak_hour_performers.py
  .\\venv\\Scripts\\python.exe candidates\\uts_v2\\reset_peak_hour_performers.py --also-reset-cloud
  .\\venv\\Scripts\\python.exe candidates\\uts_v2\\reset_peak_hour_performers.py --dry-run

NEVER git-commit archived or reset state files.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytz

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = ROOT / "candidates"
sys.path.insert(0, str(CANDIDATES))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from state_paths import state_path  # noqa: E402
from tsd_scan_pipeline.tsd_pool import DEFAULT_STARTING_POOL  # noqa: E402

ET = pytz.timezone("America/New_York")
SCHEDULER_STATE = CANDIDATES / "tsd_scan_pipeline" / "results" / "tsd_scheduler_state.json"
ARCHIVE_ROOT = CANDIDATES / "archive"

# Cloud tables that feed Peak Hour Performers dashboard KPIs / trade log.
CLOUD_CLEAR_TABLES = (
    "tsd_closed_legs",
    "tsd_positions",
    "tsd_watchlist",
    "tsd_pool_snapshots",
)
# Optional (may be missing until tsd_cloud.sql re-run):
CLOUD_OPTIONAL_TABLES = ("tsd_watch_queue",)


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


def _delete_all_rows(client: object, table: str) -> None:
    """Best-effort delete-all via PostgREST filter (Supabase requires a filter)."""
    attempts = (
        ("symbol", ""),
        ("snapshot_date", ""),
        ("leg_opened_at", ""),
    )
    last_exc: Exception | None = None
    for col, val in attempts:
        try:
            client.table(table).delete().neq(col, val).execute()
            return
        except Exception as exc:
            last_exc = exc
            continue
    if last_exc:
        raise last_exc


def reset_cloud_dashboard(*, dry_run: bool = False) -> None:
    """
    Clear Peak Hour Performers Supabase tables and seed pool=$3000.

    Dashboard Live Status reads cloud, not local JSON — local-only reset
    leaves stale cash/P&L/closed legs on the UI.
    """
    from supabase_sync import SupabaseSync

    print("")
    print("Cloud dashboard reset (Supabase TSD tables)")
    if dry_run:
        for t in CLOUD_CLEAR_TABLES:
            print(f"  DRY_RUN clear {t}")
        print(f"  DRY_RUN upsert tsd_pool_snapshots pool={DEFAULT_STARTING_POOL}")
        return

    sync = SupabaseSync()
    for table in CLOUD_CLEAR_TABLES:
        try:
            _delete_all_rows(sync.client, table)
            print(f"  CLEARED {table}")
        except Exception as exc:
            print(f"  CLEAR FAIL {table}: {exc}")
    for table in CLOUD_OPTIONAL_TABLES:
        try:
            _delete_all_rows(sync.client, table)
            print(f"  CLEARED {table}")
        except Exception as exc:
            print(f"  SKIP {table}: {exc}")

    seed = {
        "snapshot_date": date.today().isoformat(),
        "pool": float(DEFAULT_STARTING_POOL),
        "deployed": 0.0,
        "open_positions": 0,
        "open_names": 0,
        "starting_pool": float(DEFAULT_STARTING_POOL),
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    # Prefer full snapshot (regime cols); fall back if cloud schema lags.
    try:
        sync.upsert_tsd_pool_snapshot({
            **seed,
            "spy_regime": "UNKNOWN",
            "vix_regime": "NORMAL",
            "sizing_pct": "100%",
        })
    except Exception as exc:
        print(f"  FULL seed failed ({exc}); retrying core columns only")
        sync.client.table("tsd_pool_snapshots").upsert(
            seed, on_conflict="snapshot_date"
        ).execute()
    print(f"  SEEDED tsd_pool_snapshots pool=${DEFAULT_STARTING_POOL:,.0f} deployed=$0")

    latest = sync.get_latest_tsd_pool() or {}
    closed_n = len(sync.get_tsd_closed_legs())
    open_n = len(sync.get_tsd_positions(status="OPEN"))
    print(
        f"  VERIFY pool={latest.get('pool')} deployed={latest.get('deployed')} "
        f"open={open_n} closed_legs={closed_n}"
    )


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
    print("Next: .\\venv\\Scripts\\python.exe candidates\\uts_v2\\flatten_tws_paper.py --dry-run then --live")
    print("=" * 64)
    return archive_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="PHP v3.0 archive + reset local paper state")
    parser.add_argument("--dry-run", action="store_true", help="Print actions only")
    parser.add_argument(
        "--also-flatten-tws",
        action="store_true",
        help="After JSON reset, run flatten_tws_paper.py --live",
    )
    parser.add_argument(
        "--also-reset-cloud",
        action="store_true",
        help="Clear Supabase TSD tables + seed pool=$3000 (dashboard KPIs)",
    )
    args = parser.parse_args()
    archive_and_reset(dry_run=args.dry_run)
    if args.also_reset_cloud:
        reset_cloud_dashboard(dry_run=args.dry_run)
    if args.also_flatten_tws and not args.dry_run:
        import importlib.util

        path = Path(__file__).resolve().parent / "flatten_tws_paper.py"
        spec = importlib.util.spec_from_file_location("flatten_tws_paper", path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return int(mod.run(live=True, port=7497, allow_live_port=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
