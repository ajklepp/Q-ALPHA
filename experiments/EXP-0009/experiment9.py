# ============================================================
# Q-ALPHA | EXP-0009 | experiment9.py
# Polygon.io data source | Train 2019-2022 | Sim 2023-2024
# Fixes from EXP-0008: more positions, lower threshold, WF
# ============================================================
import os
import modal
import warnings
warnings.filterwarnings("ignore")

app = modal.App("q-alpha-exp009b")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install([
        "pandas", "pandas-ta",
        "scikit-learn", "numpy", "requests",
    ])
)

polygon_secret = modal.Secret.from_name("polygon-api-key")

UNIVERSE = [
    "AAPL","MSFT","NVDA","GOOGL","META","AMZN","TSLA","AMD",
    "INTC","CRM","ORCL","ADBE","QCOM","TXN","AMAT","LRCX",
    "MU","SNPS","CDNS","MRVL","KLAC","FTNT","PANW","CRWD",
    "NOW","SNOW","DDOG","ZS","NET","MDB","TEAM","HUBS",
    "JPM","BAC","WFC","GS","MS","C","BLK","SCHW","AXP",
    "V","MA","PYPL","COF","USB","PNC","TFC","MTB","CFG",
    "FITB","KEY","RF","HBAN","ZION","WAL","FHN",
    "JNJ","UNH","LLY","ABBV","PFE","MRK","TMO","ABT",
    "DHR","BMY","AMGN","GILD","REGN","VRTX","BIIB","ILMN",
    "IQV","ZBH","BSX","EW","STE","HSIC",
    "WMT","COST","TGT","HD","LOW","MCD","SBUX","NKE",
    "PG","KO","PEP","CL","EL","ULTA","ROST","TJX",
    "BKNG","MAR","HLT","CCL","RCL","NCLH",
    "XOM","CVX","COP","SLB","EOG","MPC","PSX",
    "VLO","HAL","DVN","OXY","APA",
    "CAT","DE","BA","HON","UPS","FDX","LMT","RTX","GE",
    "MMM","EMR","ETN","PH","ROK","DOV","XYL","IEX",
    "LIN","APD","SHW","FCX","NEM","NUE","VMC","MLM","CF",
    "NEE","DUK","SO","D","AEP","EXC","XEL","ES","WEC",
    "PLD","AMT","EQIX","CCI","SPG","O","DLR","PSA","EQR",
    "NFLX","DIS","CMCSA","T","VZ","TMUS","CHTR",
]
UNIVERSE = list(dict.fromkeys(UNIVERSE))

FEATURE_COLUMNS = [
    "return_1d", "return_5d", "return_10d",
    "close_vs_sma20", "close_vs_sma50",
    "rsi_14", "macd", "macd_signal", "macd_hist",
    "bb_position", "volume_ratio",
    "volatility_10d", "volatility_20d",
]

TRAIN_START    = "2019-01-01"
TRAIN_END      = "2022-12-31"
SIM_START      = "2023-01-01"
SIM_END        = "2024-12-31"
STARTING_CAP   = 100_000.0
MAX_TRADES_DAY = 5
MAX_POSITIONS  = 8
COST_PER_TRADE = 0.0015
S4_RUNNER_DAYS = 30
THRESHOLD_CAP  = 0.52


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

    atr_pct = row.get("atr_14", row["Close"]*0.02) / row["Close"]
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
        if df["Close"].iloc[-1] < 5:
            return None
        if df["Volume"].mean() < 500_000:
            return None

        df["return_1d"]  = df["Close"].pct_change(1)
        df["return_5d"]  = df["Close"].pct_change(5)
        df["return_10d"] = df["Close"].pct_change(10)
        df["sma_20"]     = ta.sma(df["Close"], length=20)
        df["sma_50"]     = ta.sma(df["Close"], length=50)
        df["close_vs_sma20"] = (df["Close"]-df["sma_20"])/df["sma_20"]
        df["close_vs_sma50"] = (df["Close"]-df["sma_50"])/df["sma_50"]
        df["rsi_14"]     = ta.rsi(df["Close"], length=14)
        macd = ta.macd(df["Close"], fast=12, slow=26, signal=9)
        df["macd"]        = macd["MACD_12_26_9"]
        df["macd_signal"] = macd["MACDs_12_26_9"]
        df["macd_hist"]   = macd["MACDh_12_26_9"]
        bbands = ta.bbands(df["Close"], length=20, std=2)
        df["bb_upper"]    = bbands.iloc[:, 0]
        df["bb_lower"]    = bbands.iloc[:, 2]
        df["bb_position"] = (df["Close"]-df["bb_lower"])/(df["bb_upper"]-df["bb_lower"])
        df["volume_sma_20"]  = ta.sma(df["Volume"], length=20)
        df["volume_ratio"]   = df["Volume"]/df["volume_sma_20"]
        df["volatility_10d"] = df["return_1d"].rolling(10).std()
        df["volatility_20d"] = df["return_1d"].rolling(20).std()
        df["atr_14"]         = ta.atr(df["High"],df["Low"],df["Close"],length=14)
        df["future_return_5d"] = df["Close"].pct_change(5).shift(-5)
        df["target"] = (df["future_return_5d"] > 0.01).astype(int)
        df["ticker"] = ticker
        df.dropna(inplace=True)
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

    def deployed_capital(self):
        return sum(self.position_size * s["alloc"]
                   for s in self.slices.values()
                   if not s["closed"])

    def update(self, current_price, current_date):
        released = 0.0
        for sid, s in self.slices.items():
            if s["closed"]: continue
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
        open_sids = [sid for sid,s in self.slices.items()
                     if not s["closed"]]
        if open_sids == [4]:
            self.is_bonus_runner = True
        if all(s["closed"] for s in self.slices.values()):
            self.fully_closed = True
        return released


