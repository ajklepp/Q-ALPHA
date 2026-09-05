"""
Live deep features for continuation_score_v1 (room / bounce / ticker prior).

Causal:
  - daily bars strictly before today for room/bounce
  - 1H path prior from prior buy/early_bull signals only (before as_of)
  - profile analogs as fallback when path n < MIN_PATH_PRIOR

Never blocks entry — missing data → zeros.
"""
from __future__ import annotations

import json
import statistics
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

# Path prior: need this many completed prior 1H signals before preferring path over profile
MIN_PATH_PRIOR = 3
# Forward window after each prior signal (1H bars) — ~1–2 RTH sessions
PATH_FORWARD_BARS = 20
KILL_PCT = 0.05
TARGET_PCT = 0.05  # +1R at 5% when kill=5%
PRIOR_LOOKBACK_DAYS = 90


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
    """Soft ticker prior from TSD profile analogs (fallback)."""
    prof = _load_tsd_profile(symbol)
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


def _path_label(
    entry: float,
    future_highs: list[float],
    future_lows: list[float],
    *,
    kill_pct: float = KILL_PCT,
    target_pct: float = TARGET_PCT,
) -> dict[str, float | int]:
    """Walk forward bars: kill stops path; hit_1r if target before kill."""
    if entry <= 0 or not future_highs:
        return {"hit_1r": 0, "mfe": 0.0, "killed": 0}
    kill_px = entry * (1.0 - kill_pct)
    target_px = entry * (1.0 + target_pct)
    mfe = 0.0
    hit = 0
    killed = 0
    for hi, lo in zip(future_highs, future_lows):
        mfe = max(mfe, (hi - entry) / entry)
        if lo <= kill_px:
            killed = 1
            break
        if hi >= target_px:
            hit = 1
    return {"hit_1r": int(hit), "mfe": float(mfe), "killed": int(killed)}


def prior_from_1h_path(
    symbol: str,
    *,
    api_key: str | None = None,
    as_of: datetime | None = None,
) -> dict[str, float]:
    """
    Expanding-window prior from completed 1H buy/early_bull signals before as_of.

    Uses only bars after each signal (no look-ahead). Returns zeros if thin.
    """
    now = as_of or datetime.now(ET)
    if now.tzinfo is None:
        now = ET.localize(now)
    else:
        now = now.astimezone(ET)

    cache_key = f"pathprior:{symbol.upper()}:{now.strftime('%Y%m%d%H')}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    empty: dict[str, float] = {
        "ticker_prior_mfe_p50": 0.0,
        "ticker_prior_hit1r_rate": 0.0,
        "ticker_prior_n": 0.0,
        "ticker_prior_source": 0.0,
    }

    try:
        from tsd_scan_pipeline.tsd_1h_signal import _bars_1h_polygon, last_completed_1h_bar
        from tsd_scan_pipeline.tsd_signals import enrich_tsd

        key = api_key or load_polygon_key()
        raw = _bars_1h_polygon(symbol, api_key=key, days=PRIOR_LOOKBACK_DAYS)
        time.sleep(0.12)
        df = last_completed_1h_bar(raw, now=now)
        if df is None or len(df) < 80:
            _cache_set(cache_key, empty)
            return empty

        enriched = enrich_tsd(df.copy())
        highs = enriched["high"].astype(float).tolist()
        lows = enriched["low"].astype(float).tolist()
        closes = enriched["close"].astype(float).tolist()
        buys = enriched["buy_signal"].astype(bool).tolist()
        early = enriched["early_bull"].astype(bool).tolist()
        idx = list(enriched.index)

        labels: list[dict[str, float | int]] = []
        for i in range(len(enriched) - 1):
            ts = idx[i]
            try:
                ts_et = (
                    ts.tz_convert(ET)
                    if getattr(ts, "tzinfo", None)
                    else ET.localize(ts.to_pydatetime())
                )
            except Exception:
                ts_et = now
            if ts_et >= now:
                break
            if not (buys[i] or early[i]):
                continue
            j_end = min(i + 1 + PATH_FORWARD_BARS, len(enriched))
            if j_end <= i + 1:
                continue

            usable_h: list[float] = []
            usable_l: list[float] = []
            for j in range(i + 1, j_end):
                t2 = idx[j]
                try:
                    t2e = (
                        t2.tz_convert(ET)
                        if getattr(t2, "tzinfo", None)
                        else ET.localize(t2.to_pydatetime())
                    )
                except Exception:
                    break
                if t2e >= now:
                    break
                usable_h.append(highs[j])
                usable_l.append(lows[j])
            if len(usable_h) < 3:
                continue
            labels.append(_path_label(closes[i], usable_h, usable_l))

        if len(labels) < MIN_PATH_PRIOR:
            out = dict(empty)
            out["ticker_prior_n"] = float(len(labels))
            if labels:
                out["ticker_prior_path_n"] = float(len(labels))
                out["ticker_prior_path_hit1r"] = float(
                    sum(int(x["hit_1r"]) for x in labels) / len(labels)
                )
                out["ticker_prior_path_mfe_p50"] = float(
                    statistics.median(float(x["mfe"]) for x in labels)
                )
            _cache_set(cache_key, out)
            return out

        hit_rate = sum(int(x["hit_1r"]) for x in labels) / len(labels)
        mfe_p50 = statistics.median(float(x["mfe"]) for x in labels)
        out = {
            "ticker_prior_mfe_p50": float(mfe_p50),
            "ticker_prior_hit1r_rate": float(hit_rate),
            "ticker_prior_n": float(len(labels)),
            "ticker_prior_source": 2.0,  # 2 = 1H path
            "ticker_prior_path_n": float(len(labels)),
            "ticker_prior_path_hit1r": float(hit_rate),
            "ticker_prior_path_mfe_p50": float(mfe_p50),
        }
        _cache_set(cache_key, out)
        return out
    except Exception:
        _cache_set(cache_key, empty)
        return empty


