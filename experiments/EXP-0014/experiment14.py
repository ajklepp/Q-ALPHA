# ============================================================
# Q-ALPHA | EXP-0014 | experiment14.py
# Catalyst-filtered gap days | LightGBM | Option D label
# Filter BEFORE labeling: gap>=3% AND vol_ratio>=2x
# 14 pre-gap features | BracketPosition from EXP-0012
# ============================================================
import os
import modal
import warnings
warnings.filterwarnings("ignore")

app = modal.App("q-alpha-exp014")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install([
        "pandas", "pandas-ta",
        "scikit-learn", "numpy", "requests", "lightgbm", "scipy",
    ])
)

polygon_secret = modal.Secret.from_name("polygon-api-key")

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
THRESHOLD_BULL       = 0.45
THRESHOLD_BEAR       = 0.55
COST_PER_TRADE       = 0.0015
S4_RUNNER_DAYS       = 30
GAP_MIN              = 0.03
VOL_RATIO_MIN        = 2.0
PRECISION_GATE       = 0.40
LIFT_GATE            = 1.3
SHARPE_GATE          = 1.0
BASE_RATE_MIN        = 0.15
BASE_RATE_MAX        = 0.40
MAX_MARKET_CAP       = 10_000_000_000
SCREEN_DATE          = "2024-01-03"
PRICE_MIN            = 5.0
PRICE_MAX            = 200.0
MIN_AVG_VOLUME       = 500_000
POLYGON_SLEEP        = 0.12
EXP0013_SHARPE       = 0.99


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
    """Dynamic screener identical to EXP-0013 Run 2."""
    print(f"\n  Screening universe (reference date: {screen_date})...")

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

    print(f"    Market cap < $10B (reference API): {len(cap_tickers)}")

    grouped_url = (
        f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/"
        f"{screen_date}"
    )
    grouped = polygon_get(grouped_url, {"adjusted": "true"}, api_key)
    if not grouped or not grouped.get("results"):
        print("    WARNING: grouped daily unavailable")
        if cap_tickers:
            return sorted(cap_tickers)[:120]
        return []

    passed = []
    for bar in grouped.get("results", []):
        ticker = bar.get("T")
        if not ticker:
            continue
        close = bar.get("c", 0) or 0
        volume = bar.get("v", 0) or 0
        if not (PRICE_MIN <= close <= PRICE_MAX and volume >= MIN_AVG_VOLUME):
            continue
        if cap_tickers and ticker not in cap_tickers:
            continue
        passed.append((ticker, close * volume))

    if not passed and cap_tickers:
        print("    WARNING: cap/reference mismatch — using price/volume only")
        for bar in grouped.get("results", []):
            ticker = bar.get("T")
            close = bar.get("c", 0) or 0
            volume = bar.get("v", 0) or 0
            if (ticker and PRICE_MIN <= close <= PRICE_MAX and
                    volume >= MIN_AVG_VOLUME):
                passed.append((ticker, close * volume))

    passed.sort(key=lambda x: x[1], reverse=True)
    universe = [t for t, _ in passed[:120]]
    print(f"    Passed price/volume/cap filter: {len(universe)} tickers "
          f"(top {min(120, len(passed))} by dollar volume)")
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
    Option D for candidate rows: entry at Open, stop=entry-1xATR, target=entry+2xATR.
    Forward simulation on days +1 through +5 only.
    ATR uses prior-day values only (shift applied in process_stock).
    """
    import numpy as np

    mask = candidate_mask(df)
    targets = np.full(len(df), np.nan)
    opens = df["Open"].values
    highs = df["High"].values
    lows = df["Low"].values
    atrs = df["atr_14"].values

    for i in range(len(df) - 6):
        if not mask.iloc[i]:
            continue
        entry = opens[i]
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


def build_lgbm_model(y_train):
    """LightGBM with scale_pos_weight for imbalanced Option D labels."""
    import lightgbm as lgb

    pos = float(y_train.sum())
    neg = float(len(y_train) - pos)
    spw = neg / pos if pos > 0 else 1.0
    return lgb.LGBMClassifier(
        n_estimators=500,
        learning_rate=0.05,
        max_depth=6,
        num_leaves=31,
        min_child_samples=20,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=spw,
        random_state=42,
        n_jobs=-1,
        verbose=-1,
    )


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
    """Pull daily OHLCV and compute 14 pre-gap features + filter columns."""
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
        if ticker != REGIME_TICKER and df["Close"].iloc[-1] < 5:
            return None
        if ticker != REGIME_TICKER and df["Volume"].mean() < 500_000:
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

        # Columns needed for simulation / scoring (not model features)
        df["atr_14"] = atr_raw.shift(1)  # SHIFT APPLIED — used for Option D + stops
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


def run_simulation(test_df, price_pivot, label="", trade_universe=None):
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

    all_dates = sorted(price_pivot.index.unique())

    for current_date in all_dates:
        regime = get_regime(current_date, price_pivot)
        max_pos_today = MAX_POSITIONS_BULL if regime == "BULL" else MAX_POSITIONS_BEAR
        threshold_today = THRESHOLD_BULL if regime == "BULL" else THRESHOLD_BEAR

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
                all_closed.append(pos)
            else:
                still_running.append(pos)
        runners = still_running

        significant_positions = [p for p in active if p.open_slice_count() > 1]
        runner_tickers = {p.ticker for p in runners}
        active_tickers = {p.ticker for p in significant_positions}
        available_slots = max_pos_today - len(significant_positions)

        if (current_date in day_lookup and
                available_slots > 0 and
                day_trade_count < MAX_TRADES_DAY):

            candidates = day_lookup[current_date]
            candidates = candidates[
                (candidates["gap_pct"] >= GAP_MIN) &
                (candidates["volume_ratio_20d"] >= VOL_RATIO_MIN)
            ]
            candidates = candidates[candidates["prob"] >= threshold_today]
            candidates = candidates.sort_values("score", ascending=False)
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

                pos_size = max(MIN_POSITION, min(MAX_POSITION, pool * POSITION_PCT))
                if pool < pos_size:
                    continue
                if pos_size < MIN_POSITION:
                    continue

                atr_val = float(row.get("atr_14", entry_px * 0.02))
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

                pool -= pos_size
                active.append(pos)
                active_tickers.add(ticker)
                available_slots -= 1
                day_trade_count += 1
                trades_taken += 1

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
    }


def print_model_quality(model, df, threshold, label="Validation"):
    """Print precision/lift at experiment threshold."""
    from sklearn.metrics import precision_score, recall_score

    if df.empty:
        return {"base_rate": 0.0, "precision": 0.0, "recall": 0.0, "lift": 0.0}

    X = df[FEATURE_COLUMNS].values
    y = df["target"].values
    probs = model.predict_proba(X)[:, 1]
    preds = (probs >= threshold).astype(int)
    precision = precision_score(y, preds, zero_division=0)
    recall = recall_score(y, preds, zero_division=0)
    base_rate = float(y.mean())
    lift = precision / base_rate if base_rate > 0 else 0.0

    print(f"\n  Model Quality ({label}) @ {threshold:.2f}:")
    print(f"  Rows                   : {len(df):,}")
    print(f"  Base rate (% label=1)  : {base_rate:.2%}")
    print(f"  Precision              : {precision:.2%}  (gate: >{PRECISION_GATE:.0%})")
    print(f"  Recall                 : {recall:.2%}")
    print(f"  Lift                   : {lift:.2f}x  (gate: >{LIFT_GATE:.1f}x)")

    return {
        "base_rate": base_rate,
        "precision": precision,
        "recall": recall,
        "lift": lift,
    }


def evaluate_gates(base_rate, mq, bt, wf_pass_count, wf_total):
    """Score EXP-0014 success gates; return pass count and status."""
    gates = {
        "base_rate_15_40": BASE_RATE_MIN <= base_rate <= BASE_RATE_MAX,
        "precision_40": mq["precision"] >= PRECISION_GATE,
        "lift_1_3": mq["lift"] >= LIFT_GATE,
        "sharpe_1": bt["sharpe"] >= SHARPE_GATE,
        "max_dd_15": bt["max_dd"] >= -0.15,
        "wf_2_4": wf_pass_count >= 2,
        "trades_40": bt["n_trades"] > 40,
        "beats_exp0013": bt["sharpe"] > EXP0013_SHARPE,
    }
    passed = sum(gates.values())
    if passed == len(gates):
        status = "PASS"
    elif passed >= 5:
        status = "INVESTIGATE"
    else:
        status = "FAIL"
    return gates, passed, status


@app.function(image=image, timeout=3600, memory=8192, secrets=[polygon_secret])
def run_exp014():
    import pandas as pd
    import numpy as np
    import warnings
    import time

    warnings.filterwarnings("ignore")
    api_key = os.environ["POLYGON_API_KEY"]

    global TRADE_UNIVERSE
    TRADE_UNIVERSE = build_dynamic_universe(api_key)
    download_universe = [REGIME_TICKER] + TRADE_UNIVERSE

    t0 = time.time()
    print("=" * 55)
    print("EXP-0014: Catalyst Gap Filter | LightGBM | Option D")
    print(f"Universe : {len(TRADE_UNIVERSE)} stocks + SPY (regime)")
    print(f"Filter   : gap>={GAP_MIN:.0%} AND vol_ratio>={VOL_RATIO_MIN:.0f}x")
    print(f"Train    : {TRAIN_START} to {TRAIN_END}")
    print(f"Validate : {VALIDATE_START} to {VALIDATE_END}")
    print(f"Test     : {SIM_START} to {SIM_END}")
    print(f"Threshold: BULL={THRESHOLD_BULL}  BEAR={THRESHOLD_BEAR}")
    print("=" * 55)

    # ── STEP 1: Download OHLCV ────────────────────────────────
    print("\nSTEP 1: Downloading universe from Polygon.io...")
    all_candidates = []
    all_prices = []
    total_rows = 0
    spy_returns = None

    for i, ticker in enumerate(download_universe):
        raw = process_stock(ticker, DATA_START, DATA_END, api_key)
        if raw is None:
            continue
        total_rows += len(raw)
        price_slice = raw[["Close", "ticker"]].copy()
        all_prices.append(price_slice)
        if ticker == REGIME_TICKER:
            spy_returns = raw[["return_20d"]].rename(
                columns={"return_20d": "spy_return_20d"})
        else:
            cand = extract_candidates(raw)
            if len(cand) > 0:
                all_candidates.append(cand)
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(download_universe)} tickers "
                  f"[{time.time() - t0:.0f}s]")

    if not all_candidates:
        return {"status": "error", "message": "No candidate rows after filter"}

    combined = pd.concat(all_candidates)
    combined.sort_index(inplace=True)
    pre_filter = len(combined)
    if spy_returns is None:
        return {"status": "error", "message": "SPY data missing for rs_vs_spy"}
    combined = add_rs_vs_spy(combined, spy_returns)
    for col in FEATURE_COLUMNS:
        if combined[col].isna().any():
            combined[col] = combined[col].fillna(combined[col].median())
    null_report = {
        c: int(combined[c].isna().sum()) for c in FEATURE_COLUMNS + ["target"]
    }
    combined.dropna(subset=FEATURE_COLUMNS + ["target"], inplace=True)

    n_candidates = len(combined)
    cand_rate = pre_filter / total_rows if total_rows else 0
    base_rate = float(combined["target"].mean()) if n_candidates else 0.0

    if pre_filter > 0 and n_candidates == 0:
        print(f"  ERROR: all {pre_filter} candidates lost in dropna")
        for col, nnull in null_report.items():
            print(f"    NaN {col}: {nnull}/{pre_filter}")
        return {
            "status": "error",
            "message": f"Feature dropna removed all rows: {null_report}",
        }

    print(f"\n  Total rows: {total_rows:,}")
    print(f"  Candidate rows (gap>={GAP_MIN:.0%} AND vol>={VOL_RATIO_MIN:.0f}x): "
          f"{pre_filter:,}")
    print(f"  After feature dropna: {n_candidates:,}")
    print(f"  Candidate rate: {cand_rate:.2%}")
    print(f"  Base rate (label=1): {base_rate:.2%}")

    if n_candidates < 1000:
        print(f"  WARNING: candidate count {n_candidates} < 1,000")
    if n_candidates > 15000:
        print(f"  WARNING: candidate count {n_candidates} > 15,000 — check look-ahead")
    if base_rate < BASE_RATE_MIN:
        print(f"  WARNING: base rate {base_rate:.2%} < {BASE_RATE_MIN:.0%}")
    if base_rate > 0.50:
        print(f"  ERROR: base rate {base_rate:.2%} > 50% — possible look-ahead bias")
        return {"status": "error", "message": "Base rate > 50% — look-ahead suspected"}

    # ── STEP 2: Train LightGBM ───────────────────────────────
    print("\nSTEP 2: Training LightGBM on catalyst candidates...")
    train_df = combined[
        (combined.index >= pd.Timestamp(TRAIN_START)) &
        (combined.index <= pd.Timestamp(TRAIN_END))
    ].copy()
    validate_df = combined[
        (combined.index >= pd.Timestamp(VALIDATE_START)) &
        (combined.index <= pd.Timestamp(VALIDATE_END))
    ].copy()
    test_df = combined[
        (combined.index >= pd.Timestamp(SIM_START)) &
        (combined.index <= pd.Timestamp(SIM_END))
    ].copy()
    print(f"Train: {len(train_df):,}  Validate: {len(validate_df):,}  "
          f"Test: {len(test_df):,}")

    if len(train_df) < 200:
        return {"status": "error",
                "message": f"Insufficient train rows: {len(train_df)}"}

    model = build_lgbm_model(train_df["target"])
    model.fit(train_df[FEATURE_COLUMNS], train_df["target"])

    mq_val = print_model_quality(
        model, validate_df, THRESHOLD_BULL, label="2022 Validation")

    # Feature importance
    imp = sorted(
        zip(FEATURE_COLUMNS, model.feature_importances_),
        key=lambda x: -x[1],
    )
    print(f"\n  Feature Importance (top 10):")
    for rank, (feat, score) in enumerate(imp[:10], 1):
        print(f"    {rank:>2}. {feat:<22} {score:.0f}")

    # ── STEP 3: Predict test period ──────────────────────────
    print("\nSTEP 3: Batch predicting 2023-2024...")
    test_df = test_df.copy()
    test_df["prob"] = model.predict_proba(
        test_df[FEATURE_COLUMNS].values)[:, 1]

    # ── STEP 4: Price pivot (full daily closes) ─────────────
    print("\nSTEP 4: Building price pivot...")
    prices_all = pd.concat(all_prices)
    prices = prices_all[
        (prices_all.index >= pd.Timestamp(SIM_START)) &
        (prices_all.index <= pd.Timestamp(SIM_END))
    ]
    price_pivot = prices.pivot_table(
        index=prices.index, columns="ticker",
        values="Close", aggfunc="last")
    print(f"Price pivot: {price_pivot.shape}  "
          f"[{time.time() - t0:.0f}s elapsed]")

    # ── STEP 5: Simulation ───────────────────────────────────
    print("\nSTEP 5: Running simulation (entry at gap-day Close)...")
    bt = run_simulation(
        test_df, price_pivot,
        label="OOS 2023-2024",
        trade_universe=TRADE_UNIVERSE,
    )
    if bt is None:
        return {"status": "error", "message": "Simulation failed"}

    # ── STEP 6: Walk-forward ─────────────────────────────────
    print("\nSTEP 6: Walk forward across 4 windows...")
    wf_windows = [
        ("2019-01-01", "2021-01-01", "2021-01-01", "2022-01-01", "2021"),
        ("2019-01-01", "2022-01-01", "2022-01-01", "2023-01-01", "2022"),
        ("2019-01-01", "2023-01-01", "2023-01-01", "2024-01-01", "2023"),
        ("2019-01-01", "2024-01-01", "2024-01-01", "2025-01-01", "2024"),
    ]
    wf_results = []
    for ts, te, vs, ve, label in wf_windows:
        train_w = combined[
            (combined.index >= pd.Timestamp(ts)) &
            (combined.index < pd.Timestamp(te))
        ]
        if len(train_w) < 200:
            continue
        test_w = combined[
            (combined.index >= pd.Timestamp(vs)) &
            (combined.index < pd.Timestamp(ve))
        ].copy()
        if len(test_w) < 20:
            continue

        m_w = build_lgbm_model(train_w["target"])
        m_w.fit(train_w[FEATURE_COLUMNS], train_w["target"])
        test_w["prob"] = m_w.predict_proba(
            test_w[FEATURE_COLUMNS].values)[:, 1]

        price_slice = prices_all[
            (prices_all.index >= pd.Timestamp(vs)) &
            (prices_all.index < pd.Timestamp(ve))
        ]
        pp_w = price_slice.pivot_table(
            index=price_slice.index, columns="ticker",
            values="Close", aggfunc="last")

        res = run_simulation(
            test_w, pp_w, label=label, trade_universe=TRADE_UNIVERSE)
        if res is None:
            continue
        passed = res["total_ret"] > 0 and res["sharpe"] >= 0.5
        wf_results.append({**res, "passed": passed})
        print(f"  {'PASS' if passed else 'FAIL'} {label}: "
              f"Ret={res['total_ret']:.2%}  Sharpe={res['sharpe']:.2f}  "
              f"Trades={res['n_trades']}")

    wf_pass_count = sum(1 for w in wf_results if w["passed"])

    # ── STEP 7: Report ───────────────────────────────────────
    gates, gates_passed, verdict = evaluate_gates(
        base_rate, mq_val, bt, wf_pass_count, len(wf_results))

    print(f"\n{'=' * 55}")
    print("EXP-0014 RESULTS")
    print(f"{'=' * 55}")
    print(f"  Runtime            : {time.time() - t0:.0f}s")
    print(f"  Final Equity       : ${bt['total_pnl'] + STARTING_CAP:,.2f}")
    print(f"  Total Return       : {bt['total_ret']:.2%}")
    print(f"  Sharpe             : {bt['sharpe']:.2f}  (gate: >{SHARPE_GATE})")
    print(f"  Max Drawdown       : {bt['max_dd']:.2%}")
    print(f"  Total Trades       : {bt['n_trades']}  (gate: >40)")
    print(f"  Win Rate           : {bt['win_rate']:.2%}")
    print(f"  Base Rate          : {base_rate:.2%}  (gate: 15-40%)")
    print(f"  Precision @ {THRESHOLD_BULL:.2f}     : {mq_val['precision']:.2%}")
    print(f"  Lift               : {mq_val['lift']:.2f}x")
    print(f"  Walk Forward       : {wf_pass_count}/{len(wf_results)}")
    print(f"  vs EXP-0013 Sharpe : {bt['sharpe']:.2f} vs {EXP0013_SHARPE}")

    print(f"\n  Success Gates:")
    for name, ok in gates.items():
        print(f"    {'PASS' if ok else 'FAIL'} {name}")

    print(f"\n  VERDICT: {verdict} ({gates_passed}/{len(gates)} gates)")
    print(f"{'=' * 55}")

    return {
        "status": "success",
        "verdict": verdict,
        "gates_passed": gates_passed,
        "total_ret": round(float(bt["total_ret"]), 4),
        "sharpe": round(float(bt["sharpe"]), 3),
        "max_dd": round(float(bt["max_dd"]), 4),
        "n_trades": int(bt["n_trades"]),
        "win_rate": round(float(bt["win_rate"]), 4),
        "base_rate": round(float(base_rate), 4),
        "precision": round(float(mq_val["precision"]), 4),
        "lift": round(float(mq_val["lift"]), 4),
        "wf_count": wf_pass_count,
        "runtime_s": round(time.time() - t0, 0),
    }


@app.local_entrypoint()
def main():
    print("=" * 55)
    print("  Q-ALPHA | EXP-0014 | Catalyst Gap Filter + LightGBM")
    print("  Running on Modal cloud")
    print("=" * 55)

    result = run_exp014.remote()

    if result.get("status") != "success":
        print(f"Error: {result}")
        return

    print(f"\n  Final Summary:")
    print(f"  Verdict       : {result['verdict']}")
    print(f"  Gates         : {result['gates_passed']}/8")
    print(f"  Return        : {result['total_ret']:.2%}")
    print(f"  Sharpe        : {result['sharpe']:.2f}")
    print(f"  Drawdown      : {result['max_dd']:.2%}")
    print(f"  Trades        : {result['n_trades']}")
    print(f"  Base Rate     : {result['base_rate']:.2%}")
    print(f"  Precision     : {result['precision']:.2%}")
    print(f"  Lift          : {result['lift']:.2f}x")
    print(f"  Walk Fwd      : {result['wf_count']}/4")
    print(f"  Runtime       : {result['runtime_s']:.0f}s")
