"""
Compare TSD BUY cross counts: IBKR 3H vs legacy Polygon hourly resample.

Usage:
  py -3 candidates/tsd_scan_pipeline/tsd_bar_parity.py
  py -3 candidates/tsd_scan_pipeline/tsd_bar_parity.py --symbols SPY,TSLA,PACB
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytz
from ib_insync import IB, util

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

from tsd_scan_pipeline.build_3h_bars import bars_from_ibkr_history
from tsd_scan_pipeline.tsd_profiler import (
    _bars_3h_polygon_legacy_hourly,
    _extract_analogs,
    find_tsd_analog_days_polygon_legacy,
    load_polygon_key,
)
from tsd_scan_pipeline.tsd_signals import enrich_tsd

TWS_HOST = "127.0.0.1"
TWS_PORT = 7497
TWS_CLIENT_ID = 94
ET = pytz.timezone("America/New_York")
LOOKBACK_DAYS = 365
DEFAULT_SYMBOLS = ("SPY", "TSLA", "PACB")


def _filter_last_days(analogs: list[dict], days: int = LOOKBACK_DAYS) -> list[dict]:
    cutoff = datetime.now(ET) - timedelta(days=days)
    out = []
    for a in analogs:
        ts = pd.Timestamp(a["time"])
        if ts.tzinfo is None:
            ts = ET.localize(ts)
        else:
            ts = ts.astimezone(ET)
        if ts >= cutoff:
            out.append(a)
    return out


def _cross_summary(label: str, analogs: list[dict]) -> None:
    recent = analogs[-5:]
    print(f"  {label}: BUY crosses={len(analogs)}")
    if recent:
        print(f"    Last {len(recent)} crosses:")
        for a in recent:
            print(f"      {a['time']} close={a.get('close')}")


def compare_symbol(ib: IB | None, symbol: str, api_key: str) -> None:
    sym = symbol.upper()
    print(f"\n=== {sym} (last {LOOKBACK_DAYS}d) ===")

    ibkr_analogs: list[dict] = []
    if ib is not None and ib.isConnected():
        try:
            df = bars_from_ibkr_history(ib, sym, days=LOOKBACK_DAYS)
            enriched = enrich_tsd(df)
            ibkr_analogs = _filter_last_days(_extract_analogs(enriched))
            _cross_summary("IBKR 3H", ibkr_analogs)
        except Exception as exc:
            print(f"  IBKR 3H: ERROR {exc}")
    else:
        print("  IBKR 3H: (not connected)")

    legacy = find_tsd_analog_days_polygon_legacy(sym, api_key=api_key)
    legacy_analogs = _filter_last_days(list(legacy.get("analogs") or []))
    _cross_summary("Polygon hourly resample (legacy)", legacy_analogs)

    from tsd_scan_pipeline.tsd_profiler import find_tsd_analog_days

    bucketed = find_tsd_analog_days(sym, api_key=api_key, ib=None)
    bucket_analogs = _filter_last_days(list(bucketed.get("analogs") or []))
    _cross_summary(f"Polygon 30m IBKR buckets ({bucketed.get('bar_source')})", bucket_analogs)

    if ibkr_analogs:
        delta = len(ibkr_analogs) - len(legacy_analogs)
        print(f"  Delta IBKR vs legacy: {delta:+d}")


def main() -> int:
    parser = argparse.ArgumentParser(description="TSD bar parity — IBKR vs Polygon")
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--no-ibkr", action="store_true", help="Polygon-only comparison")
    args = parser.parse_args()

    api_key = load_polygon_key()
    if not api_key:
        print("ERROR: POLYGON_API_KEY required")
        return 1

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    ib: IB | None = None
    if not args.no_ibkr:
        util.startLoop()
        ib = IB()
        try:
            ib.connect(TWS_HOST, TWS_PORT, clientId=TWS_CLIENT_ID, timeout=12)
            print(f"Connected TWS {TWS_HOST}:{TWS_PORT} clientId={TWS_CLIENT_ID}")
        except Exception as exc:
            print(f"WARN: TWS connect failed ({exc}) — Polygon-only mode")
            ib = None

    for sym in symbols:
        compare_symbol(ib, sym, api_key)

    if ib is not None and ib.isConnected():
        ib.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
