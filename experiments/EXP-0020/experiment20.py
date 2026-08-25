# ============================================================
# Q-ALPHA | EXP-0020 | experiment20.py
# Ticker-Profiler R:R ranker (replaces EXP-0017 ScoreCard)
# Sacred: BracketPosition, classify_profile, get_regime (EXP-0012)
# Reuses candidates/ticker_profiler.py — do NOT fork profiler logic
# Entry/label: MOC_CLOSE_ATR_D0 (v1 locked — isolate ranker vs 0017–19)
# NO body_ratio_d0 / close_vs_range_d0 ScoreCard rules · PM features OFF
# ============================================================
import os
import modal
import warnings
warnings.filterwarnings("ignore")

app = modal.App("q-alpha-exp020")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install([
        "pandas", "pandas-ta",
        "numpy", "requests", "scipy",
    ])
    # Mount single profiler module — no forked copy of analog/MAE logic
    .add_local_file(
        "candidates/ticker_profiler.py",
        remote_path="/root/ticker_profiler.py",
    )
)

polygon_secret = modal.Secret.from_name("polygon-api-key")
# Persistent profile cache (ticker+as_of JSON); full-300 is Phase-2 after pilot
profile_volume = modal.Volume.from_name(
    "qalpha-exp020-profiles", create_if_missing=True,
)

TRADE_UNIVERSE = []
REGIME_TICKER = "SPY"

FEATURE_COLUMNS = [
    "gap_pct",
    "volume_ratio_20d",
    "bbw_percentile_52w",
    "ttm_squeeze_active",
    "atr_14_pct",
    "price_location_20d",
    "obv_slope_10d",
    "up_down_vol_ratio",
    "rs_vs_spy_20d",
    "close_vs_range_pct",
    "dist_from_20ma",
    "rsi_14",
    "base_tightness",
    "volume_thrust_prior",
    "close_vs_range_d0",
    "volume_ratio_d0",
    "upper_wick_ratio_d0",
    "gap_pct_d0",
    "body_ratio_d0",
]

DATA_START           = "2019-01-01"
DATA_END             = "2024-12-31"
TRAIN_START          = "2019-01-01"
TRAIN_END            = "2021-12-31"
VALIDATE_START       = "2022-01-01"
VALIDATE_END         = "2022-12-31"
SIM_START            = "2023-01-01"
SIM_END              = "2024-12-31"
STARTING_CAP         = 3000.0
MAX_POSITIONS_BULL   = 10
MAX_POSITIONS_BEAR   = 3
MAX_TRADES_DAY       = 3
POSITION_PCT         = 0.10
MIN_POSITION         = 50.0
MAX_POSITION         = 500.0
COST_PER_TRADE       = 0.0015
MC_SIMS              = 5000
MC_P_GATE            = 0.05
SELECTOR             = "PROFILER_RR"  # rank by profiler reward_risk
ENTRY_CONVENTION     = "MOC_CLOSE_ATR_D0"  # v1 locked; morning/~09:33 follow-up later
# --- Director decisions (v1 locked — see DESIGN.md) ---
PILOT_MAX_TICKERS    = 50     # full TARGET_COUNT=300 is Phase-2 after pilot PASS/FAIL
INSUFFICIENT_POLICY  = "SKIP"  # no trade if profiler INSUFFICIENT
RR_MIN_GATE          = None   # rank-only; still report share with R:R < 1.5
RR_WARN_REPORT       = 1.5    # informational thin-R:R threshold (not a hard gate)
USE_PREMARKET_FEATURES = False  # PM features OFF for v1
PROFILE_CACHE_DIR    = "/cache/exp020_profiles"  # Modal Volume mount
S4_RUNNER_DAYS       = 30
# External baselines (do not re-run)
BASELINE_0017 = {"sharpe": 1.87, "n_trades": 103, "total_ret": 0.2720}
BASELINE_0018 = {"sharpe": 0.864, "n_trades": 93, "total_ret": 0.1341}
BASELINE_0019_FULL = {"sharpe": 1.87, "n_trades": 103}
GAP_MIN              = 0.03
VOL_RATIO_MIN        = 2.0
SHARPE_GATE          = 1.50
MAX_DD_GATE          = -0.15
TRADES_GATE          = 60
BASE_RATE_MIN        = 0.18
BASE_RATE_MAX        = 0.28
WF_PASS_MIN          = 3
MAX_MARKET_CAP       = 20_000_000_000
SCREEN_DATE          = "2024-01-03"
PRICE_MIN            = 3.0
PRICE_MAX            = 500.0
MIN_AVG_VOLUME       = 300_000
TARGET_COUNT         = 300
MAX_UNIVERSE         = 400
POLYGON_SLEEP        = 0.12
VIX_TICKER           = "I:VIX"


def polygon_get(url, params, api_key, timeout=30):
    """Polygon GET with rate limiting and basic retry."""
    import time
    import requests

    params = dict(params)
    params["apiKey"] = api_key
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            time.sleep(POLYGON_SLEEP)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            if attempt == 2:
                return None
            time.sleep(0.5)
    return None


def build_dynamic_universe(api_key, screen_date=SCREEN_DATE):
    """Dynamic screener — expanded to ~300 tickers (EXP-0016)."""
    print(f"\n  Screening universe (reference date: {screen_date})...")
    print(f"    Filters: price ${PRICE_MIN}-${PRICE_MAX}, "
          f"vol>={MIN_AVG_VOLUME:,}, mcap<${MAX_MARKET_CAP/1e9:.0f}B, "
          f"target={TARGET_COUNT}")

    cap_tickers = set()
    url = "https://api.polygon.io/v3/reference/tickers"
    params = {
        "market": "stocks",
        "locale": "us",
        "active": "true",
        "type": "CS",
        "market_cap.lte": MAX_MARKET_CAP,
        "limit": 1000,
    }
    pages = 0
    while url:
        data = polygon_get(url, params, api_key)
        if not data:
            break
        for item in data.get("results", []):
            ticker = item.get("ticker")
            if ticker:
                cap_tickers.add(ticker)
        pages += 1
        next_url = data.get("next_url")
        if not next_url:
            break
        url = next_url
        params = {}
        if pages % 5 == 0:
            print(f"    reference pages: {pages}  "
                  f"cap-filtered: {len(cap_tickers)}")

    print(f"    Market cap < $20B (reference API): {len(cap_tickers)}")

    grouped_url = (
        f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/"
        f"{screen_date}"
    )
    grouped = polygon_get(grouped_url, {"adjusted": "true"}, api_key)
    if not grouped or not grouped.get("results"):
        print("    WARNING: grouped daily unavailable")
        if cap_tickers:
            return sorted(cap_tickers)[:TARGET_COUNT]
        return []

    def _screen_bars(bars, vol_min):
        out = []
        for bar in bars:
            ticker = bar.get("T")
            if not ticker:
                continue
            close = bar.get("c", 0) or 0
            volume = bar.get("v", 0) or 0
            if not (PRICE_MIN <= close <= PRICE_MAX and volume >= vol_min):
                continue
            if cap_tickers and ticker not in cap_tickers:
                continue
            out.append((ticker, close * volume))
        return out

    vol_threshold = MIN_AVG_VOLUME
    passed = _screen_bars(grouped.get("results", []), vol_threshold)

    if not passed and cap_tickers:
        print("    WARNING: cap/reference mismatch — using price/volume only")
        passed = []
        for bar in grouped.get("results", []):
            ticker = bar.get("T")
            close = bar.get("c", 0) or 0
            volume = bar.get("v", 0) or 0
            if (ticker and PRICE_MIN <= close <= PRICE_MAX and
                    volume >= vol_threshold):
                passed.append((ticker, close * volume))

    while len(passed) > MAX_UNIVERSE and vol_threshold < 2_000_000:
        vol_threshold += 50_000
        passed = _screen_bars(grouped.get("results", []), vol_threshold)
        if not passed:
            vol_threshold -= 50_000
            passed = _screen_bars(grouped.get("results", []), vol_threshold)
            break
        print(f"    Tightened volume filter to {vol_threshold:,} "
              f"→ {len(passed)} tickers")

    passed.sort(key=lambda x: x[1], reverse=True)
    universe = [t for t, _ in passed[:TARGET_COUNT]]
    print(f"    Universe: {len(universe)} tickers loaded")
    if len(universe) < 200:
        print(f"    WARNING: universe {len(universe)} < 200 — continuing")
    return universe