def resolve_ticker_prior(
    symbol: str,
    *,
    api_key: str | None = None,
    as_of: datetime | None = None,
) -> dict[str, float]:
    """Prefer 1H path prior when n>=MIN_PATH_PRIOR; else profile fallback."""
    path = prior_from_1h_path(symbol, api_key=api_key, as_of=as_of)
    if float(path.get("ticker_prior_source") or 0) >= 2.0:
        return path
    prof = prior_from_profile(symbol)
    for k in ("ticker_prior_path_n", "ticker_prior_path_hit1r", "ticker_prior_path_mfe_p50"):
        if k in path:
            prof[k] = path[k]
    if float(prof.get("ticker_prior_n") or 0) > 0:
        return prof
    if float(path.get("ticker_prior_path_n") or path.get("ticker_prior_n") or 0) > 0:
        return {
            "ticker_prior_mfe_p50": float(path.get("ticker_prior_path_mfe_p50") or 0),
            "ticker_prior_hit1r_rate": float(path.get("ticker_prior_path_hit1r") or 0),
            "ticker_prior_n": float(
                path.get("ticker_prior_path_n") or path.get("ticker_prior_n") or 0
            ),
            "ticker_prior_source": 0.5,
            **{k: path[k] for k in path if str(k).startswith("ticker_prior_path")},
        }
    return path


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
            "dist_52w_high_pct": 0.0,
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
    end = as_of_date - timedelta(days=1)
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
    high52 = max(highs[-min(252, len(highs)):])
    dist_low = (signal_close - low20) / signal_close
    room = (high20 - signal_close) / signal_close
    room52 = (high52 - signal_close) / signal_close
    bounce = _clip01(1.0 - dist_low / 0.08) if dist_low >= 0 else 0.0
    avg_vol = sum(vols[-20:]) / 20.0 if vols else 0.0
    last_vol = vols[-1] if vols else 0.0
    vol_ratio = (last_vol / avg_vol) if avg_vol > 0 else 1.0

    out = {
        "dist_20d_high_pct": float(room),
        "dist_52w_high_pct": float(room52),
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
            merged.update(resolve_ticker_prior(sym, api_key=key, as_of=as_of))
        except Exception:
            try:
                merged.update(prior_from_profile(sym))
            except Exception:
                pass
        if merged.get("tsd_profile") is None:
            prof = _load_tsd_profile(sym)
            if prof:
                merged["tsd_profile"] = prof
        out.append(merged)
    return out
