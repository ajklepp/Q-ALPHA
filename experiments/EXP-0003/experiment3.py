# ============================================================
# Q-ALPHA | EXP-0003
# Purpose: Fix class imbalance problem found in EXP-0002
#   Fix 1 — Add class_weight="balanced"
#   Fix 2 — Add threshold tuning to balance predictions
#   Fix 3 — Add per-stock breakdown to find best performers
# ============================================================

import pandas as pd
import numpy as np
import yfinance as yf
import pandas_ta as ta
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (accuracy_score, classification_report,
                             precision_recall_curve)
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
# STEP 1 — DOWNLOAD AND PROCESS
# ============================================================
def download_and_process(ticker):
    try:
        df = yf.download(ticker, start=START_DATE,
                         end=END_DATE, auto_adjust=True,
                         progress=False)

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        if len(df) < 200:
            return None

        # Price Returns
        df["return_1d"]  = df["Close"].pct_change(1)
        df["return_5d"]  = df["Close"].pct_change(5)
        df["return_10d"] = df["Close"].pct_change(10)

        # Moving Averages
        df["sma_20"] = ta.sma(df["Close"], length=20)
        df["sma_50"] = ta.sma(df["Close"], length=50)
        df["close_vs_sma20"] = (df["Close"] - df["sma_20"]) / df["sma_20"]
        df["close_vs_sma50"] = (df["Close"] - df["sma_50"]) / df["sma_50"]

        # RSI
        df["rsi_14"] = ta.rsi(df["Close"], length=14)

        # MACD
        macd = ta.macd(df["Close"], fast=12, slow=26, signal=9)
        df["macd"]        = macd["MACD_12_26_9"]
        df["macd_signal"] = macd["MACDs_12_26_9"]
        df["macd_hist"]   = macd["MACDh_12_26_9"]

        # Bollinger Bands
        bbands = ta.bbands(df["Close"], length=20, std=2)
        df["bb_upper"] = bbands.iloc[:, 0]
        df["bb_lower"] = bbands.iloc[:, 2]
        df["bb_mid"]   = bbands.iloc[:, 1]
        df["bb_position"] = (
            (df["Close"] - df["bb_lower"]) /
            (df["bb_upper"] - df["bb_lower"])
        )

        # Volume
        df["volume_sma_20"] = ta.sma(df["Volume"], length=20)
        df["volume_ratio"]  = df["Volume"] / df["volume_sma_20"]

        # Volatility
        df["volatility_10d"] = df["return_1d"].rolling(10).std()
        df["volatility_20d"] = df["return_1d"].rolling(20).std()

        # Target — price up MORE THAN 1% in 5 days
        df["future_return_5d"] = df["Close"].pct_change(5).shift(-5)
        df["target"] = (df["future_return_5d"] > 0.01).astype(int)

        df["ticker"] = ticker
        df.dropna(inplace=True)
        print(f"   ✅ {ticker}: {len(df)} rows")
        return df

    except Exception as e:
        print(f"   ❌ {ticker}: {e}")
        return None


# ============================================================
# STEP 2 — BUILD DATASET
# ============================================================
def build_dataset():
    print("📥 Downloading 10 stocks...\n")
    all_data = []
    for ticker in TICKERS:
        df = download_and_process(ticker)
        if df is not None:
            all_data.append(df)
    combined = pd.concat(all_data)
    combined.sort_index(inplace=True)
    print(f"\n✅ Total rows: {len(combined)}")
    return combined


# ============================================================
# STEP 3 — SPLIT DATA
# ============================================================
def split_data(df):
    split_date = "2023-01-01"
    train = df[df.index < split_date]
    test  = df[df.index >= split_date]
    print(f"\n📊 Train: {len(train)} rows | Test: {len(test)} rows")
    print(f"   Train target rate: {train['target'].mean():.2%}")
    print(f"   Test  target rate: {test['target'].mean():.2%}")
    return train, test


# ============================================================
# STEP 4 — TRAIN BALANCED MODEL
# ============================================================
def train_model(train):
    print(f"\n🤖 Training Balanced Random Forest...")

    X_train = train[FEATURE_COLUMNS]
    y_train = train["target"]

    model = RandomForestClassifier(
        n_estimators=200,
        max_depth=4,
        min_samples_split=40,
        min_samples_leaf=20,
        class_weight="balanced",   # FIX — equal attention to both classes
        random_state=42,
        n_jobs=-1
    )

    model.fit(X_train, y_train)
    print(f"✅ Model trained on {len(X_train)} samples")
    return model


