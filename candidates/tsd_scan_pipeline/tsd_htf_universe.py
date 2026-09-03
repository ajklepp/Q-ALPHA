"""
Q-ALPHA UTS v2.6 — daily HTF pre-filter universe.

From the liquid TSD universe (mcap>=$300M, $vol20d>=$5M, price>=$5), keep names
that pass the same daily HTF math as tsd_htf_gates:
  20d range >= 25%, close > SMA50, SMA20 rising, price >= $5.

Hourly 1H launch scans this set only (~hundreds, not 2899).
Recompute once per session (04:30 ET) and optionally at noon.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytz

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

from tsd_scan_pipeline.tsd_htf_gates import (
    HTF_BARS_NEEDED,
    HTF_RANGE_20D_MIN,
    compute_htf_metrics,
    compute_htf_rank_score,
)
from tsd_scan_pipeline.universe_tsd import (
    MIN_PRICE,
    POLYGON_BASE,
    RESULTS_DIR,
    build_daily_universe,
    load_polygon_key,
    polygon_get,
)

ET = pytz.timezone("America/New_York")
HTF_CACHE_DIR = RESULTS_DIR / "htf_universe"
GROUPED_LOOKBACK_DAYS = 90
MIN_PRICE_HTF = MIN_PRICE  # $5


def _cache_path(day: str | None = None) -> Path:
    HTF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = day or datetime.now(ET).strftime("%Y%m%d")
    return HTF_CACHE_DIR / f"htf_pass_{stamp}.json"


def load_htf_universe(*, day: str | None = None) -> dict[str, Any] | None:
    path = _cache_path(day)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def htf_pass_symbols(*, day: str | None = None) -> list[str]:
    doc = load_htf_universe(day=day)
    if not doc:
        return []
    return [str(s).upper() for s in doc.get("symbols") or []]


def _collect_grouped_ohlc(api_key: str, days: int = GROUPED_LOOKBACK_DAYS) -> dict[str, dict[str, list[float]]]:
    """Oldest→newest daily OHLC per symbol from Polygon grouped dailies."""
    series: dict[str, dict[str, list[float]]] = {}
    collected = 0
    probe = datetime.now(ET).date() - timedelta(days=1)
    tries = 0
    while collected < days and tries < days * 4:
        d = probe.strftime("%Y-%m-%d")
        url = f"{POLYGON_BASE}/v2/aggs/grouped/locale/us/market/stocks/{d}"
        try:
            data = polygon_get(url, {"adjusted": "true"}, api_key)
        except Exception as exc:
            print(f"    WARN grouped {d}: {exc}", flush=True)
            data = {}
        results = data.get("results") or []
        if results:
            collected += 1
            for row in results:
                sym = str(row.get("T") or "").upper()
                if not sym:
                    continue
                c = float(row.get("c") or 0)
                h = float(row.get("h") or 0)
                l = float(row.get("l") or 0)
                if c <= 0:
                    continue
                bucket = series.setdefault(sym, {"c": [], "h": [], "l": []})
                bucket["c"].append(c)
                bucket["h"].append(h if h > 0 else c)
                bucket["l"].append(l if l > 0 else c)
        probe -= timedelta(days=1)
        tries += 1
    # grouped loop walks newest→oldest; reverse to oldest→newest
    for bucket in series.values():
        bucket["c"].reverse()
        bucket["h"].reverse()
        bucket["l"].reverse()
    return series


def build_htf_universe(*, refresh: bool = False, polygon_key: str | None = None) -> dict[str, Any]:
    """Build today's HTF-pass set from liquid universe + grouped dailies."""
    today = datetime.now(ET).strftime("%Y%m%d")
    path = _cache_path(today)
    if path.exists() and not refresh:
        doc = json.loads(path.read_text(encoding="utf-8"))
        print(f"  HTF universe cache hit: {len(doc.get('symbols') or [])} names ({today})", flush=True)
        return doc

    key = polygon_key or load_polygon_key()
    liquid = build_daily_universe(key, refresh=False)
    liquid_syms = {str(r["symbol"]).upper() for r in liquid}
    print(f"  HTF pre-filter on {len(liquid_syms)} liquid names...", flush=True)
    ohlc = _collect_grouped_ohlc(key)

    passed: list[dict[str, Any]] = []
    for sym in sorted(liquid_syms):
        bucket = ohlc.get(sym)
        if not bucket or len(bucket["c"]) < HTF_BARS_NEEDED:
            continue
        metrics = compute_htf_metrics(bucket["c"], bucket["h"], bucket["l"])
        if metrics.get("insufficient_bars"):
            continue
        px = float(metrics.get("signal_close") or 0)
        if not metrics.get("price_ok") or px < MIN_PRICE_HTF:
            continue
        if not (
            metrics.get("range_ok")
            and metrics.get("close_above_sma50")
            and metrics.get("sma20_rising")
        ):
            continue
        passed.append({
            "symbol": sym,
            "htf_range_20d_pct": metrics.get("range_20d_pct"),
            "htf_close_above_sma50": True,
            "htf_sma20_rising": True,
            "htf_dist_sma50_pct": metrics.get("dist_sma50_pct"),
            "htf_sma20_slope_pct": metrics.get("sma20_slope_pct"),
            "htf_score": compute_htf_rank_score(metrics),
            "close": px,
        })

    doc = {
        "built_at": datetime.now(ET).isoformat(),
        "date": today,
        "liquid_count": len(liquid_syms),
        "htf_pass_count": len(passed),
        "range_min": HTF_RANGE_20D_MIN,
        "min_price": MIN_PRICE_HTF,
        "symbols": [p["symbol"] for p in passed],
        "rows": passed,
    }
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(f"  HTF-pass: {len(passed)} / {len(liquid_syms)} (cached {path.name})", flush=True)
    return doc


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build daily HTF-pass universe")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    doc = build_htf_universe(refresh=args.refresh)
    print(f"HTF-pass names: {doc['htf_pass_count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
