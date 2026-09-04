"""
Q-ALPHA TSD pipeline — ET slot scheduler (UTS v2.6).

LAUNCH scans at 07:15 / 10:15 / 11:15 / 12:15 / 13:15 / 14:15 / 15:15 ET
(1H bar close + 15 min). Continuation ranker (EXP-0021); 2 slots/scan.
Delayed Polygon: wait so the completed hour is in the API (no front-run).
HTF universe refresh at 04:30 ET (optional noon).
Trail monitor unchanged (each tick unless dedicated loop is alive).

3H :20/:03 clocks are no longer the launch trigger. Force with --polygon / --tws.

Usage:
  py -3 candidates/tsd_scan_pipeline/scheduler.py --tick
  py -3 candidates/tsd_scan_pipeline/scheduler.py --launch [--live]
  py -3 candidates/tsd_scan_pipeline/scheduler.py --htf-universe
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
LAUNCH_HOURS_ET = (7, 10, 11, 12, 13, 14, 15)
LAUNCH_LAG_MIN = 15  # delayed Polygon settle; do not front-run forming 1H bar
HTF_REFRESH_AT = time(4, 30)
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


def launch_run_at(bar_hour: int, on_date: date) -> datetime:
    """1H LAUNCH scan at bar_close + 15 min (delayed Polygon settle)."""
    naive = datetime.combine(on_date, time(bar_hour, LAUNCH_LAG_MIN))
    return ET.localize(naive)


def htf_refresh_at(on_date: date) -> datetime:
    return ET.localize(datetime.combine(on_date, HTF_REFRESH_AT))


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
        hours = LAUNCH_HOURS_ET if kind == "launch" else IBKR_3H_CLOSE_HOURS_ET
        for h in hours:
            if kind == "polygon":
                sched = polygon_run_at(h, d)
            elif kind == "launch":
                sched = launch_run_at(h, d)
            else:
                sched = tws_run_at(h, d)
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


def run_launch_pass(*, live: bool) -> int:
    from tsd_scan_pipeline.tsd_1h_launch_scan import run_1h_launch_scan

    return run_1h_launch_scan(live=live)


def run_htf_universe_pass() -> int:
    from tsd_scan_pipeline.tsd_htf_universe import build_htf_universe

    build_htf_universe(refresh=True)
    return 0


def run_trail_pass(*, dry_run: bool = False) -> int:
    from tsd_scan_pipeline.tsd_trail_monitor import run_monitor

    run_monitor(dry_run=dry_run)
    return 0


def _due_clock(now: datetime, at: datetime, kind: str, hour_key: int) -> bool:
    """True when `at` is in the past within TICK_WINDOW_MIN and not yet marked."""
    if now < at:
        return False
    if (now - at) > timedelta(minutes=TICK_WINDOW_MIN):
        return False
    last = (_load_state().get("last_runs") or {})
    return _slot_key(kind, hour_key, at) not in last


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
    for bar_hour, sched in _due_slots(now, kind="launch"):
        print(f"DUE 1H launch bar={bar_hour:02d}:00 scheduled={sched.isoformat()} live={live}")
        if dry_run:
            continue
        rc = max(rc, run_launch_pass(live=live))
        _mark_ran("launch", bar_hour, sched)

    htf_0430 = htf_refresh_at(now.date())
    htf_noon = ET.localize(datetime.combine(now.date(), time(12, 0)))
    for hour_key, sched in ((4, htf_0430), (12, htf_noon)):
        if not _due_clock(now, sched, "htf", hour_key):
            continue
        print(f"DUE HTF universe refresh scheduled={sched.isoformat()}")
        if dry_run:
            continue
        rc = max(rc, run_htf_universe_pass())
        _mark_ran("htf", hour_key, sched)

    if not dry_run:
        if not _trail_loop_active():
            run_trail_pass(dry_run=False)

    if dry_run:
        launch = _due_slots(now, kind="launch")
        print(f"Dry-run summary: launch_due={len(launch)} hours={sorted(LAUNCH_HOURS_ET)}")
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
    parser.add_argument("--tick", action="store_true", help="Run schedule tick (default) — Live Paper")
    parser.add_argument(
        "--polygon",
        action="store_true",
        help="RESEARCH ONLY: force Polygon 3H hunt-list (not Live Paper entry)",
    )
    parser.add_argument(
        "--tws",
        action="store_true",
        help="RESEARCH ONLY: force 3H context scan; --live redirects to 1H LAUNCH",
    )
    parser.add_argument("--launch", action="store_true", help="Force 1H LAUNCH Peak Hour scan")
    parser.add_argument("--htf-universe", action="store_true", help="Force HTF-pass universe rebuild")
    parser.add_argument("--trail", action="store_true", help="Force one trail monitor pass")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Live Paper entries (1H LAUNCH / tick). Not for research --polygon",
    )
    parser.add_argument("--dry-run", action="store_true", help="Log due slots only")
    args = parser.parse_args()

    if not (args.polygon or args.tws or args.trail or args.tick or args.launch or args.htf_universe):
        args.tick = True

    if args.polygon:
        return run_polygon_pass()
    if args.launch:
        return run_launch_pass(live=args.live)
    if args.htf_universe:
        return run_htf_universe_pass()
    if args.tws:
        return run_tws_pass(live=args.live)
    if args.trail:
        return run_trail_pass(dry_run=args.dry_run)

    return tick(dry_run=args.dry_run, live=args.live)


if __name__ == "__main__":
    sys.exit(main())
