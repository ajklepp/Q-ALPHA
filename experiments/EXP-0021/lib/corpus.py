"""
EXP-0021 corpus builder — all RTH 1H buy/early_bull signals + path labels.

Uses Polygon aggs + tsd_signals.enrich_tsd. Causal daily MTF via prior closes.
"""
from __future__ import annotations

import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import pytz

EXP_DIR = Path(__file__).resolve().parent.parent
ROOT = EXP_DIR.parent.parent
CANDIDATES = ROOT / "candidates"
for p in (str(CANDIDATES), str(EXP_DIR / "lib"), str(EXP_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from features import (  # noqa: E402
    PEAK_HOURS,
    all_hours_admit,
    bar_state_from_ohlc,
    continuation_score_v1,
    daily_mtf_features,
    path_labels_after_entry,
    peak_hour_v0_admit,
    prior_ticker_stats,
    session_features_at,
)
from social import fetch_social_bundle  # noqa: E402

from tsd_scan_pipeline.tsd_htf_gates import compute_htf_metrics, compute_htf_rank_score  # noqa: E402
from tsd_scan_pipeline.tsd_launch_score import (  # noqa: E402
    compute_launch_phase,
    compute_launch_score,
    compute_continuation_score_v0,
)
from tsd_scan_pipeline.tsd_signals import enrich_tsd  # noqa: E402
from tsd_scan_pipeline.universe_tsd import POLYGON_BASE, load_polygon_key, polygon_get  # noqa: E402

ET = pytz.timezone("America/New_York")
POLYGON_SLEEP = 0.12
KILL_PCT = 0.05

# Case-study + liquid momentum names for pilot corpus (expand via --full)
DEFAULT_PILOT = [
    "IREN", "TARS", "CHPT", "ARX", "JANX", "MRVI", "CBLL", "TRLV",
    "SOUN", "PLTR", "SMCI", "MARA", "RIOT", "AFRM", "UPST", "HOOD",
    "CVNA", "RIVN", "LCID", "NIO", "SOFI", "OPEN", "PATH", "SNOW",
    "DKNG", "COIN", "MSTR", "CLSK", "HIMS", "OSCR",
]


def bar_close_hour_et(ts: pd.Timestamp) -> int:
    """Polygon 1H timestamp is bar start; close hour = start+1."""
    if ts.tzinfo is None:
        ts = ET.localize(ts.to_pydatetime()) if hasattr(ts, "to_pydatetime") else ET.localize(ts)
    else:
        ts = ts.tz_convert(ET)
    return (int(ts.hour) + 1) % 24


def fetch_aggs(
    api_key: str,
    symbol: str,
    *,
    mult: int,
    span: str,
    start: date,
    end: date,
) -> list[dict]:
    """Paginated Polygon aggregates."""
    url: str | None = (
        f"{POLYGON_BASE}/v2/aggs/ticker/{symbol.upper()}/range/{mult}/{span}/"
        f"{start}/{end}"
    )
    params: dict[str, Any] | None = {"adjusted": "true", "sort": "asc", "limit": 50000}
    rows: list[dict] = []
    while url:
        data = polygon_get(url, params, api_key)
        rows.extend(data.get("results") or [])
        url = data.get("next_url") or None
        params = None
        time.sleep(POLYGON_SLEEP)
    return rows


def aggs_to_df(aggs: list[dict]) -> pd.DataFrame:
    if not aggs:
        return pd.DataFrame()
    rows = []
    for b in aggs:
        ts = pd.Timestamp(b["t"], unit="ms", tz="UTC").tz_convert(ET)
        rows.append({
            "time": ts,
            "open": float(b["o"]),
            "high": float(b["h"]),
            "low": float(b["l"]),
            "close": float(b["c"]),
            "volume": float(b.get("v") or 0),
        })
    return pd.DataFrame(rows).set_index("time").sort_index()


def filter_rth_1h(df: pd.DataFrame) -> pd.DataFrame:
    """Keep bars whose close hour is in RTH (10–16) or Peak 07."""
    if df.empty:
        return df
    hours = [bar_close_hour_et(ts) for ts in df.index]
    mask = [(h >= 10 and h <= 16) or h in PEAK_HOURS for h in hours]
    out = df.loc[mask].copy()
    out["close_hour"] = [bar_close_hour_et(ts) for ts in out.index]
    return out


def rest_of_day_path(
    df_1h: pd.DataFrame,
    i: int,
) -> tuple[list[float], list[float], float]:
    """Future 1H highs/lows after signal bar until session end; also day MFE from entry."""
    entry = float(df_1h.iloc[i]["close"])
    ts = df_1h.index[i]
    day = ts.date()
    highs: list[float] = []
    lows: list[float] = []
    for j in range(i + 1, len(df_1h)):
        tj = df_1h.index[j]
        if tj.date() != day:
            break
        highs.append(float(df_1h.iloc[j]["high"]))
        lows.append(float(df_1h.iloc[j]["low"]))
    # also include remaining day from daily if no more 1H — still use what we have
    day_mfe = 0.0
    if highs:
        day_mfe = max(0.0, (max(highs) - entry) / entry)
    return highs, lows, day_mfe


def build_ticker_rows(
    symbol: str,
    *,
    api_key: str,
    lookback_days: int = 90,
    include_social: bool = True,
    social_every_n: int = 8,
) -> list[dict[str, Any]]:
    """
    Build labeled signal rows for one ticker.

    Social/news fetched sparsely (every Nth signal) to respect rate limits;
    other rows inherit last bundle or zeros.
    """
    end = datetime.now(ET).date()
    start = end - timedelta(days=lookback_days + 40)
    daily_start = end - timedelta(days=lookback_days + 400)

    hourly = aggs_to_df(fetch_aggs(api_key, symbol, mult=1, span="hour", start=start, end=end))
    daily = aggs_to_df(fetch_aggs(api_key, symbol, mult=1, span="day", start=daily_start, end=end))
    if hourly.empty or len(hourly) < 80:
        return []

    hourly = filter_rth_1h(hourly)
    if len(hourly) < 50:
        return []

    enriched = enrich_tsd(hourly.copy())
    enriched["close_hour"] = [bar_close_hour_et(ts) for ts in enriched.index]

    # HTF pass on rolling prior daily (as_of each signal day)
    rows_out: list[dict[str, Any]] = []
    prior_for_ticker: list[dict[str, Any]] = []
    last_social: dict[str, Any] = {}
    sig_count = 0

    min_date = end - timedelta(days=lookback_days)
    daily_dates = pd.Series(daily.index.map(lambda t: t.date()), index=daily.index)

    for i in range(len(enriched)):
        r = enriched.iloc[i]
        ts = enriched.index[i]
        if ts.date() < min_date:
            continue
        buy = bool(r.get("buy_signal"))
        early = bool(r.get("early_bull"))
        if not (buy or early):
            continue

        # Daily HTF using bars strictly before signal date (no same-day look-ahead)
        d_prior = daily.loc[daily_dates < ts.date()]
        if len(d_prior) < 60:
            continue
        closes = d_prior["close"].astype(float).tolist()
        highs = d_prior["high"].astype(float).tolist()
        lows = d_prior["low"].astype(float).tolist()
        htf = compute_htf_metrics(closes, highs, lows)
        if htf.get("insufficient_bars"):
            continue
        if not (htf.get("range_ok") and htf.get("close_above_sma50") and htf.get("sma20_rising") and htf.get("price_ok")):
            continue

        o, h, low, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
        scan = float(r["scan_score"]) if pd.notna(r.get("scan_score")) else 0.0
        bar_state = bar_state_from_ohlc(o, h, low, c, scan=scan)
        hour = int(r.get("close_hour") or bar_close_hour_et(ts))

        sess = session_features_at(enriched, i)
        mtf = daily_mtf_features(daily, as_of_date=ts.date(), signal_close=c)
        hist = prior_ticker_stats(prior_for_ticker)

        # gap vs prior RTH close
        gap_pct = 0.0
        if i > 0:
            # prior calendar day close
            prev_days = enriched.iloc[:i]
            prev_days = prev_days[prev_days.index.map(lambda t: t.date()) < ts.date()]
            if not prev_days.empty:
                prior_c = float(prev_days.iloc[-1]["close"])
                # today's first bar open
                today = enriched.iloc[: i + 1]
                today = today[today.index.map(lambda t: t.date()) == ts.date()]
                if not today.empty and prior_c > 0:
                    gap_pct = (float(today.iloc[0]["open"]) - prior_c) / prior_c

        feat: dict[str, Any] = {
            "symbol": symbol.upper(),
            "signal_ts": str(ts),
            "signal_date": str(ts.date()),
            "hour": hour,
            "peak_hour": int(hour in PEAK_HOURS),
            "open": o,
            "high": h,
            "low": low,
            "close": c,
            "buy_signal": buy,
            "early_bull": early,
            "scan_score": scan,
            "wt1": float(r["wt1"]) if pd.notna(r.get("wt1")) else None,
            "wt2": float(r["wt2"]) if pd.notna(r.get("wt2")) else None,
            "trend_strength": float(r["trend_strength"]) if pd.notna(r.get("trend_strength")) else 0.0,
            "bar_state": bar_state,
            "htf_range_20d_pct": htf.get("range_20d_pct"),
            "htf_dist_sma50_pct": htf.get("dist_sma50_pct"),
            "htf_sma20_slope_pct": htf.get("sma20_slope_pct"),
            "htf_score": compute_htf_rank_score(htf),
            "gap_pct": gap_pct,
            **sess,
            **{k: v for k, v in mtf.items() if k != "insufficient_daily"},
            **hist,
        }

        launch_row = {
            "scan_score": scan,
            "trend_strength": feat["trend_strength"],
            "buy_signal": buy,
            "early_bull": early,
            "open": o,
            "high": h,
            "low": low,
            "close": c,
            "bar_state": bar_state,
            "htf_score": feat["htf_score"],
            "htf_1h_bar_hour": hour,
        }
        feat["launch_score"] = compute_launch_score(launch_row)
        feat["phase"] = compute_launch_phase(launch_row)
        feat["continuation_score_v0"] = compute_continuation_score_v0({**launch_row, "launch_score": feat["launch_score"]})

        if include_social and (sig_count % social_every_n == 0 or not last_social):
            try:
                last_social = fetch_social_bundle(
                    symbol,
                    api_key=api_key,
                    as_of=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else datetime.now(ET),
                    include_x=True,
                )
            except Exception:
                last_social = {"social_missing": 1, "news_velocity_24h": 0.0, "st_msg_24h": 0.0}
        feat.update(last_social or {"social_missing": 1})

        feat["continuation_score_v1"] = continuation_score_v1(feat)
        feat["php_v0_admit"] = int(peak_hour_v0_admit(feat))
        feat["all_hours_admit"] = int(all_hours_admit(feat))

        fut_h, fut_l, day_mfe = rest_of_day_path(enriched, i)
        labels = path_labels_after_entry(c, fut_h, fut_l, kill_pct=KILL_PCT)
        feat.update(labels)
        feat["day_mfe"] = round(day_mfe, 6)
        # expectancy proxy: +target if hit else -kill if killed else 0 (simplified)
        if labels["hit_1r"]:
            feat["r_multiple"] = 1.0
        elif labels["killed"]:
            feat["r_multiple"] = -1.0
        else:
            feat["r_multiple"] = float(labels["mfe"]) / KILL_PCT if KILL_PCT else 0.0

        rows_out.append(feat)
        prior_for_ticker.append({"mfe": labels["mfe"], "hit_1r": labels["hit_1r"]})
        sig_count += 1

    return rows_out


def build_corpus(
    symbols: Iterable[str],
    *,
    api_key: str | None = None,
    lookback_days: int = 90,
    include_social: bool = True,
) -> pd.DataFrame:
    """Build multi-ticker corpus DataFrame."""
    key = api_key or load_polygon_key()
    all_rows: list[dict[str, Any]] = []
    syms = list(symbols)
    for n, sym in enumerate(syms, 1):
        print(f"  [{n}/{len(syms)}] {sym} ...", flush=True)
        try:
            rows = build_ticker_rows(
                sym,
                api_key=key,
                lookback_days=lookback_days,
                include_social=include_social,
            )
            print(f"    -> {len(rows)} signals", flush=True)
            all_rows.extend(rows)
        except Exception as exc:
            print(f"    FAIL {sym}: {exc}", flush=True)
        time.sleep(POLYGON_SLEEP)
    return pd.DataFrame(all_rows)
