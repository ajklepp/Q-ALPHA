"""
Q-ALPHA TSD pipeline — orchestrator (Polygon :20 + TWS :03).

Two-pass swing scanner:
  PASS 1  polygon_hunt_list.py  @ :20 ET  (delayed Polygon — hunt list only)
  PASS 2  tsd_scan_ibkr.py       @ :03 ET  (TWS live — signal SoT)

Usage:
  py -3 candidates/tsd_scan_pipeline/pipeline.py --polygon
  py -3 candidates/tsd_scan_pipeline/pipeline.py --tws
  py -3 candidates/tsd_scan_pipeline/pipeline.py --full
  py -3 candidates/tsd_scan_pipeline/pipeline.py --polygon --max-scan 80
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

LIVE_BLOCK_MSG = (
    "Phase 4 trail monitor required. Run tsd_trail_monitor.py on schedule "
    "or set TSD_ALLOW_LIVE_WITHOUT_TRAIL=1 to override."
)
TRAIL_MONITOR_PATH = PIPELINE_DIR / "tsd_trail_monitor.py"


def _trail_monitor_ready() -> bool:
    return TRAIL_MONITOR_PATH.exists()


def _guard_live(live: bool) -> None:
    if live and os.environ.get("TSD_ALLOW_LIVE_WITHOUT_TRAIL") != "1" and not _trail_monitor_ready():
        print(f"ERROR: {LIVE_BLOCK_MSG}")
        sys.exit(1)


def run_polygon_pass(*, refresh_universe: bool, max_scan: int | None) -> int:
    from tsd_scan_pipeline.polygon_hunt_list import build_polygon_hunt_list
    from tsd_scan_pipeline.universe_tsd import build_daily_universe, load_polygon_key

    api_key = load_polygon_key()
    universe = build_daily_universe(api_key, refresh=refresh_universe)
    if not universe:
        print("FAIL: empty TSD universe")
        return 1
    build_polygon_hunt_list(api_key, universe, max_scan=max_scan)
    return 0


def run_tws_pass(
    *,
    use_scanners: bool,
    max_symbols: int | None,
    live: bool = False,
    enforce_profiler: bool = False,
) -> int:
    from tsd_scan_pipeline.tsd_capacity import load_state, open_symbols
    from tsd_scan_pipeline.tsd_scan_ibkr import run_scan

    skip_profiler = not (live or enforce_profiler)
    book_opens = open_symbols(load_state())

    return run_scan(
        symbols=None,
        hunt_list_file=PIPELINE_DIR / "polygon_hunt_list.json",
        use_scanners=use_scanners,
        max_symbols=max_symbols,
        skip_profiler=skip_profiler,
        open_positions=book_opens,
        live=live,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="TSD 3HR swing pipeline orchestrator")
    parser.add_argument("--polygon", action="store_true", help="Run PASS 1 Polygon hunt list")
    parser.add_argument("--tws", action="store_true", help="Run PASS 2 TWS dry scan")
    parser.add_argument("--full", action="store_true", help="Polygon then TWS")
    parser.add_argument("--refresh-universe", action="store_true")
    parser.add_argument("--max-scan", type=int, default=None, help="Cap Polygon TSD eval count")
    parser.add_argument("--max-symbols", type=int, default=None, help="Cap TWS hunt list")
    parser.add_argument("--use-scanners", action="store_true", help="Union TWS scanners on PASS 2")
    parser.add_argument("--live", action="store_true", help="PASS 2 paper entries (TWS required)")
    parser.add_argument(
        "--enforce-profiler",
        action="store_true",
        help="Require profiler v2 on watch-10 (MIN 30 analogs)",
    )
    args = parser.parse_args()

    if not (args.polygon or args.tws or args.full):
        parser.print_help()
        return 1

    _guard_live(args.live)

    rc = 0
    if args.polygon or args.full:
        print("\n>>> PASS 1 - Polygon hunt list\n")
        rc = run_polygon_pass(refresh_universe=args.refresh_universe, max_scan=args.max_scan)
        if rc != 0:
            return rc

    if args.tws or args.full:
        mode = "LIVE" if args.live else "DRY_RUN"
        print(f"\n>>> PASS 2 - TWS scan ({mode})\n")
        rc = run_tws_pass(
            use_scanners=args.use_scanners,
            max_symbols=args.max_symbols,
            live=args.live,
            enforce_profiler=args.enforce_profiler,
        )
    return rc


if __name__ == "__main__":
    sys.exit(main())
