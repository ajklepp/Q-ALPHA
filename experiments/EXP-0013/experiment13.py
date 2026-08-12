# ============================================================
# Q-ALPHA | EXP-0013 | experiment13.py
# Polygon.io | $3K account | Small/mid-cap momentum universe
# LightGBM + Option D label (2R before stop within 5 days)
# Infrastructure copied from EXP-0012 (BracketPosition, regime, sim)
# ============================================================
import os
import modal
import warnings
warnings.filterwarnings("ignore")

app = modal.App("q-alpha-exp013")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install([
        "pandas", "pandas-ta",
        "scikit-learn", "numpy", "requests", "lightgbm",
    ])
)

polygon_secret = modal.Secret.from_name("polygon-api-key")

MOMENTUM_UNIVERSE = [
    "SPY",  # regime only
    # Small/mid cap momentum candidates
    "CELH", "DUOL", "APPS", "CLFD", "AEHR", "BOOT", "GMED", "VCEL",
    "IRTC", "INSP", "TMDX", "AXSM", "NTRA", "RXRX", "TWST", "BEAM",
    "ACMR", "SLAB", "FORM", "COHU", "ICHR", "KLIC", "ONTO", "UCTT",
    "MGNI", "TTD", "PUBM", "IAS", "DV", "BURL", "FIVE", "OLLI",
    "CAVA", "BROS", "WING", "TXRH", "SHAK", "TAST", "FAT", "DINE",
    "AGIO", "ACAD", "FOLD", "PTGX", "AGEN", "ARQT", "PRAX", "KYMR",
    "AAON", "BCPC", "CBT", "KFRC", "MGRC", "PIPR", "SFBS", "TOWN",
    "HIMS", "DOCS", "SDGR", "VEEV", "PCTY", "PAYC", "NCNO", "EVBG",
    "POWL", "IESC", "GVP", "SHLS", "ARRY", "NOVA", "STEM", "JOBY",
    "UFPT", "KTOS", "ESSC", "MFAC", "HAFC", "EGBN", "FBMS", "HTLF",
]
MOMENTUM_UNIVERSE = list(dict.fromkeys(MOMENTUM_UNIVERSE))
TRADE_UNIVERSE = [t for t in MOMENTUM_UNIVERSE if t != "SPY"]

FEATURE_COLUMNS = [
    # Price-based
    "return_1d", "return_5d", "return_10d",
    "close_vs_sma20", "close_vs_sma50",
    # Momentum
    "rsi_14", "macd", "macd_signal", "macd_hist",
    # Volatility / compression
    "bb_position", "bbw_pct",
    "atr_14_normalized",
    "volatility_10d", "volatility_20d",
    # Volume
    "volume_ratio",
    "volume_ratio_5d",
    # Setup quality
    "price_location_30d",
    "dist_from_52w_high",
    "obv_norm",
    "cmf_20",
    "rs_vs_spy_20d",
]

DATA_START           = "2019-01-01"   # warmup for 52w / rolling features
TRAIN_START          = "2020-01-01"
TRAIN_END            = "2022-12-31"
VALIDATE_START       = "2023-01-01"
VALIDATE_END         = "2023-12-31"
SIM_START            = "2023-01-01"
SIM_END              = "2024-12-31"
STARTING_CAP         = 3000.0
MAX_POSITIONS_BULL   = 10
MAX_POSITIONS_BEAR   = 3
MAX_TRADES_DAY       = 3
POSITION_PCT         = 0.10
MIN_POSITION         = 50.0
MAX_POSITION         = 500.0
THRESHOLD_BULL       = 0.55
THRESHOLD_BEAR       = 0.65
COST_PER_TRADE       = 0.0015
S4_RUNNER_DAYS       = 30
SHARPE_PASS          = 1.5
PRECISION_GATE       = 0.55
LIFT_GATE            = 1.5


