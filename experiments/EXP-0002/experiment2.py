# ============================================================
# Q-ALPHA | EXP-0002 | experiment2.py
# Purpose: Fix the 3 problems found in EXP-0001
#   Fix 1 — New target: price up MORE THAN 1% in 5 days
#   Fix 2 — Tighter model to reduce overfitting
#   Fix 3 — Train on 10 stocks instead of 1
# ============================================================

import pandas as pd
import numpy as np
import yfinance as yf
import pandas_ta as ta
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report
import warnings
warnings.filterwarnings("ignore")


# ============================================================
# SETTINGS
# ============================================================
TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META",
    "JPM",  "JNJ",  "XOM",   "WMT",  "TSLA"
]

START_DATE = "2019-01-01"
END_DATE   = "2024-12-31"

FEATURE_COLUMNS = [
    "return_1d",
    "return_5d",
    "return_10d",
    "close_vs_sma20",
    "close_vs_sma50",
    "rsi_14",
    "macd",
    "macd_signal",
    "macd_hist",
    "bb_position",
    "volume_ratio",
    "volatility_10d",
    "volatility_20d",
]


# ============================================================
# STEP 1 — DOWNLOAD AND PROCESS ALL 10 STOCKS
# ============================================================
def download_and_process(ticker):
    """Download one stock and calculate all features"""
    try:
        df = yf.download(ticker, start=START_DATE,
                         end=END_DATE, auto_adjust=True,
                         progress=False)

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        if len(df) < 200:
            print(f"   ⚠️  {ticker}: Not enough data, skipping")
            return None

        # --- Price Returns ---
        df["return_1d"]  = df["Close"].pct_change(1)
        df["return_5d"]  = df["Close"].pct_change(5)
        df["return_10d"] = df["Close"].pct_change(10)

        # --- Moving Averages ---
        df["sma_20"] = ta.sma(df["Close"], length=20)
        df["sma_50"] = ta.sma(df["Close"], length=50)
        df["close_vs_sma20"] = (df["Close"] - df["sma_20"]) / df["sma_20"]
        df["close_vs_sma50"] = (df["Close"] - df["sma_50"]) / df["sma_50"]

        # --- RSI ---
        df["rsi_14"] = ta.rsi(df["Close"], length=14)

        # --- MACD ---
        macd = ta.macd(df["Close"], fast=12, slow=26, signal=9)
        df["macd"]        = macd["MACD_12_26_9"]
        df["macd_signal"] = macd["MACDs_12_26_9"]
        df["macd_hist"]   = macd["MACDh_12_26_9"]

        # --- Bollinger Bands ---
        bbands = ta.bbands(df["Close"], length=20, std=2)
        df["bb_upper"] = bbands.iloc[:, 0]
        df["bb_lower"] = bbands.iloc[:, 2]
        df["bb_mid"]   = bbands.iloc[:, 1]
        df["bb_position"] = (
            (df["Close"] - df["bb_lower"]) /
            (df["bb_upper"] - df["bb_lower"])
        )

        # --- Volume ---
        df["volume_sma_20"] = ta.sma(df["Volume"], length=20)
        df["volume_ratio"]  = df["Volume"] / df["volume_sma_20"]

        # --- Volatility ---
        df["volatility_10d"] = df["return_1d"].rolling(10).std()
        df["volatility_20d"] = df["return_1d"].rolling(20).std()

        # --- NEW TARGET: Up MORE THAN 1% in 5 days ---
        df["future_return_5d"] = df["Close"].pct_change(5).shift(-5)
        df["target"] = (df["future_return_5d"] > 0.01).astype(int)

        # --- Add ticker column so we know which stock ---
        df["ticker"] = ticker

        df.dropna(inplace=True)
        print(f"   ✅ {ticker}: {len(df)} rows processed")
        return df

    except Exception as e:
        print(f"   ❌ {ticker}: Error — {e}")
        return None


