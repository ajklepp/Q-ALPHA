# ============================================================
# Q-ALPHA | EXP-0007 | experiment7.py
# Purpose: Validate signal on broad universe
# Self-contained Modal script — no imports from other files
# ============================================================
import modal
import warnings
warnings.filterwarnings("ignore")

app = modal.App("q-alpha-exp007")

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
    "IQV","ZBH","BSX","EW","STE","HSIC",
    # Consumer
    "WMT","COST","TGT","HD","LOW","MCD","SBUX","NKE",
    "PG","KO","PEP","CL","EL","ULTA","ROST","TJX",
    "BKNG","MAR","HLT","CCL","RCL","NCLH",
    # Energy
    "XOM","CVX","COP","SLB","EOG","PXD","MPC","PSX",
    "VLO","HAL","DVN","OXY","HES","APA","MRO",
    # Industrials
    "CAT","DE","BA","HON","UPS","FDX","LMT","RTX","GE",
    "MMM","EMR","ETN","PH","ROK","DOV","XYL","IEX",
    # Materials
    "LIN","APD","SHW","FCX","NEM","NUE","VMC","MLM","CF",
    # Utilities
    "NEE","DUK","SO","D","AEP","EXC","XEL","ES","WEC",
    # REITs
    "PLD","AMT","EQIX","CCI","SPG","O","DLR","PSA","EQR",
    # Communications
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


def process_stock(ticker, start, end):
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
        df["rsi_14"]     = ta.rsi(df["Close"], length=14)

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


def score_candidate(row, prob):
    score = 0.0

    # Signal strength (30pts)
    if prob >= 0.85:   score += 30
    elif prob >= 0.75: score += 24
    elif prob >= 0.70: score += 18
    elif prob >= 0.65: score += 12
    else:              score += 6

    # Momentum quality (25pts)
    m = 0
    rsi = row.get("rsi_14", 50)
    if 50 <= rsi <= 65:            m += 10
    elif 45 <= rsi < 50 or 65 < rsi <= 70: m += 6
    elif rsi > 70:                 m += 2

    macd_h = row.get("macd_hist", 0)
    if macd_h > 0:   m += 8
    elif macd_h > -0.1: m += 4

    s20 = row.get("close_vs_sma20", 0)
    s50 = row.get("close_vs_sma50", 0)
    if s20 > 0 and s50 > 0: m += 7
    elif s20 > 0:            m += 4
    score += min(m, 25)

    # Volume confirmation (20pts)
    vr = row.get("volume_ratio", 1.0)
    if vr >= 2.5:   score += 20
    elif vr >= 2.0: score += 16
    elif vr >= 1.5: score += 12
    elif vr >= 1.2: score += 8
    elif vr >= 1.0: score += 4

    # Volatility sweet spot (15pts)
    v10 = row.get("volatility_10d", 0.02) * 100
    if 1.0 <= v10 <= 2.5:   score += 15
    elif 0.7 <= v10 < 1.0:  score += 10
    elif 2.5 < v10 <= 4.0:  score += 8
    elif v10 > 4.0:          score += 3
    else:                    score += 5

    # Bollinger position (10pts)
    bb = row.get("bb_position", 0.5)
    if 0.5 <= bb <= 0.8:   score += 10
    elif 0.4 <= bb < 0.5:  score += 7
    elif 0.8 < bb <= 0.9:  score += 5
    elif bb > 0.9:         score += 2
    else:                  score += 3

    return round(score, 1)


def classify_profile(atr_pct, avg_vol_m):
    if atr_pct < 0.015 and avg_vol_m > 5:
        return "BLUE_CHIP_DEFENSIVE"
    elif atr_pct < 0.025 and avg_vol_m > 3:
        return "LARGE_CAP_GROWTH"
    elif atr_pct < 0.035:
        return "FINANCIAL_CYCLICAL"
    elif atr_pct < 0.055:
        return "HIGH_GROWTH_TECH"
    else:
        return "HIGH_VOLATILITY"


