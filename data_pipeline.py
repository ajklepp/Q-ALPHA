# ============================================================
# Q-ALPHA | data_pipeline.py
# Purpose: Download and save stock data for experiments
# ============================================================

import yfinance as yf
import pandas as pd
import os

def get_stock_data(ticker, start="2019-01-01", end="2024-12-31"):
    """
    Downloads daily OHLCV data for a given stock ticker.
    Saves it to the /data folder as a CSV file.
    
    Parameters:
        ticker : str  — Stock symbol e.g. "AAPL"
        start  : str  — Start date YYYY-MM-DD
        end    : str  — End date YYYY-MM-DD
    
    Returns:
        df : pandas DataFrame with OHLCV data
    """
    
    print(f"Downloading data for {ticker}...")
    
    # Download from Yahoo Finance
    df = yf.download(ticker, start=start, end=end, auto_adjust=True)
    
    # Flatten multi-level columns if present
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    
    # Basic cleaning
    df.dropna(inplace=True)
    df.index = pd.to_datetime(df.index)
    
    # Save to /data folder
    os.makedirs("data", exist_ok=True)
    filepath = f"data/{ticker}.csv"
    df.to_csv(filepath)
    
    print(f"✅ {ticker}: {len(df)} rows downloaded")
    print(f"✅ Saved to {filepath}")
    print(f"✅ Date range: {df.index[0].date()} to {df.index[-1].date()}")
    print(df.tail(3))
    
    return df


# ============================================================
# RUN IT — Download our first stock
# ============================================================
if __name__ == "__main__":
    df = get_stock_data("AAPL", start="2019-01-01", end="2024-12-31")
