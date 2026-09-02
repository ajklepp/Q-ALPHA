"""
Q-ALPHA UTS v2 Phase 2.5 — 1H TSD buy_signal gate (strict HTF entry).

Uses Polygon 1H aggs + same TSD math as 3H pipeline on the last completed bar.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import pytz

from tsd_scan_pipeline.tsd_signals import enrich_tsd, last_bar_summary
from tsd_scan_pipeline.universe_tsd import POLYGON_BASE, load_polygon_key, polygon_get

ET = pytz.timezone("America/New_York")
HTF_1H_BARS_MIN = 80


def _bars_1h_polygon(symbol: str, *, api_key: str, days: int = 90) -> pd.DataFrame:
    sym = symbol.upper()
    end = datetime.now(ET).date()
    start = end - timedelta(days=days)
    url = f"{POLYGON_BASE}/v2/aggs/ticker/{sym}/range/1/hour/{start}/{end}"
    params = {"adjusted": "true", "sort": "asc", "limit": 50000}
    data = polygon_get(url, params, api_key)
    results = data.get("results") or []
    if not results:
        return pd.DataFrame()

    rows = []
    for b in results:
        ts = pd.Timestamp(b["t"], unit="ms", tz="UTC").tz_convert(ET)
        rows.append({
            "time": ts,
            "open": float(b["o"]),
            "high": float(b["h"]),
            "low": float(b["l"]),
            "close": float(b["c"]),
            "volume": float(b.get("v") or 0),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.set_index("time").sort_index()


def evaluate_1h_buy_signal(
    row: dict[str, Any],
    *,
    polygon_key: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    """
    True when last completed 1H bar has TSD buy_signal.

    Pre-enriched row may set htf_1h_buy_signal=True to skip fetch.
    """
    if row.get("htf_1h_buy_signal") is not None:
        ok = bool(row.get("htf_1h_buy_signal"))
        return ok, {"htf_1h_buy_signal": ok, "source": "row"}

    sym = str(row.get("symbol", "")).upper()
    if not sym:
        return False, {"htf_1h_buy_signal": False, "source": "no_symbol"}

    key = polygon_key or load_polygon_key()
    try:
        df = _bars_1h_polygon(sym, api_key=key)
        time.sleep(0.12)
    except Exception as exc:
        return False, {"htf_1h_buy_signal": False, "source": f"fetch_err:{exc}"}

    if len(df) < HTF_1H_BARS_MIN:
        return False, {"htf_1h_buy_signal": False, "source": "insufficient_1h_bars"}

    enriched = enrich_tsd(df)
    summary = last_bar_summary(enriched)
    ok = bool(summary.get("buy_signal"))
    return ok, {
        "htf_1h_buy_signal": ok,
        "htf_1h_close": summary.get("close"),
        "htf_1h_scan_score": summary.get("scan_score"),
        "source": "polygon_1h",
    }