def calculate_option_d_labels(df):
    """
    For each row, simulate forward 5 days.
    Label = 1 if 2R target hit before stop hit.
    Label = 0 if stop hit first OR 5 days pass.

    Uses ATR from the signal day; forward path uses only future OHLC.
    Assumes entry at close of signal day (daily backtest).
    """
    import numpy as np

    labels = np.zeros(len(df), dtype=int)
    closes = df["Close"].values
    highs = df["High"].values
    lows = df["Low"].values
    atrs = df["atr_14"].values

    for i in range(len(df) - 6):
        entry_price = closes[i]
        stop_distance = 1.5 * atrs[i]
        stop_price = entry_price - stop_distance
        target_price = entry_price + (2.0 * stop_distance)

        label = 0
        for j in range(i + 1, min(i + 6, len(df))):
            if lows[j] <= stop_price:
                label = 0
                break
            if highs[j] >= target_price:
                label = 1
                break
        labels[i] = label

    return labels


def build_lgbm_model():
    """Return configured LightGBM classifier for Option D prediction."""
    import lightgbm as lgb

    return lgb.LGBMClassifier(
        n_estimators=500,
        learning_rate=0.05,
        max_depth=6,
        num_leaves=31,
        min_child_samples=20,
        subsample=0.8,
        colsample_bytree=0.8,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
        verbose=-1,
    )


