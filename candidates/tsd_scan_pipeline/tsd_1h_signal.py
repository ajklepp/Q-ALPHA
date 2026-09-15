"""
Q-ALPHA UTS v2.6 — 1H LAUNCH trigger (the buy, not a side gate).

Bar source: Polygon 1H aggregates (timestamp = bar START / left-labeled).
Close hour ET = start.hour + 1, matching Chat A 1h label=right closed=right.

Last COMPLETED 1H bar must have:
  (buy_signal OR early_bull) + is_continuation_list_candidate
  and close hour in ALLOWED_HOURS {5–15}.
Peak hours {7,11,12,13} are score bonus only (EXP-0021).
Premarket 05/06/08/09 admitted for hitch (HOURS_04_15_WINNERS study).
Color does NOT veto — bar_state is rank/telemetry only.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytz

from tsd_scan_pipeline.tsd_kill import (
    structure_area_low,
    structure_risk_pct,
    structure_too_wide,
)
from tsd_scan_pipeline.tsd_launch_score import (
    EXTENSION_SCAN_AUTO,
    classify_bar_state,
    enrich_launch_fields,
    is_continuation_list_candidate,
    signal_bar_red,
)
from tsd_scan_pipeline.tsd_signals import enrich_tsd, last_bar_summary
from tsd_scan_pipeline.universe_tsd import POLYGON_BASE, load_polygon_key, polygon_get

ET = pytz.timezone("America/New_York")
HTF_1H_BARS_MIN = 80
# 90d covers ticker path-prior in one cached fetch; last-bar WT uses ~80 bars.
H1_LOOKBACK_DAYS = 90
MAX_AGG_PAGES = 10  # Safety cap for unexpected Polygon cursor depth.
MAX_COMPLETED_BAR_AGE = timedelta(minutes=90)
# Through 15:00 bar close → 15:15 scan; premarket 05/06/08/09 from EXP-0021 hitch study
ALLOWED_HOURS = {5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15}
BAR_SOURCE = "polygon_1h_aggs_start_labeled"
# Don't let one hung ticker stall a worker for polygon_get's 60s default.
BARS_GET_TIMEOUT_SEC = 20
# When disk/mem cache is stale by one hour, pull a short window and merge.
H1_INCREMENTAL_DAYS = 3
H1_CACHE_DIR = Path(__file__).resolve().parent / "results" / "h1_bar_cache"

_H1_MEM: dict[str, pd.DataFrame] = {}
_H1_MEM_LOCK = threading.Lock()
_H1_DISK_LOCK = threading.Lock()


def bar_close_hour_et(ts: datetime | pd.Timestamp) -> int:
    """Polygon 1H timestamp is bar start; close hour is start+1h (right label)."""
    if ts.tzinfo is None:
        ts = ET.localize(ts) if isinstance(ts, datetime) else ts.tz_localize(ET)
    else:
        ts = ts.tz_convert(ET) if hasattr(ts, "tz_convert") else ts.astimezone(ET)
    return (int(ts.hour) + 1) % 24


def is_allowed_hour(hour: int) -> bool:
    """True when 1H bar-close hour ET is in the launch allowlist."""
    return int(hour) in ALLOWED_HOURS


def is_launch_hour_window(now: datetime | None = None) -> bool:
    """True when current ET hour is an allowed launch-entry hour (incl. 07:00)."""
    dt = now or datetime.now(ET)
    if dt.tzinfo is None:
        dt = ET.localize(dt)
    else:
        dt = dt.astimezone(ET)
    return is_allowed_hour(dt.hour)


def _as_et(now: datetime | None) -> datetime:
    dt = now or datetime.now(ET)
    if dt.tzinfo is None:
        return ET.localize(dt)
    return dt.astimezone(ET)


def _cache_path(symbol: str) -> Path:
    return H1_CACHE_DIR / f"{symbol.upper()}.pkl"


def _mem_get(symbol: str) -> pd.DataFrame | None:
    with _H1_MEM_LOCK:
        df = _H1_MEM.get(symbol.upper())
        if df is None or df.empty:
            return None
        return df.copy()


def _mem_set(symbol: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    with _H1_MEM_LOCK:
        _H1_MEM[symbol.upper()] = df.copy()


def _disk_get(symbol: str) -> pd.DataFrame | None:
    path = _cache_path(symbol)
    if not path.exists():
        return None
    try:
        with _H1_DISK_LOCK:
            df = pd.read_pickle(path)
        if df is None or getattr(df, "empty", True):
            return None
        return df
    except Exception:
        return None


def _disk_set(symbol: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    try:
        H1_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with _H1_DISK_LOCK:
            df.to_pickle(_cache_path(symbol))
    except Exception:
        pass


def clear_1h_bar_cache() -> None:
    """Drop in-memory 1H bar cache (tests / new session). Disk files kept."""
    with _H1_MEM_LOCK:
        _H1_MEM.clear()


def _cache_covers_last_completed(df: pd.DataFrame, now: datetime | None) -> bool:
    """True when cached bars include the 1H bar that has already closed."""
    if df is None or df.empty:
        return False
    completed = last_completed_1h_bar(df, now=now)
    if completed.empty:
        return False
    now_et = _as_et(now)
    latest_end = pd.Timestamp(completed.index[-1]) + pd.Timedelta(hours=1)
    expected_end = now_et.replace(minute=0, second=0, microsecond=0)
    if latest_end.tzinfo is None:
        latest_end = latest_end.tz_localize(ET)
    return latest_end >= expected_end - pd.Timedelta(seconds=1)


def _merge_1h_frames(old: pd.DataFrame | None, new: pd.DataFrame | None) -> pd.DataFrame:
    """Concat + last-wins dedupe so incremental fetches update the last hour."""
    frames = [f for f in (old, new) if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame()
    if len(frames) == 1:
        return frames[0].sort_index()
    both = pd.concat(frames)
    return both[~both.index.duplicated(keep="last")].sort_index()


def _bars_1h_polygon(
    symbol: str,
    *,
    api_key: str,
    days: int = H1_LOOKBACK_DAYS,
) -> pd.DataFrame:
    """Fetch the complete Polygon window, following aggregate cursor pages."""
    sym = symbol.upper()
    end = datetime.now(ET).date()
    start = end - timedelta(days=days)
    url = f"{POLYGON_BASE}/v2/aggs/ticker/{sym}/range/1/hour/{start}/{end}"
    params = {"adjusted": "true", "sort": "asc", "limit": 50000}
    results_by_time: dict[int, dict[str, Any]] = {}
    pages = 0
    while url and pages < MAX_AGG_PAGES:
        data = polygon_get(url, params, api_key, timeout=BARS_GET_TIMEOUT_SEC)
        for bar in data.get("results") or []:
            if bar.get("t") is not None:
                results_by_time[int(bar["t"])] = bar
        pages += 1
        url = str(data.get("next_url") or "")
        params = {}
    if url:
        raise RuntimeError(
            f"Polygon 1H pagination exceeded {MAX_AGG_PAGES} pages for {sym}"
        )
    results = [results_by_time[key] for key in sorted(results_by_time)]
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


def load_1h_bars(
    symbol: str,
    *,
    api_key: str,
    days: int = H1_LOOKBACK_DAYS,
    now: datetime | None = None,
) -> pd.DataFrame:
    """
    1H OHLCV for `symbol`, preferring memory then disk cache.

    Fresh cache (last completed hour present) → no Polygon call.
    Stale cache → 3-day incremental fetch merged onto the cached series.
    Miss → full `days` lookback. polygon_get already rate-sleeps; no extra sleep.
    """
    sym = symbol.upper()
    cached = _mem_get(sym)
    if cached is None:
        cached = _disk_get(sym)
        if cached is not None:
            _mem_set(sym, cached)

    if cached is not None and _cache_covers_last_completed(cached, now):
        return cached.copy()

    fetch_days = H1_INCREMENTAL_DAYS if cached is not None else int(days)
    try:
        fresh = _bars_1h_polygon(sym, api_key=api_key, days=fetch_days)
    except Exception:
        if cached is not None:
            return cached.copy()
        raise

    merged = _merge_1h_frames(cached, fresh)
    if merged.empty and cached is not None:
        return cached.copy()
    if not merged.empty and not _cache_covers_last_completed(merged, now):
        if fetch_days < int(days):
            try:
                full = _bars_1h_polygon(sym, api_key=api_key, days=int(days))
                merged = _merge_1h_frames(merged, full)
            except Exception:
                pass
    if merged.empty:
        return merged
    now_et = _as_et(now)
    cutoff = now_et - timedelta(days=int(days) + 5)
    try:
        merged = merged[merged.index >= cutoff]
    except Exception:
        pass
    _mem_set(sym, merged)
    _disk_set(sym, merged)
    return merged.copy()


def session_bars_from_cache(symbol: str, day) -> list[dict[str, Any]]:
    """
    Convert cached 1H bars for one ET calendar day into decision-context dicts.

    Empty list on cache miss — caller may fall back to a Polygon day fetch.
    """
    df = _mem_get(symbol)
    if df is None:
        df = _disk_get(symbol)
    if df is None or df.empty:
        return []
    out: list[dict[str, Any]] = []
    for ts, row in df.iterrows():
        try:
            ts_et = ts.tz_convert(ET) if getattr(ts, "tzinfo", None) else ET.localize(ts.to_pydatetime())
            if ts_et.date() != day:
                continue
            out.append({
                "et": ts_et.to_pydatetime() if hasattr(ts_et, "to_pydatetime") else ts_et,
                "close_hour": bar_close_hour_et(ts_et),
                "o": float(row["open"]),
                "h": float(row["high"]),
                "l": float(row["low"]),
                "c": float(row["close"]),
                "v": float(row["volume"] if "volume" in row else row.get("v") or 0),
            })
        except Exception:
            continue
    return out


def last_completed_1h_bar(
    df: pd.DataFrame,
    *,
    now: datetime | None = None,
) -> pd.DataFrame:
    """Drop the still-forming hour so the last row is a completed 1H bar."""
    if df.empty:
        return df
    now_et = _as_et(now)
    idx = df.index
    if not isinstance(idx, pd.DatetimeIndex):
        return df
    bar_end = idx + pd.Timedelta(hours=1)
    completed = df.loc[bar_end <= now_et]
    return completed if not completed.empty else df.iloc[0:0]


def _phase_3h_from_hourly(df_1h: pd.DataFrame) -> str | None:
    """3H phase context from 1H bars (IBKR-style 3h right-label resample)."""
    try:
        from tsd_scan_pipeline.build_3h_bars import aggregate_hourly_to_3h

        bars_3h = aggregate_hourly_to_3h(df_1h)
        if len(bars_3h) < 80:
            return None
        enriched = enrich_tsd(bars_3h)
        summary = last_bar_summary(enriched)
        row = enrich_launch_fields(summary)
        return str(row.get("phase") or "NEUTRAL")
    except Exception:
        return None


def evaluate_1h_buy_signal(
    row: dict[str, Any],
    *,
    polygon_key: str | None = None,
    now: datetime | None = None,
) -> tuple[bool, dict[str, Any]]:
    """
    Evaluate last completed 1H bar as the LAUNCH trigger.

    Returns (passed, launch_row). Color does NOT veto; bar_state is telemetry/rank.
    Soft skip when structure risk > 5% (structure_too_wide) — not used as kill.
    """
    if row.get("htf_1h_buy_signal") is not None and row.get("htf_1h_close") is None:
        ok = bool(row.get("htf_1h_buy_signal"))
        hour = row.get("htf_1h_bar_hour")
        meta = enrich_launch_fields({
            **row,
            "htf_1h_buy_signal": ok,
            "buy_signal": bool(ok or row.get("buy_signal") or row.get("early_bull")),
            "source": "row",
            "htf_1h_bar_hour": hour,
            "hour_allowed": is_allowed_hour(int(hour)) if hour is not None else True,
        })
        return ok and meta["hour_allowed"], meta

    if row.get("htf_1h_close") is not None and row.get("htf_1h_buy_signal") is not None:
        ok = bool(row.get("htf_1h_buy_signal"))
        hour = int(row.get("htf_1h_bar_hour") or 0)
        hour_ok = is_allowed_hour(hour) if row.get("htf_1h_bar_hour") is not None else True
        out = enrich_launch_fields(dict(row))
        out["hour_allowed"] = hour_ok
        out["source"] = out.get("source") or "row"
        if out.get("structure_level") and structure_too_wide(
            float(out.get("htf_1h_close") or out.get("close") or 0),
            out.get("structure_level"),
        ):
            out["reject_reason"] = "structure_too_wide"
            return False, out
        return ok and hour_ok, out

    sym = str(row.get("symbol", "")).upper()
    if not sym:
        return False, {"htf_1h_buy_signal": False, "source": "no_symbol"}

    key = polygon_key or load_polygon_key()
    try:
        df = load_1h_bars(sym, api_key=key, days=H1_LOOKBACK_DAYS, now=now)
    except Exception as exc:
        return False, {"htf_1h_buy_signal": False, "source": f"fetch_err:{exc}"}

    completed = last_completed_1h_bar(df, now=now)
    if len(completed) < HTF_1H_BARS_MIN:
        return False, {
            "htf_1h_buy_signal": False,
            "source": "insufficient_1h_bars",
            "symbol": sym,
        }

    now_et = _as_et(now)
    latest_start = pd.Timestamp(completed.index[-1])
    latest_end = latest_start + pd.Timedelta(hours=1)
    bar_age = now_et - latest_end.to_pydatetime()
    if bar_age > MAX_COMPLETED_BAR_AGE:
        return False, {
            "htf_1h_buy_signal": False,
            "source": "stale_1h_bars",
            "reject_reason": "stale_1h_bars",
            "symbol": sym,
            "htf_1h_bar_time": latest_start,
            "htf_1h_bar_hour": bar_close_hour_et(latest_start),
            "bar_age_minutes": round(bar_age.total_seconds() / 60.0, 1),
        }

    enriched = enrich_tsd(completed)
    summary = last_bar_summary(enriched)
    launch = enrich_launch_fields({**row, **summary, "symbol": sym})
    close_hour = bar_close_hour_et(pd.Timestamp(summary["time"]))
    trigger = bool(launch.get("buy_signal")) or bool(launch.get("early_bull"))
    # EXP-0021: list = buy/early + quality floors; peak hour is score-only
    ok_launch = is_continuation_list_candidate(launch) and trigger
    hour_ok = is_allowed_hour(close_hour)
    phase_3h = _phase_3h_from_hourly(completed)
    bar_state = classify_bar_state(launch)

    lows = [float(x) for x in completed["low"].tolist() if x is not None]
    area_low = structure_area_low(lows)
    entry_px = float(summary.get("close") or 0)
    struct_risk = structure_risk_pct(entry_px, area_low)

    launch_row: dict[str, Any] = {
        **launch,
        "symbol": sym,
        "htf_1h_buy_signal": ok_launch,
        "htf_1h_close": summary.get("close"),
        "htf_1h_scan_score": summary.get("scan_score"),
        "htf_1h_bar_time": summary.get("time"),
        "htf_1h_bar_hour": close_hour,
        "hour_allowed": hour_ok,
        "entry_price": summary.get("close"),
        "close": summary.get("close"),
        "open": summary.get("open"),
        "high": summary.get("high"),
        "low": summary.get("low"),
        "buy_signal": bool(launch.get("buy_signal")),
        "early_bull": bool(launch.get("early_bull")),
        "signal_bar_red": signal_bar_red(launch),
        "bar_state": bar_state,
        "phase_3h": phase_3h,
        "structure_level": area_low,
        "structure_risk_pct": struct_risk,
        "source": BAR_SOURCE,
        "structure_mode": "KILL ONLY until +1R",
    }
    launch_row = enrich_launch_fields(launch_row)

    if structure_too_wide(entry_px, area_low):
        launch_row["htf_1h_buy_signal"] = False
        launch_row["reject_reason"] = "structure_too_wide"
        return False, launch_row

    passed = ok_launch and hour_ok
    return passed, launch_row
