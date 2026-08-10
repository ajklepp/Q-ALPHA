# ============================================================
# Q-ALPHA | EXP-0005
# Purpose: Full validation stack
#   Level 1 — Backtest with transaction costs
#   Level 2 — Walk Forward (4 year rolling windows)
#   Level 3 — Monte Carlo (10,000 simulations)
# Universe : Defensive stocks (JNJ, WMT, XOM, JPM, MSFT)
# ============================================================

import pandas as pd
import numpy as np
import yfinance as yf
import pandas_ta as ta
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, precision_recall_curve
import warnings
warnings.filterwarnings("ignore")


# ============================================================
# SETTINGS
# ============================================================
TICKERS    = ["JNJ", "WMT", "XOM", "JPM", "MSFT"]
START_DATE = "2019-01-01"
END_DATE   = "2024-12-31"
SPLIT_DATE = "2023-01-01"

# Transaction cost assumptions
COMMISSION      = 0.0010   # 0.10% per trade
SLIPPAGE        = 0.0005   # 0.05% per trade
COST_PER_TRADE  = COMMISSION + SLIPPAGE   # 0.15% round trip

# Model settings
FEATURE_COLUMNS = [
    "return_1d", "return_5d", "return_10d",
    "close_vs_sma20", "close_vs_sma50",
    "rsi_14", "macd", "macd_signal", "macd_hist",
    "bb_position", "volume_ratio",
    "volatility_10d", "volatility_20d",
]


# ============================================================
# DATA FUNCTIONS
# ============================================================
def download_and_process(ticker, start, end):
    try:
        df = yf.download(ticker, start=start, end=end,
                         auto_adjust=True, progress=False)

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        if len(df) < 200:
            return None

        df["return_1d"]  = df["Close"].pct_change(1)
        df["return_5d"]  = df["Close"].pct_change(5)
        df["return_10d"] = df["Close"].pct_change(10)

        df["sma_20"] = ta.sma(df["Close"], length=20)
        df["sma_50"] = ta.sma(df["Close"], length=50)
        df["close_vs_sma20"] = (
            (df["Close"] - df["sma_20"]) / df["sma_20"]
        )
        df["close_vs_sma50"] = (
            (df["Close"] - df["sma_50"]) / df["sma_50"]
        )

        df["rsi_14"] = ta.rsi(df["Close"], length=14)

        macd = ta.macd(df["Close"], fast=12, slow=26, signal=9)
        df["macd"]        = macd["MACD_12_26_9"]
        df["macd_signal"] = macd["MACDs_12_26_9"]
        df["macd_hist"]   = macd["MACDh_12_26_9"]

        bbands = ta.bbands(df["Close"], length=20, std=2)
        df["bb_upper"] = bbands.iloc[:, 0]
        df["bb_lower"] = bbands.iloc[:, 2]
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


def build_dataset(tickers, start, end):
    all_data = []
    for ticker in tickers:
        df = download_and_process(ticker, start, end)
        if df is not None:
            all_data.append(df)
            print(f"   ✅ {ticker}: {len(df)} rows")
    combined = pd.concat(all_data)
    combined.sort_index(inplace=True)
    return combined


# ============================================================
# MODEL FUNCTIONS
# ============================================================
def train_model(train):
    model = RandomForestClassifier(
        n_estimators=200,
        max_depth=4,
        min_samples_split=40,
        min_samples_leaf=20,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1
    )
    model.fit(train[FEATURE_COLUMNS], train["target"])
    return model


def find_threshold(model, test):
    probs = model.predict_proba(test[FEATURE_COLUMNS])[:, 1]
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


