# ============================================================
# Q-ALPHA | cloud/modal_test.py
# Quick test to confirm Modal cloud is working
# ============================================================
import modal

app = modal.App("q-alpha-test")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install([
        "yfinance",
        "pandas",
        "pandas-ta",
        "scikit-learn",
        "numpy",
    ])
)

@app.function(image=image, timeout=120)
def test_cloud():
    import yfinance as yf
    import pandas as pd
    import pandas_ta as ta
    import sklearn
    print("All packages loaded successfully in cloud")
    df = yf.download("AAPL", start="2024-01-01",
                     end="2024-12-31", progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df["rsi"] = ta.rsi(df["Close"], length=14)
    df.dropna(inplace=True)
    print(f"Downloaded {len(df)} rows")
    print(f"RSI min={df['rsi'].min():.1f} max={df['rsi'].max():.1f}")
    return {
        "status":       "success",
        "rows":         len(df),
        "sklearn_ver":  sklearn.__version__,
    }

@app.local_entrypoint()
def main():
    print("Sending test to Modal cloud...")
    result = test_cloud.remote()
    print(f"Status  : {result['status']}")
    print(f"Rows    : {result['rows']}")
    print(f"sklearn : {result['sklearn_ver']}")
    print("Modal cloud is working correctly")
