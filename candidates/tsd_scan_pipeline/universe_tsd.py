"""
Q-ALPHA TSD pipeline — daily Polygon universe (mcap + liquidity).

Builds the TSD swing scan universe:
  - market_cap >= $300M (Polygon ticker DETAIL endpoint — list API omits/ignores mcap)
  - 20-day avg dollar volume >= $5M
  - passes_instrument_safety() name/symbol gates

Polygon list `/v3/reference/tickers` no longer returns `market_cap` and its
`market_cap.gte` filter is unreliable (microcaps like FGI still appear). Every
liquid candidate must be verified via `/v3/reference/tickers/{symbol}`.

Polygon Developer tier (15-min delay) — research / hunt-list ONLY, not live signals.

Usage:
  py -3 candidates/tsd_scan_pipeline/universe_tsd.py
  py -3 candidates/tsd_scan_pipeline/universe_tsd.py --refresh
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytz

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
ROOT = CANDIDATES_DIR.parent
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

from universe_filter import EXCLUDE_SYMBOLS, is_leveraged_or_fund, passes_instrument_safety

POLYGON_BASE = "https://api.polygon.io"
ET = pytz.timezone("America/New_York")

MCAP_MIN = 300_000_000
MIN_DOLLAR_VOL_20D = 5_000_000
MIN_PRICE = 5.0
DOLLAR_VOL_LOOKBACK_DAYS = 20
LIQUID_CORE_TOP_N = 150
POLYGON_SLEEP_SEC = 0.12
RESULTS_DIR = PIPELINE_DIR / "results"
UNIVERSE_CACHE_DIR = RESULTS_DIR / "universe_cache"


def load_polygon_key() -> str:
    """Load POLYGON_API_KEY from env or repo .env."""
    key = os.environ.get("POLYGON_API_KEY")
    if key:
        return key.strip()
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("POLYGON_API_KEY") and "=" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("POLYGON_API_KEY not found in environment or .env")


def polygon_get(url: str, params: dict | None, api_key: str, timeout: int = 60) -> dict:
    """GET with retry — 0.12s sleep between ticker-scale calls."""
    from urllib.parse import urlencode

    full_url = url
    if params:
        sep = "&" if "?" in url else "?"
        full_url = f"{url}{sep}{urlencode(params)}"
    if "apiKey=" not in full_url:
        sep = "&" if "?" in full_url else "?"
        full_url = f"{full_url}{sep}apiKey={api_key}"

    for attempt in range(3):
        try:
            with urllib.request.urlopen(full_url, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            time.sleep(POLYGON_SLEEP_SEC)
            return data
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 2:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise
    return {}


def fetch_mcap_universe(api_key: str) -> dict[str, dict[str, Any]]:
    """
    Paginate Polygon reference tickers (CS, active US).

    NOTE: list responses omit market_cap and market_cap.gte is unreliable.
    Callers must verify mcap via fetch_ticker_market_cap / filter_by_verified_mcap.
    """
    url = f"{POLYGON_BASE}/v3/reference/tickers"
    params: dict[str, Any] = {
        "market": "stocks",
        "locale": "us",
        "active": "true",
        "type": "CS",
        "limit": 1000,
    }
    out: dict[str, dict[str, Any]] = {}
    pages = 0
    while url:
        data = polygon_get(url, params, api_key)
        for row in data.get("results") or []:
            sym = str(row.get("ticker") or "").upper()
            name = str(row.get("name") or "")
            if not sym or sym in EXCLUDE_SYMBOLS:
                continue
            if is_leveraged_or_fund(name):
                continue
            if not passes_instrument_safety(sym, require_cs_cache=False):
                continue
            out[sym] = {
                "symbol": sym,
                "name": name,
                "market_cap": None,  # filled by detail endpoint
                "primary_exchange": str(row.get("primary_exchange") or ""),
            }
        pages += 1
        if pages % 5 == 0:
            print(f"  reference pages={pages} symbols={len(out)}", flush=True)
        nxt = data.get("next_url")
        url = nxt or ""
        params = {}
    return out


def fetch_ticker_market_cap(symbol: str, api_key: str) -> float | None:
    """
    Fetch market_cap from Polygon ticker DETAIL endpoint.

    List/search endpoints omit this field; detail is the only reliable source.
    """
    sym = symbol.upper()
    url = f"{POLYGON_BASE}/v3/reference/tickers/{sym}"
    try:
        data = polygon_get(url, None, api_key)
    except Exception:
        return None
    results = data.get("results") or {}
    mcap = results.get("market_cap")
    if mcap is None:
        return None
    try:
        return float(mcap)
    except (TypeError, ValueError):
        return None


def filter_by_verified_mcap(
    rows: list[dict[str, Any]],
    api_key: str,
    *,
    mcap_min: float = MCAP_MIN,
) -> list[dict[str, Any]]:
    """
    Attach detail-endpoint market_cap and keep only names >= mcap_min.

    Fail closed: missing/unparseable mcap is rejected.
    """
    kept: list[dict[str, Any]] = []
    rejected_low = 0
    rejected_unknown = 0
    total = len(rows)
    for i, row in enumerate(rows, start=1):
        sym = str(row.get("symbol") or "").upper()
        mcap = fetch_ticker_market_cap(sym, api_key)
        if mcap is None:
            rejected_unknown += 1
            continue
        if mcap < mcap_min:
            rejected_low += 1
            continue
        kept.append({**row, "market_cap": mcap, "symbol": sym})
        if i % 25 == 0 or i == total:
            print(
                f"  mcap verify {i}/{total} kept={len(kept)} "
                f"low={rejected_low} unknown={rejected_unknown}",
                flush=True,
            )
    return kept


def build_dollar_vol_map(api_key: str, days: int = DOLLAR_VOL_LOOKBACK_DAYS) -> dict[str, float]:
    """20-day average dollar volume from grouped daily bars (whole market per day)."""
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    collected = 0
    probe = datetime.now(ET).date() - timedelta(days=1)
    tries = 0
    print(f"  Building {days}-day dollar-volume baseline...", flush=True)
    while collected < days and tries < days * 4:
        d = probe.strftime("%Y-%m-%d")
        url = f"{POLYGON_BASE}/v2/aggs/grouped/locale/us/market/stocks/{d}"
        try:
            data = polygon_get(url, {"adjusted": "true"}, api_key)
        except Exception as exc:
            print(f"    WARN grouped {d}: {exc}", flush=True)
            data = {}
        for row in data.get("results") or []:
            sym = row.get("T")
            close = row.get("c") or 0
            vol = row.get("v") or 0
            if sym and close and vol:
                dv = float(close) * float(vol)
                totals[sym] = totals.get(sym, 0.0) + dv
                counts[sym] = counts.get(sym, 0) + 1
        if data.get("results"):
            collected += 1
        probe -= timedelta(days=1)
        tries += 1
    return {s: totals[s] / counts[s] for s in totals if counts.get(s)}


def build_daily_universe(api_key: str, *, refresh: bool = False) -> list[dict[str, Any]]:
    """
    Full TSD universe with verified mcap + liquidity fields.
    Cached per ET calendar day under results/universe_cache/.
    """
    UNIVERSE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(ET).strftime("%Y-%m-%d")
    cache_path = UNIVERSE_CACHE_DIR / f"tsd_universe_{today}.json"
    if cache_path.exists() and not refresh:
        doc = json.loads(cache_path.read_text(encoding="utf-8"))
        rows = doc.get("tickers") or []
        # Stale caches from before mcap verification are unsafe — rebuild.
        if rows and all(r.get("market_cap") is None for r in rows):
            print(
                f"  Universe cache INVALID (all market_cap=null) — rebuilding {today}",
                flush=True,
            )
        else:
            print(f"  Universe cache hit: {len(rows)} tickers ({today})", flush=True)
            return rows

    print("  Fetching active CS reference universe...", flush=True)
    ref_map = fetch_mcap_universe(api_key)
    print(f"  Reference universe: {len(ref_map)} symbols", flush=True)

    dv_map = build_dollar_vol_map(api_key)
    liquid: list[dict[str, Any]] = []
    for sym, meta in ref_map.items():
        dv = dv_map.get(sym)
        if dv is None or dv < MIN_DOLLAR_VOL_20D:
            continue
        liquid.append(
            {
                **meta,
                "dollar_vol_20d_avg": round(dv, 2),
            }
        )
    print(
        f"  Dollar-vol >= ${MIN_DOLLAR_VOL_20D/1e6:.0f}M: {len(liquid)} names — verifying mcap...",
        flush=True,
    )
    rows = filter_by_verified_mcap(liquid, api_key, mcap_min=MCAP_MIN)
    rows.sort(key=lambda r: r.get("dollar_vol_20d_avg") or 0, reverse=True)
    payload = {
        "built_at": datetime.now(ET).isoformat(),
        "date": today,
        "mcap_min": MCAP_MIN,
        "min_dollar_vol_20d": MIN_DOLLAR_VOL_20D,
        "mcap_source": "polygon_ticker_detail",
        "count": len(rows),
        "tickers": rows,
    }
    cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"  Universe built: {len(rows)} liquid names (cached {cache_path.name})", flush=True)
    return rows


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build TSD Polygon universe")
    parser.add_argument("--refresh", action="store_true", help="Ignore today's cache")
    args = parser.parse_args()
    api_key = load_polygon_key()
    rows = build_daily_universe(api_key, refresh=args.refresh)
    print(f"Top 10 by dollar vol:")
    for r in rows[:10]:
        print(
            f"  {r['symbol']:<6} mcap=${(r.get('market_cap') or 0)/1e6:.0f}M "
            f"dv20=${r.get('dollar_vol_20d_avg', 0)/1e6:.1f}M"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
