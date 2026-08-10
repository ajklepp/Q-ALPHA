# ============================================================
# Q-ALPHA | EXP-0004
# Purpose: Answer the Quant Assassin's key question:
#   Is volatility predicting direction OR just volatility?
#
#   TEST A — Remove volatility features, retest
#   TEST B — Focus on defensive stocks only
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
ALL_TICKERS        = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META",
    "JPM",  "JNJ",  "XOM",   "WMT",  "TSLA"
]

DEFENSIVE_TICKERS  = ["JNJ", "WMT", "XOM", "JPM", "MSFT"]

START_DATE = "2019-01-01"
END_DATE   = "2024-12-31"

# Full feature set (same as EXP-0003)
FEATURES_FULL = [
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

# Reduced feature set — NO volatility
FEATURES_NO_VOL = [
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
]


# ============================================================
# DOWNLOAD AND PROCESS
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

        df["return_1d"]  = df["Close"].pct_change(1)
        df["return_5d"]  = df["Close"].pct_change(5)
        df["return_10d"] = df["Close"].pct_change(10)

        df["sma_20"] = ta.sma(df["Close"], length=20)
        df["sma_50"] = ta.sma(df["Close"], length=50)
        df["close_vs_sma20"] = (df["Close"] - df["sma_20"]) / df["sma_20"]
        df["close_vs_sma50"] = (df["Close"] - df["sma_50"]) / df["sma_50"]

        df["rsi_14"] = ta.rsi(df["Close"], length=14)

        macd = ta.macd(df["Close"], fast=12, slow=26, signal=9)
        df["macd"]        = macd["MACD_12_26_9"]
        df["macd_signal"] = macd["MACDs_12_26_9"]
        df["macd_hist"]   = macd["MACDh_12_26_9"]

        bbands = ta.bbands(df["Close"], length=20, std=2)
        df["bb_upper"] = bbands.iloc[:, 0]
        df["bb_lower"] = bbands.iloc[:, 2]
        df["bb_mid"]   = bbands.iloc[:, 1]
        df["bb_position"] = (
            (df["Close"] - df["bb_lower"]) /
            (df["bb_upper"] - df["bb_lower"])
        )

        df["volume_sma_20"] = ta.sma(df["Volume"], length=20)
        df["volume_ratio"]  = df["Volume"] / df["volume_sma_20"]

        df["volatility_10d"] = df["return_1d"].rolling(10).std()
        df["volatility_20d"] = df["return_1d"].rolling(20).std()

        df["future_return_5d"] = df["Close"].pct_change(5).shift(-5)
        df["target"] = (df["future_return_5d"] > 0.01).astype(int)

        df["ticker"] = ticker
        df.dropna(inplace=True)
        return df

    except Exception as e:
        print(f"   ❌ {ticker}: {e}")
        return None


def build_dataset(tickers):
    all_data = []
    for ticker in tickers:
        df = download_and_process(ticker)
        if df is not None:
            all_data.append(df)
            print(f"   ✅ {ticker}: {len(df)} rows")
    combined = pd.concat(all_data)
    combined.sort_index(inplace=True)
    return combined


def split_data(df):
    split_date = "2023-01-01"
    train = df[df.index < split_date]
    test  = df[df.index >= split_date]
    return train, test


def train_model(train, features):
    model = RandomForestClassifier(
        n_estimators=200,
        max_depth=4,
        min_samples_split=40,
        min_samples_leaf=20,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1
    )
    model.fit(train[features], train["target"])
    return model


def find_best_threshold(model, test, features):
    probs = model.predict_proba(test[features])[:, 1]
    precisions, recalls, thresholds = precision_recall_curve(
        test["target"], probs
    )
    best_threshold = 0.5
    best_precision = 0
    for p, r, t in zip(precisions, recalls, thresholds):
        if r >= 0.20 and p > best_precision:
            best_precision = p
            best_threshold = t
    return best_threshold


def evaluate(model, train, test, features, label):
    """Run full evaluation and return key metrics"""
    threshold = find_best_threshold(model, test, features)

    train_probs = model.predict_proba(train[features])[:, 1]
    test_probs  = model.predict_proba(test[features])[:, 1]

    train_preds = (train_probs >= threshold).astype(int)
    test_preds  = (test_probs  >= threshold).astype(int)

    train_acc   = accuracy_score(train["target"], train_preds)
    test_acc    = accuracy_score(test["target"],  test_preds)
    overfit_gap = train_acc - test_acc
    baseline    = test["target"].mean()

    rep = classification_report(
        test["target"], test_preds,
        target_names=["No Edge", "Edge"],
        output_dict=True,
        zero_division=0
    )
    edge_recall    = rep["Edge"]["recall"]
    edge_precision = rep["Edge"]["precision"]

    print(f"\n{'='*55}")
    print(f"  {label}")
    print(f"{'='*55}")
    print(f"  Features used  : {len(features)}")
    print(f"  Threshold      : {threshold:.3f}")
    print(f"  Train Accuracy : {train_acc:.2%}")
    print(f"  Test Accuracy  : {test_acc:.2%}")
    print(f"  Overfit Gap    : {overfit_gap:.2%}")
    print(f"  Baseline       : {baseline:.2%}")
    print(f"  Beats Baseline : {(test_acc - baseline):+.2%}")
    print(f"  Edge Precision : {edge_precision:.2%}")
    print(f"  Edge Recall    : {edge_recall:.2%}")

    # Quant Assassin
    print(f"\n  ⚔️  Quant Assassin:")
    checks = 0
    if overfit_gap <= 0.10:
        print(f"  ✅ Overfit gap OK      : {overfit_gap:.2%}")
        checks += 1
    else:
        print(f"  ❌ Overfit gap HIGH    : {overfit_gap:.2%}")

    if test_acc > baseline + 0.03:
        print(f"  ✅ Beats baseline      : {(test_acc-baseline):+.2%}")
        checks += 1
    else:
        print(f"  ❌ Below baseline      : {(test_acc-baseline):+.2%}")

    if edge_recall >= 0.20:
        print(f"  ✅ Edge recall OK      : {edge_recall:.2%}")
        checks += 1
    else:
        print(f"  ❌ Edge recall LOW     : {edge_recall:.2%}")

    print(f"\n  VERDICT: {checks}/3 checks passed")

    return {
        "label":         label,
        "test_acc":      test_acc,
        "overfit_gap":   overfit_gap,
        "baseline":      baseline,
        "edge_recall":   edge_recall,
        "edge_precision":edge_precision,
        "checks_passed": checks
    }


# ============================================================
# RUN ALL TESTS
# ============================================================
if __name__ == "__main__":

    results = []

    # ----------------------------------------------------------
    # TEST A — Full features vs No volatility features
    # Same stocks (all 10), different feature sets
    # ----------------------------------------------------------
    print("\n" + "="*55)
    print("  TEST A: Does volatility drive the edge?")
    print("  Universe: All 10 stocks")
    print("="*55)

    print("\n📥 Downloading all 10 stocks...")
    df_all        = build_dataset(ALL_TICKERS)
    train_all, test_all = split_data(df_all)

    # A1 — With volatility (replication of EXP-0003)
    print("\n🤖 Training A1: Full features (WITH volatility)...")
    model_a1 = train_model(train_all, FEATURES_FULL)
    r_a1     = evaluate(model_a1, train_all, test_all,
                        FEATURES_FULL,
                        "TEST A1 — All stocks, WITH volatility")
    results.append(r_a1)

    # A2 — Without volatility
    print("\n🤖 Training A2: Reduced features (NO volatility)...")
    model_a2 = train_model(train_all, FEATURES_NO_VOL)
    r_a2     = evaluate(model_a2, train_all, test_all,
                        FEATURES_NO_VOL,
                        "TEST A2 — All stocks, NO volatility")
    results.append(r_a2)

    # ----------------------------------------------------------
    # TEST B — Defensive stocks only
    # ----------------------------------------------------------
    print("\n" + "="*55)
    print("  TEST B: Do defensive stocks perform better?")
    print(f"  Universe: {DEFENSIVE_TICKERS}")
    print("="*55)

    print("\n📥 Downloading defensive stocks...")
    df_def          = build_dataset(DEFENSIVE_TICKERS)
    train_def, test_def = split_data(df_def)

    # B1 — Defensive with full features
    print("\n🤖 Training B1: Defensive stocks, full features...")
    model_b1 = train_model(train_def, FEATURES_FULL)
    r_b1     = evaluate(model_b1, train_def, test_def,
                        FEATURES_FULL,
                        "TEST B1 — Defensive stocks, WITH volatility")
    results.append(r_b1)

    # B2 — Defensive without volatility
    print("\n🤖 Training B2: Defensive stocks, no volatility...")
    model_b2 = train_model(train_def, FEATURES_NO_VOL)
    r_b2     = evaluate(model_b2, train_def, test_def,
                        FEATURES_NO_VOL,
                        "TEST B2 — Defensive stocks, NO volatility")
    results.append(r_b2)

    # ----------------------------------------------------------
    # FINAL COMPARISON TABLE
    # ----------------------------------------------------------
    print(f"\n\n{'='*55}")
    print(f"  FINAL COMPARISON")
    print(f"{'='*55}")
    print(f"  {'Test':<40} {'Acc':>6} {'Gap':>6} "
          f"{'Recall':>7} {'Pass':>5}")
    print(f"  {'-'*55}")
    for r in results:
        print(f"  {r['label']:<40} "
              f"{r['test_acc']:>6.1%} "
              f"{r['overfit_gap']:>6.1%} "
              f"{r['edge_recall']:>7.1%} "
              f"{r['checks_passed']:>4}/3")

    print(f"\n{'='*55}")
    print(f"EXP-0004 Complete.")
    print(f"{'='*55}")