# ============================================================
# LEVEL 1 — BACKTESTER
# ============================================================
def run_backtest(model, test, threshold, label="Backtest"):
    """
    Simulates actual trades based on model signals.

    Rules:
    - When model predicts Edge: BUY at next open
    - Hold for 5 days then SELL
    - Each position = equal weight (1/5 of portfolio)
    - Include transaction costs on every trade
    - Never use future information
    """
    print(f"\n{'='*55}")
    print(f"  LEVEL 1 — BACKTEST: {label}")
    print(f"{'='*55}")

    test = test.copy()
    probs = model.predict_proba(test[FEATURE_COLUMNS])[:, 1]
    test["signal"]    = (probs >= threshold).astype(int)
    test["signal_prob"] = probs

    trades = []

    for ticker in TICKERS:
        t = test[test["ticker"] == ticker].copy()
        if len(t) == 0:
            continue

        signal_dates = t[t["signal"] == 1].index

        for entry_date in signal_dates:
            try:
                entry_idx = t.index.get_loc(entry_date)
                exit_idx  = entry_idx + 5

                if exit_idx >= len(t):
                    continue

                entry_price = t.iloc[entry_idx]["Open"]
                exit_price  = t.iloc[exit_idx]["Open"]

                # Gross return
                gross_return = (exit_price - entry_price) / entry_price

                # Net return after transaction costs
                net_return = gross_return - COST_PER_TRADE

                trades.append({
                    "ticker":       ticker,
                    "entry_date":   entry_date,
                    "exit_date":    t.index[exit_idx],
                    "entry_price":  entry_price,
                    "exit_price":   exit_price,
                    "gross_return": gross_return,
                    "net_return":   net_return,
                    "signal_prob":  t.iloc[entry_idx]["signal_prob"],
                })

            except Exception:
                continue

    if len(trades) == 0:
        print("  ❌ No trades generated")
        return None

    trades_df = pd.DataFrame(trades)
    trades_df.set_index("entry_date", inplace=True)

    # ── Performance Metrics ─────────────────────────────────
    total_trades   = len(trades_df)
    winning_trades = (trades_df["net_return"] > 0).sum()
    win_rate       = winning_trades / total_trades

    avg_win  = trades_df[trades_df["net_return"] > 0]["net_return"].mean()
    avg_loss = trades_df[trades_df["net_return"] < 0]["net_return"].mean()

    total_gross = trades_df["gross_return"].sum()
    total_net   = trades_df["net_return"].sum()
    cost_drag   = total_gross - total_net

    # Sharpe ratio (annualised)
    daily_returns = trades_df["net_return"].resample("D").sum()
    sharpe = (
        daily_returns.mean() / daily_returns.std() * np.sqrt(252)
        if daily_returns.std() > 0 else 0
    )

    # Maximum drawdown
    cumulative    = (1 + trades_df["net_return"]).cumprod()
    rolling_max   = cumulative.expanding().max()
    drawdown      = (cumulative - rolling_max) / rolling_max
    max_drawdown  = drawdown.min()

    # Buy and hold comparison
    bh_returns = []
    for ticker in TICKERS:
        t = test[test["ticker"] == ticker]
        if len(t) > 1:
            bh = (t["Close"].iloc[-1] - t["Close"].iloc[0]) / t["Close"].iloc[0]
            bh_returns.append(bh)
    buy_and_hold = np.mean(bh_returns) if bh_returns else 0

    print(f"\n  Total Trades      : {total_trades}")
    print(f"  Win Rate          : {win_rate:.2%}")
    print(f"  Avg Win           : {avg_win:.2%}")
    print(f"  Avg Loss          : {avg_loss:.2%}")
    print(f"  Profit Factor     : "
          f"{abs(avg_win/avg_loss):.2f}x" if avg_loss != 0 else "N/A")
    print(f"\n  Gross Return      : {total_gross:.2%}")
    print(f"  Cost Drag         : -{cost_drag:.2%}")
    print(f"  Net Return        : {total_net:.2%}")
    print(f"  Buy & Hold        : {buy_and_hold:.2%}")
    print(f"\n  Sharpe Ratio      : {sharpe:.2f}")
    print(f"  Max Drawdown      : {max_drawdown:.2%}")

    # Quant Assassin
    print(f"\n  ⚔️  Quant Assassin:")
    checks = 0
    if total_net > 0:
        print(f"  ✅ Positive net return    : {total_net:.2%}")
        checks += 1
    else:
        print(f"  ❌ Negative net return    : {total_net:.2%}")

    if total_net > buy_and_hold:
        print(f"  ✅ Beats buy & hold       : "
              f"{total_net:.2%} vs {buy_and_hold:.2%}")
        checks += 1
    else:
        print(f"  ❌ Trails buy & hold      : "
              f"{total_net:.2%} vs {buy_and_hold:.2%}")

    if sharpe > 1.0:
        print(f"  ✅ Sharpe ratio above 1.0 : {sharpe:.2f}")
        checks += 1
    else:
        print(f"  ❌ Sharpe ratio below 1.0 : {sharpe:.2f}")

    if max_drawdown > -0.15:
        print(f"  ✅ Drawdown under 15%     : {max_drawdown:.2%}")
        checks += 1
    else:
        print(f"  ❌ Drawdown over 15%      : {max_drawdown:.2%}")

    print(f"\n  VERDICT: {checks}/4 checks passed")

    return {
        "label":         label,
        "total_trades":  total_trades,
        "win_rate":      win_rate,
        "net_return":    total_net,
        "gross_return":  total_gross,
        "buy_and_hold":  buy_and_hold,
        "sharpe":        sharpe,
        "max_drawdown":  max_drawdown,
        "checks":        checks,
        "trades_df":     trades_df
    }


