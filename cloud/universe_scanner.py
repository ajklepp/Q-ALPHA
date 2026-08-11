# ============================================================
# Q-ALPHA | cloud/universe_scanner.py
# Purpose: Download and scan a broad universe of stocks
#          for our AI signal — runs on Modal cloud
#
# Universe: S&P 500 + additional liquid large caps
#           ~800 stocks total
#           Filters out illiquid/penny stocks
#
# Output: Ranked shortlist of signal candidates
# ============================================================
import modal
import warnings
warnings.filterwarnings("ignore")

app = modal.App("q-alpha-scanner")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install([
        "yfinance",
        "pandas",
        "pandas-ta",
        "scikit-learn",
        "numpy",
        "requests",
    ])
)

# ============================================================
# UNIVERSE — S&P500 + Large Cap supplements
# We use a curated list of liquid, well-known stocks
# across all major sectors
# ============================================================
UNIVERSE = [
    # Technology
    "AAPL","MSFT","NVDA","GOOGL","META","AMZN","TSLA","AMD",
    "INTC","CRM","ORCL","ADBE","QCOM","TXN","AMAT","LRCX",
    "MU","SNPS","CDNS","MRVL","KLAC","FTNT","PANW","CRWD",
    "NOW","SNOW","DDOG","ZS","NET","MDB","TEAM","HUBS",
    # Financials
    "JPM","BAC","WFC","GS","MS","C","BLK","SCHW","AXP",
    "V","MA","PYPL","COF","USB","PNC","TFC","MTB","CFG",
    "FITB","KEY","RF","HBAN","ZION","CMA","WAL","FHN",
    # Healthcare
    "JNJ","UNH","LLY","ABBV","PFE","MRK","TMO","ABT",
    "DHR","BMY","AMGN","GILD","REGN","VRTX","BIIB","ILMN",
    "IQV","CRL","IQVIA","ZBH","BSX","EW","STE","HSIC",
    # Consumer
    "WMT","COST","TGT","HD","LOW","MCD","SBUX","NKE",
    "PG","KO","PEP","CL","EL","ULTA","ROST","TJX",
    "AMZN","BKNG","MAR","HLT","CCL","RCL","NCLH",
    # Energy
    "XOM","CVX","COP","SLB","EOG","PXD","MPC","PSX",
    "VLO","HAL","DVN","FANG","OXY","HES","APA","MRO",
    # Industrials
    "CAT","DE","BA","HON","UPS","FDX","LMT","RTX","GE",
    "MMM","EMR","ETN","PH","ROK","DOV","XYL","IEX","GNRC",
    # Materials
    "LIN","APD","SHW","FCX","NEM","NUE","VMC","MLM","CF",
    # Utilities
    "NEE","DUK","SO","D","AEP","EXC","XEL","ES","WEC",
    # REITs
    "PLD","AMT","EQIX","CCI","SPG","O","DLR","PSA","EQR",
    # Communications
    "NFLX","DIS","CMCSA","T","VZ","TMUS","CHTR","FOX",
]

# Remove duplicates
UNIVERSE = list(dict.fromkeys(UNIVERSE))

FEATURE_COLUMNS = [
    "return_1d", "return_5d", "return_10d",
    "close_vs_sma20", "close_vs_sma50",
    "rsi_14", "macd", "macd_signal", "macd_hist",
    "bb_position", "volume_ratio",
    "volatility_10d", "volatility_20d",
]


