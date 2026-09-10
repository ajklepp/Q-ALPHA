"""
Self-contained Wave Cross (TSD) math for Track 100.

Port of Pine / Q-ALPHA tsd_signals constants — copied here so the study
never depends on editing live pipeline files.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

WT_CHANNEL = 10
WT_AVG = 21
WT_OB = 53
WT_OS = -53
TREND_VWMA_LEN = 10
TREND_ATR_LEN = 14
TREND_SMOOTH_LEN = 3
TREND_CLAMP = 2.0
CONFIRM_ZONE = 0.6
MFI_LEN = 58


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length, min_periods=length).mean()


def hlc3(df: pd.DataFrame) -> pd.Series:
    return (df["high"] + df["low"] + df["close"]) / 3.0


def vwma(close: pd.Series, volume: pd.Series, length: int) -> pd.Series:
    num = (close * volume).rolling(length, min_periods=length).sum()
    den = volume.rolling(length, min_periods=length).sum()
    return num / den.replace(0, np.nan)


def atr_wilder(high: pd.Series, low: pd.Series, close: pd.Series, length: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False).mean()


def enrich_wave(df: pd.DataFrame) -> pd.DataFrame:
    """Full Wave Cross (TSD) columns on OHLCV (1H or 3H)."""
    out = df.copy()
    ap = hlc3(out)
    esa = ema(ap, WT_CHANNEL)
    d = ema((ap - esa).abs(), WT_CHANNEL)
    ci = (ap - esa) / (0.015 * d.replace(0, np.nan))
    out["wt1"] = ema(ci, WT_AVG)
    out["wt2"] = sma(out["wt1"], 4)

    v = vwma(out["close"], out["volume"], TREND_VWMA_LEN)
    a = atr_wilder(out["high"], out["low"], out["close"], TREND_ATR_LEN)
    raw = (out["close"] - v) / a.replace(0, np.nan)
    out["trend_strength"] = raw.clip(-TREND_CLAMP, TREND_CLAMP)
    out["trend_smooth"] = ema(out["trend_strength"], TREND_SMOOTH_LEN)
    out["atr"] = a

    prev_below = out["wt1"].shift(1) <= out["wt2"].shift(1)
    now_above = out["wt1"] > out["wt2"]
    out["buy_signal"] = prev_below & now_above

    # Early bull = green-ish turn while still under wt2 (Pine)
    out["early_bull"] = (
        (out["wt1"] > out["wt1"].shift(1))
        & (out["wt1"].shift(1) <= out["wt1"].shift(2))
        & (out["wt1"] < out["wt2"])
    )

    prev_above = out["wt1"].shift(1) >= out["wt2"].shift(1)
    now_below = out["wt1"] < out["wt2"]
    out["sell_signal"] = prev_above & now_below

    # Local trough turn: prior bar was a local wt1 min, this bar rises
    out["wt_trough_turn"] = (
        (out["wt1"].shift(1) <= out["wt1"].shift(2))
        & (out["wt1"].shift(1) <= out["wt1"].shift(3))
        & (out["wt1"] > out["wt1"].shift(1))
    )

    out["green_dot"] = out["buy_signal"] | out["early_bull"]
    out["wt_min"] = out[["wt1", "wt2"]].min(axis=1)
    out["deep_os"] = out["wt_min"] <= WT_OS  # classic −53
    out["os_50"] = out["wt_min"] <= -50.0
    out["os_40"] = out["wt_min"] <= -40.0

    # Higher highs structure proxy: close above SMA50 of closes
    out["sma50"] = sma(out["close"], 50)
    out["above_sma50"] = out["close"] > out["sma50"]
    out["sma50_rising"] = out["sma50"] > out["sma50"].shift(5)

    return out