def rolling_percentile(series, window=252):
    """Percentile rank of the last value within a rolling window."""
    import numpy as np

    def _pct(x):
        if len(x) < 2:
            return np.nan
        val = x[-1]
        if not np.isfinite(val):
            return np.nan
        prior = x[:-1]
        prior = prior[np.isfinite(prior)]
        if len(prior) == 0:
            return np.nan
        return (prior <= val).mean() * 100.0

    return series.rolling(window, min_periods=60).apply(_pct, raw=True)


def obv_slope_10d(obv, vol_ma20):
    """Linear regression slope of OBV over 10 days, normalized by vol."""
    import numpy as np

    def _slope(x):
        if len(x) < 5:
            return np.nan
        t = np.arange(len(x))
        return np.polyfit(t, x, 1)[0]

    slope = obv.rolling(10, min_periods=5).apply(_slope, raw=True)
    return slope / (vol_ma20 + 1e-9)


def candidate_mask(df):
    """Gap >= 3% and volume >= 2x 20-day average (prior days only)."""
    return (df["gap_pct"] >= GAP_MIN) & (df["volume_ratio_20d"] >= VOL_RATIO_MIN)


def calculate_option_d_labels(df):
    """
    Option D: entry at Day 0 Close (MOC), stop=entry-1xATR_d0, target=entry+2xATR_d0.
    Forward simulation on days +1 through +5 only (no look-ahead on label).
    """
    import numpy as np

    mask = candidate_mask(df)
    targets = np.full(len(df), np.nan)
    closes = df["Close"].values
    highs = df["High"].values
    lows = df["Low"].values
    atrs = df["atr_14_d0"].values

    for i in range(len(df) - 6):
        if not mask.iloc[i]:
            continue
        entry = closes[i]
        atr = atrs[i]
        if not np.isfinite(entry) or not np.isfinite(atr) or atr <= 0:
            targets[i] = 0
            continue
        stop_price = entry - (1.0 * atr)
        target_price = entry + (2.0 * atr)
        label = 0
        for j in range(i + 1, min(i + 6, len(df))):
            if lows[j] <= stop_price:
                label = 0
                break
            if highs[j] >= target_price:
                label = 1
                break
        targets[i] = label

    return targets


def extract_candidates(df):
    """Apply catalyst filter and Option D labels; keep candidate rows only."""
    import numpy as np

    df = df.copy()
    df["target"] = calculate_option_d_labels(df)
    mask = candidate_mask(df) & np.isfinite(df["target"])
    return df[mask].copy()


def get_vix_regime(vix_val):
    """Map VIX level to volatility regime bucket."""
    if vix_val < 15:
        return "LOW_VOL"
    if vix_val <= 25:
        return "NORMAL"
    if vix_val <= 35:
        return "ELEVATED"
    return "EXTREME"


def vix_position_multiplier(vix_regime):
    """Position size multiplier by VIX regime (EXTREME = no new entries)."""
    if vix_regime in ("LOW_VOL", "NORMAL"):
        return 1.0
    if vix_regime == "ELEVATED":
        return 0.5
    return 0.0


def get_vix_data(api_key, start, end, spy_df=None):
    """
    Pull I:VIX daily closes from Polygon. Fall back to SPY vol proxy if unavailable.
    Returns Series indexed by date with VIX-like values.
    """
    import pandas as pd
    import numpy as np

    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{VIX_TICKER}"
        f"/range/1/day/{start}/{end}"
    )
    data = polygon_get(url, {"adjusted": "true", "sort": "asc", "limit": 50000},
                       api_key)
    if data and data.get("results"):
        rows = []
        for bar in data["results"]:
            rows.append({
                "Date": pd.to_datetime(bar["t"], unit="ms"),
                "vix": bar["c"],
            })
        vix_df = pd.DataFrame(rows).set_index("Date").sort_index()
        vix_series = vix_df["vix"].astype(float)
        if len(vix_series) > 50:
            print(f"    VIX data loaded: {len(vix_series)} days (I:VIX)")
            return vix_series

    print("    WARNING: I:VIX unavailable — using SPY volatility proxy")
    if spy_df is None or "Close" not in spy_df.columns:
        return pd.Series(dtype=float)
    proxy = spy_df["Close"].pct_change().rolling(20).std() * 100 * 16
    proxy = proxy.dropna()
    return proxy


def add_rs_vs_spy(combined, spy_returns):
    """Compute rs_vs_spy_20d using SPY series kept outside candidate rows."""
    combined = combined.merge(
        spy_returns, left_index=True, right_index=True, how="left")
    combined["rs_vs_spy_20d"] = (
        combined["return_20d"] - combined["spy_return_20d"]
    )
    return combined


def classify_profile(atr_pct, avg_vol_m):
    if atr_pct < 0.020 and avg_vol_m > 3:
        p = "BLUE_CHIP_DEFENSIVE"
        params = {"master_atr_mult": 2.5,
                  "trail_1": 0.04, "trail_2": 0.07,
                  "trail_3": 0.11, "trail_4": 0.16,
                  "alloc_1": 0.50, "alloc_2": 0.20,
                  "alloc_3": 0.20, "alloc_4": 0.10}
    elif atr_pct < 0.030 and avg_vol_m > 2:
        p = "LARGE_CAP_GROWTH"
        params = {"master_atr_mult": 2.0,
                  "trail_1": 0.05, "trail_2": 0.09,
                  "trail_3": 0.14, "trail_4": 0.20,
                  "alloc_1": 0.50, "alloc_2": 0.20,
                  "alloc_3": 0.20, "alloc_4": 0.10}
    elif atr_pct < 0.040:
        p = "FINANCIAL_CYCLICAL"
        params = {"master_atr_mult": 2.0,
                  "trail_1": 0.06, "trail_2": 0.10,
                  "trail_3": 0.16, "trail_4": 0.22,
                  "alloc_1": 0.50, "alloc_2": 0.20,
                  "alloc_3": 0.20, "alloc_4": 0.10}
    elif atr_pct < 0.060:
        p = "HIGH_GROWTH_TECH"
        params = {"master_atr_mult": 1.5,
                  "trail_1": 0.08, "trail_2": 0.12,
                  "trail_3": 0.18, "trail_4": 0.25,
                  "alloc_1": 0.40, "alloc_2": 0.25,
                  "alloc_3": 0.20, "alloc_4": 0.15}
    else:
        p = "HIGH_VOLATILITY"
        params = {"master_atr_mult": 1.5,
                  "trail_1": 0.10, "trail_2": 0.15,
                  "trail_3": 0.22, "trail_4": 0.30,
                  "alloc_1": 0.40, "alloc_2": 0.25,
                  "alloc_3": 0.20, "alloc_4": 0.15}
    return p, params


