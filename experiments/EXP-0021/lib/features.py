"""
EXP-0021 causal feature pack (A+B + score v1).

All features use only data available at the close of the signal 1H bar.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

PEAK_HOURS = frozenset({7, 11, 12, 13})
BAR_STATE_PTS = {"yellow": 12.0, "red": 8.0, "green": 5.0, "orange": 0.0, "extended": -20.0}
EPS = 1e-9
KILL_PCT = 0.05
TARGET_R = 0.05  # +1R at 5% when kill=5%


def _clip01(x: float) -> float:
    return float(max(0.0, min(1.0, x)))


def bar_state_from_ohlc(o: float, h: float, low: float, c: float, *, scan: float = 0.0) -> str:
    """Match tsd_launch_score.classify_bar_state without importing candidates at module load."""
    if scan >= 75.0:
        return "extended"
    rng = max(h - low, EPS)
    body = abs(c - o) / rng
    if body < 0.25:
        return "orange"
    if c < o:
        return "red"
    if body < 0.50:
        return "yellow"
    return "green"


def path_labels_after_entry(
    entry: float,
    future_highs: list[float],
    future_lows: list[float],
    *,
    kill_pct: float = KILL_PCT,
    target_pct: float = TARGET_R,
) -> dict[str, Any]:
    """
    Path-first labels after entry (signal bar close).

    Walk bars in order: kill if low <= entry*(1-kill); hit_1r if high >= entry*(1+target)
    before kill. MFE is max favorable excursion before kill (or end of path).
    """
    if entry <= 0 or not future_highs:
        return {
            "hit_1r": 0,
            "mfe": 0.0,
            "mae": 0.0,
            "mfe_ge_3": 0,
            "mfe_ge_5": 0,
            "killed": 0,
        }
    kill_px = entry * (1.0 - kill_pct)
    target_px = entry * (1.0 + target_pct)
    mfe = 0.0
    mae = 0.0
    hit = 0
    killed = 0
    for hi, lo in zip(future_highs, future_lows):
        mae = max(mae, (entry - lo) / entry)
        mfe = max(mfe, (hi - entry) / entry)
        if lo <= kill_px:
            killed = 1
            break
        if hi >= target_px:
            hit = 1
            # continue accumulating MFE until kill or end (path-first still records hit)
            # but stop adverse past kill — for hit we already recorded
    return {
        "hit_1r": int(hit),
        "mfe": round(mfe, 6),
        "mae": round(mae, 6),
        "mfe_ge_3": int(mfe >= 0.03),
        "mfe_ge_5": int(mfe >= 0.05),
        "killed": int(killed),
    }


def session_features_at(
    df_1h: pd.DataFrame,
    i: int,
) -> dict[str, float]:
    """Intraday tape features at index i (causal within session)."""
    row = df_1h.iloc[i]
    o, h, low, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
    vol = float(row.get("volume") or 0.0)
    rng = max(h - low, EPS)
    body = abs(c - o) / rng
    close_loc = (c - low) / rng

    vol20 = float(df_1h["volume"].iloc[max(0, i - 19) : i + 1].mean()) if i >= 0 else vol
    vol_ratio = vol / vol20 if vol20 > 0 else 1.0

    ts = df_1h.index[i]
    day = ts.date() if hasattr(ts, "date") else pd.Timestamp(ts).date()
    same = df_1h.iloc[: i + 1]
    same = same[same.index.map(lambda t: (t.date() if hasattr(t, "date") else pd.Timestamp(t).date()) == day)]
    session_high = float(same["high"].max()) if not same.empty else h
    session_low = float(same["low"].min()) if not same.empty else low
    bars_since = int(len(same))

    consec = 0
    for j in range(i, -1, -1):
        rj = df_1h.iloc[j]
        if float(rj["close"]) > float(rj["open"]):
            consec += 1
        else:
            break

    return {
        "bar_range_pct": (h - low) / c if c else 0.0,
        "bar_body_pct": body,
        "close_loc": close_loc,
        "vol_ratio_20": vol_ratio,
        "dollar_vol_1h": c * vol,
        "bars_since_rth_open": float(bars_since),
        "dist_hod_pct": (session_high - c) / c if c else 0.0,
        "dist_lod_pct": (c - session_low) / c if c else 0.0,
        "consec_green": float(consec),
    }


def daily_mtf_features(
    daily: pd.DataFrame,
    *,
    as_of_date,
    signal_close: float,
) -> dict[str, float | int | None]:
    """
    Multi-timeframe daily confluence using only daily bars with date < as_of_date
    (prior closes — no same-day daily look-ahead for intraday signals).
    """
    if daily.empty or signal_close <= 0:
        return {}
    # Compare by calendar date to avoid tz-aware vs naive Timestamp errors
    as_of = pd.Timestamp(as_of_date).date()
    idx_dates = daily.index.map(lambda t: pd.Timestamp(t).date())
    d = daily.loc[idx_dates < as_of].copy()
    if len(d) < 20:
        return {"insufficient_daily": 1}

    closes = d["close"].astype(float)
    highs = d["high"].astype(float)
    lows = d["low"].astype(float)

    low20 = float(lows.tail(20).min())
    high20 = float(highs.tail(20).max())
    high52 = float(highs.tail(min(252, len(highs))).max())

    def _sma(s: pd.Series, n: int) -> float | None:
        if len(s) < n:
            return None
        return float(s.tail(n).mean())

    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    sma200 = _sma(closes, 200)

    # HH/HL vs ~20d ago
    hh_hl = 0
    if len(highs) >= 25 and len(lows) >= 25:
        if float(highs.iloc[-1]) > float(highs.iloc[-21]) and float(lows.iloc[-1]) > float(lows.iloc[-21]):
            hh_hl = 1

    out: dict[str, float | int | None] = {
        "dist_20d_low_pct": (signal_close - low20) / signal_close,
        "dist_20d_high_pct": (high20 - signal_close) / signal_close,  # room left
        "dist_52w_high_pct": (high52 - signal_close) / signal_close,
        "close_vs_sma20": ((signal_close - sma20) / sma20) if sma20 else None,
        "close_vs_sma50": ((signal_close - sma50) / sma50) if sma50 else None,
        "close_vs_sma200": ((signal_close - sma200) / sma200) if sma200 else None,
        "hh_hl_20": hh_hl,
        "insufficient_daily": 0,
    }
    # bounce quality: closer to 20d low (but above) → higher soft score later
    dist_low = float(out["dist_20d_low_pct"] or 0.0)
    out["dist_20d_low_bounce"] = _clip01(1.0 - dist_low / 0.08) if dist_low >= 0 else 0.0
    return out


def prior_ticker_stats(
    prior_labels: list[dict[str, Any]],
) -> dict[str, float]:
    """Expanding-window ticker history from prior labeled signals only."""
    if not prior_labels:
        return {"ticker_prior_mfe_p50": 0.0, "ticker_prior_hit1r_rate": 0.0, "ticker_prior_n": 0.0}
    mfes = [float(x.get("mfe") or 0.0) for x in prior_labels]
    hits = [int(x.get("hit_1r") or 0) for x in prior_labels]
    return {
        "ticker_prior_mfe_p50": float(np.median(mfes)),
        "ticker_prior_hit1r_rate": float(np.mean(hits)),
        "ticker_prior_n": float(len(prior_labels)),
    }


def continuation_score_v1(feat: dict[str, Any]) -> float:
    """
    Heuristic challenger score (FEATURES.md).

    Peak hour is a bonus — all RTH hours can enter the list.
    """
    peak = 25.0 if int(feat.get("hour") or -1) in PEAK_HOURS or feat.get("peak_hour") else 0.0
    bs = str(feat.get("bar_state") or "orange")
    bar_pts = float(BAR_STATE_PTS.get(bs, 0.0))

    room = float(feat.get("dist_20d_high_pct") or 0.0)
    # Room left helps; negative room (already through highs) is a chase penalty
    if room < 0:
        room_term = -15.0 * _clip01((-room) / 0.05)
    else:
        room_term = 20.0 * _clip01(room / 0.15)

    bounce = float(feat.get("dist_20d_low_bounce") or 0.0)
    bounce_term = 15.0 * _clip01(bounce)

    vr = float(feat.get("vol_ratio_20") or 1.0)
    vol_term = 15.0 * _clip01(math.log1p(max(vr, 0.0)) / math.log1p(5.0))
    if vr < 0.5:
        vol_term -= 8.0  # dead tape

    prior_hit = float(feat.get("ticker_prior_hit1r_rate") or 0.0)
    prior_mfe = float(feat.get("ticker_prior_mfe_p50") or 0.0)
    hist_term = 10.0 * _clip01(prior_hit) + 12.0 * _clip01(prior_mfe / 0.05)

    news_v = float(feat.get("news_velocity_24h") or feat.get("news_headline_count_48h") or 0.0)
    news_term = 10.0 * _clip01(news_v / 5.0)

    st_msg = float(feat.get("st_msg_24h") or 0.0)
    st_bull = float(feat.get("st_bull_ratio") or 0.5)
    st_term = 8.0 * _clip01(st_bull) if st_msg > 0 else 0.0

    x_sent = float(feat.get("x_sent_lex") or 0.0)
    x_term = 5.0 * _clip01((x_sent + 1.0) / 2.0) if not feat.get("social_missing") else 0.0

    launch = float(feat.get("launch_score") or 0.0)
    launch_term = 0.25 * launch  # wire existing launch quality into rank

    scan = float(feat.get("scan_score") or 55.0)
    # Soft sweet-spot preference (25–45)
    if 25.0 <= scan <= 45.0:
        scan_term = 10.0
    elif scan <= 55.0:
        scan_term = 4.0
    else:
        scan_term = -10.0

    score = (
        peak + bar_pts + room_term + bounce_term + vol_term + hist_term
        + news_term + st_term + x_term + launch_term + scan_term
    )

    if feat.get("guidance_cut"):
        score -= 25.0
    if feat.get("dilution_flag"):
        score -= 30.0
    if feat.get("distress_flag"):
        score -= 40.0
    if scan > 55.0:
        score -= 20.0  # soft EXTENSION penalty

    return round(score, 2)


def peak_hour_v0_admit(feat: dict[str, Any]) -> bool:
    """Baseline Peak Hour list gate (matches live LAUNCH lane spirit)."""
    hour = int(feat.get("hour") or -1)
    if hour not in PEAK_HOURS:
        return False
    if not (feat.get("buy_signal") or feat.get("early_bull")):
        return False
    if float(feat.get("scan_score") or 99) > 55.0:
        return False
    if str(feat.get("bar_state") or "") == "extended":
        return False
    if str(feat.get("phase") or "") == "EXTENSION":
        return False
    return True


def all_hours_admit(feat: dict[str, Any]) -> bool:
    """
    Challenger list: RTH (and peak 07) with buy/early_bull + quality floors.

    Peak hour is NOT required. Hard-block only auto-extended (scan>=75).
    Drop late-day bars with no path left (hour>=15) — labels are rest-of-day.
    """
    hour = int(feat.get("hour") or -1)
    if hour >= 16:
        return False
    if hour < 10 and hour not in PEAK_HOURS:
        return False
    if not (feat.get("buy_signal") or feat.get("early_bull")):
        return False
    scan = float(feat.get("scan_score") or 99.0)
    if scan >= 75.0 or str(feat.get("bar_state") or "") == "extended":
        return False
    # Quality floor: launch_score or early-scan band (not Peak Hour exclusive)
    launch = float(feat.get("launch_score") or 0.0)
    if launch < 40.0 and scan > 55.0:
        return False
    return True
