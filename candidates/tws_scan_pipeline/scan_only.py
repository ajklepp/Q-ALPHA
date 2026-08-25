"""
TWS scan pipeline — dry scan-only (no orders).

Uses clientId 97 (spike id) so it does not collide with agent=5 / connector=1.

Usage:
  .\\venv\\Scripts\\python.exe candidates/tws_scan_pipeline/scan_only.py
  .\\venv\\Scripts\\python.exe candidates/tws_scan_pipeline/scan_only.py --limit 40
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent
CANDIDATES = PKG.parent
if str(CANDIDATES) not in sys.path:
    sys.path.insert(0, str(CANDIDATES))
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from ib_insync import IB, util  # noqa: E402

from pipeline import (  # noqa: E402
    MCAP_LEARN_MIN,
    MCAP_TRADE_MIN,
    SCAN_ROWS_PER_CODE,
    TARGET_UNIVERSE,
    TRADE_TOP_N,
    WATCH_TOP_N,
    run_morning_pipeline,
)

TWS_HOST = "127.0.0.1"
TWS_PORT = 7497
TWS_CLIENT_ID = 97  # dry / spike — not agent 5


def main() -> int:
    parser = argparse.ArgumentParser(description="TWS scan pipeline dry run (no orders)")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional enrich cap (default: full union ~TARGET_UNIVERSE)",
    )
    parser.add_argument(
        "--watch", type=int, default=WATCH_TOP_N, help=f"Watch top-N (default {WATCH_TOP_N})",
    )
    parser.add_argument(
        "--trade", type=int, default=TRADE_TOP_N, help=f"Trade top-N (default {TRADE_TOP_N})",
    )
    args = parser.parse_args()

    util.startLoop()
    ib = IB()
    print("=" * 64)
    print("TWS SCAN PIPELINE — SCAN-ONLY (no orders)")
    print(f"Host={TWS_HOST} Port={TWS_PORT} clientId={TWS_CLIENT_ID}")
    print(
        f"Funnel: {SCAN_ROWS_PER_CODE}/scanner -> ~{TARGET_UNIVERSE} union | "
        f"watch={args.watch} trade={args.trade}"
    )
    print(
        f"Lanes: TRADE>=${MCAP_TRADE_MIN/1e6:.0f}M  "
        f"LEARN ${MCAP_LEARN_MIN/1e6:.0f}-{MCAP_TRADE_MIN/1e6:.0f}M  "
        f"IGNORE <${MCAP_LEARN_MIN/1e6:.0f}M"
    )
    print("=" * 64)

    try:
        ib.connect(TWS_HOST, TWS_PORT, clientId=TWS_CLIENT_ID, timeout=12)
    except Exception as exc:
        print(f"CONNECT FAILED: {exc}")
        return 1

    accounts = list(ib.managedAccounts() or [])
    print(f"CONNECTED accounts={accounts}")

    try:
        result = run_morning_pipeline(
            ib,
            watch_n=args.watch,
            trade_n=args.trade,
            enrich_limit=args.limit,
        )
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass

    stats = result.get("stats") or {}
    watch = result.get("watch") or []
    trade = result.get("trade") or []
    print("\n" + "=" * 64)
    print("SCAN-ONLY SUMMARY (orders placed: 0)")
    print("=" * 64)
    print(f"  scanner_union:     {stats.get('scanner_union')}")
    print(f"  scored_shortlist:  {stats.get('scored_shortlist')}")
    print(f"  TRADE lane:        {stats.get('trade_lane')}  (entry_eligible={stats.get('trade_entry_eligible')})")
    print(f"  LEARN lane:        {stats.get('learn')}")
    print(f"  IGNORE lane:       {stats.get('ignore')}")
    print(f"  safety_block:      {stats.get('blocked_safety')}")
    print(f"  no_quote:          {stats.get('no_quote')}")
    print(f"  WATCH top {len(watch)}: {[(c['ticker'], c.get('lane')) for c in watch]}")
    print(f"  TRADE top {len(trade)}: {[c['ticker'] for c in trade]}")
    learn_in_watch = sum(1 for c in watch if c.get("lane") == "LEARN")
    print(f"  LEARN in watch: {learn_in_watch} (must not receive brackets)")
    print("  ORDERS: none (scan-only)")
    print("  clientId used: 97 (agent live uses 5)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