def score_candidate(row, prob):
    score = 0.0
    if prob >= 0.85:   score += 30
    elif prob >= 0.75: score += 24
    elif prob >= 0.70: score += 18
    elif prob >= 0.65: score += 12
    else:              score += 6

    m = 0
    rsi = row.get("rsi_14", 50)
    if 50 <= rsi <= 65:                     m += 10
    elif 45 <= rsi < 50 or 65 < rsi <= 70: m += 6
    elif rsi > 70:                          m += 2
    macd_h = row.get("macd_hist", 0)
    if macd_h > 0:      m += 8
    elif macd_h > -0.1: m += 4
    s20 = row.get("close_vs_sma20", 0)
    s50 = row.get("close_vs_sma50", 0)
    if s20 > 0 and s50 > 0: m += 7
    elif s20 > 0:            m += 4
    score += min(m, 25)

    vr = row.get("volume_ratio_20d", 1.0)
    if vr >= 2.5:   score += 20
    elif vr >= 2.0: score += 16
    elif vr >= 1.5: score += 12
    elif vr >= 1.2: score += 8
    elif vr >= 1.0: score += 4

    v10 = row.get("volatility_10d", 0.02) * 100
    if 1.0 <= v10 <= 2.5:   score += 15
    elif 0.7 <= v10 < 1.0:  score += 10
    elif 2.5 < v10 <= 4.0:  score += 8
    elif v10 > 4.0:          score += 3
    else:                    score += 5

    bb = row.get("bb_position", 0.5)
    if 0.5 <= bb <= 0.8:   score += 10
    elif 0.4 <= bb < 0.5:  score += 7
    elif 0.8 < bb <= 0.9:  score += 5
    elif bb > 0.9:         score += 2
    else:                  score += 3

    atr_pct = row.get("atr_14", row["Close"] * 0.02) / row["Close"]
    avg_vol = row.get("volume_sma_20", 1e6) / 1e6
    profile, _ = classify_profile(atr_pct, avg_vol)
    adj = {"BLUE_CHIP_DEFENSIVE": +15,
           "LARGE_CAP_GROWTH":    +10,
           "FINANCIAL_CYCLICAL":    0,
           "HIGH_GROWTH_TECH":    -15,
           "HIGH_VOLATILITY":     -25}
    score += adj.get(profile, 0)
    return round(score, 1), profile


def process_stock(ticker, start, end, api_key):
    """Pull daily OHLCV and compute 19 features (14 pre-gap + 5 gap-day)."""
    try:
        import time
        import requests
        import numpy as np
        import pandas as pd
        import pandas_ta as ta

        url = (
            f"https://api.polygon.io/v2/aggs/ticker/{ticker}"
            f"/range/1/day/{start}/{end}"
        )
        params = {
            "adjusted": "true",
            "sort": "asc",
            "limit": 50000,
            "apiKey": api_key,
        }
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not data.get("results"):
            return None

        df = pd.DataFrame(data["results"])
        df["Date"] = pd.to_datetime(df["t"], unit="ms")
        df.set_index("Date", inplace=True)
        df.rename(columns={
            "o": "Open", "h": "High", "l": "Low",
            "c": "Close", "v": "Volume",
        }, inplace=True)
        df = df[["Open", "High", "Low", "Close", "Volume"]]

        if len(df) < 100:
            return None
        if ticker != REGIME_TICKER and df["Close"].iloc[-1] < PRICE_MIN:
            return None
        if ticker != REGIME_TICKER and df["Volume"].mean() < MIN_AVG_VOLUME:
            return None

        atr_raw = ta.atr(df["High"], df["Low"], df["Close"], length=14)
        bbands = ta.bbands(df["Close"], length=20, std=2)
        bb_upper = bbands.iloc[:, 0]
        bb_mid = bbands.iloc[:, 1]
        bb_lower = bbands.iloc[:, 2]
        bbw = (bb_upper - bb_lower) / bb_mid.replace(0, np.nan)
        obv = ta.obv(df["Close"], df["Volume"])
        sma20 = ta.sma(df["Close"], length=20)
        vol_ma20 = df["Volume"].rolling(20).mean().shift(1)  # SHIFT APPLIED

        # F01 gap_pct — available at market open (signal-day filter column)
        df["gap_pct"] = (
            (df["Open"] - df["Close"].shift(1)) / df["Close"].shift(1)
        )
        # F02 volume_ratio_20d — today's volume vs prior 20d average
        df["volume_ratio_20d"] = df["Volume"] / vol_ma20  # SHIFT APPLIED

        # F03 bbw_percentile_52w — prior-day BB width percentile
        bbw_prior = bbw.shift(1)  # SHIFT APPLIED
        df["bbw_percentile_52w"] = rolling_percentile(bbw_prior, 252)

        # F04 ttm_squeeze_active — prior-day squeeze flag
        sqz = ta.squeeze(
            df["High"], df["Low"], df["Close"],
            bb_length=20, bb_std=2, kc_length=20, kc_scalar=1.5,
        )
        if sqz is not None and len(sqz.columns) > 0:
            sqz_col = [c for c in sqz.columns if "SQZ" in c.upper()][0]
            sqz_on = (sqz[sqz_col] != 0).astype(int)
        else:
            import pandas as pd
            sqz_on = pd.Series(0, index=df.index)
        df["ttm_squeeze_active"] = sqz_on.shift(1)  # SHIFT APPLIED

        # F05 atr_14_pct — prior-day normalized ATR
        df["atr_14_pct"] = atr_raw.shift(1) / df["Close"].shift(1)  # SHIFT APPLIED

        # F06 price_location_20d — prior-day position in 20d range
        hi20 = df["High"].rolling(20).max().shift(1)  # SHIFT APPLIED
        lo20 = df["Low"].rolling(20).min().shift(1)   # SHIFT APPLIED
        df["price_location_20d"] = (
            (df["Close"].shift(1) - lo20) / (hi20 - lo20 + 1e-9)
        )

        # F07 obv_slope_10d — prior-day OBV trend normalized by volume
        df["obv_slope_10d"] = obv_slope_10d(obv, vol_ma20).shift(1)  # SHIFT APPLIED

        # F08 up_down_vol_ratio — prior 10d up/down volume means
        up_day = df["Close"] > df["Open"]
        vol_up = (
            df["Volume"].where(up_day)
            .rolling(10, min_periods=1).mean().shift(1)
        )  # SHIFT APPLIED
        vol_dn = (
            df["Volume"].where(~up_day)
            .rolling(10, min_periods=1).mean().shift(1)
        )  # SHIFT APPLIED
        ratio = vol_up / vol_dn.replace(0, np.nan)
        df["up_down_vol_ratio"] = (
            ratio.replace([np.inf, -np.inf], np.nan).fillna(1.0)
        )

        # F09 rs_vs_spy_20d — filled after SPY merge
        df["return_20d"] = df["Close"].pct_change(20).shift(1)  # SHIFT APPLIED

        # F10 close_vs_range_pct — prior-day candle position
        df["close_vs_range_pct"] = (
            (df["Close"] - df["Low"]) / (df["High"] - df["Low"] + 1e-9)
        ).shift(1)  # SHIFT APPLIED

        # F11 dist_from_20ma — prior-day distance from SMA20
        df["dist_from_20ma"] = (
            (df["Close"] - sma20) / sma20
        ).shift(1)  # SHIFT APPLIED

        # F12 rsi_14 — prior-day RSI
        df["rsi_14"] = ta.rsi(df["Close"], length=14).shift(1)  # SHIFT APPLIED

        # F13 base_tightness — prior-day coefficient of variation
        df["base_tightness"] = (
            df["Close"].rolling(20).std() / df["Close"].rolling(20).mean()
        ).shift(1)  # SHIFT APPLIED

        # F14 volume_thrust_prior — prior-day thrust * volume ratio
        thrust = (df["Close"] - df["Open"]) / (df["Open"] + 1e-9)
        df["volume_thrust_prior"] = (
            thrust * df["volume_ratio_20d"]
        ).shift(1)  # SHIFT APPLIED

        # F15-F19 gap-day features — known at gap-day close, entry is Day 0 Close (MOC)
        hl_range = df["High"] - df["Low"] + 1e-9
        df["close_vs_range_d0"] = (df["Close"] - df["Low"]) / hl_range
        df["volume_ratio_d0"] = df["Volume"] / vol_ma20
        df["upper_wick_ratio_d0"] = (df["High"] - df["Close"]) / hl_range
        df["gap_pct_d0"] = df["gap_pct"]
        df["body_ratio_d0"] = (df["Close"] - df["Open"]).abs() / hl_range

        # Columns needed for simulation / scoring (not model features)
        df["atr_14_d0"] = atr_raw  # Day 0 ATR for Option D labels + MOC entry stops
        df["atr_14"] = atr_raw.shift(1)  # SHIFT APPLIED — legacy/scoring helper
        df["volume_sma_20"] = df["Volume"].rolling(20).mean()
        df["return_1d"] = df["Close"].pct_change(1)
        df["volatility_10d"] = df["return_1d"].rolling(10).std().shift(1)
        macd = ta.macd(df["Close"], fast=12, slow=26, signal=9)
        df["macd_hist"] = macd["MACDh_12_26_9"].shift(1)
        df["close_vs_sma20"] = ((df["Close"] - sma20) / sma20).shift(1)
        df["close_vs_sma50"] = (
            (df["Close"] - ta.sma(df["Close"], 50)) / ta.sma(df["Close"], 50)
        ).shift(1)
        df["bb_position"] = (
            (df["Close"] - bb_lower) / (bb_upper - bb_lower + 1e-9)
        ).shift(1)

        df["ticker"] = ticker
        time.sleep(POLYGON_SLEEP)
        return df if len(df) >= 100 else None
    except Exception:
        return None


