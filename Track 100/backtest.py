"""
Track 100 setup variants + forward-path measurement.

Long-only. Entry at next bar open after signal bar close (no look-ahead fill).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd

from wave import enrich_wave, WT_OS

COST_PER_TRADE = 0.0015


@dataclass
class SetupSpec:
    name: str
    describe: str
    mask_fn: Callable[[pd.DataFrame], pd.Series]


def setup_catalog() -> list[SetupSpec]:
    """Ablation set â€” TSLA-chart thesis first."""

    def green(df: pd.DataFrame) -> pd.Series:
        return df["green_dot"].fillna(False)

    def deep_os_green(df: pd.DataFrame) -> pd.Series:
        return green(df) & df["os_50"].fillna(False)

    def classic_os_green(df: pd.DataFrame) -> pd.Series:
        return green(df) & df["deep_os"].fillna(False)

    def trough_os_green(df: pd.DataFrame) -> pd.Series:
        return green(df) & df["os_50"].fillna(False) & df["wt_trough_turn"].fillna(False)

    def buy_only_os(df: pd.DataFrame) -> pd.Series:
        return df["buy_signal"].fillna(False) & df["os_50"].fillna(False)

    def early_only_os(df: pd.DataFrame) -> pd.Series:
        return df["early_bull"].fillna(False) & df["os_50"].fillna(False)

    def os_green_uptrend(df: pd.DataFrame) -> pd.Series:
        return (
            green(df)
            & df["os_50"].fillna(False)
            & df["above_sma50"].fillna(False)
            & df["sma50_rising"].fillna(False)
        )

    def os_40_green_uptrend(df: pd.DataFrame) -> pd.Series:
        return (
            green(df)
            & df["os_40"].fillna(False)
            & df["above_sma50"].fillna(False)
            & df["sma50_rising"].fillna(False)
        )

    def os_green_launch_score_proxy(df: pd.DataFrame) -> pd.Series:
        # Soft: not already extended â€” close within 8% of 20-bar high
        hh = df["high"].rolling(20, min_periods=5).max()
        room = (hh - df["close"]) / df["close"].replace(0, np.nan)
        return green(df) & df["os_50"].fillna(False) & (room >= 0.01)

    return [
        SetupSpec("A_green_any", "Any green dot (buy|early_bull)", green),
        SetupSpec("B_os50_green", "TSLA chart: OSâ‰¤âˆ’50 + green dot", deep_os_green),
        SetupSpec("C_os53_green", "Classic OSâ‰¤âˆ’53 + green", classic_os_green),
        SetupSpec("D_trough_os_green", "OSâ‰¤âˆ’50 + trough turn + green", trough_os_green),
        SetupSpec("E_buy_cross_os", "Buy cross only + OSâ‰¤âˆ’50", buy_only_os),
        SetupSpec("F_early_os", "Early-bull only + OSâ‰¤âˆ’50", early_only_os),
        SetupSpec("G_os_green_uptrend", "OSâ‰¤âˆ’50 + green + SMA50 rising", os_green_uptrend),
        SetupSpec("H_os40_green_uptrend", "OSâ‰¤âˆ’40 + green + SMA50 rising", os_40_green_uptrend),
        SetupSpec("I_os_green_room", "OSâ‰¤âˆ’50 + green + room to 20b high", os_green_launch_score_proxy),
    ]


def measure_path(
    df: pd.DataFrame,
    signal_i: int,
    *,
    horizon_bars: int = 40,
    stop_pct: float = 0.05,
    target_pct: float = 0.10,
) -> dict[str, Any]:
    """
    Enter next bar open after signal_i. Path on subsequent bars.
    stop / target are % from entry. Cost subtracted from return.
    """
    entry_i = signal_i + 1
    if entry_i >= len(df):
        return {"valid": False, "reason": "no_entry_bar"}

    entry = float(df.iloc[entry_i]["open"])
    if entry <= 0:
        return {"valid": False, "reason": "bad_entry"}

    stop_px = entry * (1.0 - stop_pct)
    target_px = entry * (1.0 + target_pct)
    end_i = min(len(df) - 1, entry_i + horizon_bars)

    mfe = 0.0
    mae = 0.0
    hit_target = False
    hit_stop = False
    exit_i = end_i
    exit_px = float(df.iloc[end_i]["close"])
    exit_reason = "time"

    for j in range(entry_i, end_i + 1):
        hi = float(df.iloc[j]["high"])
        lo = float(df.iloc[j]["low"])
        mfe = max(mfe, (hi - entry) / entry)
        mae = min(mae, (lo - entry) / entry)
        # Conservative: stop before target same bar
        if lo <= stop_px:
            hit_stop = True
            exit_i = j
            exit_px = stop_px
            exit_reason = "stop"
            break
        if hi >= target_px:
            hit_target = True
            exit_i = j
            exit_px = target_px
            exit_reason = "target"
            break

    gross = (exit_px - entry) / entry
    net = gross - COST_PER_TRADE
    sig = df.iloc[signal_i]
    return {
        "valid": True,
        "entry_ts": str(df.index[entry_i]),
        "signal_ts": str(df.index[signal_i]),
        "entry": entry,
        "exit": exit_px,
        "exit_reason": exit_reason,
        "bars_held": int(exit_i - entry_i + 1),
        "mfe": float(mfe),
        "mae": float(mae),
        "gross_ret": float(gross),
        "net_ret": float(net),
        "hit_target": hit_target,
        "hit_stop": hit_stop,
        "hit_2r": bool(mfe >= 2.0 * stop_pct and not (hit_stop and abs(mae) >= stop_pct and exit_reason == "stop")),
        "wt1": float(sig["wt1"]) if pd.notna(sig.get("wt1")) else None,
        "wt2": float(sig["wt2"]) if pd.notna(sig.get("wt2")) else None,
        "buy_signal": bool(sig.get("buy_signal")),
        "early_bull": bool(sig.get("early_bull")),
        "close": float(sig["close"]),
    }


def collect_signals(
    df: pd.DataFrame,
    spec: SetupSpec,
    *,
    signal_start: str,
    signal_end: str,
) -> list[int]:
    """Indices of signal bars inside [signal_start, signal_end]."""
    enriched = enrich_wave(df) if "wt1" not in df.columns else df
    mask = spec.mask_fn(enriched).fillna(False)
    start_ts = pd.Timestamp(signal_start, tz=enriched.index.tz)
    end_ts = pd.Timestamp(signal_end, tz=enriched.index.tz) + pd.Timedelta(days=1)
    hits: list[int] = []
    for i, (ts, ok) in enumerate(zip(enriched.index, mask.tolist())):
        if not ok:
            continue
        if ts < start_ts or ts >= end_ts:
            continue
        # Need at least one bar after for entry
        if i + 1 >= len(enriched):
            continue
        hits.append(i)
    return hits


def summarize_trades(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "n": 0,
            "win_rate": None,
            "avg_net": None,
            "sum_net": 0.0,
            "avg_mfe": None,
            "avg_mae": None,
            "hit_target_rate": None,
            "hit_stop_rate": None,
            "expectancy": None,
            "profit_factor": None,
        }
    nets = np.array([t["net_ret"] for t in trades], dtype=float)
    wins = nets > 0
    losses = nets <= 0
    gp = float(nets[wins].sum()) if wins.any() else 0.0
    gl = float(abs(nets[losses].sum())) if losses.any() else 0.0
    return {
        "n": len(trades),
        "win_rate": float(wins.mean()),
        "avg_net": float(nets.mean()),
        "sum_net": float(nets.sum()),
        "median_net": float(np.median(nets)),
        "avg_mfe": float(np.mean([t["mfe"] for t in trades])),
        "avg_mae": float(np.mean([t["mae"] for t in trades])),
        "hit_target_rate": float(np.mean([t["hit_target"] for t in trades])),
        "hit_stop_rate": float(np.mean([t["hit_stop"] for t in trades])),
        "expectancy": float(nets.mean()),
        "profit_factor": (gp / gl) if gl > 0 else (999.0 if gp > 0 else 0.0),
        "p25_net": float(np.percentile(nets, 25)),
        "p75_net": float(np.percentile(nets, 75)),
    }

