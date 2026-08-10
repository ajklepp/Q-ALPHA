# ============================================================
# Q-ALPHA | model.py
# Purpose: Train a Random Forest AI to predict whether
#          a stock will be higher 5 days from now
# ============================================================

import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import (accuracy_score, classification_report,
                             confusion_matrix)
from sklearn.preprocessing import StandardScaler
import os
import warnings
warnings.filterwarnings("ignore")


# ============================================================
# FEATURES THE AI WILL USE TO MAKE PREDICTIONS
# ============================================================
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

TARGET_COLUMN = "target"


def load_data(filepath):
    """Load feature data from CSV"""
    print(f"Loading data from {filepath}...")
    df = pd.read_csv(filepath, index_col="Date", parse_dates=True)
    df.dropna(inplace=True)
    print(f"✅ Loaded {len(df)} rows")
    return df


def split_data(df):
    """
    Split data into TRAIN and TEST sets.

    IMPORTANT: We use TIME-BASED splitting.
    We NEVER shuffle the data randomly because that would
    cause look-ahead bias — the model would train on future
    data and cheat during testing.

    Train: First 80% of data (older history)
    Test:  Last 20% of data  (most recent — never seen by model)
    """
    split_index = int(len(df) * 0.80)

    train = df.iloc[:split_index]
    test  = df.iloc[split_index:]

    print(f"\n📊 Data Split:")
    print(f"   Train: {len(train)} rows "
          f"({train.index[0].date()} to {train.index[-1].date()})")
    print(f"   Test:  {len(test)} rows  "
          f"({test.index[0].date()} to {test.index[-1].date()})")
    print(f"   Train target distribution: "
          f"{train[TARGET_COLUMN].value_counts().to_dict()}")
    print(f"   Test  target distribution: "
          f"{test[TARGET_COLUMN].value_counts().to_dict()}") 

    return train, test


def train_model(train):
    """
    Train a Random Forest Classifier on the training data.

    Random Forest works by building hundreds of decision trees,
    each trained on a random subset of the data and features.
    The final prediction is a vote across all trees.
    This makes it robust and hard to overfit.
    """
    print(f"\n🤖 Training Random Forest...")

    X_train = train[FEATURE_COLUMNS]
    y_train = train[TARGET_COLUMN]

    model = RandomForestClassifier(
        n_estimators=200,      # Number of trees in the forest
        max_depth=6,           # How deep each tree can go
        min_samples_split=20,  # Minimum samples to split a node
        min_samples_leaf=10,   # Minimum samples in a leaf
        random_state=42,       # For reproducibility
        n_jobs=-1              # Use all CPU cores
    )

    model.fit(X_train, y_train)
    print(f"✅ Model trained on {len(X_train)} samples")

    return model


def evaluate_model(model, train, test):
    """
    Evaluate the model on BOTH train and test sets.

    We check both because:
    - If train accuracy is high but test accuracy is low
      = the model is OVERFITTING (memorising, not learning)
    - If both are similar and reasonable
      = the model is GENERALISING (what we want)
    """
    print(f"\n📈 MODEL EVALUATION")
    print(f"{'='*50}")

    for name, dataset in [("TRAIN", train), ("TEST", test)]:
        X = dataset[FEATURE_COLUMNS]
        y = dataset[TARGET_COLUMN]

        predictions = model.predict(X)
        accuracy = accuracy_score(y, predictions)

        print(f"\n{name} SET RESULTS:")
        print(f"   Accuracy: {accuracy:.2%}")
        print(f"\n{classification_report(y, predictions, 
              target_names=['Down/Flat', 'Up'])}")


def feature_importance(model):
    """
    Show which features the AI found most useful.
    This is one of the great advantages of Random Forest —
    we can see exactly what it is paying attention to.
    """
    print(f"\n🔍 FEATURE IMPORTANCE")
    print(f"{'='*50}")
    print(f"(Higher = more important to the AI's decisions)\n")

    importance_df = pd.DataFrame({
        "feature":    FEATURE_COLUMNS,
        "importance": model.feature_importances_
    }).sort_values("importance", ascending=False)

    for _, row in importance_df.iterrows():
        bar = "█" * int(row["importance"] * 100)
        print(f"   {row['feature']:<20} {bar} "
              f"{row['importance']:.3f}")

    return importance_df


def quant_assassin_checks(model, train, test):
    """
    Basic Quant Assassin checks.
    These are the first questions a skeptic would ask.
    """
    print(f"\n⚔️  QUANT ASSASSIN CHECKS")
    print(f"{'='*50}")

    train_acc = accuracy_score(
        train[TARGET_COLUMN],
        model.predict(train[FEATURE_COLUMNS])
    )
    test_acc = accuracy_score(
        test[TARGET_COLUMN],
        model.predict(test[FEATURE_COLUMNS])
    )

    overfit_gap = train_acc - test_acc

    print(f"\n   Train Accuracy : {train_acc:.2%}")
    print(f"   Test Accuracy  : {test_acc:.2%}")
    print(f"   Overfit Gap    : {overfit_gap:.2%}")

    if overfit_gap > 0.10:
        print(f"\n   ❌ WARNING: Large overfit gap detected.")
        print(f"      Model may be memorising training data.")
    else:
        print(f"\n   ✅ Overfit gap is acceptable.")

    # Baseline — what if we just always predicted UP?
    baseline = test[TARGET_COLUMN].mean()
    print(f"\n   Baseline (always predict UP): {baseline:.2%}")

    if test_acc > baseline + 0.03:
        print(f"   ✅ Model beats baseline by "
              f"{(test_acc - baseline):.2%}")
    else:
        print(f"   ❌ Model does NOT meaningfully beat baseline.")
        print(f"      This strategy needs more work.")


# ============================================================
# RUN EVERYTHING
# ============================================================
if __name__ == "__main__":

    # 1. Load data
    df = load_data("data/AAPL_features.csv")

    # 2. Split into train and test
    train, test = split_data(df)

    # 3. Train the model
    model = train_model(train)

    # 4. Evaluate results
    evaluate_model(model, train, test)

    # 5. Show feature importance
    importance = feature_importance(model)

    # 6. Run Quant Assassin checks
    quant_assassin_checks(model, train, test)

    print(f"\n{'='*50}")
    print(f"Model training complete.")
    print(f"Review results carefully before proceeding.")
    print(f"{'='*50}")