def add_rs_vs_spy(combined):
    """Compute relative strength vs SPY after all tickers are merged."""
    import pandas as pd

    spy = combined[combined["ticker"] == "SPY"][["return_20d"]].copy()
    spy = spy.rename(columns={"return_20d": "spy_return_20d"})
    combined = combined.merge(
        spy, left_index=True, right_index=True, how="left")
    combined["rs_vs_spy_20d"] = (
        combined["return_20d"] - combined["spy_return_20d"]
    )
    combined.loc[combined["ticker"] == "SPY", "rs_vs_spy_20d"] = 0.0
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

    vr = row.get("volume_ratio", 1.0)
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
    try:
        import time
        import requests
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
        if ticker != "SPY" and df["Close"].iloc[-1] < 5:
            return None
        if ticker != "SPY" and df["Volume"].mean() < 500_000:
            return None

        df["return_1d"] = df["Close"].pct_change(1)
        df["return_5d"] = df["Close"].pct_change(5)
        df["return_10d"] = df["Close"].pct_change(10)
        df["return_20d"] = df["Close"].pct_change(20)
        df["sma_20"] = ta.sma(df["Close"], length=20)
        df["sma_50"] = ta.sma(df["Close"], length=50)
        df["close_vs_sma20"] = (df["Close"] - df["sma_20"]) / df["sma_20"]
        df["close_vs_sma50"] = (df["Close"] - df["sma_50"]) / df["sma_50"]
        df["rsi_14"] = ta.rsi(df["Close"], length=14)
        macd = ta.macd(df["Close"], fast=12, slow=26, signal=9)
        df["macd"] = macd["MACD_12_26_9"]
        df["macd_signal"] = macd["MACDs_12_26_9"]
        df["macd_hist"] = macd["MACDh_12_26_9"]
        bbands = ta.bbands(df["Close"], length=20, std=2)
        df["bb_upper"] = bbands.iloc[:, 0]
        df["bb_mid"] = bbands.iloc[:, 1]
        df["bb_lower"] = bbands.iloc[:, 2]
        df["bb_position"] = (
            (df["Close"] - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"])
        )
        df["bbw_pct"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
        df["volume_sma_20"] = ta.sma(df["Volume"], length=20)
        df["volume_ratio"] = df["Volume"] / df["volume_sma_20"]
        df["volume_sma_5"] = ta.sma(df["Volume"], length=5)
        df["volume_ratio_5d"] = df["Volume"] / df["volume_sma_5"]
        df["volatility_10d"] = df["return_1d"].rolling(10).std()
        df["volatility_20d"] = df["return_1d"].rolling(20).std()
        df["atr_14"] = ta.atr(
            df["High"], df["Low"], df["Close"], length=14)
        df["atr_14_normalized"] = df["atr_14"] / df["Close"]

        df["high_30d"] = df["High"].rolling(30).max()
        df["low_30d"] = df["Low"].rolling(30).min()
        df["price_location_30d"] = (
            (df["Close"] - df["low_30d"])
            / (df["high_30d"] - df["low_30d"] + 1e-9)
        )

        df["high_52w"] = df["High"].rolling(252).max()
        df["dist_from_52w_high"] = (
            (df["Close"] - df["high_52w"]) / df["high_52w"]
        )

        obv = ta.obv(df["Close"], df["Volume"])
        df["obv_norm"] = obv / (df["volume_sma_20"] * 20 + 1e-9)
        df["cmf_20"] = ta.cmf(
            df["High"], df["Low"], df["Close"], df["Volume"], length=20)

        df["target"] = calculate_option_d_labels(df)
        df["ticker"] = ticker

        feature_cols = [c for c in FEATURE_COLUMNS if c != "rs_vs_spy_20d"]
        df.dropna(subset=feature_cols + ["target"], inplace=True)
        time.sleep(0.12)
        return df if len(df) >= 50 else None
    except Exception:
        return None


class BracketPosition:
    def __init__(self, ticker, entry_price, entry_date,
                 position_size, params):
        self.ticker        = ticker
        self.entry_price   = entry_price
        self.entry_date    = entry_date
        self.position_size = position_size
        atr_val  = params.get("atr_at_entry", entry_price * 0.02)
        atr_pct  = params["master_atr_mult"] * (atr_val / entry_price)
        self.master_stop = entry_price * (1 - atr_pct)
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
                    gross = (current_price - self.entry_price) / self.entry_price
                    pnl   = slice_cap * (gross - COST_PER_TRADE)
                    self.realized_pnl += pnl
                    s["closed"] = True
                    released += slice_cap + pnl
                    self.exit_log.append({"stop": "MASTER", "sid": sid,
                        "date": current_date, "pnl": pnl})
                continue
            if current_price <= trail_level:
                gross = (current_price - self.entry_price) / self.entry_price
                pnl   = slice_cap * (gross - COST_PER_TRADE)
                self.realized_pnl += pnl
                s["closed"] = True
                released += slice_cap + pnl
                self.exit_log.append({"stop": f"S{sid}", "sid": sid,
                    "date": current_date, "pnl": pnl})
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


def run_simulation(test_df, price_pivot, label=""):
    import pandas as pd
    import numpy as np

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

    all_dates = sorted(test_df.index.unique())

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
            if pos.fully_closed:
                all_closed.append(pos)
            else:
                still_running.append(pos)
        runners = still_running

        significant_positions = [p for p in active if p.open_slice_count() > 1]
        runner_tickers = {p.ticker for p in runners}
        active_tickers = {p.ticker for p in significant_positions}
        available_slots = max_pos_today - len(significant_positions)
        if available_slots <= 0:
            deployed = (sum(p.deployed_capital() for p in active) + sum(p.deployed_capital() for p in runners))
            equity_curve.append({"date": current_date, "equity": pool + deployed, "regime": regime})
            continue

        if (current_date in day_lookup and
                available_slots > 0 and
                day_trade_count < MAX_TRADES_DAY):

            candidates = day_lookup[current_date]
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
                if ticker not in TRADE_UNIVERSE:
                    continue
                if ticker in active_tickers or ticker in runner_tickers:
                    continue

                try:
                    entry_px = float(price_pivot.loc[current_date, ticker])
                except Exception:
                    continue

                pos_size = max(MIN_POSITION, min(MAX_POSITION, pool * POSITION_PCT))
                if pos_size < MIN_POSITION:
                    continue
                if pool < pos_size:
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
                trades_taken    += 1

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

    if equity_curve:
        equity_curve[-1]["equity"] = pool

    eq_df = pd.DataFrame(equity_curve).set_index("date")
    if eq_df.empty:
        return None
    eq_df["ret"] = eq_df["equity"].pct_change().fillna(0)

    final_eq  = eq_df["equity"].iloc[-1]
    total_ret = (final_eq - STARTING_CAP) / STARTING_CAP
    total_pnl = final_eq - STARTING_CAP
    sharpe    = (eq_df["ret"].mean() / eq_df["ret"].std() * np.sqrt(252)
                 if eq_df["ret"].std() > 0 else 0.0)
    roll_max  = eq_df["equity"].expanding().max()
    drawdown  = (eq_df["equity"] - roll_max) / roll_max
    max_dd    = drawdown.min()

    bull_rets = eq_df.loc[eq_df["regime"] == "BULL", "ret"]
    bear_rets = eq_df.loc[eq_df["regime"] == "BEAR", "ret"]
    bull_perf = float((1 + bull_rets).prod() - 1) if len(bull_rets) > 0 else 0.0
    bear_perf = float((1 + bear_rets).prod() - 1) if len(bear_rets) > 0 else 0.0
    bull_sharpe = (bull_rets.mean() / bull_rets.std() * np.sqrt(252)
                   if len(bull_rets) > 1 and bull_rets.std() > 0 else 0.0)
    bear_sharpe = (bear_rets.mean() / bear_rets.std() * np.sqrt(252)
                   if len(bear_rets) > 1 and bear_rets.std() > 0 else 0.0)

    n_trades   = len(all_closed)
    win_trades = sum(1 for p in all_closed if p.realized_pnl > 0)
    win_rate   = win_trades / n_trades if n_trades > 0 else 0
    trades_per_day = trades_taken / trading_days if trading_days > 0 else 0

    bh_list = []
    for ticker in TRADE_UNIVERSE:
        try:
            col = price_pivot[ticker].dropna()
            if len(col) > 1:
                bh_list.append((col.iloc[-1] - col.iloc[0]) / col.iloc[0])
        except Exception:
            pass
    buy_hold = float(np.mean(bh_list)) if bh_list else 0.0

    checks = 0
    if total_ret > 0:          checks += 1
    if total_ret > buy_hold:   checks += 1
    if sharpe >= SHARPE_PASS:  checks += 1
    if max_dd >= -0.15:        checks += 1

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
        "checks": checks,
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


def print_model_quality(model, df, label="Validation"):
    """Print precision/lift metrics at 0.60 probability threshold."""
    from sklearn.metrics import precision_score, recall_score

    X = df[FEATURE_COLUMNS].values
    y = df["target"].values
    probs = model.predict_proba(X)[:, 1]
    preds_60 = (probs >= 0.60).astype(int)
    precision_60 = precision_score(y, preds_60, zero_division=0)
    recall_60 = recall_score(y, preds_60, zero_division=0)
    base_rate = y.mean()
    lift_60 = precision_60 / base_rate if base_rate > 0 else 0.0

    print(f"\n  Model Quality ({label}):")
    print(f"  Base rate (% label=1)  : {base_rate:.2%}")
    print(f"  Precision @ 0.60       : {precision_60:.2%}  "
          f"(target: >={PRECISION_GATE:.0%})")
    print(f"  Recall @ 0.60          : {recall_60:.2%}")
    print(f"  Lift @ 0.60            : {lift_60:.2f}x  "
          f"(target: >={LIFT_GATE:.1f}x)")

    return {
        "base_rate": base_rate,
        "precision_60": precision_60,
        "recall_60": recall_60,
        "lift_60": lift_60,
    }


@app.function(image=image, timeout=3600, memory=8192, secrets=[polygon_secret])
def run_exp013():
    import pandas as pd
    import numpy as np
    import warnings
    import time

    warnings.filterwarnings("ignore")
    api_key = os.environ["POLYGON_API_KEY"]

    t0 = time.time()
    print("=" * 55)
    print("EXP-0013: Small/Mid-Cap | LightGBM | Option D Label")
    print(f"Universe : {len(TRADE_UNIVERSE)} stocks + SPY (regime)")
    print(f"Train    : {TRAIN_START} to {TRAIN_END}")
    print(f"Simulate : {SIM_START} to {SIM_END}")
    print(f"Capital  : ${STARTING_CAP:,.0f}  Pos pct: {POSITION_PCT:.0%}")
    print(f"Regime   : BULL max={MAX_POSITIONS_BULL} thr={THRESHOLD_BULL}  "
          f"BEAR max={MAX_POSITIONS_BEAR} thr={THRESHOLD_BEAR}")
    print("=" * 55)

    # ── STEP 1: Download from Polygon.io ─────────────────────
    print("\nSTEP 1: Downloading universe from Polygon.io...")
    all_data = []
    for i, ticker in enumerate(MOMENTUM_UNIVERSE):
        df = process_stock(ticker, DATA_START, SIM_END, api_key)
        if df is not None:
            all_data.append(df)
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(MOMENTUM_UNIVERSE)} tickers processed "
                  f"[{time.time() - t0:.0f}s]")

    print(f"Loaded {len(all_data)} stocks  "
          f"[{time.time() - t0:.0f}s elapsed]")

    if not all_data:
        return {"status": "error", "message": "No data from Polygon.io"}

    combined = pd.concat(all_data)
    combined.sort_index(inplace=True)
    combined = add_rs_vs_spy(combined)
    combined.dropna(subset=FEATURE_COLUMNS + ["target"], inplace=True)
    print(f"Total rows: {len(combined):,}")

    # ── STEP 2: Train LightGBM on 2020-2022 ──────────────────
    print("\nSTEP 2: Training LightGBM on 2020-2022 (Option D label)...")
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

    model = build_lgbm_model()
    model.fit(train_df[FEATURE_COLUMNS], train_df["target"])

    mq = print_model_quality(model, validate_df, label="2023 Validation")

    print(f"Thresholds: BULL={THRESHOLD_BULL}  BEAR={THRESHOLD_BEAR}  "
          f"[{time.time() - t0:.0f}s elapsed]")

    # ── STEP 3: Batch predict test period ────────────────────
    print("\nSTEP 3: Batch predicting 2023-2024...")
    test_df["prob"] = model.predict_proba(
        test_df[FEATURE_COLUMNS].values)[:, 1]
    print(f"Predictions done  [{time.time() - t0:.0f}s elapsed]")

    # ── STEP 4: Build price pivot ────────────────────────────
    print("\nSTEP 4: Building price pivot...")
    price_pivot = test_df.pivot_table(
        index=test_df.index,
        columns="ticker",
        values="Close",
        aggfunc="last")
    print(f"Price pivot: {price_pivot.shape}  "
          f"[{time.time() - t0:.0f}s elapsed]")

    # ── STEP 5: Day-by-day simulation 2023-2024 ────────────
    print("\nSTEP 5: Running day-by-day simulation with regime filter...")
    bt = run_simulation(test_df, price_pivot,
                        label="OOS 2023-2024")
    if bt is None:
        return {"status": "error", "message": "Simulation failed"}

    print(f"Simulation done  [{time.time() - t0:.0f}s elapsed]")

    # ── STEP 6: Walk forward across 4 windows ────────────────
    print("\nSTEP 6: Walk forward across 4 windows...")
    wf_windows = [
        ("2020-01-01", "2022-01-01", "2022-01-01", "2023-01-01", "2022"),
        ("2020-01-01", "2023-01-01", "2023-01-01", "2024-01-01", "2023"),
        ("2020-01-01", "2024-01-01", "2024-01-01", "2024-07-01", "2024 H1"),
        ("2020-01-01", "2024-07-01", "2024-07-01", "2025-01-01", "2024 H2"),
    ]
    wf_results = []
    for ts, te, vs, ve, label in wf_windows:
        train_w = combined[
            (combined.index >= pd.Timestamp(ts)) &
            (combined.index < pd.Timestamp(te))
        ]
        if len(train_w) < 500:
            continue
        test_w = combined[
            (combined.index >= pd.Timestamp(vs)) &
            (combined.index < pd.Timestamp(ve))
        ].copy()
        if len(test_w) < 50:
            continue

        m_w = build_lgbm_model()
        m_w.fit(train_w[FEATURE_COLUMNS], train_w["target"])
        test_w["prob"] = m_w.predict_proba(
            test_w[FEATURE_COLUMNS].values)[:, 1]
        pp_w = test_w.pivot_table(
            index=test_w.index, columns="ticker",
            values="Close", aggfunc="last")
        res = run_simulation(test_w, pp_w, label=label)
        if res is None:
            continue
        passed = res["total_ret"] > 0 and res["checks"] >= 2
        wf_results.append({**res, "passed": passed})
        print(f"  {'PASS' if passed else 'FAIL'} {label}: "
              f"Ret={res['total_ret']:.2%}  "
              f"Sharpe={res['sharpe']:.2f}  "
              f"Trades={res['n_trades']}  "
              f"Avg/day={res['trades_per_day']:.2f}  "
              f"Bull={res['bull_days']}d Bear={res['bear_days']}d  "
              f"Pool=${res['final_pool']:,.2f}")

    wf_pass_count = sum(1 for w in wf_results if w["passed"])
    print(f"Walk Forward: {wf_pass_count}/{len(wf_results)} passed")

    # ── STEP 7: Metrics and reporting ────────────────────────
    print("\nSTEP 7: Calculating metrics...")
    all_closed = bt["all_closed"]

    all_exits = []
    for pos in all_closed:
        all_exits.extend(pos.exit_log)
    exits_df = pd.DataFrame(all_exits) if all_exits \
               else pd.DataFrame(columns=["stop"])
    stop_counts = exits_df["stop"].value_counts() \
                  if not exits_df.empty else pd.Series()

    stock_pnl = {}
    for pos in all_closed:
        stock_pnl.setdefault(pos.ticker, []).append(pos.realized_pnl)

    print(f"\n{'=' * 55}")
    print("EXP-0013 RESULTS")
    print(f"{'=' * 55}")
    print(f"  Total runtime      : {time.time() - t0:.0f}s")
    print(f"\n  Starting Capital   : ${STARTING_CAP:>12,.2f}")
    print(f"  Final Equity       : ${bt['total_pnl'] + STARTING_CAP:>12,.2f}")
    print(f"  Total Return       : {bt['total_ret']:>10.2%}")
    print(f"  Total PnL          : ${bt['total_pnl']:>12,.2f}")
    print(f"  Buy and Hold       : {bt['buy_hold']:>10.2%}")
    print(f"  Sharpe Ratio       : {bt['sharpe']:>10.2f}")
    print(f"  Max Drawdown       : {bt['max_dd']:>10.2%}")
    print(f"  Total Trades       : {bt['n_trades']}")
    print(f"  Win Rate           : {bt['win_rate']:.2%}")
    print(f"  Trades Taken       : {bt['trades_taken']}")
    print(f"  Trading Days       : {bt['trading_days']}")
    print(f"  Avg Trades/Day     : {bt['trades_per_day']:.2f}")
    print(f"  Total Signals      : {bt['n_signals']:,}")
    print(f"  Position sizing    : {POSITION_PCT:.0%} of pool")
    print(f"  Pool at end        : ${bt['final_pool']:,.2f}")

    print(f"\n  vs EXP-0012 Baseline:")
    print(f"    Sharpe   EXP-0012: 1.64   EXP-0013: {bt['sharpe']:.2f}")
    print(f"    Max DD   EXP-0012: -3.17% EXP-0013: {bt['max_dd']:.2%}")
    print(f"    Win Rate EXP-0012: 39.76% EXP-0013: {bt['win_rate']:.2%}")

    print(f"\n  Market Regime Filter (SPY vs SMA50):")
    print(f"    BULL days          : {bt['bull_days']}")
    print(f"    BEAR days          : {bt['bear_days']}")
    print(f"    BULL performance   : {bt['bull_perf']:.2%}  "
          f"(Sharpe {bt['bull_sharpe']:.2f})")
    print(f"    BEAR performance   : {bt['bear_perf']:.2%}  "
          f"(Sharpe {bt['bear_sharpe']:.2f})")
    print(f"    BULL max positions : {MAX_POSITIONS_BULL}  "
          f"threshold: {THRESHOLD_BULL}")
    print(f"    BEAR max positions : {MAX_POSITIONS_BEAR}  "
          f"threshold: {THRESHOLD_BEAR}")

    print(f"\n  Stop Breakdown:")
    for st in ["MASTER", "S1", "S2", "S3", "S4"]:
        print(f"    {st}: {stop_counts.get(st, 0)} exits")

    print(f"\n  Profile Breakdown:")
    for prof, cnt in sorted(bt["profile_counts"].items(),
                            key=lambda x: -x[1]):
        print(f"    {prof:<25}: {cnt} trades")

    print(f"\n  Top 10 Stocks by PnL:")
    sorted_s = sorted(stock_pnl.items(),
                      key=lambda x: sum(x[1]), reverse=True)
    for tk, pnls in sorted_s[:10]:
        tot  = sum(pnls)
        wins = sum(1 for p in pnls if p > 0)
        print(f"    {tk:<6}: {len(pnls):>3} trades  "
              f"PnL=${tot:>8,.0f}  "
              f"WR={wins / len(pnls):.0%}")

    checks = bt["checks"]
    print(f"\n  Quant Assassin:")
    if bt["total_ret"] > 0:
        print(f"  PASS Positive return    : {bt['total_ret']:.2%}")
    else:
        print(f"  FAIL Negative return    : {bt['total_ret']:.2%}")
    if bt["total_ret"] > bt["buy_hold"]:
        print(f"  PASS Beats buy and hold : "
              f"{bt['total_ret']:.2%} vs {bt['buy_hold']:.2%}")
    else:
        print(f"  FAIL Trails buy and hold: "
              f"{bt['total_ret']:.2%} vs {bt['buy_hold']:.2%}")
    if bt["sharpe"] >= SHARPE_PASS:
        print(f"  PASS Sharpe above {SHARPE_PASS:.1f}   : {bt['sharpe']:.2f}")
    else:
        print(f"  FAIL Sharpe below {SHARPE_PASS:.1f}   : {bt['sharpe']:.2f}")
    if bt["max_dd"] >= -0.15:
        print(f"  PASS Drawdown under 15% : {bt['max_dd']:.2%}")
    else:
        print(f"  FAIL Drawdown over 15%  : {bt['max_dd']:.2%}")
    print(f"\n  VERDICT: {checks}/4")

    if mq["precision_60"] >= PRECISION_GATE:
        print(f"  PASS Precision @ 0.60   : {mq['precision_60']:.2%}")
    else:
        print(f"  FAIL Precision @ 0.60   : {mq['precision_60']:.2%}")
    if mq["lift_60"] >= LIFT_GATE:
        print(f"  PASS Lift @ 0.60        : {mq['lift_60']:.2f}x")
    else:
        print(f"  FAIL Lift @ 0.60        : {mq['lift_60']:.2f}x")

    # Monte Carlo
    print(f"\n  Monte Carlo (5,000 simulations)...")
    real_rets = np.array([p.realized_pnl for p in all_closed])
    mc_pass   = False
    if len(real_rets) >= 5 and real_rets.std() > 0:
        real_sharpe = (real_rets.mean() / real_rets.std() * np.sqrt(252))
        rng = np.random.default_rng(42)
        sims = []
        for _ in range(5000):
            s = rng.choice(real_rets, size=len(real_rets), replace=True)
            sims.append(s.mean() / s.std() * np.sqrt(252)
                        if s.std() > 0 else 0.0)
        sims    = np.array(sims)
        pct     = (sims < real_sharpe).mean() * 100
        p_val   = 1 - pct / 100
        mc_pass = pct >= 95
        print(f"  Real Sharpe  : {real_sharpe:.3f}")
        print(f"  Beats random : {pct:.1f}%")
        print(f"  p-value      : {p_val:.3f}")
        print(f"  Trades       : {len(real_rets)}")
        print(f"  Result       : {'PASS' if mc_pass else 'FAIL'}")
        if len(real_rets) < 100:
            print("  WARNING: <100 trades — MC validity questionable")
    else:
        print("  Not enough trades for Monte Carlo")

    wf_pass = wf_pass_count >= 3
    print(f"\n  Walk Forward: {wf_pass_count}/{len(wf_results)} "
          f"({'PASS' if wf_pass else 'FAIL'})")

    total_time = time.time() - t0
    print(f"\n{'=' * 55}")
    model_pass = (mq["precision_60"] >= PRECISION_GATE and
                  mq["lift_60"] >= LIFT_GATE)
    all_pass = checks >= 3 and mc_pass and wf_pass and model_pass
    if all_pass:
        print("  OVERALL: ALL PASSED")
        print("  APPROVED FOR PAPER TRADING")
    elif checks >= 2:
        print("  OVERALL: INVESTIGATE — partial pass, review metrics")
    else:
        print("  OVERALL: FAIL — review before proceeding")
    print(f"  Total runtime: {total_time:.0f} seconds")
    print(f"{'=' * 55}")

    return {
        "status":         "success",
        "total_ret":      round(bt["total_ret"], 4),
        "total_pnl":      round(bt["total_pnl"], 2),
        "buy_hold":       round(bt["buy_hold"], 4),
        "sharpe":         round(bt["sharpe"], 3),
        "max_dd":         round(bt["max_dd"], 4),
        "n_trades":       bt["n_trades"],
        "win_rate":       round(bt["win_rate"], 4),
        "trades_per_day": round(bt["trades_per_day"], 2),
        "final_pool":     round(bt["final_pool"], 2),
        "bull_days":      bt["bull_days"],
        "bear_days":      bt["bear_days"],
        "bull_perf":      round(bt["bull_perf"], 4),
        "bear_perf":      round(bt["bear_perf"], 4),
        "checks":         checks,
        "mc_pass":        mc_pass,
        "wf_pass":        wf_pass,
        "wf_count":       wf_pass_count,
        "base_rate":      round(mq["base_rate"], 4),
        "precision_60":   round(mq["precision_60"], 4),
        "lift_60":        round(mq["lift_60"], 4),
        "model_pass":     model_pass,
        "runtime_s":      round(total_time, 0),
    }


