"""
Sector relative strength for continuation_score v1.3 (blind-spot #4).

Causal: prior daily closes only (no same-day look-ahead).
SIC → sector ETF map matches EXP-0021 study_blindspot_04.
Never blocks entry — missing data → zeros / omit RS terms.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

import pytz

from tsd_scan_pipeline.universe_tsd import POLYGON_BASE, load_polygon_key, polygon_get

ET = pytz.timezone("America/New_York")

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
CACHE_TTL_SEC = 1800.0


def _cache_get(key: str) -> dict[str, Any] | None:
    hit = _CACHE.get(key)
    if not hit:
        return None
    ts, val = hit
    if time.time() - ts > CACHE_TTL_SEC:
        return None
    return val


def _cache_set(key: str, val: dict[str, Any]) -> None:
    _CACHE[key] = (time.time(), val)


def sic_to_etf(sic: str | None) -> str:
    """Map SIC code → liquid sector ETF (coarse; study-aligned)."""
    if not sic:
        return "SPY"
    s = str(sic).strip()
    if not s.isdigit():
        return "SPY"
    code = int(s[:2]) if len(s) >= 2 else int(s)
    if 10 <= code <= 14 or code == 29:
        return "XLE"
    if 15 <= code <= 17 or 30 <= code <= 39:
        if code in (35, 36):
            return "XLK"
        if code == 28:
            return "XLV"
        return "XLI"
    if 20 <= code <= 21:
        return "XLP"
    if 22 <= code <= 27 or code == 31:
        return "XLY"
    if 40 <= code <= 47:
        return "XLI"
    if 48 <= code <= 49:
        return "XLU" if code == 49 else "XLC"
    if 50 <= code <= 59:
        return "XLY"
    if 60 <= code <= 67:
        return "XLF"
    if 70 <= code <= 89:
        if code == 73:
            return "XLK"
        if 80 <= code <= 89:
            return "XLV"
        if code in (65, 70):
            return "XLRE"
        return "XLC"
    return "SPY"


def _daily_closes(symbol: str, *, api_key: str, as_of_date) -> dict[str, float]:
    """Prior daily closes ending as_of_date - 1."""
    cache_key = f"closes:{symbol.upper()}:{as_of_date.isoformat()}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached.get("closes") or {}

    end = as_of_date - timedelta(days=1)
    start = end - timedelta(days=50)
    url = (
        f"{POLYGON_BASE}/v2/aggs/ticker/{symbol.upper()}/range/1/day/"
        f"{start.isoformat()}/{end.isoformat()}"
    )
    closes: dict[str, float] = {}
    try:
        data = polygon_get(url, {"adjusted": "true", "sort": "asc", "limit": 80}, api_key)
        time.sleep(0.12)
        for b in data.get("results") or []:
            try:
                d = datetime.utcfromtimestamp(int(b["t"]) / 1000).date().isoformat()
                closes[d] = float(b["c"])
            except Exception:
                continue
    except Exception:
        closes = {}
    _cache_set(cache_key, {"closes": closes})
    return closes


def _ticker_sic(symbol: str, *, api_key: str) -> str | None:
    cache_key = f"sic:{symbol.upper()}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached.get("sic")
    sic = None
    try:
        data = polygon_get(
            f"{POLYGON_BASE}/v3/reference/tickers/{symbol.upper()}",
            {},
            api_key,
        )
        time.sleep(0.12)
        res = data.get("results") or {}
        raw = str(res.get("sic_code") or "").strip()
        sic = raw or None
    except Exception:
        sic = None
    _cache_set(cache_key, {"sic": sic})
    return sic


def _ret_n(closes: dict[str, float], n: int) -> float | None:
    """Return over last n sessions using closes already truncated to prior day."""
    keys = sorted(closes.keys())
    if len(keys) < n + 1:
        return None
    a, b = closes[keys[-(n + 1)]], closes[keys[-1]]
    if a <= 0:
        return None
    return b / a - 1.0


def sector_rs_features(
    symbol: str,
    *,
    api_key: str | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """
    Compute rs_spy_5d / rs_sector_5d for soft score (v1.3).

    Returns zeros-friendly dict; rs_ok=0 when data missing.
    """
    empty = {
        "sic_code": None,
        "sector_etf": "SPY",
        "rs_spy_5d": 0.0,
        "rs_sector_5d": 0.0,
        "rs_ok": 0,
    }
    sym = str(symbol or "").upper()
    if not sym:
        return empty

    now = as_of or datetime.now(ET)
    if now.tzinfo is None:
        now = ET.localize(now)
    else:
        now = now.astimezone(ET)
    as_of_date = now.date()
    key = api_key or load_polygon_key()

    try:
        sic = _ticker_sic(sym, api_key=key)
        etf = sic_to_etf(sic)
        stock = _daily_closes(sym, api_key=key, as_of_date=as_of_date)
        spy = _daily_closes("SPY", api_key=key, as_of_date=as_of_date)
        sec = spy if etf == "SPY" else _daily_closes(etf, api_key=key, as_of_date=as_of_date)

        stock_5 = _ret_n(stock, 5)
        spy_5 = _ret_n(spy, 5)
        sec_5 = _ret_n(sec, 5)
        if stock_5 is None or spy_5 is None:
            return {**empty, "sic_code": sic, "sector_etf": etf}

        rs_spy = stock_5 - spy_5
        rs_sec = (stock_5 - sec_5) if sec_5 is not None else rs_spy
        return {
            "sic_code": sic,
            "sector_etf": etf,
            "rs_spy_5d": float(rs_spy),
            "rs_sector_5d": float(rs_sec),
            "rs_ok": 1,
        }
    except Exception:
        return empty
