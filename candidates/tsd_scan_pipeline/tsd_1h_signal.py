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

import time
from datetime import datetime, timedelta
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
# Through 15:00 bar close → 15:15 scan; premarket 05/06/08/09 from EXP-0021 hitch study
ALLOWED_HOURS = {5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15}
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