# ============================================================
# STEP 2 — COMBINE ALL STOCKS INTO ONE DATASET
# ============================================================
def build_dataset():
    print("📥 Downloading and processing 10 stocks...\n")
    all_data = []

    for ticker in TICKERS:
        df = download_and_process(ticker)
        if df is not None:
            all_data.append(df)

    combined = pd.concat(all_data)
    combined.sort_index(inplace=True)
    print(f"\n✅ Combined dataset: {len(combined)} total rows")
    print(f"✅ Stocks included: {combined['ticker'].nunique()}")
    return combined


# ============================================================
# STEP 3 — TIME BASED TRAIN / TEST SPLIT
# ============================================================
def split_data(df):
    split_date = "2023-01-01"

    train = df[df.index < split_date]
    test  = df[df.index >= split_date]

    print(f"\n📊 Data Split:")
    print(f"   Train: {len(train)} rows (up to {split_date})")
    print(f"   Test:  {len(test)} rows  (from {split_date} onwards)")
    print(f"   Train target rate: {train['target'].mean():.2%}")
    print(f"   Test  target rate: {test['target'].mean():.2%}")

    return train, test


# ============================================================
# STEP 4 — TRAIN TIGHTER MODEL
# ============================================================
def train_model(train):
    print(f"\n🤖 Training Random Forest (tighter settings)...")

    X_train = train[FEATURE_COLUMNS]
    y_train = train["target"]

    model = RandomForestClassifier(
        n_estimators=200,
        max_depth=4,           # Reduced from 6
        min_samples_split=40,  # Increased from 20
        min_samples_leaf=20,   # Increased from 10
        random_state=42,
        n_jobs=-1
    )

    model.fit(X_train, y_train)
    print(f"✅ Model trained on {len(X_train)} samples")
    return model


# ============================================================
# STEP 5 — EVALUATE AND QUANT ASSASSIN CHECKS
# ============================================================
def evaluate(model, train, test):
    print(f"\n📈 RESULTS")
    print(f"{'='*50}")

    train_acc = accuracy_score(
        train["target"],
        model.predict(train[FEATURE_COLUMNS])
    )
    test_acc = accuracy_score(
        test["target"],
        model.predict(test[FEATURE_COLUMNS])
    )
    overfit_gap = train_acc - test_acc
    baseline    = test["target"].mean()

    print(f"\n   Train Accuracy : {train_acc:.2%}")
    print(f"   Test Accuracy  : {test_acc:.2%}")
    print(f"   Overfit Gap    : {overfit_gap:.2%}")
    print(f"   Baseline       : {baseline:.2%}")

    print(f"\n{classification_report(test['target'], model.predict(test[FEATURE_COLUMNS]), target_names=['No Edge', 'Edge'])}")

    print(f"\n⚔️  QUANT ASSASSIN CHECKS")
    print(f"{'='*50}")

    if overfit_gap > 0.10:
        print(f"   ❌ Overfit gap too large: {overfit_gap:.2%}")
    else:
        print(f"   ✅ Overfit gap acceptable: {overfit_gap:.2%}")

    if test_acc > baseline + 0.03:
        print(f"   ✅ Beats baseline by {(test_acc - baseline):.2%}")
    else:
        print(f"   ❌ Does not beat baseline")

    print(f"\n🔍 TOP 5 FEATURES:")
    importance_df = pd.DataFrame({
        "feature":    FEATURE_COLUMNS,
        "importance": model.feature_importances_
    }).sort_values("importance", ascending=False)

    for _, row in importance_df.head(5).iterrows():
        bar = "█" * int(row["importance"] * 100)
        print(f"   {row['feature']:<20} {bar} {row['importance']:.3f}")


# ============================================================
# RUN EVERYTHING
# ============================================================
if __name__ == "__main__":
    df            = build_dataset()
    train, test   = split_data(df)
    model         = train_model(train)
    evaluate(model, train, test)

    print(f"\n{'='*50}")
    print(f"EXP-0002 Complete. Review results carefully.")
    print(f"{'='*50}")