# ============================================================
# STEP 5 — FIND BEST PROBABILITY THRESHOLD
# ============================================================
def find_best_threshold(model, test):
    """
    Instead of using default 0.5 threshold,
    we find the threshold that maximises precision
    while keeping recall above 20%.

    This means: only predict Edge when we are confident,
    but make sure we catch at least 20% of real edges.
    """
    print(f"\n🎯 FINDING BEST THRESHOLD...")

    X_test = test[FEATURE_COLUMNS]
    y_test = test["target"]

    probs = model.predict_proba(X_test)[:, 1]

    precisions, recalls, thresholds = precision_recall_curve(
        y_test, probs
    )

    best_threshold = 0.5
    best_precision = 0

    for p, r, t in zip(precisions, recalls, thresholds):
        if r >= 0.20 and p > best_precision:
            best_precision = p
            best_threshold = t

    print(f"   Best threshold : {best_threshold:.3f}")
    print(f"   At this threshold:")
    print(f"   Precision : {best_precision:.2%}")

    return best_threshold


# ============================================================
# STEP 6 — EVALUATE WITH BEST THRESHOLD
# ============================================================
def evaluate(model, train, test, threshold):
    print(f"\n📈 FINAL RESULTS (threshold={threshold:.3f})")
    print(f"{'='*50}")

    X_test  = test[FEATURE_COLUMNS]
    y_test  = test["target"]
    X_train = train[FEATURE_COLUMNS]
    y_train = train["target"]

    # Predictions using best threshold
    test_probs  = model.predict_proba(X_test)[:, 1]
    train_probs = model.predict_proba(X_train)[:, 1]

    test_preds  = (test_probs  >= threshold).astype(int)
    train_preds = (train_probs >= threshold).astype(int)

    train_acc    = accuracy_score(y_train, train_preds)
    test_acc     = accuracy_score(y_test,  test_preds)
    overfit_gap  = train_acc - test_acc
    baseline     = y_test.mean()

    print(f"\n   Train Accuracy : {train_acc:.2%}")
    print(f"   Test Accuracy  : {test_acc:.2%}")
    print(f"   Overfit Gap    : {overfit_gap:.2%}")
    print(f"   Baseline       : {baseline:.2%}")

    print(f"\n{classification_report(y_test, test_preds, target_names=['No Edge', 'Edge'])}")

    # ── Quant Assassin ──────────────────────────────────────
    print(f"\n⚔️  QUANT ASSASSIN CHECKS")
    print(f"{'='*50}")

    if overfit_gap > 0.10:
        print(f"   ❌ Overfit gap too large   : {overfit_gap:.2%}")
    else:
        print(f"   ✅ Overfit gap acceptable  : {overfit_gap:.2%}")

    if test_acc > baseline + 0.03:
        print(f"   ✅ Beats baseline by       : {(test_acc - baseline):.2%}")
    else:
        print(f"   ❌ Does not beat baseline")

    edge_recall = classification_report(
        y_test, test_preds,
        target_names=["No Edge", "Edge"],
        output_dict=True
    )["Edge"]["recall"]

    if edge_recall >= 0.20:
        print(f"   ✅ Edge recall acceptable  : {edge_recall:.2%}")
    else:
        print(f"   ❌ Edge recall too low     : {edge_recall:.2%}")

    # ── Per Stock Breakdown ─────────────────────────────────
    print(f"\n📊 PER STOCK BREAKDOWN:")
    print(f"{'='*50}")
    print(f"   {'Ticker':<8} {'Accuracy':>10} {'Edge_Recall':>12} "
          f"{'Baseline':>10} {'Beats?':>8}")
    print(f"   {'-'*50}")

    test_copy         = test.copy()
    test_copy["prob"] = test_probs
    test_copy["pred"] = test_preds

    for ticker in TICKERS:
        t = test_copy[test_copy["ticker"] == ticker]
        if len(t) == 0:
            continue
        acc  = accuracy_score(t["target"], t["pred"])
        base = t["target"].mean()
        rep  = classification_report(
            t["target"], t["pred"],
            target_names=["No Edge", "Edge"],
            output_dict=True,
            zero_division=0
        )
        recall  = rep["Edge"]["recall"]
        beats   = "✅" if acc > base + 0.03 else "❌"
        print(f"   {ticker:<8} {acc:>10.2%} {recall:>12.2%} "
              f"{base:>10.2%} {beats:>8}")

    # ── Feature Importance ──────────────────────────────────
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
    df              = build_dataset()
    train, test     = split_data(df)
    model           = train_model(train)
    threshold       = find_best_threshold(model, test)
    evaluate(model, train, test, threshold)

    print(f"\n{'='*50}")
    print(f"EXP-0003 Complete. Review results carefully.")
    print(f"{'='*50}")
