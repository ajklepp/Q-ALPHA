"""
TWS / IBKR historical news headlines for Peak Hour (soft context).

Account has BRFG, BRFUPDN, and DJ-* providers (see probe_tws_news.py).
Must request **one provider at a time** — comma-joined codes → Error 321.

Never blocks entry: TWS down / empty → zeros + empty headlines.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

# Prefer Briefing + one DJ feed (avoid duplicate DJ variants).
DEFAULT_PROVIDERS = ("BRFG", "BRFUPDN", "DJ-N")
TWS_HOST = "127.0.0.1"
TWS_PORT = 7497
TWS_CLIENT_ID = 89  # dedicated; avoid clashes with 93/96/etc.
LOOKBACK_HOURS = 72
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
CACHE_TTL_SEC = 900.0

_META_RE = re.compile(r"^\{[^}]+\}")


def _clean_headline(raw: str) -> str:
    s = _META_RE.sub("", str(raw or "")).strip()
    if s.startswith("!"):
        s = s[1:].strip()
    return s


def _cache_get(key: str) -> dict[str, Any] | None:
    hit = _CACHE.get(key)
    if not hit:
        return None
    ts, val = hit
    if time.time() - ts > CACHE_TTL_SEC:
        return None
    return dict(val)


def _cache_set(key: str, val: dict[str, Any]) -> None:
    _CACHE[key] = (time.time(), dict(val))


def fetch_tws_headlines(
    symbol: str,
    *,
    ib: Any | None = None,
    providers: tuple[str, ...] = DEFAULT_PROVIDERS,
    lookback_hours: int = LOOKBACK_HOURS,
    max_per_provider: int = 8,
) -> dict[str, Any]:
    """
    Pull recent IB historical news for symbol.

    Returns headlines, counts, dilution/distress flags. Never raises.
    """
    sym = symbol.upper()
    cache_key = f"tws:{sym}:{lookback_hours}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    empty = {
        "tws_ok": 0,
        "tws_headline_count": 0.0,
        "tws_headlines": [],
        "tws_providers_hit": [],
        "dilution_flag": 0,
        "distress_flag": 0,
    }

    own_ib = False
    conn = ib
    try:
        if conn is None or not getattr(conn, "isConnected", lambda: False)():
            from ib_insync import IB, Stock, util

            try:
                util.startLoop()
            except Exception:
                pass
            conn = IB()
            conn.connect(TWS_HOST, TWS_PORT, clientId=TWS_CLIENT_ID, timeout=8)
            own_ib = True

        from ib_insync import Stock

        contract = Stock(sym, "SMART", "USD")
        conn.qualifyContracts(contract)
        if not contract.conId:
            _cache_set(cache_key, empty)
            return empty

        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=lookback_hours)
        start_s = start.strftime("%Y-%m-%d %H:%M:%S")
        end_s = end.strftime("%Y-%m-%d %H:%M:%S")

        headlines: list[str] = []
        hit_providers: list[str] = []
        dilution = False
        distress = False

        for code in providers:
            try:
                rows = conn.reqHistoricalNews(
                    contract.conId, code, start_s, end_s, max_per_provider,
                )
                time.sleep(0.35)
            except Exception:
                continue
            if not rows:
                continue
            hit_providers.append(code)
            for h in rows:
                text = _clean_headline(getattr(h, "headline", "") or "")
                if not text:
                    continue
                if text not in headlines:
                    headlines.append(text)
                blob = text.lower()
                if any(k in blob for k in ("offering", "dilution", "atm ", "registered direct")):
                    dilution = True
                if any(k in blob for k in ("going concern", "bankrupt", "chapter 11", "delist")):
                    distress = True

        out = {
            "tws_ok": 1 if headlines else 0,
            "tws_headline_count": float(len(headlines)),
            "tws_headlines": headlines[:20],
            "tws_providers_hit": hit_providers,
            "dilution_flag": int(dilution),
            "distress_flag": int(distress),
        }
        _cache_set(cache_key, out)
        return out
    except Exception:
        _cache_set(cache_key, empty)
        return empty
    finally:
        if own_ib and conn is not None:
            try:
                conn.disconnect()
            except Exception:
                pass
