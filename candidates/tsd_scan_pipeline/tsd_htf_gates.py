"""
Q-ALPHA UTS v2 Phase 2.5 — daily HTF entry gates (signal-day close, no look-ahead).

Gates (all required):
  - 20d range >= 25%
  - close > SMA50
  - SMA20 rising (SMA20 now > SMA20 10 sessions ago)
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

import pytz

from tsd_scan_pipeline.universe_tsd import POLYGON_BASE, load_polygon_key, polygon_get

ET = pytz.timezone("America/New_York")

HTF_RANGE_20D_MIN = 0.25
HTF_SMA_RISE_LOOKBACK = 10
HTF_BARS_NEEDED = 60
HTF_MIN_PRICE = 5.0


def _sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    window = values[-period:]
    return sum(window) / period


def compute_htf_metrics(closes: list[float], highs: list[float], lows: list[float]) -> dict[str, Any]:
    """
    Compute HTF gate metrics from completed daily bars (oldest → newest).

    Uses the last bar as signal-day close (no intraday look-ahead).
    """
    if len(closes) < HTF_BARS_NEEDED:
        return {"insufficient_bars": True}

    c20 = closes[-20:]
    h20 = highs[-20:]
    l20 = lows[-20:]
    low_20 = min(l20)
    high_20 = max(h20)
    range_pct = (high_20 - low_20) / low_20 if low_20 > 0 else 0.0

    signal_close = closes[-1]
    sma50 = _sma(closes, 50)
    sma20_now = _sma(closes, 20)
    sma20_prior = _sma(closes[:-HTF_SMA_RISE_LOOKBACK], 20) if len(closes) > 20 + HTF_SMA_RISE_LOOKBACK else None

    return {
        "insufficient_bars": False,
        "range_20d_pct": round(range_pct, 4),
        "signal_close": signal_close,
        "sma50": sma50,
        "sma20": sma20_now,
        "sma20_prior": sma20_prior,
        "close_above_sma50": sma50 is not None and signal_close > sma50,
        "sma20_rising": (
            sma20_now is not None
            and sma20_prior is not None
            and sma20_now > sma20_prior
        ),
        "range_ok": range_pct >= HTF_RANGE_20D_MIN,
        "price_ok": signal_close >= HTF_MIN_PRICE,
    }


def evaluate_htf_daily_gates(
    row: dict[str, Any],
    *,
    polygon_key: str | None = None,
) -> tuple[bool, dict[str, bool], list[str], float]:
    """
    Evaluate daily HTF gates for a candidate.

    Returns (passed, gates, reasons, htf_score 0-100).
    Pre-enriched row fields bypass Polygon fetch when all present.
    """
    sym = str(row.get("symbol", "")).upper()
    reasons: list[str] = []

    if row.get("htf_range_20d_pct") is not None:
        metrics = {
            "insufficient_bars": False,
            "range_20d_pct": float(row["htf_range_20d_pct"]),
            "close_above_sma50": bool(row.get("htf_close_above_sma50")),
            "sma20_rising": bool(row.get("htf_sma20_rising")),
            "range_ok": float(row["htf_range_20d_pct"]) >= HTF_RANGE_20D_MIN,
            "price_ok": float(row.get("close") or row.get("htf_1h_close") or 99) >= HTF_MIN_PRICE,
        }
    else:
        metrics = _fetch_htf_metrics(sym, polygon_key=polygon_key)

    if metrics.get("insufficient_bars"):
        gates = {
            "htf_bars": False,
            "range_20d": False,
            "close_above_sma50": False,
            "sma20_rising": False,
            "price_floor": False,
        }
        return False, gates, ["htf_insufficient_bars"], 0.0

    gates = {
        "htf_bars": True,
        "range_20d": bool(metrics.get("range_ok")),
        "close_above_sma50": bool(metrics.get("close_above_sma50")),
        "sma20_rising": bool(metrics.get("sma20_rising")),
        "price_floor": bool(metrics.get("price_ok", True)),
    }
    if not gates["range_20d"]:
        reasons.append(f"range_20d<{HTF_RANGE_20D_MIN:.0%}")
    if not gates["close_above_sma50"]:
        reasons.append("close<=sma50")
    if not gates["sma20_rising"]:
        reasons.append("sma20_flat_or_falling")
    if not gates["price_floor"]:
        reasons.append(f"price<{HTF_MIN_PRICE:.0f}")

  # HTF score: ~33 pts per gate
    htf_score = round(
        sum(33.3 for k in ("range_20d", "close_above_sma50", "sma20_rising") if gates[k]),
        1,
    )
    passed = all(gates.values())
    return passed, gates, reasons, min(100.0, htf_score)


def compute_combined_rank_score(row: dict[str, Any]) -> float:
    """Combined HTF + launch score for slot ranking."""
    launch = float(row.get("launch_score") or 0)
    htf = float(row.get("htf_score") or 0)
    return round(launch + htf, 1)


def _fetch_htf_metrics(symbol: str, *, polygon_key: str | None = None) -> dict[str, Any]:
    key = polygon_key or load_polygon_key()
    sym = symbol.upper()
    end = datetime.now(ET).date()
    start = end - timedelta(days=120)
    url = f"{POLYGON_BASE}/v2/aggs/ticker/{sym}/range/1/day/{start}/{end}"
    params = {"adjusted": "true", "sort": "asc", "limit": 50000}
    try:
        data = polygon_get(url, params, key)
        results = data.get("results") or []
        time.sleep(0.12)
    except Exception:
        return {"insufficient_bars": True}

    if len(results) < HTF_BARS_NEEDED:
        return {"insufficient_bars": True}

    closes = [float(b["c"]) for b in results]
    highs = [float(b["h"]) for b in results]
    lows = [float(b["l"]) for b in results]
    return compute_htf_metrics(closes, highs, lows)