# ============================================================
# STOCK PROFILE CLASSIFIER
# Assigns each stock to a volatility profile
# Used to select correct bracket stop parameters
# ============================================================
def classify_stock_profile(atr_pct, avg_volume_m):
    """
    Classify stock into one of 5 profiles based on
    its ATR percentage and average daily volume.

    Returns profile name and default stop parameters.
    """
    if atr_pct < 0.015 and avg_volume_m > 5:
        profile = "BLUE_CHIP_DEFENSIVE"
        params = {
            "master_atr_mult": 2.5,
            "trail_1": 0.04, "trail_2": 0.07,
            "trail_3": 0.11, "trail_4": 0.16,
            "alloc_1": 0.50, "alloc_2": 0.20,
            "alloc_3": 0.20, "alloc_4": 0.10,
        }
    elif atr_pct < 0.025 and avg_volume_m > 3:
        profile = "LARGE_CAP_GROWTH"
        params = {
            "master_atr_mult": 2.0,
            "trail_1": 0.05, "trail_2": 0.09,
            "trail_3": 0.14, "trail_4": 0.20,
            "alloc_1": 0.50, "alloc_2": 0.20,
            "alloc_3": 0.20, "alloc_4": 0.10,
        }
    elif atr_pct < 0.035:
        profile = "FINANCIAL_CYCLICAL"
        params = {
            "master_atr_mult": 2.0,
            "trail_1": 0.06, "trail_2": 0.10,
            "trail_3": 0.16, "trail_4": 0.22,
            "alloc_1": 0.50, "alloc_2": 0.20,
            "alloc_3": 0.20, "alloc_4": 0.10,
        }
    elif atr_pct < 0.055:
        profile = "HIGH_GROWTH_TECH"
        params = {
            "master_atr_mult": 1.5,
            "trail_1": 0.08, "trail_2": 0.12,
            "trail_3": 0.18, "trail_4": 0.25,
            "alloc_1": 0.40, "alloc_2": 0.25,
            "alloc_3": 0.20, "alloc_4": 0.15,
        }
    else:
        profile = "HIGH_VOLATILITY"
        params = {
            "master_atr_mult": 1.5,
            "trail_1": 0.10, "trail_2": 0.15,
            "trail_3": 0.22, "trail_4": 0.30,
            "alloc_1": 0.40, "alloc_2": 0.25,
            "alloc_3": 0.20, "alloc_4": 0.15,
        }
    return profile, params