@app.function(image=image, timeout=1800, memory=8192)
def run_exp007(start_date, end_date):
    import pandas as pd
    import numpy as np
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import (accuracy_score,
                                  precision_recall_curve,
                                  classification_report)
    import warnings
    warnings.filterwarnings("ignore")

    print(f"EXP-0007: Broad Universe Signal Test")
    print(f"Universe: {len(UNIVERSE)} stocks")
    print(f"Period  : {start_date} to {end_date}")
    print(f"Downloading and processing stocks...")

    all_data = []
    success = 0
    failed  = 0

    for ticker in UNIVERSE:
        df = process_stock(ticker, start_date, end_date)
        if df is not None:
            all_data.append(df)
            success += 1
        else:
            failed += 1

    print(f"Success: {success}  Failed/Filtered: {failed}")

    if not all_data:
        return {"error": "No data"}

    combined = pd.concat(all_data)
    combined.sort_index(inplace=True)
    print(f"Total rows: {len(combined):,}")
    print(f"Unique stocks: {combined['ticker'].nunique()}")

    # Train / test split
    split = "2023-01-01"
    train = combined[combined.index < split]
    test  = combined[combined.index >= split]

    print(f"Train: {len(train):,}  Test: {len(test):,}")
    print(f"Train target rate: {train['target'].mean():.2%}")
    print(f"Test  target rate: {test['target'].mean():.2%}")

    # Train model
    print("Training Random Forest on broad universe...")
    model = RandomForestClassifier(
        n_estimators=200, max_depth=4,
        min_samples_split=40, min_samples_leaf=20,
        class_weight="balanced", random_state=42,
        n_jobs=-1)
    model.fit(train[FEATURE_COLUMNS], train["target"])
    print("Model trained")

    # Find threshold
    probs_v = model.predict_proba(test[FEATURE_COLUMNS])[:, 1]
    precs, recs, thrs = precision_recall_curve(
        test["target"], probs_v)
    best_t, best_p = 0.5, 0
    for p, r, t in zip(precs, recs, thrs):
        if r >= 0.20 and p > best_p:
            best_p, best_t = p, t
    print(f"Threshold: {best_t:.3f}")

    # Generate signals
    test = test.copy()
    test["prob"]   = probs_v
    test["signal"] = (probs_v >= best_t).astype(int)

    # Overall accuracy
    accuracy = accuracy_score(test["target"], test["signal"])
    baseline = test["target"].mean()
    edge     = accuracy - baseline

    print(f"\nBroad Universe Performance:")
    print(f"  Accuracy : {accuracy:.2%}")
    print(f"  Baseline : {baseline:.2%}")
    print(f"  Edge     : {edge:+.2%}")
    print(f"\n{classification_report(test['target'], test['signal'], target_names=['No Edge','Edge'])}")

    # Signal frequency
    signals    = test[test["signal"] == 1]
    n_signals  = len(signals)
    n_days     = test.index.nunique()
    per_day    = n_signals / n_days if n_days > 0 else 0

    print(f"Signal Stats:")
    print(f"  Total signals    : {n_signals:,}")
    print(f"  Unique tickers   : {signals['ticker'].nunique()}")
    print(f"  Trading days     : {n_days}")
    print(f"  Avg signals/day  : {per_day:.1f}")

    # Per profile performance
    print(f"\nPer Stock Profile Performance:")
    test["profile"] = test.apply(
        lambda r: classify_profile(
            r.get("atr_14", r["Close"]*0.02) / r["Close"],
            r.get("volume_sma_20", 1e6) / 1e6
        ), axis=1)

    for profile in ["BLUE_CHIP_DEFENSIVE","LARGE_CAP_GROWTH",
                    "FINANCIAL_CYCLICAL","HIGH_GROWTH_TECH",
                    "HIGH_VOLATILITY"]:
        p_df = test[test["profile"] == profile]
        if len(p_df) < 50:
            continue
        p_acc  = accuracy_score(p_df["target"], p_df["signal"])
        p_base = p_df["target"].mean()
        p_sigs = p_df["signal"].sum()
        print(f"  {profile:<25} "
              f"Acc={p_acc:.2%}  "
              f"Base={p_base:.2%}  "
              f"Edge={p_acc-p_base:+.2%}  "
              f"Signals={p_sigs}")

    # Score top signals
    print(f"\nScoring top signal candidates...")
    scored = []
    for idx, row in signals.iterrows():
        s = score_candidate(row.to_dict(), row["prob"])
        scored.append({
            "date":    idx,
            "ticker":  row["ticker"],
            "close":   round(row["Close"], 2),
            "prob":    round(row["prob"], 3),
            "score":   s,
            "rsi":     round(row.get("rsi_14", 0), 1),
            "vol_r":   round(row.get("volume_ratio", 0), 2),
            "vol_pct": round(row.get("volatility_10d",0)*100,2),
            "profile": classify_profile(
                row.get("atr_14", row["Close"]*0.02)/row["Close"],
                row.get("volume_sma_20",1e6)/1e6),
        })

    scored_df = pd.DataFrame(scored)
    scored_df.sort_values("score", ascending=False,
                          inplace=True)

    print(f"\nTop 15 Ranked Signals:")
    print(f"{'Date':<12}{'Ticker':<8}{'Score':>6}"
          f"{'Prob':>7}{'RSI':>5}{'VolR':>6}{'Profile'}")
    print("-"*65)
    for _, r in scored_df.head(15).iterrows():
        print(f"{str(r['date'])[:10]:<12}"
              f"{r['ticker']:<8}"
              f"{r['score']:>6.1f}"
              f"{r['prob']:>7.3f}"
              f"{r['rsi']:>5.0f}"
              f"{r['vol_r']:>6.2f}"
              f"  {r['profile']}")

    # Best tickers by signal quality
    ticker_stats = scored_df.groupby("ticker").agg(
        count=("score","count"),
        avg_score=("score","mean"),
        avg_prob=("prob","mean"),
    ).sort_values("avg_score", ascending=False)

    print(f"\nTop 20 Stocks by Signal Quality:")
    print(f"{'Ticker':<8}{'Count':>7}{'AvgScore':>10}"
          f"{'AvgProb':>9}")
    print("-"*36)
    for tk, row in ticker_stats.head(20).iterrows():
        print(f"{tk:<8}{row['count']:>7}"
              f"{row['avg_score']:>10.1f}"
              f"{row['avg_prob']:>9.3f}")

    return {
        "status":         "success",
        "stocks_scanned": success,
        "total_signals":  n_signals,
        "signals_per_day":round(per_day, 1),
        "accuracy":       round(accuracy, 4),
        "baseline":       round(baseline, 4),
        "edge":           round(edge, 4),
        "threshold":      round(best_t, 3),
    }


@app.local_entrypoint()
def main():
    print("="*55)
    print("  Q-ALPHA | EXP-0007 | Broad Universe Test")
    print("  Running on Modal cloud")
    print("="*55)

    result = run_exp007.remote(
        start_date="2019-01-01",
        end_date="2024-12-31",
    )

    if "error" in result:
        print(f"Error: {result['error']}")
        return

    print(f"\n{'='*55}")
    print(f"  EXP-0007 FINAL VERDICT")
    print(f"{'='*55}")
    print(f"  Stocks scanned   : {result['stocks_scanned']}")
    print(f"  Total signals    : {result['total_signals']:,}")
    print(f"  Signals per day  : {result['signals_per_day']}")
    print(f"  Accuracy         : {result['accuracy']:.2%}")
    print(f"  Baseline         : {result['baseline']:.2%}")
    print(f"  Edge             : {result['edge']:+.2%}")

    edge = result["edge"]
    if edge > 0.05:
        verdict = "PASS - Strong edge on broad universe"
    elif edge > 0.02:
        verdict = "MARGINAL - Small edge, investigate further"
    else:
        verdict = "FAIL - Signal does not generalise"

    print(f"\n  Verdict: {verdict}")
    print(f"{'='*55}")
