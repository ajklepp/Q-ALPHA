"""
Q-ALPHA UTS v2.6 — 1H LAUNCH trigger (the buy, not a side gate).

Bar source: Polygon 1H aggregates (timestamp = bar START / left-labeled).
Close hour ET = start.hour + 1, matching Chat A 1h label=right closed=right.

Last COMPLETED 1H bar must have:
  TSD buy_signal + is_launch_candidate + signal_bar_red
and close hour in ALLOWED_HOURS {7, 11, 12, 13}.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import pytz

from tsd_scan_pipeline.tsd_launch_score import (
    enrich_launch_fields,
    is_launch_candidate,
    signal_bar_red,
)
from tsd_scan_pipeline.tsd_signals import enrich_tsd, last_bar_summary
from tsd_scan_pipeline.universe_tsd import POLYGON_BASE, load_polygon_key, polygon_get

ET = pytz.timezone("America/New_York")
HTF_1H_BARS_MIN = 80
ALLOWED_HOURS = {7, 11, 12, 13}
BAR_SOURCE = "polygon_1h_aggs_start_labeled"


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

    Returns (passed, launch_row). launch_row includes OHLC, buy/launch fields,
    bar close hour, and entry_price = 1H close.

    Pre-enriched rows (tests / scan) skip Polygon when htf_1h_buy_signal is set.
    """
    if row.get("htf_1h_buy_signal") is not None and row.get("htf_1h_close") is None:
        ok = bool(row.get("htf_1h_buy_signal"))
        hour = row.get("htf_1h_bar_hour")
        meta = {
            **row,
            "htf_1h_buy_signal": ok,
            "buy_signal": bool(ok or row.get("buy_signal")),
            "source": "row",
            "htf_1h_bar_hour": hour,
            "hour_allowed": is_allowed_hour(int(hour)) if hour is not None else True,
        }
        return ok and meta["hour_allowed"], meta

    if row.get("htf_1h_close") is not None and row.get("htf_1h_buy_signal") is not None:
        ok = bool(row.get("htf_1h_buy_signal"))
        hour = int(row.get("htf_1h_bar_hour") or 0)
        hour_ok = is_allowed_hour(hour) if row.get("htf_1h_bar_hour") is not None else True
        out = dict(row)
        out["hour_allowed"] = hour_ok
        out["source"] = out.get("source") or "row"
        return ok and hour_ok, out

    sym = str(row.get("symbol", "")).upper()
    if not sym:
        return False, {"htf_1h_buy_signal": False, "source": "no_symbol"}

    key = polygon_key or load_polygon_key()
    try:
        df = _bars_1h_polygon(sym, api_key=key)
        time.sleep(0.12)
    except Exception as exc:
        return False, {"htf_1h_buy_signal": False, "source": f"fetch_err:{exc}"}

    completed = last_completed_1h_bar(df, now=now)
    if len(completed) < HTF_1H_BARS_MIN:
        return False, {
            "htf_1h_buy_signal": False,
            "source": "insufficient_1h_bars",
            "symbol": sym,
        }

    enriched = enrich_tsd(completed)
    summary = last_bar_summary(enriched)
    launch = enrich_launch_fields({**row, **summary, "symbol": sym})
    close_hour = bar_close_hour_et(pd.Timestamp(summary["time"]))
    ok_launch = is_launch_candidate(launch) and bool(launch.get("buy_signal")) and signal_bar_red(launch)
    hour_ok = is_allowed_hour(close_hour)
    phase_3h = _phase_3h_from_hourly(completed)

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
        "signal_bar_red": signal_bar_red(launch),
        "phase_3h": phase_3h,
        "source": BAR_SOURCE,
        "structure_mode": "KILL ONLY until +1R",
    }
    passed = ok_launch and hour_ok
    return passed, launch_row