@app.local_entrypoint()
def main():
    print("=" * 55)
    print("  Q-ALPHA | EXP-0013 | Small/Mid-Cap + LightGBM + Option D")
    print("  Running on Modal cloud")
    print("=" * 55)

    result = run_exp013.remote()

    if result.get("status") != "success":
        print(f"Error: {result}")
        return

    print(f"\n  Final Summary:")
    print(f"  Return        : {result['total_ret']:.2%}")
    print(f"  PnL           : ${result['total_pnl']:,.2f}")
    print(f"  Sharpe        : {result['sharpe']:.2f}")
    print(f"  Drawdown      : {result['max_dd']:.2%}")
    print(f"  Trades        : {result['n_trades']}")
    print(f"  Win Rate      : {result['win_rate']:.2%}")
    print(f"  Avg Trades/Day: {result['trades_per_day']:.2f}")
    print(f"  Pool at end   : ${result['final_pool']:,.2f}")
    print(f"  Base Rate     : {result['base_rate']:.2%}")
    print(f"  Precision@0.60: {result['precision_60']:.2%}")
    print(f"  Lift@0.60     : {result['lift_60']:.2f}x")
    print(f"  BULL days     : {result['bull_days']}")
    print(f"  BEAR days     : {result['bear_days']}")
    print(f"  BULL perf     : {result['bull_perf']:.2%}")
    print(f"  BEAR perf     : {result['bear_perf']:.2%}")
    print(f"  Checks        : {result['checks']}/4")
    print(f"  Model Quality : {'PASS' if result['model_pass'] else 'FAIL'}")
    print(f"  MC            : {'PASS' if result['mc_pass'] else 'FAIL'}")
    print(f"  Walk Fwd      : {result['wf_count']}/4 "
          f"({'PASS' if result['wf_pass'] else 'FAIL'})")
    print(f"  Runtime       : {result['runtime_s']:.0f}s")