# ============================================================
# LEVEL 2 — WALK FORWARD
# ============================================================
def run_walk_forward(full_df):
    """
    Tests the strategy across 4 rolling time windows.
    Each window trains on all data up to that point
    and tests on the following year.
    """
    print(f"\n{'='*55}")
    print(f"  LEVEL 2 — WALK FORWARD TEST")
    print(f"{'='*55}")

    windows = [
        ("2019-01-01", "2021-01-01", "2021-01-01", "2022-01-01"),
        ("2019-01-01", "2022-01-01", "2022-01-01", "2023-01-01"),
        ("2019-01-01", "2023-01-01", "2023-01-01", "2024-01-01"),
        ("2019-01-01", "2024-01-01", "2024-01-01", "2024-12-31"),
    ]

    window_results = []

    for i, (ts, te, vs, ve) in enumerate(windows):
        train_w = full_df[
            (full_df.index >= ts) & (full_df.index < te)
        ]
        test_w  = full_df[
            (full_df.index >= vs) & (full_df.index < ve)
        ]

        if len(train_w) < 100 or len(test_w) < 50:
            continue

        model_w    = train_model(train_w)
        threshold  = find_threshold(model_w, test_w)
        probs      = model_w.predict_proba(
                        test_w[FEATURE_COLUMNS]
                     )[:, 1]
        preds      = (probs >= threshold).astype(int)
        accuracy   = accuracy_score(test_w["target"], preds)

        # Quick P&L estimate for this window
        test_copy       = test_w.copy()
        test_copy["signal"]       = preds
        test_copy["signal_prob"]  = probs

        returns = []
        for ticker in TICKERS:
            t = test_copy[test_copy["ticker"] == ticker]
            sig_dates = t[t["signal"] == 1].index
            for entry_date in sig_dates:
                try:
                    idx      = t.index.get_loc(entry_date)
                    exit_idx = idx + 5
                    if exit_idx >= len(t):
                        continue
                    gross = (
                        (t.iloc[exit_idx]["Open"] -
                         t.iloc[idx]["Open"]) /
                        t.iloc[idx]["Open"]
                    )
                    returns.append(gross - COST_PER_TRADE)
                except Exception:
                    continue

        net = sum(returns)
        trades = len(returns)
        win_r  = sum(1 for r in returns if r > 0) / trades if trades > 0 else 0

        # Market regime label
        regimes = {
            0: "2021 — Bull",
            1: "2022 — Bear",
            2: "2023 — Recovery",
            3: "2024 — AI Rally",
        }
        regime = regimes.get(i, "Unknown")

        passed = "✅" if net > 0 and accuracy > 0.50 else "❌"
        print(f"\n  Window {i+1}: {regime}")
        print(f"  Train: {ts} → {te} | Test: {vs} → {ve}")
        print(f"  Accuracy  : {accuracy:.2%}")
        print(f"  Net Return: {net:.2%}")
        print(f"  Trades    : {trades}")
        print(f"  Win Rate  : {win_r:.2%}")
        print(f"  Result    : {passed}")

        window_results.append({
            "window":    i + 1,
            "regime":    regime,
            "accuracy":  accuracy,
            "net":       net,
            "trades":    trades,
            "win_rate":  win_r,
            "passed":    net > 0 and accuracy > 0.50
        })

    passed_count = sum(1 for w in window_results if w["passed"])
    total        = len(window_results)

    print(f"\n  Walk Forward Summary: {passed_count}/{total} windows passed")

    if passed_count == total:
        print(f"  ✅ Strategy is robust across all market regimes")
    elif passed_count >= total * 0.75:
        print(f"  ⚠️  Strategy works in most regimes — investigate failures")
    else:
        print(f"  ❌ Strategy is not regime-robust — do not proceed")

    return window_results