class BracketPosition:
    def __init__(self, ticker, entry_price, entry_date,
                 position_size, params):
        self.ticker        = ticker
        self.entry_price   = entry_price
        self.entry_date    = entry_date
        self.position_size = position_size
        atr_val  = params.get("atr_at_entry", entry_price*0.02)
        atr_pct  = params["master_atr_mult"]*(atr_val/entry_price)
        self.master_stop = entry_price*(1-atr_pct)
        self.slices = {
            1: {"trail": params["trail_1"], "alloc": params["alloc_1"],
                "active": False, "high": entry_price,
                "closed": False, "days_no_high": 0},
            2: {"trail": params["trail_2"], "alloc": params["alloc_2"],
                "active": False, "high": entry_price,
                "closed": False, "days_no_high": 0},
            3: {"trail": params["trail_3"], "alloc": params["alloc_3"],
                "active": False, "high": entry_price,
                "closed": False, "days_no_high": 0},
            4: {"trail": params["trail_4"], "alloc": params["alloc_4"],
                "active": False, "high": entry_price,
                "closed": False, "days_no_high": 0},
        }
        self.realized_pnl    = 0.0
        self.is_bonus_runner = False
        self.fully_closed    = False
        self.exit_log        = []

    def is_s4_runner(self):
        open_sids = [sid for sid, s in self.slices.items()
                     if not s["closed"]]
        return open_sids == [4]

    def deployed_capital(self):
        return sum(self.position_size * s["alloc"]
                   for s in self.slices.values()
                   if not s["closed"])

    def open_slice_count(self):
        return sum(1 for s in self.slices.values() if not s["closed"])

    def update(self, current_price, current_date):
        released = 0.0
        for sid, s in self.slices.items():
            if s["closed"]:
                continue
            slice_cap = self.position_size * s["alloc"]
            if current_price > s["high"]:
                s["high"] = current_price
                s["days_no_high"] = 0
            else:
                s["days_no_high"] += 1
            trail = s["trail"]
            if sid == 4 and s["days_no_high"] >= S4_RUNNER_DAYS:
                trail = min(trail, 0.10)
            trail_level = s["high"] * (1 - trail)
            if not s["active"]:
                if trail_level > self.entry_price:
                    s["active"] = True
            if not s["active"]:
                if current_price <= self.master_stop:
                    gross = (current_price-self.entry_price)/self.entry_price
                    pnl   = slice_cap * (gross - COST_PER_TRADE)
                    self.realized_pnl += pnl
                    s["closed"] = True
                    released += slice_cap + pnl
                    self.exit_log.append({"stop":"MASTER","sid":sid,
                        "date":current_date,"pnl":pnl})
                continue
            if current_price <= trail_level:
                gross = (current_price-self.entry_price)/self.entry_price
                pnl   = slice_cap * (gross - COST_PER_TRADE)
                self.realized_pnl += pnl
                s["closed"] = True
                released += slice_cap + pnl
                self.exit_log.append({"stop":f"S{sid}","sid":sid,
                    "date":current_date,"pnl":pnl})
        self.is_bonus_runner = self.is_s4_runner()
        if all(s["closed"] for s in self.slices.values()):
            self.fully_closed = True
        return released


def get_regime(current_date, price_pivot):
    try:
        spy_prices = price_pivot["SPY"].dropna()
        spy_prices = spy_prices[spy_prices.index <= current_date]
        if len(spy_prices) < 50:
            return "BULL"
        spy_current = spy_prices.iloc[-1]
        spy_sma50   = spy_prices.iloc[-50:].mean()
        if spy_current >= spy_sma50:
            return "BULL"
        else:
            return "BEAR"
    except Exception:
        return "BULL"



