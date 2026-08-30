"""Build and normalize 3-hour OHLCV bars for TSD swing pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

import pandas as pd
import pytz

if TYPE_CHECKING:
    from ib_insync import IB

ET = pytz.timezone("America/New_York")

# IBKR 3H bar close hours (ET) — see results/ibkr_probe.md
IBKR_3H_CLOSE_HOURS_ET = (1, 4, 5, 8, 11, 14, 17, 19, 22)


def bars_from_ibkr(ib_bars: Iterable) -> pd.DataFrame:
    """
    Convert ib_insync BarDataList to a datetime-indexed OHLCV DataFrame.
    IBKR 3H bars arrive with bar.date as datetime or str.
    """
    rows = []
    for b in ib_bars:
        o = getattr(b, "open_", getattr(b, "open", None))
        rows.append(
            {
                "time": pd.Timestamp(b.date),
                "open": float(o),
                "high": float(b.high),
                "low": float(b.low),
                "close": float(b.close),
                "volume": float(b.volume),
            }
        )
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows).set_index("time").sort_index()
    if df.index.tz is None:
        df.index = df.index.tz_localize(ET)
    else:
        df.index = df.index.tz_convert(ET)
    return df


def ibkr_duration_str(days: int) -> str:
    """IBKR durationStr — >365 days must use years, not days."""
    if days > 365:
        years = max(1, round(days / 365))
        return f"{years} Y"
    return f"{days} D"


def bars_from_ibkr_history(ib: IB, symbol: str, days: int = 730) -> pd.DataFrame:
    """
    Fetch native IBKR 3H bars for profiler / parity (preferred over Polygon).
    """
    from ib_insync import Stock

    contract = Stock(symbol.upper(), "SMART", "USD")
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        raise ValueError(f"no contract for {symbol}")
    bars = ib.reqHistoricalData(
        contract,
        endDateTime="",
        durationStr=ibkr_duration_str(days),
        barSizeSetting="3 hours",
        whatToShow="TRADES",
        useRTH=False,
        formatDate=1,
    )
    ib.sleep(0.3)
    return bars_from_ibkr(bars)


def _ibkr_close_timestamps(anchor: pd.Timestamp, days_span: int = 3) -> list[pd.Timestamp]:
    """Generate IBKR 3H bar close timestamps around anchor."""
    anchor = pd.Timestamp(anchor).tz_convert(ET)
    start = (anchor - pd.Timedelta(days=days_span)).normalize()
    end = anchor + pd.Timedelta(days=1)
    ts_list: list[pd.Timestamp] = []
    cur = start
    while cur <= end:
        for h in IBKR_3H_CLOSE_HOURS_ET:
            t = cur + pd.Timedelta(hours=h)
            if t.tzinfo is None:
                t = t.tz_localize(ET)
            else:
                t = t.tz_convert(ET)
            ts_list.append(t)
        cur += pd.Timedelta(days=1)
    return sorted(ts_list)


def ibkr_3h_bucket_end(bar_end: pd.Timestamp) -> pd.Timestamp:
    """Map a 30-min bar end time to the IBKR-style 3H bucket close."""
    bar_end = pd.Timestamp(bar_end).tz_convert(ET)
    closes = _ibkr_close_timestamps(bar_end, days_span=2)
    for close_ts in closes:
        if close_ts >= bar_end:
            return close_ts
    return closes[-1]


def aggregate_30m_to_ibkr_3h(df_30m: pd.DataFrame) -> pd.DataFrame:
    """
    Bucket Polygon 30-min bars into IBKR-aligned 3H OHLCV (not midnight resample).
    """
    if df_30m.empty:
        return df_30m.copy()
    work = df_30m.copy()
    if not isinstance(work.index, pd.DatetimeIndex):
        work.index = pd.to_datetime(work.index)
    if work.index.tz is None:
        work.index = work.index.tz_localize(ET)
    else:
        work.index = work.index.tz_convert(ET)
    work = work.sort_index()
    work["bucket"] = [
        ibkr_3h_bucket_end(ts + pd.Timedelta(minutes=30)) for ts in work.index
    ]
    agg = work.groupby("bucket").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )
    agg.index.name = "time"
    return agg.dropna(subset=["open", "close"])


def aggregate_to_3h(df_1m: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate 1-minute OHLCV to 3-hour bars (extended hours included).
    Anchor at midnight ET — legacy helper for probe comparison only.
    """
    if df_1m.empty:
        return df_1m.copy()
    work = df_1m.copy()
    if not isinstance(work.index, pd.DatetimeIndex):
        work.index = pd.to_datetime(work.index)
    work = work.sort_index()
    agg = work.resample("3h", label="right", closed="right").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    return agg.dropna(subset=["open", "close"])


def bars_from_polygon_aggs(results: list[dict]) -> pd.DataFrame:
    """Convert Polygon aggregate results to OHLCV DataFrame."""
    rows = []
    for r in results:
        ts = pd.Timestamp(r["t"], unit="ms", tz="UTC").tz_convert("America/New_York")
        rows.append(
            {
                "time": ts,
                "open": float(r["o"]),
                "high": float(r["h"]),
                "low": float(r["l"]),
                "close": float(r["c"]),
                "volume": float(r["v"]),
            }
        )
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows).set_index("time").sort_index()
    return df


def aggregate_hourly_to_3h(df_hourly: pd.DataFrame) -> pd.DataFrame:
    """Resample 1-hour Polygon bars to 3-hour OHLCV (legacy — misaligned vs IBKR)."""
    if df_hourly.empty:
        return df_hourly.copy()
    work = df_hourly.copy()
    if not isinstance(work.index, pd.DatetimeIndex):
        work.index = pd.to_datetime(work.index)
    work = work.sort_index()
    agg = work.resample("3h", label="right", closed="right").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return agg.dropna(subset=["open", "close"])


def bar_count_for_lookback(days: int = 90) -> str:
    """IBKR durationStr helper — ~60-90 day 3H history."""
    return f"{days} D"
