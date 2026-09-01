"""TSD (3HR Trend Finder) signal math — Pine Script port for Q-ALPHA swing track."""

from __future__ import annotations

import numpy as np
import pandas as pd

# Locked constants from Pine reference
WT_CHANNEL = 10
WT_AVG = 21
WT_OB = 53
WT_OS = -53
TREND_VWMA_LEN = 10
TREND_ATR_LEN = 14
TREND_SMOOTH_LEN = 3
TREND_CLAMP = 2.0
CONFIRM_ZONE = 0.6
REVERSAL_ZONE = 0.3
MFI_LEN = 58
SCAN_SCORE_MIN = 60


def ema(series: pd.Series, length: int) -> pd.Series:
    """TradingView-compatible EMA (adjust=False)."""
    return series.ewm(span=length, adjust=False).mean()


def sma(series: pd.Series, length: int) -> pd.Series:
    """Simple moving average."""
    return series.rolling(length, min_periods=length).mean()


def hlc3(df: pd.DataFrame) -> pd.Series:
    """Typical price (H+L+C)/3."""
    return (df["high"] + df["low"] + df["close"]) / 3.0


def vwma(close: pd.Series, volume: pd.Series, length: int) -> pd.Series:
    """Volume-weighted moving average."""
    num = (close * volume).rolling(length, min_periods=length).sum()
    den = volume.rolling(length, min_periods=length).sum()
    return num / den.replace(0, np.nan)


def atr_wilder(high: pd.Series, low: pd.Series, close: pd.Series, length: int) -> pd.Series:
    """Wilder ATR matching Pine ta.atr."""
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


def compute_wave(df: pd.DataFrame) -> pd.DataFrame:
    """
    Wave oscillator: wt1 / wt2 from hlc3 pipeline.
    Returns df with wt1, wt2 columns.
    """
    out = df.copy()
    ap = hlc3(out)
    esa = ema(ap, WT_CHANNEL)
    d = ema((ap - esa).abs(), WT_CHANNEL)
    ci = (ap - esa) / (0.015 * d.replace(0, np.nan))
    out["wt1"] = ema(ci, WT_AVG)
    out["wt2"] = sma(out["wt1"], 4)
    return out


def compute_trend_strength(df: pd.DataFrame) -> pd.DataFrame:
    """Trend strength from close vs VWMA normalized by ATR."""
    out = df.copy()
    v = vwma(out["close"], out["volume"], TREND_VWMA_LEN)
    a = atr_wilder(out["high"], out["low"], out["close"], TREND_ATR_LEN)
    raw = (out["close"] - v) / a.replace(0, np.nan)
    out["trend_strength"] = raw.clip(-TREND_CLAMP, TREND_CLAMP)
    out["trend_smooth"] = ema(out["trend_strength"], TREND_SMOOTH_LEN)
    return out


def compute_mfi_custom(df: pd.DataFrame) -> pd.DataFrame:
    """
    Custom MFI: 58-bar money-flow ratio scaled to (mf-50)*3.
    Pine uses upper/lower MF sums over MFI_LEN bars.
    """
    out = df.copy()
    tp = hlc3(out)
    raw_mf = tp * out["volume"]
    direction = tp.diff()
    pos = raw_mf.where(direction > 0, 0.0)
    neg = raw_mf.where(direction < 0, 0.0).abs()
    pos_sum = pos.rolling(MFI_LEN, min_periods=MFI_LEN).sum()
    neg_sum = neg.rolling(MFI_LEN, min_periods=MFI_LEN).sum()
    ratio = pos_sum / neg_sum.replace(0, np.nan)
    mf = 100.0 - (100.0 / (1.0 + ratio))
    out["mfi"] = (mf - 50.0) * 3.0
    return out


def volume_ratio(df: pd.DataFrame, length: int = 20) -> pd.Series:
    """Current volume / SMA(volume) for scan_score vol component."""
    base = sma(df["volume"].astype(float), length)
    return df["volume"] / base.replace(0, np.nan)


