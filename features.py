# ============================================================
# Q-ALPHA | features.py
# Purpose: Take raw OHLCV data and calculate technical
#          indicators that the AI will learn from
# ============================================================

import pandas as pd
import pandas_ta as ta
import numpy as np
import os


def calculate_features(df):
    """
    Takes a raw OHLCV DataFrame and adds technical indicator
    columns as features for the AI model.

    Parameters:
        df : pandas DataFrame with columns
             [Open, High, Low, Close, Volume]

    Returns:
        df : same DataFrame with new feature columns added
    """

    print("Calculating features...")

    # ----------------------------------------------------------
    # 1. PRICE RETURNS
    # How much did the price move each day?
    # ----------------------------------------------------------
    df["return_1d"]  = df["Close"].pct_change(1)   # 1 day return
    df["return_5d"]  = df["Close"].pct_change(5)   # 5 day return
    df["return_10d"] = df["Close"].pct_change(10)  # 10 day return

    # ----------------------------------------------------------
    # 2. MOVING AVERAGES
    # Is price above or below its average?
    # ----------------------------------------------------------
    df["sma_10"]  = ta.sma(df["Close"], length=10)
    df["sma_20"]  = ta.sma(df["Close"], length=20)
    df["sma_50"]  = ta.sma(df["Close"], length=50)

    # Price relative to moving average (normalised)
    df["close_vs_sma20"] = (df["Close"] - df["sma_20"]) / df["sma_20"]
    df["close_vs_sma50"] = (df["Close"] - df["sma_50"]) / df["sma_50"]

    # ----------------------------------------------------------
    # 3. RSI — Relative Strength Index
    # Is the stock overbought (>70) or oversold (<30)?
    # ----------------------------------------------------------
    df["rsi_14"] = ta.rsi(df["Close"], length=14)

    # ----------------------------------------------------------
    # 4. MACD — Moving Average Convergence Divergence
    # Is momentum shifting direction?
    # ----------------------------------------------------------
    macd = ta.macd(df["Close"], fast=12, slow=26, signal=9)
    df["macd"]        = macd["MACD_12_26_9"]
    df["macd_signal"] = macd["MACDs_12_26_9"]
    df["macd_hist"]   = macd["MACDh_12_26_9"]

    # ----------------------------------------------------------
    # 5. BOLLINGER BANDS
    # How far is price from its normal range?
    # ----------------------------------------------------------
        # ----------------------------------------------------------
    # 5. BOLLINGER BANDS
    # How far is price from its normal range?
    # ----------------------------------------------------------
    bbands = ta.bbands(df["Close"], length=20, std=2)
    print("Bollinger Band columns:", bbands.columns.tolist())
    df["bb_upper"] = bbands.iloc[:, 0]
    df["bb_lower"] = bbands.iloc[:, 2]
    df["bb_mid"]   = bbands.iloc[:, 1]
    df["bb_position"] = (
        (df["Close"] - df["bb_lower"]) /
        (df["bb_upper"] - df["bb_lower"])
    )


    # ----------------------------------------------------------
    # 6. VOLUME FEATURES
    # Is today's volume unusual compared to recent average?
    # ----------------------------------------------------------
    df["volume_sma_20"]  = ta.sma(df["Volume"], length=20)
    df["volume_ratio"]   = df["Volume"] / df["volume_sma_20"]

    # ----------------------------------------------------------
    # 7. VOLATILITY
    # How much is the stock moving day to day?
    # ----------------------------------------------------------
    df["volatility_10d"] = df["return_1d"].rolling(10).std()
    df["volatility_20d"] = df["return_1d"].rolling(20).std()

    # ----------------------------------------------------------
    # 8. TARGET VARIABLE — What we want the AI to predict
    # Will the stock be HIGHER 5 days from now?
    # 1 = Yes (price goes up)
    # 0 = No  (price goes down or flat)
    # ----------------------------------------------------------
    df["future_return_5d"] = df["Close"].pct_change(5).shift(-5)
    df["target"] = (df["future_return_5d"] > 0).astype(int)

    # ----------------------------------------------------------
    # DROP ROWS WITH MISSING VALUES
    # First ~50 rows will have NaN from indicators
    # ----------------------------------------------------------
    df.dropna(inplace=True)

    print(f"✅ Features calculated: {len(df.columns)} columns")
    print(f"✅ Clean rows available: {len(df)}")
    print(f"\nFeature columns:")
    for col in df.columns:
        print(f"   {col}")

    return df


# ============================================================
# RUN IT — Load AAPL data and calculate features
# ============================================================
if __name__ == "__main__":

    # Load the data we already downloaded
    df = pd.read_csv("data/AAPL.csv", index_col="Date", parse_dates=True)

    # Calculate all features
    df = calculate_features(df)

    # Save the processed data
    os.makedirs("data", exist_ok=True)
    df.to_csv("data/AAPL_features.csv")
    print(f"\n✅ Saved to data/AAPL_features.csv")

    # Show a sample
    print(f"\nSample of processed data:")
    print(df[["Close", "rsi_14", "macd", "volume_ratio",
              "bb_position", "target"]].tail(5))