def _import_ticker_profiler():
    """Import mounted candidates/ticker_profiler.py (Modal /root or repo)."""
    import sys
    from pathlib import Path

    for root in ("/root", str(Path(__file__).resolve().parents[2] / "candidates")):
        if root not in sys.path:
            sys.path.insert(0, root)
    # Prefer /root mount on Modal
    if "/root" not in sys.path:
        sys.path.insert(0, "/root")
    import ticker_profiler as tp
    return tp


def profile_cache_path(ticker, as_of_str, cache_dir=PROFILE_CACHE_DIR):
    """Stable cache file path for (ticker, as_of) profile JSON."""
    from pathlib import Path

    safe = f"{ticker.upper()}_{as_of_str[:10]}.json"
    return Path(cache_dir) / safe


def load_or_build_profile(ticker, as_of_date, api_key, cache_dir=PROFILE_CACHE_DIR):
    """
    Build ticker profile as_of signal day with disk cache.

    WHY cache: build_ticker_profile pulls 1-min bars per analog — unaffordable
    to recompute across WF windows. Key = ticker + as_of (no look-ahead).
    """
    import json
    from pathlib import Path

    tp = _import_ticker_profiler()
    as_of_str = str(as_of_date)[:10]
    path = profile_cache_path(ticker, as_of_str, cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    profile = tp.build_ticker_profile(
        ticker, as_of_date=as_of_str, api_key=api_key,
    )
    # Drop bulky per-analog bar dumps if present — keep rank fields
    slim = {k: v for k, v in profile.items() if k not in ("per_analog", "analogs")}
    if "analog_finder" in slim and isinstance(slim["analog_finder"], dict):
        af = dict(slim["analog_finder"])
        af.pop("analogs", None)
        slim["analog_finder"] = af
    try:
        path.write_text(json.dumps(slim, default=str), encoding="utf-8")
    except Exception:
        pass
    return slim


def extract_rank_fields(profile):
    """
    Map profiler JSON → eligibility + R:R rank fields.

    Primary rank key = reward_risk (MFE p50 target / safe-max stop).
    Does NOT use ScoreCard candle features.
    """
    if not profile:
        return {
            "prof_eligible": False,
            "reward_risk": -1.0,
            "confidence": "INSUFFICIENT",
            "analog_count": 0,
            "mfe_p50": None,
            "mae_safe": None,
        }
    conf = str(profile.get("confidence") or "INSUFFICIENT")
    meaningful = bool(profile.get("stats_meaningful", False))
    if conf == "INSUFFICIENT" or not meaningful:
        return {
            "prof_eligible": False,
            "reward_risk": -1.0,
            "confidence": conf,
            "analog_count": int(profile.get("analog_count") or 0),
            "mfe_p50": None,
            "mae_safe": None,
        }
    bracket = profile.get("bracket") or {}
    outcomes = profile.get("outcomes") or {}
    rr = outcomes.get("reward_risk")
    if rr is None:
        target = float(bracket.get("target_pct") or 0.0)
        safe = float(bracket.get("safe_max_stop_pct") or 0.0)
        rr = (target / safe) if safe > 0 else 0.0
    mfe = (profile.get("percentiles") or {}).get("mfe") or {}
    return {
        "prof_eligible": True,
        "reward_risk": float(rr) if rr is not None else 0.0,
        "confidence": conf,
        "analog_count": int(profile.get("analog_count") or 0),
        "mfe_p50": mfe.get("p50"),
        "mae_safe": bracket.get("safe_max_stop_pct"),
    }


def attach_profiler_ranks(df, api_key, cache_dir=PROFILE_CACHE_DIR, progress_every=25):
    """Attach profiler R:R columns to candidate rows (cached per ticker+as_of)."""
    import time

    rows = []
    t0 = time.time()
    n = len(df)
    for i, (idx, row) in enumerate(df.iterrows()):
        ticker = str(row.get("ticker") or "").upper()
        as_of = idx.date() if hasattr(idx, "date") else str(idx)[:10]
        try:
            prof = load_or_build_profile(ticker, as_of, api_key, cache_dir)
            fields = extract_rank_fields(prof)
        except Exception as exc:
            fields = {
                "prof_eligible": False,
                "reward_risk": -1.0,
                "confidence": "ERROR",
                "analog_count": 0,
                "mfe_p50": None,
                "mae_safe": None,
                "prof_error": str(exc)[:120],
            }
        rows.append(fields)
        if (i + 1) % progress_every == 0:
            print(f"  profiles {i + 1}/{n} [{time.time() - t0:.0f}s]")
    out = df.copy()
    for key in ("prof_eligible", "reward_risk", "confidence", "analog_count", "mfe_p50", "mae_safe"):
        out[key] = [r.get(key) for r in rows]
    return out


def apply_pilot_universe(tickers):
    """Shrink screener list to pilot size (v1: PILOT_MAX_TICKERS=50)."""
    if PILOT_MAX_TICKERS is None:
        return tickers
    return tickers[: int(PILOT_MAX_TICKERS)]


def run_simulation(test_df, price_pivot, vix_series,
                   label="", trade_universe=None):
    """Simulate bracket trades; entry at Day 0 Close (MOC) on signal day.

    SELECTOR=PROFILER_RR: hard-gate on profiler eligibility (INSUFFICIENT policy),
    optional RR_MIN_GATE, then rank by reward_risk descending (no ScoreCard).
    """
    import pandas as pd
    import numpy as np

    if trade_universe is None:
        trade_universe = TRADE_UNIVERSE

    test_df = test_df.copy()
    scores, profiles = [], []
    for _, row in test_df.iterrows():
        s, prof = score_candidate(row.to_dict(), row["prob"])
        scores.append(s)
        profiles.append(prof)
    test_df["score"]   = scores
    test_df["profile"] = profiles

    day_lookup = {}
    for date, grp in test_df.groupby(level=0):
        day_lookup[date] = grp

    pool            = STARTING_CAP
    active          = []
    runners         = []
    all_closed      = []
    equity_curve    = []
    prev_day        = None
    day_trade_count = 0
    trades_taken    = 0
    trading_days    = 0
    profile_counts  = {}
    bull_days       = 0
    bear_days       = 0
    n_signals       = 0
    vix_trade_counts = {
        "LOW_VOL": 0, "NORMAL": 0, "ELEVATED": 0, "EXTREME": 0}
    vix_pnl = {k: 0.0 for k in vix_trade_counts}

    all_dates = sorted(price_pivot.index.unique())

    for current_date in all_dates:
        regime = get_regime(current_date, price_pivot)
        max_pos_today = MAX_POSITIONS_BULL if regime == "BULL" else MAX_POSITIONS_BEAR
        # SELECTOR=PROFILER_RR — eligibility + R:R rank (no ScoreCard)
        vix_today = float(vix_series.get(current_date, 20.0))
        vix_regime_today = get_vix_regime(vix_today)
        vix_mult = vix_position_multiplier(vix_regime_today)

        cur_day = current_date.date()
        if cur_day != prev_day:
            day_trade_count = 0
            trading_days += 1
            if regime == "BULL":
                bull_days += 1
            else:
                bear_days += 1
            prev_day = cur_day

        still_active = []
        for pos in active:
            try:
                px = float(price_pivot.loc[current_date, pos.ticker])
            except Exception:
                still_active.append(pos)
                continue
            released = pos.update(px, current_date)
            pool += released
            if not np.isfinite(pool):
                pool = 0.0
            pool = max(0.0, pool)
            if pos.fully_closed:
                vix_key = getattr(pos, "vix_regime", "NORMAL")
                vix_pnl[vix_key] = vix_pnl.get(vix_key, 0.0) + pos.realized_pnl
                all_closed.append(pos)
            elif pos.is_s4_runner():
                runners.append(pos)
            else:
                still_active.append(pos)
        active = still_active

        still_running = []
        for pos in runners:
            try:
                px = float(price_pivot.loc[current_date, pos.ticker])
            except Exception:
                still_running.append(pos)
                continue
            released = pos.update(px, current_date)
            pool += released
            if not np.isfinite(pool):
                pool = 0.0
            pool = max(0.0, pool)
            if pos.fully_closed:
                vix_key = getattr(pos, "vix_regime", "NORMAL")
                vix_pnl[vix_key] = vix_pnl.get(vix_key, 0.0) + pos.realized_pnl
                all_closed.append(pos)
            else:
                still_running.append(pos)
        runners = still_running

        significant_positions = [p for p in active if p.open_slice_count() > 1]
        runner_tickers = {p.ticker for p in runners}
        active_tickers = {p.ticker for p in significant_positions}
        available_slots = max_pos_today - len(significant_positions)

        signal_date = current_date
        if (signal_date in day_lookup and
                available_slots > 0 and
                day_trade_count < MAX_TRADES_DAY and
                vix_mult > 0):

            candidates = day_lookup[signal_date]
            candidates = candidates[
                (candidates["gap_pct"] >= GAP_MIN) &
                (candidates["volume_ratio_20d"] >= VOL_RATIO_MIN)
            ]
            # --- EXP-0020 profiler ranker (no ScoreCard candle rules) ---
            if "reward_risk" not in candidates.columns:
                raise ValueError("test_df missing reward_risk — call attach_profiler_ranks")
            if INSUFFICIENT_POLICY == "SKIP":
                candidates = candidates[candidates["prof_eligible"] == True]
            elif INSUFFICIENT_POLICY != "DEPRIORITIZE":
                raise ValueError(f"Unknown INSUFFICIENT_POLICY={INSUFFICIENT_POLICY}")
            # DEPRIORITIZE: keep rows; reward_risk=-1 sorts them last
            if RR_MIN_GATE is not None:
                candidates = candidates[
                    candidates["reward_risk"] >= float(RR_MIN_GATE)
                ]
            candidates = candidates.sort_values(
                ["reward_risk", "analog_count", "ticker"],
                ascending=[False, False, True],
            )
            n_signals += len(candidates)

            for _, row in candidates.iterrows():
                if available_slots <= 0:
                    break
                if day_trade_count >= MAX_TRADES_DAY:
                    break
                if len(significant_positions) >= max_pos_today:
                    break

                ticker = row.get("ticker")
                if ticker not in trade_universe:
                    continue
                if ticker in active_tickers or ticker in runner_tickers:
                    continue

                try:
                    entry_px = float(price_pivot.loc[current_date, ticker])
                except Exception:
                    continue
                if not np.isfinite(entry_px) or entry_px <= 0:
                    continue

                base_size = pool * POSITION_PCT * vix_mult
                pos_size = max(MIN_POSITION, min(MAX_POSITION, base_size))
                if pool < pos_size or pos_size < MIN_POSITION:
                    continue

                atr_val = float(row.get(
                    "atr_14_d0", row.get("atr_14", entry_px * 0.02)))
                atr_pct = atr_val / entry_px
                avg_vol = float(row.get("volume_sma_20", 1e6)) / 1e6
                _, params = classify_profile(atr_pct, avg_vol)
                params["atr_at_entry"] = atr_val

                pos = BracketPosition(
                    ticker=ticker,
                    entry_price=entry_px,
                    entry_date=current_date,
                    position_size=pos_size,
                    params=params)
                pos.vix_regime = vix_regime_today

                pool -= pos_size
                active.append(pos)
                active_tickers.add(ticker)
                available_slots -= 1
                day_trade_count += 1
                trades_taken += 1
                vix_trade_counts[vix_regime_today] += 1

                prof = row.get("profile", "UNKNOWN")
                profile_counts[prof] = profile_counts.get(prof, 0) + 1

        deployed = (
            sum(p.deployed_capital() for p in active) +
            sum(p.deployed_capital() for p in runners))
        equity_curve.append({
            "date": current_date,
            "equity": pool + deployed,
            "regime": regime})

    if all_dates:
        last_date = all_dates[-1]
        for pos in active + runners:
            for sid, s in pos.slices.items():
                if not s["closed"]:
                    try:
                        lp = float(price_pivot.loc[last_date, pos.ticker])
                    except Exception:
                        lp = pos.entry_price
                    gross = (lp - pos.entry_price) / pos.entry_price
                    pnl   = pos.position_size * s["alloc"] * (gross - COST_PER_TRADE)
                    pos.realized_pnl += pnl
                    s["closed"] = True
                    pool += pos.position_size * s["alloc"] + pnl
            vix_key = getattr(pos, "vix_regime", "NORMAL")
            vix_pnl[vix_key] = vix_pnl.get(vix_key, 0.0) + pos.realized_pnl
            pos.fully_closed = True
            all_closed.append(pos)

    deployed_end = (
        sum(p.deployed_capital() for p in active) +
        sum(p.deployed_capital() for p in runners))
    final_equity = pool + deployed_end
    if not np.isfinite(final_equity):
        final_equity = pool if np.isfinite(pool) else STARTING_CAP
    if equity_curve:
        equity_curve[-1]["equity"] = final_equity

    if not equity_curve:
        return None
    eq_df = pd.DataFrame(equity_curve).set_index("date")
    eq_df["ret"] = eq_df["equity"].pct_change().fillna(0)

    final_eq = eq_df["equity"].iloc[-1]
    if not np.isfinite(final_eq):
        final_eq = final_equity
    total_ret = (final_eq - STARTING_CAP) / STARTING_CAP
    total_pnl = final_eq - STARTING_CAP
    sharpe = (eq_df["ret"].mean() / eq_df["ret"].std() * np.sqrt(252)
              if eq_df["ret"].std() > 0 else 0.0)
    roll_max = eq_df["equity"].expanding().max()
    drawdown = (eq_df["equity"] - roll_max) / roll_max
    max_dd = drawdown.min()

    bull_rets = eq_df.loc[eq_df["regime"] == "BULL", "ret"]
    bear_rets = eq_df.loc[eq_df["regime"] == "BEAR", "ret"]
    bull_perf = float((1 + bull_rets).prod() - 1) if len(bull_rets) > 0 else 0.0
    bear_perf = float((1 + bear_rets).prod() - 1) if len(bear_rets) > 0 else 0.0
    bull_sharpe = (bull_rets.mean() / bull_rets.std() * np.sqrt(252)
                   if len(bull_rets) > 1 and bull_rets.std() > 0 else 0.0)
    bear_sharpe = (bear_rets.mean() / bear_rets.std() * np.sqrt(252)
                   if len(bear_rets) > 1 and bear_rets.std() > 0 else 0.0)

    n_trades = len(all_closed)
    win_trades = sum(1 for p in all_closed if p.realized_pnl > 0)
    win_rate = win_trades / n_trades if n_trades > 0 else 0
    trades_per_day = trades_taken / trading_days if trading_days > 0 else 0

    bh_list = []
    for ticker in trade_universe:
        try:
            col = price_pivot[ticker].dropna()
            if len(col) > 1:
                bh_list.append((col.iloc[-1] - col.iloc[0]) / col.iloc[0])
        except Exception:
            pass
    buy_hold = float(np.mean(bh_list)) if bh_list else 0.0

    return {
        "label": label,
        "total_ret": total_ret,
        "total_pnl": total_pnl,
        "buy_hold": buy_hold,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "n_trades": n_trades,
        "win_rate": win_rate,
        "trades_taken": trades_taken,
        "trades_per_day": trades_per_day,
        "trading_days": trading_days,
        "profile_counts": profile_counts,
        "all_closed": all_closed,
        "equity_curve": eq_df,
        "n_signals": n_signals,
        "final_pool": pool,
        "bull_days": bull_days,
        "bear_days": bear_days,
        "bull_perf": bull_perf,
        "bear_perf": bear_perf,
        "bull_sharpe": bull_sharpe,
        "bear_sharpe": bear_sharpe,
        "vix_trade_counts": vix_trade_counts,
        "vix_pnl": vix_pnl,
    }


def evaluate_gates(bt, wf_pass_count, wf_total, mc_p):
    """Phase-2+ hard gates (Precision@0.60 N/A). FAIL loudly."""
    gates = {
        "sharpe_ge_1_50": bt["sharpe"] >= SHARPE_GATE,
        "max_dd_ge_neg_15": bt["max_dd"] >= MAX_DD_GATE,
        "positive_return": bt["total_ret"] > 0,
        "beats_buy_hold": bt["total_ret"] > bt["buy_hold"],
        "wf_ge_3_4": wf_pass_count >= WF_PASS_MIN,
        "mc_p_lt_0_05": (mc_p is not None) and (mc_p < MC_P_GATE),
    }
    passed = sum(gates.values())
    if passed == len(gates):
        status = "PASS"
    elif passed >= 4:
        status = "INVESTIGATE"
    else:
        status = "FAIL"
    return gates, passed, status


def run_monte_carlo(all_closed, n_sims=MC_SIMS, seed=42):
    """5000-bootstrap Sharpe test on closed-trade P&L."""
    import numpy as np

    real_rets = np.array([p.realized_pnl for p in all_closed], dtype=float)
    if len(real_rets) < 5 or real_rets.std() <= 0:
        return {"ok": False, "p_value": None, "pct": None, "real_sharpe": None, "pass": False}
    real_sharpe = real_rets.mean() / real_rets.std() * np.sqrt(252)
    rng = np.random.default_rng(seed)
    sims = []
    for _ in range(n_sims):
        s = rng.choice(real_rets, size=len(real_rets), replace=True)
        sims.append(s.mean() / s.std() * np.sqrt(252) if s.std() > 0 else 0.0)
    sims = np.array(sims)
    pct = float((sims < real_sharpe).mean() * 100.0)
    p_val = 1.0 - pct / 100.0
    return {
        "ok": True,
        "p_value": p_val,
        "pct": pct,
        "real_sharpe": float(real_sharpe),
        "pass": p_val < MC_P_GATE,
    }


@app.function(
    image=image,
    timeout=7200,
    memory=8192,
    secrets=[polygon_secret],
    volumes={PROFILE_CACHE_DIR: profile_volume},
)
def run_exp020():
    """Profiler R:R ranker sim — replaces ScoreCard; v1 director decisions locked."""
    import pandas as pd
    import numpy as np
    import warnings
    import time

    warnings.filterwarnings("ignore")
    api_key = os.environ["POLYGON_API_KEY"]

    global TRADE_UNIVERSE
    TRADE_UNIVERSE = build_dynamic_universe(api_key)
    TRADE_UNIVERSE = apply_pilot_universe(TRADE_UNIVERSE)
    download_universe = [REGIME_TICKER] + TRADE_UNIVERSE

    t0 = time.time()
    print("=" * 55)
    print("EXP-0020: Ticker-Profiler R:R Ranker | replaces ScoreCard")
    print(f"Universe : {len(TRADE_UNIVERSE)} stocks + SPY "
          f"(pilot_cap={PILOT_MAX_TICKERS})")
    print(f"Filter   : gap>={GAP_MIN:.0%} AND vol_ratio>={VOL_RATIO_MIN:.0f}x")
    print(f"Entry    : {ENTRY_CONVENTION} (v1 locked; morning/~09:33 follow-up later)")
    print("Label    : Option D (2R before stop within 5 days)")
    print(f"Selector : {SELECTOR} | INSUFFICIENT={INSUFFICIENT_POLICY} | "
          f"RR_MIN_GATE={RR_MIN_GATE} | PM={USE_PREMARKET_FEATURES}")
    print("Banned   : ScoreCard body_ratio_d0 / close_vs_range_d0 rules")
    print(f"Cache    : Modal Volume qalpha-exp020-profiles → {PROFILE_CACHE_DIR}")
    print(f"Baselines: 0017 Sharpe={BASELINE_0017['sharpe']} | "
          f"0018 Sharpe={BASELINE_0018['sharpe']}")
    print("Risk note: Lab oos_r2 MFE p50 was negative — portfolio gates decide")
    print(f"Pilot     : {SIM_START} to {SIM_END}")
    print("=" * 55)
    print(f"  Pilot: first {PILOT_MAX_TICKERS} screener names; "
          f"full {TARGET_COUNT} is Phase-2 after pilot PASS/FAIL.")

    print("\nSTEP 1: Downloading daily universe from Polygon.io...")
    all_candidates = []
    all_prices = []
    total_rows = 0
    spy_returns = None
    spy_ohlc = None

    for i, ticker in enumerate(download_universe):
        raw = process_stock(ticker, DATA_START, DATA_END, api_key)
        if raw is None:
            continue
        total_rows += len(raw)
        price_slice = raw[["Close", "ticker"]].copy()
        all_prices.append(price_slice)
        if ticker == REGIME_TICKER:
            spy_ohlc = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
            spy_returns = raw[["return_20d"]].rename(
                columns={"return_20d": "spy_return_20d"}
            )
        else:
            cand = extract_candidates(raw)
            if len(cand) > 0:
                all_candidates.append(cand)
        if (i + 1) % 25 == 0:
            print(
                f"  {i + 1}/{len(download_universe)} tickers "
                f"[{time.time() - t0:.0f}s]"
            )

    if not all_candidates:
        return {"status": "error", "message": "No candidate rows after filter"}

    combined = pd.concat(all_candidates)
    combined.sort_index(inplace=True)
    if spy_returns is None:
        return {"status": "error", "message": "SPY data missing for rs_vs_spy"}
    combined = add_rs_vs_spy(combined, spy_returns)
    for col in FEATURE_COLUMNS:
        if combined[col].isna().any():
            combined[col] = combined[col].fillna(combined[col].median())
    combined.dropna(subset=FEATURE_COLUMNS + ["target"], inplace=True)

    n_candidates = len(combined)
    labels = combined["target"]
    base_rate = float(labels.mean()) if n_candidates else 0.0
    print(f"\n  Candidates: {n_candidates:,}  Base rate: {base_rate:.1%}")
    if base_rate > 0.50:
        return {"status": "error", "message": "Base rate > 50% — look-ahead suspected"}

    # Dummy prob for score_candidate sizing path (not used for selection)
    combined["prob"] = 1.0

    print("\nSTEP 2: Attaching profiler R:R ranks (Modal Volume cache)...")
    print(f"  Cache dir: {PROFILE_CACHE_DIR}")
    combined = attach_profiler_ranks(combined, api_key)
    try:
        profile_volume.commit()
    except Exception as exc:
        print(f"  WARNING: profile_volume.commit failed: {exc}")
    n_elig = int(combined["prof_eligible"].sum())
    elig = combined[combined["prof_eligible"] == True]
    n_rr_thin = int((elig["reward_risk"] < RR_WARN_REPORT).sum()) if len(elig) else 0
    rr_thin_share = (n_rr_thin / len(elig)) if len(elig) else 0.0
    print(f"  Profiler-eligible rows: {n_elig}/{n_candidates}")
    print(
        f"  Eligible with R:R < {RR_WARN_REPORT}: {n_rr_thin}/{len(elig)} "
        f"({rr_thin_share:.1%}) — informational (not a hard gate)"
    )
    test_df = combined[
        (combined.index >= pd.Timestamp(SIM_START))
        & (combined.index <= pd.Timestamp(SIM_END))
    ].copy()

    vix_series = get_vix_data(api_key, DATA_START, DATA_END, spy_df=spy_ohlc)

    print("\nSTEP 3: Price pivot (MOC Close)...")
    prices_all = pd.concat(all_prices)
    prices = prices_all[
        (prices_all.index >= pd.Timestamp(SIM_START))
        & (prices_all.index <= pd.Timestamp(SIM_END))
    ]
    price_pivot = prices.pivot_table(
        index=prices.index, columns="ticker", values="Close", aggfunc="last"
    )

    print("\nSTEP 4: Simulation (profiler R:R capacity rank)...")
    bt = run_simulation(
        test_df, price_pivot, vix_series,
        label="OOS 2023-2024", trade_universe=TRADE_UNIVERSE,
    )
    if bt is None:
        return {"status": "error", "message": "Simulation failed"}

    print("\nSTEP 5: Walk forward (4 windows)...")
    wf_windows = [
        ("2019-01-01", "2021-01-01", "2021-01-01", "2022-01-01", "2021"),
        ("2019-01-01", "2022-01-01", "2022-01-01", "2023-01-01", "2022"),
        ("2019-01-01", "2023-01-01", "2023-01-01", "2024-01-01", "2023"),
        ("2019-01-01", "2024-01-01", "2024-01-01", "2025-01-01", "2024"),
    ]
    wf_results = []
    for ts, te, vs, ve, label in wf_windows:
        train_w = combined[
            (combined.index >= pd.Timestamp(ts)) & (combined.index < pd.Timestamp(te))
        ]
        if len(train_w) < 200:
            continue
        test_w = combined[
            (combined.index >= pd.Timestamp(vs)) & (combined.index < pd.Timestamp(ve))
        ].copy()
        if len(test_w) < 20:
            continue
        price_slice = prices_all[
            (prices_all.index >= pd.Timestamp(vs))
            & (prices_all.index < pd.Timestamp(ve))
        ]
        pp_w = price_slice.pivot_table(
            index=price_slice.index, columns="ticker", values="Close", aggfunc="last"
        )
        res = run_simulation(
            test_w, pp_w, vix_series, label=label, trade_universe=TRADE_UNIVERSE,
        )
        if res is None:
            continue
        passed = res["total_ret"] > 0 and res["sharpe"] >= 0.5
        wf_results.append({**res, "passed": passed})
        print(
            f"  {'PASS' if passed else 'FAIL'} {label}: "
            f"Ret={res['total_ret']:.2%}  Sharpe={res['sharpe']:.2f}  "
            f"Trades={res['n_trades']}"
        )

    wf_pass = sum(1 for w in wf_results if w["passed"])

    print(f"\nSTEP 6: Monte Carlo ({MC_SIMS})...")
    mc = run_monte_carlo(bt["all_closed"])
    if mc["ok"]:
        print(f"  p={mc['p_value']:.3f}  {'PASS' if mc['pass'] else 'FAIL'}")
    else:
        print("  MC FAIL: insufficient trades")

    gates, gates_passed, verdict = evaluate_gates(
        bt, wf_pass, len(wf_results), mc.get("p_value")
    )
    print(f"\n{'=' * 55}")
    print(f"EXP-0020 RESULTS — {verdict} ({gates_passed}/{len(gates)})")
    for name, ok in gates.items():
        print(f"  {'PASS' if ok else 'FAIL'} {name}")
    print(f"  vs EXP-0017 Sharpe {BASELINE_0017['sharpe']} | "
          f"vs EXP-0018 Sharpe {BASELINE_0018['sharpe']}")
    print("  *** Do not promote to /candidates ***")
    print(f"{'=' * 55}")

    return {
        "status": "success",
        "verdict": verdict,
        "gates_passed": gates_passed,
        "gates": {k: bool(v) for k, v in gates.items()},
        "total_ret": round(float(bt["total_ret"]), 4),
        "buy_hold": round(float(bt["buy_hold"]), 4),
        "sharpe": round(float(bt["sharpe"]), 3),
        "max_dd": round(float(bt["max_dd"]), 4),
        "n_trades": int(bt["n_trades"]),
        "win_rate": round(float(bt["win_rate"]), 4),
        "base_rate": round(base_rate, 4),
        "n_candidates": n_candidates,
        "n_profiler_eligible": n_elig,
        "n_rr_lt_1_5": n_rr_thin,
        "share_rr_lt_1_5": round(float(rr_thin_share), 4),
        "wf_count": wf_pass,
        "wf_total": len(wf_results),
        "mc_p_value": mc.get("p_value"),
        "universe_size": len(TRADE_UNIVERSE),
        "pilot_max": PILOT_MAX_TICKERS,
        "insufficient_policy": INSUFFICIENT_POLICY,
        "rr_min_gate": RR_MIN_GATE,
        "rr_warn_report": RR_WARN_REPORT,
        "use_premarket_features": USE_PREMARKET_FEATURES,
        "entry_convention": ENTRY_CONVENTION,
        "selector": SELECTOR,
        "baselines": {"EXP-0017": BASELINE_0017, "EXP-0018": BASELINE_0018},
        "runtime_s": round(time.time() - t0, 0),
    }


@app.local_entrypoint()
def main():
    result = run_exp020.remote()
    print("\nFinal result:")
    for k, v in (result or {}).items():
        print(f"  {k}: {v}")