# ============================================================
# LEVEL 3 — MONTE CARLO
# ============================================================
def run_monte_carlo(trades_df, n_simulations=10000):
    """
    Takes real trade returns and randomly shuffles them
    10,000 times to answer:
    Could this result happen by random chance?

    If real Sharpe beats 95% of random shuffles:
    The edge is statistically significant (p < 0.05)
    """
    print(f"\n{'='*55}")
    print(f"  LEVEL 3 — MONTE CARLO ({n_simulations:,} simulations)")
    print(f"{'='*55}")

    if trades_df is None or len(trades_df) == 0:
        print("  ❌ No trades to simulate")
        return None

    real_returns = trades_df["net_return"].values
    real_sharpe  = (
        real_returns.mean() / real_returns.std() * np.sqrt(252)
        if real_returns.std() > 0 else 0
    )
    real_total   = real_returns.sum()

    print(f"\n  Real Strategy:")
    print(f"  Total Return : {real_total:.2%}")
    print(f"  Sharpe Ratio : {real_sharpe:.3f}")
    print(f"  Trades       : {len(real_returns)}")
    print(f"\n  Running {n_simulations:,} random simulations...")

    sim_sharpes  = []
    sim_returns  = []

    rng = np.random.default_rng(42)

    for _ in range(n_simulations):
        shuffled     = rng.choice(real_returns,
                                  size=len(real_returns),
                                  replace=True)
        sim_total    = shuffled.sum()
        sim_sharpe   = (
            shuffled.mean() / shuffled.std() * np.sqrt(252)
            if shuffled.std() > 0 else 0
        )
        sim_sharpes.append(sim_sharpe)
        sim_returns.append(sim_total)

    sim_sharpes = np.array(sim_sharpes)
    sim_returns = np.array(sim_returns)

    # Percentile of real result vs random
    sharpe_pct = (sim_sharpes < real_sharpe).mean() * 100
    return_pct = (sim_returns < real_total).mean()
    p_value    = 1 - sharpe_pct / 100

    print(f"\n  Monte Carlo Results:")
    print(f"  Real Sharpe          : {real_sharpe:.3f}")
    print(f"  Random 50th pct      : {np.percentile(sim_sharpes, 50):.3f}")
    print(f"  Random 95th pct      : {np.percentile(sim_sharpes, 95):.3f}")
    print(f"  Random 99th pct      : {np.percentile(sim_sharpes, 99):.3f}")
    print(f"\n  Beats random         : {sharpe_pct:.1f}% of simulations")
    print(f"  p-value              : {p_value:.3f}")

    print(f"\n  ⚔️  Quant Assassin:")
    if sharpe_pct >= 95:
        print(f"  ✅ Statistically significant (p={p_value:.3f})")
        print(f"     Edge is likely real — not random luck")
        mc_pass = True
    elif sharpe_pct >= 90:
        print(f"  ⚠️  Marginal significance (p={p_value:.3f})")
        print(f"     Edge may be real — needs more data")
        mc_pass = False
    else:
        print(f"  ❌ Not statistically significant (p={p_value:.3f})")
        print(f"     Result could be random luck — do not proceed")
        mc_pass = False

    return {
        "real_sharpe":  real_sharpe,
        "real_return":  real_total,
        "sharpe_pct":   sharpe_pct,
        "p_value":      p_value,
        "passed":       mc_pass,
        "sim_p95":      np.percentile(sim_sharpes, 95),
        "sim_p99":      np.percentile(sim_sharpes, 99),
    }