def find_threshold(model, df):
    from sklearn.metrics import precision_recall_curve
    probs = model.predict_proba(df[FEATURE_COLUMNS])[:, 1]
    precs, recs, thrs = precision_recall_curve(df["target"], probs)
    best_t, best_p = 0.5, 0
    for p, r, t in zip(precs, recs, thrs):
        if r >= 0.20 and p > best_p:
            best_p, best_t = p, t
    return min(best_t, THRESHOLD_CAP)


def run_simulation(test_df, price_pivot, threshold, label=""):
    import pandas as pd
    import numpy as np

    all_probs = test_df["prob"].values
    test_df = test_df.copy()
    test_df["signal"] = (all_probs >= threshold).astype(int)

    signal_rows = test_df[test_df["signal"] == 1].copy()
    scores, profiles = [], []
    for _, row in signal_rows.iterrows():
        s, prof = score_candidate(row.to_dict(), row["prob"])
        scores.append(s)
        profiles.append(prof)
    signal_rows["score"]   = scores
    signal_rows["profile"] = profiles

    signal_lookup = {}
    for date, grp in signal_rows.groupby(level=0):
        signal_lookup[date] = grp.sort_values("score", ascending=False)

    pool            = STARTING_CAP
    active          = []
    runners         = []
    all_closed      = []
    equity_curve    = []
    prev_day        = None
    day_trade_count = 0
    trades_taken    = 0
    profile_counts  = {}

    all_dates = sorted(test_df.index.unique())

    for current_date in all_dates:
        cur_day = current_date.date()
        if cur_day != prev_day:
            day_trade_count = 0
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
            elif pos.is_bonus_runner:
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

        active_tickers = {p.ticker for p in active}

        if (current_date in signal_lookup and
                len(active) < MAX_POSITIONS and
                day_trade_count < MAX_TRADES_DAY):

            candidates = signal_lookup[current_date]

            for _, row in candidates.iterrows():
                if len(active) >= MAX_POSITIONS:
                    break
                if day_trade_count >= MAX_TRADES_DAY:
                    break

                ticker = row.get("ticker")
                if ticker not in UNIVERSE:
                    continue
                if ticker in active_tickers:
                    continue

                try:
                    entry_px = float(price_pivot.loc[current_date, ticker])
                except Exception:
                    continue

                pos_size = STARTING_CAP / MAX_POSITIONS
                if pool < pos_size * 0.5:
                    continue

                atr_val = float(row.get("atr_14", entry_px*0.02))
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
                day_trade_count += 1
                trades_taken    += 1

                prof = row.get("profile", "UNKNOWN")
                profile_counts[prof] = profile_counts.get(prof, 0) + 1

        deployed = (
            sum(p.deployed_capital() for p in active) +
            sum(p.deployed_capital() for p in runners))
        equity_curve.append({
            "date": current_date,
            "equity": pool + deployed})

    if all_dates:
        last_date = all_dates[-1]
        for pos in active + runners:
            for sid, s in pos.slices.items():
                if not s["closed"]:
                    try:
                        lp = float(price_pivot.loc[last_date, pos.ticker])
                    except Exception:
                        lp = pos.entry_price
                    gross = (lp-pos.entry_price)/pos.entry_price
                    pnl   = pos.position_size*s["alloc"]*(gross-COST_PER_TRADE)
                    pos.realized_pnl += pnl
                    s["closed"] = True
                    pool += pos.position_size*s["alloc"] + pnl
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
    sharpe    = (eq_df["ret"].mean()/eq_df["ret"].std()*np.sqrt(252)
                 if eq_df["ret"].std() > 0 else 0.0)
    roll_max  = eq_df["equity"].expanding().max()
    drawdown  = (eq_df["equity"]-roll_max)/roll_max
    max_dd    = drawdown.min()

    n_trades   = len(all_closed)
    win_trades = sum(1 for p in all_closed if p.realized_pnl > 0)
    win_rate   = win_trades/n_trades if n_trades > 0 else 0

    bh_list = []
    for ticker in UNIVERSE:
        try:
            col = price_pivot[ticker].dropna()
            if len(col) > 1:
                bh_list.append((col.iloc[-1]-col.iloc[0])/col.iloc[0])
        except Exception:
            pass
    buy_hold = float(np.mean(bh_list)) if bh_list else 0.0

    checks = 0
    if total_ret > 0:          checks += 1
    if total_ret > buy_hold:   checks += 1
    if sharpe >= 1.0:          checks += 1
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
        "checks": checks,
        "profile_counts": profile_counts,
        "all_closed": all_closed,
        "equity_curve": eq_df,
        "n_signals": int(test_df["signal"].sum()),
    }


