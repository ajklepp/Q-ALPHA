"""
Q-ALPHA TSD pipeline — profiler v2 (3HR swing analogs).

Historical analog definition:
  - 3H TSD BUY cross fired (IBKR-aligned bars preferred)
  - Phase 2.5 LAUNCH lane: buy_signal + launch_score>=50 + scan<=55, not EXTENSION

MINIMUM 30 analog instances to trade — INSUFFICIENT = skip entirely.

Bar sources (priority):
  1. IBKR native 3H bars when connected
  2. Polygon 30-min aggs bucketed to IBKR-style 3H keys (paginated)
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd
import pytz

from tsd_scan_pipeline.build_3h_bars import (
    aggregate_30m_to_ibkr_3h,
    aggregate_hourly_to_3h,
    bars_from_ibkr_history,
    bars_from_polygon_aggs,
)
from tsd_scan_pipeline.tsd_signals import SCAN_SCORE_MIN, enrich_tsd
from tsd_scan_pipeline.universe_tsd import POLYGON_BASE, MCAP_MIN, load_polygon_key, polygon_get

if TYPE_CHECKING:
    from ib_insync import IB

PIPELINE_DIR = Path(__file__).resolve().parent
PROFILES_DIR = PIPELINE_DIR / "profiles"
ET = pytz.timezone("America/New_York")
MIN_TSD_ANALOGS = 30
LOOKBACK_2Y_DAYS = 365 * 2
LOOKBACK_3Y_DAYS = 365 * 3
IBKR_MAX_DAYS = 730
HOLD_DAYS_PRIMARY = 5
FALLBACK_KILL_PCT = 0.07


def _analog_outcome_label(
    mfe_pct: float | None,
    mae_pct: float | None,
    kill_pct: float,
) -> str:
    """WIN if MFE >= 2× kill before MAE breaches kill; else LOSS or FLAT."""
    if mfe_pct is None or mae_pct is None:
        return "UNKNOWN"
    if mfe_pct >= 2.0 * kill_pct and mae_pct < kill_pct:
        return "WIN"
    if mae_pct >= kill_pct:
        return "LOSS"
    return "FLAT"


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def _paginate_polygon_aggs(
    api_key: str,
    url: str,
    params: dict[str, Any] | None = None,
) -> list[dict]:
    """Fetch all pages from a Polygon aggs endpoint (follows next_url)."""
    results: list[dict] = []
    next_url: str | None = url
    page_params = params
    while next_url:
        data = polygon_get(next_url, page_params, api_key)
        results.extend(data.get("results") or [])
        next_url = data.get("next_url") or None
        page_params = None
    return results


def fetch_hourly_bars(api_key: str, symbol: str, days: int = LOOKBACK_2Y_DAYS) -> list[dict]:
    """Legacy Polygon hourly fetch (parity script only). Paginated."""
    end = datetime.now(ET).date()
    start = end - timedelta(days=days)
    url = (
        f"{POLYGON_BASE}/v2/aggs/ticker/{symbol.upper()}/range/1/hour/"
        f"{start}/{end}"
    )
    return _paginate_polygon_aggs(
        api_key, url, {"adjusted": "true", "sort": "asc", "limit": 50000}
    )


def fetch_30min_bars(api_key: str, symbol: str, days: int = LOOKBACK_2Y_DAYS) -> list[dict]:
    """Polygon 30-min aggs for IBKR-style 3H bucketing. Paginated."""
    end = datetime.now(ET).date()
    start = end - timedelta(days=days)
    url = (
        f"{POLYGON_BASE}/v2/aggs/ticker/{symbol.upper()}/range/30/minute/"
        f"{start}/{end}"
    )
    return _paginate_polygon_aggs(
        api_key, url, {"adjusted": "true", "sort": "asc", "limit": 50000}
    )


def fetch_daily_bars_polygon(api_key: str, symbol: str, start: date, end: date) -> list[dict]:
    """Daily OHLCV for MAE/MFE measurement over hold window."""
    url = (
        f"{POLYGON_BASE}/v2/aggs/ticker/{symbol.upper()}/range/1/day/"
        f"{start}/{end}"
    )
    aggs = _paginate_polygon_aggs(
        api_key, url, {"adjusted": "true", "sort": "asc", "limit": 50000}
    )
    bars: list[dict] = []
    for r in aggs:
        ts = pd.Timestamp(r["t"], unit="ms", tz="UTC").tz_convert(ET)
        bars.append(
            {
                "date": ts.date(),
                "open": float(r["o"]),
                "high": float(r["h"]),
                "low": float(r["l"]),
                "close": float(r["c"]),
            }
        )
    return bars


def _bars_3h_polygon_legacy_hourly(api_key: str, symbol: str, days: int = LOOKBACK_2Y_DAYS) -> pd.DataFrame:
    """Old Polygon hourly -> midnight resample (parity comparison only)."""
    aggs = fetch_hourly_bars(api_key, symbol, days=days)
    hourly = bars_from_polygon_aggs(aggs)
    return aggregate_hourly_to_3h(hourly)


def _bars_3h_polygon_ibkr_buckets(api_key: str, symbol: str, days: int = LOOKBACK_2Y_DAYS) -> pd.DataFrame:
    aggs = fetch_30min_bars(api_key, symbol, days=days)
    df30 = bars_from_polygon_aggs(aggs)
    return aggregate_30m_to_ibkr_3h(df30)


def _bars_3h_for_profiler(
    symbol: str,
    *,
    api_key: str,
    ib: IB | None = None,
    days: int = LOOKBACK_2Y_DAYS,
) -> tuple[pd.DataFrame, str]:
    """Return 3H bars and source label."""
    sym = symbol.upper()
    ibkr_days = min(days, IBKR_MAX_DAYS)
    if ib is not None and ib.isConnected():
        try:
            df = bars_from_ibkr_history(ib, sym, days=ibkr_days)
            if len(df) >= 80:
                return df, "ibkr_3h"
        except Exception:
            pass
    df = _bars_3h_polygon_ibkr_buckets(api_key, sym, days=days)
    return df, "polygon_30m_ibkr_buckets"


def _extract_analogs(enriched: pd.DataFrame) -> list[dict[str, Any]]:
    """Launch-lane analogs: buy_signal on bar (no scan_score>=60 floor)."""
    from tsd_scan_pipeline.tsd_launch_score import (
        LAUNCH_SCAN_MAX,
        LAUNCH_SCORE_MIN,
        compute_launch_phase,
        compute_launch_score,
        signal_bar_red,
    )

    analogs: list[dict[str, Any]] = []
    for ts, row in enriched.iterrows():
        if not bool(row.get("buy_signal")):
            continue
        score = float(row.get("scan_score") or 0)
        trend = float(row.get("trend_strength") or -999)
        bar = {
            "scan_score": score,
            "trend_strength": trend,
            "buy_signal": True,
            "early_bull": bool(row.get("early_bull")),
            "open": float(row.get("open") or row["close"]),
            "close": float(row["close"]),
        }
        phase = compute_launch_phase(bar)
        if phase == "EXTENSION":
            continue
        launch_score = compute_launch_score(bar)
        if launch_score < LAUNCH_SCORE_MIN or score > LAUNCH_SCAN_MAX:
            continue
        analogs.append(
            {
                "time": str(ts),
                "entry_date": pd.Timestamp(ts).tz_convert(ET).date(),
                "close": float(row["close"]),
                "scan_score": score,
                "trend_strength": trend,
                "launch_score": launch_score,
                "signal_bar_red": signal_bar_red(bar),
                "wt1": float(row["wt1"]),
                "wt2": float(row["wt2"]),
            }
        )
    return analogs


def _find_analogs_at_lookback(
    symbol: str,
    *,
    api_key: str,
    ib: IB | None,
    days: int,
    mcap_min: float,
    min_analogs: int,
) -> dict[str, Any]:
    """Single lookback window analog search."""
    sym = symbol.upper()
    bars_3h, bar_source = _bars_3h_for_profiler(sym, api_key=api_key, ib=ib, days=days)
    if len(bars_3h) < 80:
        return {
            "symbol": sym,
            "analog_count": 0,
            "pass": False,
            "status": "INSUFFICIENT",
            "reason": "insufficient_bars",
            "bar_source": bar_source,
            "lookback_days": days,
            "analogs": [],
        }
    enriched = enrich_tsd(bars_3h)
    analogs = _extract_analogs(enriched)
    count = len(analogs)
    status = "OK" if count >= min_analogs else "INSUFFICIENT"
    return {
        "symbol": sym,
        "analog_count": count,
        "pass": count >= min_analogs,
        "status": status,
        "min_required": min_analogs,
        "mcap_min": mcap_min,
        "hold_days_primary": HOLD_DAYS_PRIMARY,
        "bar_source": bar_source,
        "lookback_days": days,
        "lookback_extended": False,
        "analogs": analogs,
        "analogs_sample": analogs[-10:],
    }


def find_tsd_analog_days(
    symbol: str,
    *,
    api_key: str | None = None,
    ib: IB | None = None,
    mcap_min: float = MCAP_MIN,
    min_analogs: int = MIN_TSD_ANALOGS,
) -> dict[str, Any]:
    """
    Count historical 3H TSD BUY signals meeting swing criteria.

    Prefers IBKR 3H bars when connected; falls back to Polygon 30m buckets.
    Extends lookback to 3yr if < min_analogs after 2yr (ticker_profiler pattern).
    """
    key = api_key or load_polygon_key()
    sym = symbol.upper()

    try:
        result = _find_analogs_at_lookback(
            sym, api_key=key, ib=ib, days=LOOKBACK_2Y_DAYS, mcap_min=mcap_min, min_analogs=min_analogs
        )
        if result["analog_count"] >= min_analogs:
            return result

        extended = _find_analogs_at_lookback(
            sym, api_key=key, ib=ib, days=LOOKBACK_3Y_DAYS, mcap_min=mcap_min, min_analogs=min_analogs
        )
        extended["lookback_extended"] = True
        extended["lookback_days"] = LOOKBACK_3Y_DAYS
        return extended
    except Exception as exc:
        return {
            "symbol": sym,
            "analog_count": 0,
            "pass": False,
            "status": "INSUFFICIENT",
            "reason": str(exc),
            "bar_source": "error",
            "analogs": [],
        }


def _measure_analog_mae_mfe(
    entry_price: float,
    entry_date: date,
    daily_bars: list[dict],
    hold_days: int = HOLD_DAYS_PRIMARY,
) -> dict[str, float] | None:
    """MAE/MFE over next N trading days from daily bars."""
    future = [b for b in daily_bars if b["date"] > entry_date][:hold_days]
    if not future:
        return None
    lows = [b["low"] for b in future]
    highs = [b["high"] for b in future]
    mae = max(0.0, (entry_price - min(lows)) / entry_price)
    mfe = max(0.0, (max(highs) - entry_price) / entry_price)
    return {"mae_pct": mae, "mfe_pct": mfe}


def build_tsd_profile(
    symbol: str,
    *,
    analog_result: dict[str, Any] | None = None,
    api_key: str | None = None,
    ib: IB | None = None,
) -> dict[str, Any]:
    """
    Build MAE/MFE profile from TSD analogs over 5 trading days.

    Returns kill_pct (MAE p75), target_pct (MFE p50), and percentile tables.
    """
    key = api_key or load_polygon_key()
    sym = symbol.upper()
    analog_doc = analog_result or find_tsd_analog_days(sym, api_key=key, ib=ib)
    analogs = list(analog_doc.get("analogs") or [])

    if not analogs:
        return {
            "symbol": sym,
            "status": "INSUFFICIENT",
            "analog_count": 0,
            "kill_pct": FALLBACK_KILL_PCT,
            "target_pct": None,
            "mae": {},
            "mfe": {},
        }

    min_date = min(a["entry_date"] for a in analogs)
    max_date = max(a["entry_date"] for a in analogs) + timedelta(days=HOLD_DAYS_PRIMARY + 10)
    daily = fetch_daily_bars_polygon(key, sym, min_date, max_date)
    time.sleep(0.12)

    measured: list[dict[str, float]] = []
    for a in analogs:
        m = _measure_analog_mae_mfe(float(a["close"]), a["entry_date"], daily)
        if m:
            measured.append(m)

    if not measured:
        return {
            "symbol": sym,
            "status": "INSUFFICIENT",
            "analog_count": len(analogs),
            "kill_pct": FALLBACK_KILL_PCT,
            "target_pct": None,
            "mae": {},
            "mfe": {},
        }

    mae_vals = [m["mae_pct"] for m in measured]
    mfe_vals = [m["mfe_pct"] for m in measured]
    mae = {
        "p50": round(_percentile(mae_vals, 50) or 0.0, 6),
        "p75": round(_percentile(mae_vals, 75) or 0.0, 6),
        "p90": round(_percentile(mae_vals, 90) or 0.0, 6),
    }
    mfe = {
        "p50": round(_percentile(mfe_vals, 50) or 0.0, 6),
        "p75": round(_percentile(mfe_vals, 75) or 0.0, 6),
        "p90": round(_percentile(mfe_vals, 90) or 0.0, 6),
    }
    kill_pct = mae["p75"] if mae["p75"] > 0 else FALLBACK_KILL_PCT
    target_pct = mfe["p50"]

    wins = losses = flats = 0
    for m in measured:
        label = _analog_outcome_label(m["mfe_pct"], m["mae_pct"], kill_pct)
        if label == "WIN":
            wins += 1
        elif label == "LOSS":
            losses += 1
        else:
            flats += 1
    decisive = wins + losses
    analog_win_rate = round(100.0 * wins / decisive, 1) if decisive else None

    return {
        "symbol": sym,
        "status": "OK",
        "analog_count": len(analogs),
        "measured_count": len(measured),
        "analog_wins": wins,
        "analog_losses": losses,
        "analog_flats": flats,
        "analog_win_rate": analog_win_rate,
        "hold_days": HOLD_DAYS_PRIMARY,
        "lookback_days": analog_doc.get("lookback_days"),
        "lookback_extended": analog_doc.get("lookback_extended", False),
        "bar_source": analog_doc.get("bar_source"),
        "kill_pct": round(kill_pct, 6),
        "target_pct": round(target_pct, 6) if target_pct else None,
        "mae": mae,
        "mfe": mfe,
        "fallback_kill_pct": FALLBACK_KILL_PCT,
        "built_at": datetime.now(ET).isoformat(),
    }


def save_tsd_profile_file(symbol: str, profile: dict[str, Any]) -> Path:
    """Persist profile to candidates/tsd_scan_pipeline/profiles/{SYMBOL}_tsd_profile.json."""
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    path = PROFILES_DIR / f"{symbol.upper()}_tsd_profile.json"
    path.write_text(json.dumps(profile, indent=2, default=str), encoding="utf-8")
    return path


def profiler_gate(
    symbol: str,
    *,
    api_key: str | None = None,
    ib: IB | None = None,
    skip: bool = False,
) -> dict[str, Any]:
    """
    Gate wrapper — returns pass + full profile blob when sufficient analogs.
    Saves profile JSON on pass.
    """
    if skip:
        return {
            "status": "SKIPPED_DRY_RUN",
            "analog_count": None,
            "pass": True,
            "profile": None,
        }

    analog_result = find_tsd_analog_days(symbol, api_key=api_key, ib=ib)
    base = {
        "status": analog_result["status"],
        "analog_count": analog_result["analog_count"],
        "pass": analog_result["pass"],
        "min_required": MIN_TSD_ANALOGS,
        "bar_source": analog_result.get("bar_source"),
        "lookback_days": analog_result.get("lookback_days"),
        "lookback_extended": analog_result.get("lookback_extended", False),
        "analog_result": {
            "symbol": analog_result.get("symbol"),
            "analog_count": analog_result.get("analog_count"),
            "status": analog_result.get("status"),
            "bar_source": analog_result.get("bar_source"),
            "lookback_days": analog_result.get("lookback_days"),
            "lookback_extended": analog_result.get("lookback_extended", False),
            "analogs_sample": analog_result.get("analogs_sample"),
        },
        "profile": None,
        "profile_path": None,
    }

    if not analog_result["pass"]:
        return base

    profile = build_tsd_profile(
        symbol, analog_result=analog_result, api_key=api_key, ib=ib
    )
    profile_path = save_tsd_profile_file(symbol, profile)
    base["profile"] = profile
    base["profile_path"] = str(profile_path)
    base["kill_pct"] = profile.get("kill_pct")
    return base


def profile_watchlist(
    watch_rows: list[dict[str, Any]],
    ib: IB | None,
    polygon_key: str | None,
    *,
    skip: bool = False,
) -> list[dict[str, Any]]:
    """
    Run profiler v2 on watch-10 only (not full hunt list).
    Adds profiler_pass, profiler gate blob, and tsd_profile to each row.
    """
    out: list[dict[str, Any]] = []
    for row in watch_rows:
        sym = row["symbol"]
        enriched = dict(row)
        gate = profiler_gate(sym, api_key=polygon_key, ib=ib, skip=skip)
        enriched["profiler"] = gate
        enriched["profiler_pass"] = bool(gate.get("pass"))
        if gate.get("profile"):
            enriched["tsd_profile"] = gate["profile"]
            enriched["profile_path"] = gate.get("profile_path")
            enriched["kill_pct"] = gate.get("kill_pct") or gate["profile"].get("kill_pct")
        elif not skip:
            enriched["profiler_reject"] = "profiler_insufficient"
        out.append(enriched)
        time.sleep(0.12)
    return out


def find_tsd_analog_days_polygon_legacy(
    symbol: str,
    *,
    api_key: str | None = None,
    days: int = LOOKBACK_2Y_DAYS,
) -> dict[str, Any]:
    """Parity helper — old hourly resample method."""
    key = api_key or load_polygon_key()
    sym = symbol.upper()
    try:
        bars_3h = _bars_3h_polygon_legacy_hourly(key, sym, days=days)
        enriched = enrich_tsd(bars_3h)
        analogs = _extract_analogs(enriched)
    except Exception as exc:
        return {"symbol": sym, "analog_count": 0, "analogs": [], "error": str(exc)}
    return {
        "symbol": sym,
        "analog_count": len(analogs),
        "analogs": analogs,
        "bar_source": "polygon_hourly_resample",
    }