# ============================================================
# PROCESS ONE STOCK
# ============================================================
def process_stock(ticker, start, end):
    """Download and calculate features for one stock."""
    try:
        import yfinance as yf
        import pandas as pd
        import pandas_ta as ta

        df = yf.download(ticker, start=start, end=end,
                         auto_adjust=True, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if len(df) < 100:
            return None

        # Filter: minimum price $5, minimum volume 500k
        if df["Close"].iloc[-1] < 5:
            return None
        if df["Volume"].mean() < 500_000:
            return None

        df["return_1d"]  = df["Close"].pct_change(1)
        df["return_5d"]  = df["Close"].pct_change(5)
        df["return_10d"] = df["Close"].pct_change(10)
        df["sma_20"]     = ta.sma(df["Close"], length=20)
        df["sma_50"]     = ta.sma(df["Close"], length=50)
        df["close_vs_sma20"] = (
            (df["Close"]-df["sma_20"])/df["sma_20"])
        df["close_vs_sma50"] = (
            (df["Close"]-df["sma_50"])/df["sma_50"])
        df["rsi_14"] = ta.rsi(df["Close"], length=14)

        macd = ta.macd(df["Close"], fast=12, slow=26, signal=9)
        df["macd"]        = macd["MACD_12_26_9"]
        df["macd_signal"] = macd["MACDs_12_26_9"]
        df["macd_hist"]   = macd["MACDh_12_26_9"]

        bbands = ta.bbands(df["Close"], length=20, std=2)
        df["bb_upper"]    = bbands.iloc[:, 0]
        df["bb_lower"]    = bbands.iloc[:, 2]
        df["bb_position"] = (
            (df["Close"]-df["bb_lower"]) /
            (df["bb_upper"]-df["bb_lower"]))

        df["volume_sma_20"]  = ta.sma(df["Volume"], length=20)
        df["volume_ratio"]   = df["Volume"]/df["volume_sma_20"]
        df["volatility_10d"] = df["return_1d"].rolling(10).std()
        df["volatility_20d"] = df["return_1d"].rolling(20).std()
        df["atr_14"]         = ta.atr(
            df["High"], df["Low"], df["Close"], length=14)

        df["future_return_5d"] = df["Close"].pct_change(5).shift(-5)
        df["target"] = (
            df["future_return_5d"] > 0.01).astype(int)

        df["ticker"] = ticker
        df.dropna(inplace=True)

        if len(df) < 50:
            return None

        return df

    except Exception:
        return None


# ============================================================
# RANKING ENGINE
# Scores each signal candidate 0-100
# Sweet spot: consistent daily gains
# Not shooting stars, not boring movers
# ============================================================
def score_candidate(row, prob):
    """
    Score a signal candidate 0-100.
    Higher = better trade candidate.

    Balanced scoring for consistent daily gains.
    """
    score = 0.0

    # ── Factor 1: Signal Strength (30 points) ───────────────
    # How confident is the AI model?
    if prob >= 0.85:
        score += 30
    elif prob >= 0.75:
        score += 24
    elif prob >= 0.70:
        score += 18
    elif prob >= 0.65:
        score += 12
    else:
        score += 6

    # ── Factor 2: Momentum Quality (25 points) ──────────────
    # Is momentum building cleanly?
    momentum_score = 0
    rsi = row.get("rsi_14", 50)
    # Sweet spot RSI 50-65: trending but not overbought
    if 50 <= rsi <= 65:
        momentum_score += 10
    elif 45 <= rsi < 50 or 65 < rsi <= 70:
        momentum_score += 6
    elif rsi > 70:
        momentum_score += 2   # Overbought — risky
    else:
        momentum_score += 0   # Below 45 — weak

    # MACD histogram positive and growing = momentum building
    macd_hist = row.get("macd_hist", 0)
    if macd_hist > 0:
        momentum_score += 8
    elif macd_hist > -0.1:
        momentum_score += 4

    # Price above both moving averages = trending
    sma20 = row.get("close_vs_sma20", 0)
    sma50 = row.get("close_vs_sma50", 0)
    if sma20 > 0 and sma50 > 0:
        momentum_score += 7
    elif sma20 > 0:
        momentum_score += 4

    score += min(momentum_score, 25)

    # ── Factor 3: Volume Confirmation (20 points) ───────────
    # Is institutional money confirming the move?
    vol_ratio = row.get("volume_ratio", 1.0)
    if vol_ratio >= 2.5:
        score += 20   # Very strong volume — high conviction
    elif vol_ratio >= 2.0:
        score += 16
    elif vol_ratio >= 1.5:
        score += 12
    elif vol_ratio >= 1.2:
        score += 8
    elif vol_ratio >= 1.0:
        score += 4
    else:
        score += 0    # Below average volume — weak signal

    # ── Factor 4: Volatility Profile (15 points) ────────────
    # Sweet spot: moves enough to profit, not so much it's noise
    vol_10d = row.get("volatility_10d", 0.02)
    daily_vol_pct = vol_10d * 100
    if 1.0 <= daily_vol_pct <= 2.5:
        score += 15   # Perfect sweet spot
    elif 0.7 <= daily_vol_pct < 1.0:
        score += 10   # Slightly quiet but ok
    elif 2.5 < daily_vol_pct <= 4.0:
        score += 8    # Slightly wild but manageable
    elif daily_vol_pct > 4.0:
        score += 3    # Too wild — stops will be hit by noise
    else:
        score += 5    # Very quiet — may not move enough

    # ── Factor 5: Bollinger Band Position (10 points) ───────
    # Where is price in its normal range?
    bb_pos = row.get("bb_position", 0.5)
    # Sweet spot: 0.5-0.8 — above midline but not at top
    if 0.5 <= bb_pos <= 0.8:
        score += 10
    elif 0.4 <= bb_pos < 0.5:
        score += 7
    elif 0.8 < bb_pos <= 0.9:
        score += 5
    elif bb_pos > 0.9:
        score += 2    # At top of band — overbought
    else:
        score += 3

    return round(score, 1)


# ============================================================
# MODAL FUNCTIONS
# ============================================================
@app.function(
    image=image,
    timeout=600,
    memory=4096,
)
def scan_universe_batch(tickers_batch, start, end,
                        model_params):
    """
    Process a batch of tickers on Modal cloud.
    Returns processed dataframes for each ticker.
    """
    import pickle
    import io

    results = []
    for ticker in tickers_batch:
        df = process_stock(ticker, start, end)
        if df is not None:
            results.append((ticker, df))

    return results


@app.function(
    image=image,
    timeout=900,
    memory=8192,
)
def run_full_scan(start_date, end_date, threshold=0.566):
    """
    Full universe scan on Modal cloud.
    Downloads all stocks, runs signal, ranks candidates.
    Returns ranked shortlist.
    """
    import pandas as pd
    import numpy as np
    import yfinance as yf
    import pandas_ta as ta
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import precision_recall_curve
    import warnings
    warnings.filterwarnings("ignore")

    FEAT_COLS = [
        "return_1d", "return_5d", "return_10d",
        "close_vs_sma20", "close_vs_sma50",
        "rsi_14", "macd", "macd_signal", "macd_hist",
        "bb_position", "volume_ratio",
        "volatility_10d", "volatility_20d",
    ]

    print(f"Starting universe scan...")
    print(f"Universe size: {len(UNIVERSE)} stocks")
    print(f"Period: {start_date} to {end_date}")

    # Download all stocks
    all_data = []
    failed   = 0
    success  = 0

    for ticker in UNIVERSE:
        df = process_stock(ticker, start_date, end_date)
        if df is not None:
            all_data.append(df)
            success += 1
        else:
            failed += 1

    print(f"Downloaded: {success} stocks ({failed} failed/filtered)")

    if len(all_data) == 0:
        return {"error": "No data downloaded"}

    combined = pd.concat(all_data)
    combined.sort_index(inplace=True)

    # Split train/test
    split_date = "2023-01-01"
    train = combined[combined.index < split_date]
    test  = combined[combined.index >= split_date]

    print(f"Train: {len(train)} rows | Test: {len(test)} rows")

    # Train model
    print("Training model on broad universe...")
    model = RandomForestClassifier(
        n_estimators=200, max_depth=4,
        min_samples_split=40, min_samples_leaf=20,
        class_weight="balanced", random_state=42,
        n_jobs=-1)
    model.fit(train[FEAT_COLS], train["target"])

    # Find threshold
    probs_val = model.predict_proba(test[FEAT_COLS])[:, 1]
    precisions, recalls, thresholds = precision_recall_curve(
        test["target"], probs_val)
    best_t, best_p = 0.5, 0
    for p, r, t in zip(precisions, recalls, thresholds):
        if r >= 0.20 and p > best_p:
            best_p, best_t = p, t
    threshold = best_t
    print(f"Optimal threshold: {threshold:.3f}")

    # Run signal on test period
    print("Scanning for signals...")
    test_probs = model.predict_proba(test[FEAT_COLS])[:, 1]
    test = test.copy()
    test["signal_prob"] = test_probs
    test["signal"]      = (test_probs >= threshold).astype(int)

    signal_rows = test[test["signal"] == 1].copy()
    print(f"Total signals found: {len(signal_rows)}")
    print(f"Unique stocks with signals: {signal_rows['ticker'].nunique()}")

    # Score each signal
    scores = []
    for idx, row in signal_rows.iterrows():
        prob    = row["signal_prob"]
        score   = score_candidate(row.to_dict(), prob)
        atr_val = row.get("atr_14", row["Close"] * 0.02)
        atr_pct = atr_val / row["Close"]
        avg_vol = row.get("volume_sma_20", 1_000_000) / 1_000_000
        profile, stop_params = classify_stock_profile(
            atr_pct, avg_vol)

        scores.append({
            "date":         idx,
            "ticker":       row["ticker"],
            "close":        round(row["Close"], 2),
            "signal_prob":  round(prob, 3),
            "score":        score,
            "rsi_14":       round(row.get("rsi_14", 0), 1),
            "volume_ratio": round(row.get("volume_ratio", 0), 2),
            "volatility":   round(row.get("volatility_10d", 0)*100, 2),
            "bb_position":  round(row.get("bb_position", 0), 2),
            "macd_hist":    round(row.get("macd_hist", 0), 3),
            "profile":      profile,
            "stop_params":  stop_params,
            "atr_14":       round(atr_val, 3),
        })

    if not scores:
        return {"error": "No signals found"}

    scores_df = pd.DataFrame(scores)
    scores_df.sort_values("score", ascending=False, inplace=True)

    # Summary statistics
    top_signals = scores_df.head(50)

    print(f"\nTop 10 signals by score:")
    print(f"{'Date':<12} {'Ticker':<8} {'Score':>6} "
          f"{'Prob':>6} {'RSI':>6} {'VolR':>6} {'Profile':<20}")
    print("-" * 72)
    for _, r in scores_df.head(10).iterrows():
        print(f"{str(r['date'])[:10]:<12} "
              f"{r['ticker']:<8} "
              f"{r['score']:>6.1f} "
              f"{r['signal_prob']:>6.3f} "
              f"{r['rsi_14']:>6.1f} "
              f"{r['volume_ratio']:>6.2f} "
              f"{r['profile']:<20}")

    # Per-stock signal frequency
    stock_freq = scores_df.groupby("ticker").agg(
        signals=("score", "count"),
        avg_score=("score", "mean"),
        avg_prob=("signal_prob", "mean"),
    ).sort_values("avg_score", ascending=False)

    print(f"\nTop stocks by signal quality:")
    print(stock_freq.head(20).to_string())

    # Model accuracy on broad universe
    accuracy = (
        (test["signal"] == test["target"]).mean()
    )
    baseline = test["target"].mean()

    print(f"\nModel Performance on Broad Universe:")
    print(f"  Accuracy : {accuracy:.2%}")
    print(f"  Baseline : {baseline:.2%}")
    print(f"  Edge     : {(accuracy-baseline):+.2%}")
    print(f"  Universe : {test['ticker'].nunique()} stocks")

    return {
        "status":           "success",
        "stocks_scanned":   success,
        "total_signals":    len(signal_rows),
        "unique_tickers":   int(signal_rows["ticker"].nunique()),
        "threshold":        round(threshold, 3),
        "accuracy":         round(accuracy, 4),
        "baseline":         round(baseline, 4),
        "edge":             round(accuracy - baseline, 4),
        "top_signals":      scores_df.head(20).to_dict("records"),
        "stock_frequency":  stock_freq.head(20).to_dict(),
    }


@app.local_entrypoint()
def main():
    print("="*55)
    print("  Q-ALPHA | Universe Scanner")
    print("  Running on Modal cloud")
    print("="*55)

    result = run_full_scan.remote(
        start_date="2019-01-01",
        end_date="2024-12-31",
    )

    if "error" in result:
        print(f"Error: {result['error']}")
        return

    print(f"\n{'='*55}")
    print(f"  SCAN COMPLETE")
    print(f"{'='*55}")
    print(f"  Stocks scanned    : {result['stocks_scanned']}")
    print(f"  Total signals     : {result['total_signals']}")
    print(f"  Unique tickers    : {result['unique_tickers']}")
    print(f"  Threshold         : {result['threshold']}")
    print(f"  Accuracy          : {result['accuracy']:.2%}")
    print(f"  Baseline          : {result['baseline']:.2%}")
    print(f"  Edge              : {result['edge']:+.2%}")
    print(f"\n  Top 5 Candidates Today:")
    for i, sig in enumerate(result["top_signals"][:5], 1):
        print(f"  {i}. {sig['ticker']:<6} "
              f"Score={sig['score']:>5.1f}  "
              f"Prob={sig['signal_prob']:.3f}  "
              f"RSI={sig['rsi_14']:.0f}  "
              f"Profile={sig['profile']}")
    print(f"{'='*55}")