@app.function(image=image, timeout=1800, memory=8192, secrets=[polygon_secret])
def run_exp009():
    import pandas as pd
    import numpy as np
    from sklearn.ensemble import RandomForestClassifier
    import warnings
    import time

    warnings.filterwarnings("ignore")
    api_key = os.environ["POLYGON_API_KEY"]

    t0 = time.time()
    print("="*55)
    print("EXP-0009: Polygon.io Full Pipeline Backtest")
    print(f"Universe : {len(UNIVERSE)} stocks")
    print(f"Train    : {TRAIN_START} to {TRAIN_END}")
    print(f"Simulate : {SIM_START} to {SIM_END}")
    print("="*55)

    # ── STEP 1: Download from Polygon.io ─────────────────────
    print("\nSTEP 1: Downloading universe from Polygon.io...")
    all_data = []
    for i, ticker in enumerate(UNIVERSE):
        df = process_stock(ticker, TRAIN_START, SIM_END, api_key)
        if df is not None:
            all_data.append(df)
        if (i + 1) % 25 == 0:
            print(f"  {i+1}/{len(UNIVERSE)} tickers processed "
                  f"[{time.time()-t0:.0f}s]")

    print(f"Loaded {len(all_data)} stocks  "
          f"[{time.time()-t0:.0f}s elapsed]")

    if not all_data:
        return {"status": "error", "message": "No data from Polygon.io"}

    combined = pd.concat(all_data)
    combined.sort_index(inplace=True)
    print(f"Total rows: {len(combined):,}")

    # ── STEP 2: Train model on 2019-2022 ─────────────────────
    print("\nSTEP 2: Training Random Forest on 2019-2022...")
    train_df = combined[
        (combined.index >= pd.Timestamp(TRAIN_START)) &
        (combined.index <= pd.Timestamp(TRAIN_END))
    ].copy()
    test_df  = combined[
        (combined.index >= pd.Timestamp(SIM_START)) &
        (combined.index <= pd.Timestamp(SIM_END))
    ].copy()
    print(f"Train: {len(train_df):,}  Test: {len(test_df):,}")

    model = RandomForestClassifier(
        n_estimators=200, max_depth=4,
        min_samples_split=40, min_samples_leaf=20,
        class_weight="balanced", random_state=42, n_jobs=-1)
    model.fit(train_df[FEATURE_COLUMNS], train_df["target"])

    threshold = find_threshold(model, train_df)
    print(f"Threshold: {threshold:.3f} (capped at {THRESHOLD_CAP})  "
          f"[{time.time()-t0:.0f}s elapsed]")

    # ── STEP 3: Batch predict test period ────────────────────
    print("\nSTEP 3: Batch predicting 2023-2024...")
    all_probs = model.predict_proba(
        test_df[FEATURE_COLUMNS].values)[:, 1]
    test_df["prob"] = all_probs
    print(f"Predictions done  [{time.time()-t0:.0f}s elapsed]")

    # ── STEP 4: Build price pivot ────────────────────────────
    print("\nSTEP 4: Building price pivot...")
    price_pivot = test_df.pivot_table(
        index=test_df.index,
        columns="ticker",
        values="Close",
        aggfunc="last")
    print(f"Price pivot: {price_pivot.shape}  "
          f"[{time.time()-t0:.0f}s elapsed]")

    # ── STEP 5: Day-by-day simulation 2023-2024 ────────────
    print("\nSTEP 5: Running day-by-day simulation...")
    bt = run_simulation(test_df, price_pivot, threshold,
                        label="OOS 2023-2024")
    if bt is None:
        return {"status": "error", "message": "Simulation failed"}

    print(f"Simulation done  [{time.time()-t0:.0f}s elapsed]")

    # ── STEP 6: Walk forward across 4 regimes ────────────────
    print("\nSTEP 6: Walk forward across 4 regimes...")
    wf_windows = [
        ("2019-01-01","2021-01-01","2021-01-01","2022-01-01","2021 Bull"),
        ("2019-01-01","2022-01-01","2022-01-01","2023-01-01","2022 Bear"),
        ("2019-01-01","2023-01-01","2023-01-01","2024-01-01","2023 Recovery"),
        ("2019-01-01","2024-01-01","2024-01-01","2024-12-31","2024 AI Rally"),
    ]
    wf_results = []
    for ts, te, vs, ve, label in wf_windows:
        train_w = combined[
            (combined.index >= pd.Timestamp(ts)) &
            (combined.index <  pd.Timestamp(te))
        ]
        if len(train_w) < 500:
            continue
        test_w = combined[
            (combined.index >= pd.Timestamp(vs)) &
            (combined.index <= pd.Timestamp(ve))
        ].copy()
        if len(test_w) < 50:
            continue

        m_w = RandomForestClassifier(
            n_estimators=200, max_depth=4,
            min_samples_split=40, min_samples_leaf=20,
            class_weight="balanced", random_state=42, n_jobs=-1)
        m_w.fit(train_w[FEATURE_COLUMNS], train_w["target"])
        thr_w = find_threshold(m_w, train_w)
        test_w["prob"] = m_w.predict_proba(
            test_w[FEATURE_COLUMNS].values)[:, 1]
        pp_w = test_w.pivot_table(
            index=test_w.index, columns="ticker",
            values="Close", aggfunc="last")
        res = run_simulation(test_w, pp_w, thr_w, label=label)
        if res is None:
            continue
        passed = res["total_ret"] > 0 and res["checks"] >= 2
        wf_results.append({**res, "passed": passed})
        print(f"  {'PASS' if passed else 'FAIL'} {label}: "
              f"Ret={res['total_ret']:.2%}  "
              f"Sharpe={res['sharpe']:.2f}  "
              f"Trades={res['n_trades']}")

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

    print(f"\n{'='*55}")
    print(f"EXP-0009 RESULTS")
    print(f"{'='*55}")
    print(f"  Total runtime      : {time.time()-t0:.0f}s")
    print(f"\n  Starting Capital   : ${STARTING_CAP:>12,.2f}")
    print(f"  Final Equity       : ${bt['total_pnl']+STARTING_CAP:>12,.2f}")
    print(f"  Total Return       : {bt['total_ret']:>10.2%}")
    print(f"  Total PnL          : ${bt['total_pnl']:>12,.2f}")
    print(f"  Buy and Hold       : {bt['buy_hold']:>10.2%}")
    print(f"  Sharpe Ratio       : {bt['sharpe']:>10.2f}")
    print(f"  Max Drawdown       : {bt['max_dd']:>10.2%}")
    print(f"  Total Trades       : {bt['n_trades']}")
    print(f"  Win Rate           : {bt['win_rate']:.2%}")
    print(f"  Total signals      : {bt['n_signals']:,}")
    print(f"  Trades taken       : {bt['trades_taken']}")

    print(f"\n  Stop Breakdown:")
    for st in ["MASTER","S1","S2","S3","S4"]:
        print(f"    {st}: {stop_counts.get(st,0)} exits")

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
              f"WR={wins/len(pnls):.0%}")

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
    if bt["sharpe"] >= 1.0:
        print(f"  PASS Sharpe above 1.0   : {bt['sharpe']:.2f}")
    else:
        print(f"  FAIL Sharpe below 1.0   : {bt['sharpe']:.2f}")
    if bt["max_dd"] >= -0.15:
        print(f"  PASS Drawdown under 15% : {bt['max_dd']:.2%}")
    else:
        print(f"  FAIL Drawdown over 15%  : {bt['max_dd']:.2%}")
    print(f"\n  VERDICT: {checks}/4")

    # Monte Carlo
    print(f"\n  Monte Carlo (5,000 simulations)...")
    real_rets = np.array([p.realized_pnl for p in all_closed])
    mc_pass   = False
    if len(real_rets) >= 5 and real_rets.std() > 0:
        real_sharpe = (real_rets.mean()/real_rets.std()*np.sqrt(252))
        rng = np.random.default_rng(42)
        sims = []
        for _ in range(5000):
            s = rng.choice(real_rets, size=len(real_rets), replace=True)
            sims.append(s.mean()/s.std()*np.sqrt(252)
                        if s.std() > 0 else 0.0)
        sims    = np.array(sims)
        pct     = (sims < real_sharpe).mean() * 100
        p_val   = 1 - pct/100
        mc_pass = pct >= 95
        print(f"  Real Sharpe  : {real_sharpe:.3f}")
        print(f"  Beats random : {pct:.1f}%")
        print(f"  p-value      : {p_val:.3f}")
        print(f"  Trades       : {len(real_rets)}")
        print(f"  Result       : {'PASS' if mc_pass else 'FAIL'}")
        if len(real_rets) < 100:
            print(f"  WARNING: <100 trades — MC validity questionable")
    else:
        print("  Not enough trades for Monte Carlo")

    wf_pass = wf_pass_count >= 3
    print(f"\n  Walk Forward: {wf_pass_count}/{len(wf_results)} "
          f"({'PASS' if wf_pass else 'FAIL'})")

    total_time = time.time() - t0
    print(f"\n{'='*55}")
    all_pass = checks >= 3 and mc_pass and wf_pass
    if all_pass:
        print(f"  OVERALL: ALL PASSED")
        print(f"  APPROVED FOR PAPER TRADING")
    else:
        print(f"  OVERALL: NOT ALL PASSED — REVIEW")
    print(f"  Total runtime: {total_time:.0f} seconds")
    print(f"{'='*55}")

    return {
        "status":    "success",
        "total_ret": round(bt["total_ret"], 4),
        "total_pnl": round(bt["total_pnl"], 2),
        "buy_hold":  round(bt["buy_hold"], 4),
        "sharpe":    round(bt["sharpe"], 3),
        "max_dd":    round(bt["max_dd"], 4),
        "n_trades":  bt["n_trades"],
        "win_rate":  round(bt["win_rate"], 4),
        "checks":    checks,
        "mc_pass":   mc_pass,
        "wf_pass":   wf_pass,
        "wf_count":  wf_pass_count,
        "runtime_s": round(total_time, 0),
    }


@app.local_entrypoint()
def main():
    print("="*55)
    print("  Q-ALPHA | EXP-0009 | Polygon.io Pipeline")
    print("  Running on Modal cloud")
    print("="*55)

    result = run_exp009.remote()

    if result.get("status") != "success":
        print(f"Error: {result}")
        return

    print(f"\n  Final Summary:")
    print(f"  Return    : {result['total_ret']:.2%}")
    print(f"  PnL       : ${result['total_pnl']:,.2f}")
    print(f"  Sharpe    : {result['sharpe']:.2f}")
    print(f"  Drawdown  : {result['max_dd']:.2%}")
    print(f"  Trades    : {result['n_trades']}")
    print(f"  Win Rate  : {result['win_rate']:.2%}")
    print(f"  Checks    : {result['checks']}/4")
    print(f"  MC        : {'PASS' if result['mc_pass'] else 'FAIL'}")
    print(f"  Walk Fwd  : {result['wf_count']}/4 "
          f"({'PASS' if result['wf_pass'] else 'FAIL'})")
    print(f"  Runtime   : {result['runtime_s']:.0f}s")
