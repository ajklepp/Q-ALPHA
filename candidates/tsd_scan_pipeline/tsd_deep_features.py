"""
Live deep features for continuation_score_v1 (room / bounce / ticker prior).

Causal: daily bars strictly before today for room; profile analogs for prior
proxy when 1H path history is not rebuilt live.

Never blocks entry — missing data → zeros.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytz

from tsd_scan_pipeline.universe_tsd import POLYGON_BASE, load_polygon_key, polygon_get

ET = pytz.timezone("America/New_York")
PIPELINE_DIR = Path(__file__).resolve().parent
PROFILES_DIR = PIPELINE_DIR / "profiles"

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
CACHE_TTL_SEC = 1800.0


def _clip01(x: float) -> float:
    return float(max(0.0, min(1.0, x)))


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


def _load_tsd_profile(symbol: str) -> dict[str, Any] | None:
    path = PROFILES_DIR / f"{symbol.upper()}_tsd_profile.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def prior_from_profile(symbol: str) -> dict[str, float]:
    """
    Soft ticker prior from TSD profile analogs (best live proxy).

    analog_win_rate is percent → convert to 0–1 for continuation_score_v1.
    """
    prof = _load_tsd_profile(symbol)
    if not prof or str(prof.get("status") or "").upper() not in ("OK", "INSUFFICIENT", ""):
        # still use numbers if present
        pass
    if not prof:
        return {
            "ticker_prior_mfe_p50": 0.0,
            "ticker_prior_hit1r_rate": 0.0,
            "ticker_prior_n": 0.0,
            "ticker_prior_source": 0.0,
        }
    wr = prof.get("analog_win_rate")
    mfe = (prof.get("mfe") or {}).get("p50")
    n = float(prof.get("measured_count") or prof.get("analog_count") or 0)
    hit = (float(wr) / 100.0) if wr is not None else 0.0
    return {
        "ticker_prior_mfe_p50": float(mfe or 0.0),
        "ticker_prior_hit1r_rate": float(hit),
        "ticker_prior_n": n,
        "ticker_prior_source": 1.0,  # 1 = profile
    }


def room_bounce_from_daily(
    symbol: str,
    *,
    signal_close: float,
    api_key: str | None = None,
    as_of: datetime | None = None,
) -> dict[str, float]:
    """20d room / bounce from prior daily bars only (no same-day look-ahead)."""
    if signal_close <= 0:
        return {
            "dist_20d_high_pct": 0.0,
            "dist_20d_low_bounce": 0.0,
            "dist_20d_low_pct": 0.0,
            "vol_ratio_20": 1.0,
        }

    now = as_of or datetime.now(ET)
    if now.tzinfo is None:
        now = ET.localize(now)
    else:
        now = now.astimezone(ET)
    as_of_date = now.date()
    cache_key = f"room:{symbol.upper()}:{as_of_date.isoformat()}:{round(signal_close, 2)}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    key = api_key or load_polygon_key()
    end = as_of_date - timedelta(days=1)  # prior closes only
    start = end - timedelta(days=80)
    url = (
        f"{POLYGON_BASE}/v2/aggs/ticker/{symbol.upper()}/range/1/day/"
        f"{start.isoformat()}/{end.isoformat()}"
    )
    params = {"adjusted": "true", "sort": "asc", "limit": 120}
    try:
        data = polygon_get(url, params, key)
        time.sleep(0.12)
        results = data.get("results") or []
    except Exception:
        out = {
            "dist_20d_high_pct": 0.0,
            "dist_20d_low_bounce": 0.0,
            "dist_20d_low_pct": 0.0,
            "vol_ratio_20": 1.0,
        }
        _cache_set(cache_key, out)
        return out

    if len(results) < 20:
        out = {
            "dist_20d_high_pct": 0.0,
            "dist_20d_low_bounce": 0.0,
            "dist_20d_low_pct": 0.0,
            "vol_ratio_20": 1.0,
        }
        _cache_set(cache_key, out)
        return out

    highs = [float(b["h"]) for b in results]
    lows = [float(b["l"]) for b in results]
    vols = [float(b.get("v") or 0) for b in results]
    high20 = max(highs[-20:])
    low20 = min(lows[-20:])
    dist_low = (signal_close - low20) / signal_close
    room = (high20 - signal_close) / signal_close
    bounce = _clip01(1.0 - dist_low / 0.08) if dist_low >= 0 else 0.0
    avg_vol = sum(vols[-20:]) / 20.0 if vols else 0.0
    last_vol = vols[-1] if vols else 0.0
    vol_ratio = (last_vol / avg_vol) if avg_vol > 0 else 1.0

    out = {
        "dist_20d_high_pct": float(room),
        "dist_20d_low_pct": float(dist_low),
        "dist_20d_low_bounce": float(bounce),
        "vol_ratio_20": float(vol_ratio),
    }
    _cache_set(cache_key, out)
    return out


def attach_deep_features(
    rows: list[dict[str, Any]],
    *,
    api_key: str | None = None,
    as_of: datetime | None = None,
) -> list[dict[str, Any]]:
    """Attach room/bounce/prior onto each row (passers only in practice)."""
    key = api_key or load_polygon_key()
    out: list[dict[str, Any]] = []
    for row in rows:
        sym = str(row.get("symbol") or "").upper()
        if not sym:
            out.append(row)
            continue
        close = float(
            row.get("htf_1h_close")
            or row.get("close")
            or row.get("entry_price")
            or 0
        )
        merged = dict(row)
        try:
            merged.update(room_bounce_from_daily(sym, signal_close=close, api_key=key, as_of=as_of))
        except Exception:
            pass
        try:
            merged.update(prior_from_profile(sym))
        except Exception:
            pass
        # Prefer profile kill context if already on row
        if merged.get("tsd_profile") is None:
            prof = _load_tsd_profile(sym)
            if prof:
                merged["tsd_profile"] = prof
        out.append(merged)
    return out
