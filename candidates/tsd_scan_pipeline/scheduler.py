"""
Q-ALPHA TSD pipeline — ET slot scheduler (:20 Polygon, :03 TWS).

Dispatches pipeline passes based on IBKR 3H bar-close schedule.
Designed for Windows Task Scheduler tick every 5 minutes.

Usage:
  py -3 candidates/tsd_scan_pipeline/scheduler.py --tick
  py -3 candidates/tsd_scan_pipeline/scheduler.py --polygon
  py -3 candidates/tsd_scan_pipeline/scheduler.py --tws [--live]
  py -3 candidates/tsd_scan_pipeline/scheduler.py --trail
  py -3 candidates/tsd_scan_pipeline/scheduler.py --tick --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytz

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

from state_paths import is_trading_day  # noqa: E402
from tsd_scan_pipeline.build_3h_bars import IBKR_3H_CLOSE_HOURS_ET  # noqa: E402

ET = pytz.timezone("America/New_York")
RESULTS_DIR = PIPELINE_DIR / "results"
STATE_PATH = RESULTS_DIR / "tsd_scheduler_state.json"
POLYGON_LAG_MIN = 20
TWS_LAG = timedelta(hours=3, minutes=3)
TICK_WINDOW_MIN = 8


def _load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"last_runs": {}}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def _save_state(state: dict[str, Any]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def _slot_key(kind: str, bar_hour: int, when: datetime) -> str:
    return f"{kind}:{when.date().isoformat()}:{bar_hour:02d}"


def polygon_run_at(bar_hour: int, on_date: date) -> datetime:
    """Polygon PASS 1 runs at bar_close + 20 minutes."""
    naive = datetime.combine(on_date, time(bar_hour, POLYGON_LAG_MIN))
    return ET.localize(naive)


def tws_run_at(bar_hour: int, on_date: date) -> datetime:
    """TWS PASS 2 runs at bar_close + 3h03m (feeds next scan slot)."""
    close = ET.localize(datetime.combine(on_date, time(bar_hour, 0)))
    return close + TWS_LAG


def _due_slots(now: datetime, *, kind: str) -> list[tuple[int, datetime]]:
    """
    Return (bar_hour, scheduled_time) pairs due within TICK_WINDOW_MIN and not yet run.
    Checks today and yesterday for overnight wrap (e.g. 22:00 bar -> 01:03 TWS).
    """
    state = _load_state()
    last = state.get("last_runs") or {}
    due: list[tuple[int, datetime]] = []
    check_dates = [now.date(), (now - timedelta(days=1)).date()]

    for d in check_dates:
        for h in IBKR_3H_CLOSE_HOURS_ET:
            sched = polygon_run_at(h, d) if kind == "polygon" else tws_run_at(h, d)
            if sched > now:
                continue
            if (now - sched) > timedelta(minutes=TICK_WINDOW_MIN):
                continue
            key = _slot_key(kind, h, sched)
            if key in last:
                continue
            due.append((h, sched))
    due.sort(key=lambda x: x[1])
    return due


def _mark_ran(kind: str, bar_hour: int, sched: datetime) -> None:
    state = _load_state()
    state.setdefault("last_runs", {})[_slot_key(kind, bar_hour, sched)] = datetime.now(ET).isoformat()
    state["last_tick_at"] = datetime.now(ET).isoformat()
    _save_state(state)


def run_polygon_pass() -> int:
    from tsd_scan_pipeline.pipeline import run_polygon_pass

    return run_polygon_pass(refresh_universe=False, max_scan=None)


def run_tws_pass(*, live: bool) -> int:
    from tsd_scan_pipeline.pipeline import run_tws_pass

    return run_tws_pass(use_scanners=False, max_symbols=None, live=live, enforce_profiler=live)


def run_trail_pass(*, dry_run: bool = False) -> int:
    from tsd_scan_pipeline.tsd_trail_monitor import run_monitor

    run_monitor(dry_run=dry_run)
    return 0


def tick(*, dry_run: bool = False, live: bool = True) -> int:
    """Evaluate ET schedule and run any due passes."""
    now = datetime.now(ET)
    print(f"TSD scheduler tick ET={now.strftime('%Y-%m-%d %H:%M:%S')}")

    if now.weekday() >= 5:
        print("Weekend — tick skipped.")
        return 0
    if not is_trading_day(now.date()):
        print("Market holiday — tick skipped.")
        return 0

    rc = 0
    for bar_hour, sched in _due_slots(now, kind="polygon"):
        print(f"DUE polygon bar={bar_hour:02d}:00 scheduled={sched.isoformat()}")
        if dry_run:
            continue
        rc = max(rc, run_polygon_pass())
        _mark_ran("polygon", bar_hour, sched)

    for bar_hour, sched in _due_slots(now, kind="tws"):
        print(f"DUE tws bar={bar_hour:02d}:00 scheduled={sched.isoformat()} live={live}")
        if dry_run:
            continue
        rc = max(rc, run_tws_pass(live=live))
        _mark_ran("tws", bar_hour, sched)

    if not dry_run:
        # Lightweight trail pass each tick when loop task is not running
        if not _trail_loop_active():
            run_trail_pass(dry_run=False)

    if dry_run:
        poly = _due_slots(now, kind="polygon")
        tws = _due_slots(now, kind="tws")
        print(f"Dry-run summary: polygon_due={len(poly)} tws_due={len(tws)}")
    return rc


def _trail_loop_active() -> bool:
    """True if dedicated trail monitor loop reported recently."""
    state = _load_state()
    last = state.get("trail_loop_heartbeat")
    if not last:
        return False
    try:
        ts = datetime.fromisoformat(str(last))
        if ts.tzinfo is None:
            ts = ET.localize(ts)
        else:
            ts = ts.astimezone(ET)
        return (datetime.now(ET) - ts) < timedelta(minutes=3)
    except Exception:
        return False


def heartbeat_trail_loop() -> None:
    """Called by trail monitor loop to suppress duplicate tick trail passes."""
    state = _load_state()
    state["trail_loop_heartbeat"] = datetime.now(ET).isoformat()
    _save_state(state)


def main() -> int:
    parser = argparse.ArgumentParser(description="TSD pipeline ET scheduler")
    parser.add_argument("--tick", action="store_true", help="Run schedule tick (default)")
    parser.add_argument("--polygon", action="store_true", help="Force Polygon PASS 1")
    parser.add_argument("--tws", action="store_true", help="Force TWS PASS 2")
    parser.add_argument("--trail", action="store_true", help="Force one trail monitor pass")
    parser.add_argument("--live", action="store_true", help="TWS pass places paper entries")
    parser.add_argument("--dry-run", action="store_true", help="Log due slots only")
    args = parser.parse_args()

    if not (args.polygon or args.tws or args.trail or args.tick):
        args.tick = True

    if args.polygon:
        return run_polygon_pass()
    if args.tws:
        return run_tws_pass(live=args.live)
    if args.trail:
        return run_trail_pass(dry_run=args.dry_run)

    return tick(dry_run=args.dry_run, live=args.live)


if __name__ == "__main__":
    sys.exit(main())