def compute_scan_score(df: pd.DataFrame) -> pd.DataFrame:
    """Composite scan score 0-100 from wave, trend, mfi, volume."""
    out = df.copy()
    vr = volume_ratio(out)
    wave_score = ((WT_OB - out["wt2"].abs()) / WT_OB * 30.0).clip(lower=0, upper=30)
    ts_score = (out["trend_strength"] / CONFIRM_ZONE * 35.0).clip(lower=0, upper=35)
    mfi_score = ((out["mfi"] + 150.0) / 300.0 * 20.0).clip(lower=0, upper=20)
    vol_score = ((vr - 1.0) * 15.0).clip(lower=0, upper=15)
    out["vol_ratio"] = vr
    out["scan_score"] = (wave_score + ts_score + mfi_score + vol_score).round(2)
    return out


def is_buy_cross(wt1: pd.Series, wt2: pd.Series) -> pd.Series:
    """Fresh crossover(wt1, wt2) on this bar."""
    prev_below = wt1.shift(1) <= wt2.shift(1)
    now_above = wt1 > wt2
    return prev_below & now_above


def is_early_bull(wt1: pd.Series, wt2: pd.Series) -> pd.Series:
    """Pine early_bull: wt1 rising from trough while still below wt2."""
    return (wt1 > wt1.shift(1)) & (wt1.shift(1) <= wt1.shift(2)) & (wt1 < wt2)


def is_near_cross(wt1: pd.Series, wt2: pd.Series, threshold: float = 20.0) -> pd.Series:
    """wt1 below wt2 but within threshold and wt1 rising."""
    gap = wt2 - wt1
    return (wt1 < wt2) & (gap < threshold) & (wt1 > wt1.shift(1))


def enrich_tsd(df: pd.DataFrame) -> pd.DataFrame:
    """Full TSD indicator pipeline on OHLCV bars (3H expected)."""
    out = compute_wave(df)
    out = compute_trend_strength(out)
    out = compute_mfi_custom(out)
    out = compute_scan_score(out)
    out["buy_signal"] = is_buy_cross(out["wt1"], out["wt2"])
    out["early_bull"] = is_early_bull(out["wt1"], out["wt2"])
    out["near_cross"] = is_near_cross(out["wt1"], out["wt2"])
    return out


def last_bar_summary(df: pd.DataFrame) -> dict:
    """Snapshot of the most recent bar for logging / probe output."""
    if df.empty:
        return {}
    row = df.iloc[-1]
    o = float(row["open"]) if "open" in row and pd.notna(row.get("open")) else float(row["close"])
    c = float(row["close"])
    return {
        "time": str(row.name) if row.name is not None else None,
        "open": o,
        "high": float(row["high"]) if pd.notna(row.get("high")) else c,
        "low": float(row["low"]) if pd.notna(row.get("low")) else c,
        "close": c,
        "wt1": float(row["wt1"]) if pd.notna(row.get("wt1")) else None,
        "wt2": float(row["wt2"]) if pd.notna(row.get("wt2")) else None,
        "trend_strength": float(row["trend_strength"]) if pd.notna(row.get("trend_strength")) else None,
        "mfi": float(row["mfi"]) if pd.notna(row.get("mfi")) else None,
        "scan_score": float(row["scan_score"]) if pd.notna(row.get("scan_score")) else None,
        "buy_signal": bool(row.get("buy_signal", False)),
        "early_bull": bool(row.get("early_bull", False)),
        "near_cross": bool(row.get("near_cross", False)),
        "signal_bar_red": c < o,
    }


def recent_buy_crosses(df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    """Return last n bars where buy_signal fired."""
    hits = df[df["buy_signal"]].tail(n)
    cols = ["close", "wt1", "wt2", "trend_strength", "scan_score"]
    return hits[[c for c in cols if c in hits.columns]]
