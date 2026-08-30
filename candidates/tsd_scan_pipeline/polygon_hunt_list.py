"""
Q-ALPHA TSD pipeline — PASS 1 Polygon hunt list (:20 ET).

Include-only pre-filter for the NEXT TWS :03 scan. NO orders. NO entries.
Writes candidates/tsd_scan_pipeline/polygon_hunt_list.json

Include tags (union — never hard-exclude without never-drop override):
  - near_cross: wt1 < wt2, gap < 20, wt1 rising
  - early_bull: Pine early_bull on last bar
  - trend_up: trend_strength improving and > -0.3
  - already_warm: wt1 > wt2
  - liquid_core: top 150 by 20d dollar volume (always included)

Usage:
  py -3 candidates/tsd_scan_pipeline/polygon_hunt_list.py
  py -3 candidates/tsd_scan_pipeline/polygon_hunt_list.py --max-scan 100
  py -3 candidates/tsd_scan_pipeline/polygon_hunt_list.py --refresh-universe
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytz

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

from tsd_scan_pipeline.build_3h_bars import aggregate_hourly_to_3h, bars_from_polygon_aggs
from tsd_scan_pipeline.tsd_signals import enrich_tsd
from tsd_scan_pipeline.universe_tsd import (
    LIQUID_CORE_TOP_N,
    POLYGON_BASE,
    build_daily_universe,
    load_polygon_key,
    polygon_get,
)

BAR_CLOSE_HOURS_ET = (1, 4, 7, 10, 13, 16, 19, 22)
HOURLY_LOOKBACK_DAYS = 90
MIN_BARS_FOR_TSD = 60
OUTPUT_PATH = PIPELINE_DIR / "polygon_hunt_list.json"
ET = pytz.timezone("America/New_York")


def schedule_meta(now_et: datetime | None = None) -> dict[str, str]:
    """
    Map a :20 Polygon run to source bar close and next TWS :03 scan.
    Polygon at H:20 after bar close H:00 feeds TWS at (H+3):03.
    """
    now = now_et or datetime.now(ET)
    if now.tzinfo is None:
        now = ET.localize(now)
    else:
        now = now.astimezone(ET)

    source_close: datetime | None = None
    for h in BAR_CLOSE_HOURS_ET:
        close_dt = now.replace(hour=h, minute=0, second=0, microsecond=0)
        if now >= close_dt + timedelta(minutes=20):
            if source_close is None or close_dt > source_close:
                source_close = close_dt

    if source_close is None:
        prev = now - timedelta(days=1)
        source_close = prev.replace(hour=22, minute=0, second=0, microsecond=0)

    tws_scan = source_close + timedelta(hours=3, minutes=3)

    return {
        "generated_at": now.isoformat(),
        "source_bar_close": source_close.isoformat(),
        "for_tws_scan_at": tws_scan.isoformat(),
    }


def fetch_hourly_bars(api_key: str, symbol: str, days: int = HOURLY_LOOKBACK_DAYS) -> list[dict]:
    """Polygon hourly aggregates for TSD resample."""
    end = datetime.now(ET).date()
    start = end - timedelta(days=days)
    url = (
        f"{POLYGON_BASE}/v2/aggs/ticker/{symbol.upper()}/range/1/hour/"
        f"{start}/{end}"
    )
    data = polygon_get(url, {"adjusted": "true", "sort": "asc", "limit": 50000}, api_key)
    return list(data.get("results") or [])


def classify_include_tags(df) -> tuple[list[str], dict[str, Any]]:
    """Evaluate include-only tags on the last fully closed bar."""
    if len(df) < MIN_BARS_FOR_TSD:
        return [], {}
    enriched = enrich_tsd(df)
    row = enriched.iloc[-1]
    prev = enriched.iloc[-2]
    tags: list[str] = []

    wt1 = float(row["wt1"])
    wt2 = float(row["wt2"])
    ts = float(row["trend_strength"])
    ts_prev = float(prev["trend_strength"])

    if wt1 < wt2 and (wt2 - wt1) < 20 and wt1 > float(prev["wt1"]):
        tags.append("near_cross")
    if bool(row.get("early_bull")):
        tags.append("early_bull")
    if ts > ts_prev and ts > -0.3:
        tags.append("trend_up")
    if wt1 > wt2:
        tags.append("already_warm")

    meta = {
        "wt1": round(wt1, 4),
        "wt2": round(wt2, 4),
        "trend_strength": round(ts, 4),
        "scan_score": round(float(row["scan_score"]), 2),
        "bar_time": str(enriched.index[-1]),
    }
    return tags, meta


def build_polygon_hunt_list(
    api_key: str,
    universe: list[dict[str, Any]],
    *,
    max_scan: int | None = None,
    liquid_top_n: int = LIQUID_CORE_TOP_N,
) -> dict[str, Any]:
    """Scan universe with Polygon hourly→3H TSD; write include-only hunt list."""
    meta = schedule_meta()
    liquid = universe[:liquid_top_n]
    liquid_syms = {r["symbol"] for r in liquid}

    scan_pool = universe[: max_scan] if max_scan else universe
    scan_syms = {r["symbol"] for r in scan_pool}

    # Always evaluate liquid_core + scan pool union
    eval_syms = list(dict.fromkeys([r["symbol"] for r in liquid] + [r["symbol"] for r in scan_pool]))

    hunt: dict[str, dict[str, Any]] = {}
    for sym in liquid_syms:
        hunt[sym] = {"symbol": sym, "tags": ["liquid_core"], "wt1": None, "wt2": None}

    print(f"  Scanning {len(eval_syms)} symbols (liquid_core={len(liquid_syms)})...", flush=True)
    t0 = time.perf_counter()
    for i, sym in enumerate(eval_syms, 1):
        if sym in liquid_syms and sym in hunt and len(hunt[sym].get("tags", [])) == 1:
            # Still fetch TSD for liquid_core metadata when scanning
            pass
        try:
            aggs = fetch_hourly_bars(api_key, sym)
            hourly = bars_from_polygon_aggs(aggs)
            bars_3h = aggregate_hourly_to_3h(hourly)
            tags, tsd_meta = classify_include_tags(bars_3h)
        except Exception as exc:
            if i % 25 == 0:
                print(f"    [{i}/{len(eval_syms)}] {sym} ERR {exc}", flush=True)
            continue

        if not tags and sym not in liquid_syms:
            if i % 25 == 0:
                print(f"    [{i}/{len(eval_syms)}] progress (no tag)", flush=True)
            continue

        existing = hunt.get(sym, {"symbol": sym, "tags": [], "wt1": None, "wt2": None})
        merged_tags = list(dict.fromkeys(existing.get("tags", []) + tags))
        if sym in liquid_syms and "liquid_core" not in merged_tags:
            merged_tags.insert(0, "liquid_core")
        existing["tags"] = merged_tags
        existing["wt1"] = tsd_meta.get("wt1")
        existing["wt2"] = tsd_meta.get("wt2")
        existing["trend_strength"] = tsd_meta.get("trend_strength")
        existing["scan_score"] = tsd_meta.get("scan_score")
        existing["bar_time"] = tsd_meta.get("bar_time")
        hunt[sym] = existing

        if tags or i % 25 == 0:
            tag_str = ",".join(merged_tags) if merged_tags else "-"
            print(f"    [{i:>4}/{len(eval_syms)}] {sym:<6} tags={tag_str}", flush=True)

    tickers = sorted(hunt.values(), key=lambda x: x.get("scan_score") or 0, reverse=True)
    elapsed = time.perf_counter() - t0
    payload = {
        **meta,
        "count": len(tickers),
        "scan_pool_size": len(eval_syms),
        "elapsed_sec": round(elapsed, 1),
        "tickers": tickers,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"  Hunt list: {len(tickers)} tickers -> {OUTPUT_PATH} ({elapsed:.1f}s)", flush=True)
    return payload


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build Polygon TSD hunt list (:20 pass)")
    parser.add_argument("--refresh-universe", action="store_true")
    parser.add_argument("--max-scan", type=int, default=None, help="Cap TSD scan pool (debug)")
    parser.add_argument("--liquid-top", type=int, default=LIQUID_CORE_TOP_N)
    args = parser.parse_args()

    print("=" * 64)
    print("TSD POLYGON HUNT LIST - PASS 1 (:20 pre-filter)")
    print("=" * 64)

    api_key = load_polygon_key()
    universe = build_daily_universe(api_key, refresh=args.refresh_universe)
    if not universe:
        print("FAIL: empty universe")
        return 1

    build_polygon_hunt_list(
        api_key,
        universe,
        max_scan=args.max_scan,
        liquid_top_n=args.liquid_top,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