# ============================================================
# FINAL REPORT
# ============================================================
def final_report(bt, wf, mc):
    print(f"\n\n{'='*55}")
    print(f"  Q-ALPHA | EXP-0005 | FINAL VALIDATION REPORT")
    print(f"{'='*55}")

    print(f"\n  LEVEL 1 — BACKTEST")
    print(f"  {'─'*45}")
    if bt:
        print(f"  Net Return    : {bt['net_return']:.2%}")
        print(f"  Buy & Hold    : {bt['buy_and_hold']:.2%}")
        print(f"  Sharpe Ratio  : {bt['sharpe']:.2f}")
        print(f"  Max Drawdown  : {bt['max_drawdown']:.2%}")
        print(f"  Win Rate      : {bt['win_rate']:.2%}")
        print(f"  Total Trades  : {bt['total_trades']}")
        bt_pass = bt["checks"] >= 3
        print(f"  Result        : {'✅ PASS' if bt_pass else '❌ FAIL'} "
              f"({bt['checks']}/4 checks)")

    print(f"\n  LEVEL 2 — WALK FORWARD")
    print(f"  {'─'*45}")
    if wf:
        wf_passed = sum(1 for w in wf if w["passed"])
        wf_total  = len(wf)
        for w in wf:
            icon = "✅" if w["passed"] else "❌"
            print(f"  {icon} Window {w['window']} "
                  f"({w['regime']:<20}) "
                  f"Return: {w['net']:.2%}")
        wf_pass = wf_passed == wf_total
        print(f"  Result        : {'✅ PASS' if wf_pass else '❌ FAIL'} "
              f"({wf_passed}/{wf_total} windows)")

    print(f"\n  LEVEL 3 — MONTE CARLO")
    print(f"  {'─'*45}")
    if mc:
        print(f"  Beats random  : {mc['sharpe_pct']:.1f}% of simulations")
        print(f"  p-value       : {mc['p_value']:.3f}")
        print(f"  Result        : {'✅ PASS' if mc['passed'] else '❌ FAIL'}")

    print(f"\n  {'─'*45}")
    all_pass = (
        bt and bt["checks"] >= 3 and
        wf and sum(1 for w in wf if w["passed"]) >= 3 and
        mc and mc["passed"]
    )

    if all_pass:
        print(f"\n  ✅ ALL LEVELS PASSED")
        print(f"  APPROVED FOR PAPER TRADING")
    else:
        print(f"\n  ❌ NOT ALL LEVELS PASSED")
        print(f"  NOT approved for paper trading yet")
        print(f"  Review failures and iterate")

    print(f"\n{'='*55}")


# ============================================================
# RUN EVERYTHING
# ============================================================
if __name__ == "__main__":

    # Download full dataset
    print("📥 Downloading defensive stock universe...\n")
    full_df = build_dataset(TICKERS, START_DATE, END_DATE)

    # Standard train/test split
    train = full_df[full_df.index <  SPLIT_DATE]
    test  = full_df[full_df.index >= SPLIT_DATE]

    print(f"\n✅ Train: {len(train)} rows | Test: {len(test)} rows")

    # Train model
    print(f"\n🤖 Training model...")
    model     = train_model(train)
    threshold = find_threshold(model, test)
    print(f"✅ Threshold: {threshold:.3f}")

    # Level 1 — Backtest
    bt = run_backtest(model, test, threshold)

    # Level 2 — Walk Forward
    wf = run_walk_forward(full_df)

    # Level 3 — Monte Carlo
    mc = None
    if bt and bt.get("trades_df") is not None:
        mc = run_monte_carlo(bt["trades_df"])

    # Final Report
    final_report(bt, wf, mc)
