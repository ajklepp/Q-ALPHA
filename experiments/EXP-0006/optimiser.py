# ============================================================
# Q-ALPHA | EXP-0006 | experiment6.py
# Purpose: Full backtest using optimised bracket parameters
#
# Fixes from previous version:
#   - Equity curve now correctly tracks pool + profit
#   - Released capital = slice capital + PnL (not just capital)
#   - End of period close correctly returns capital + profit
#   - Position sizing correctly depletes pool on entry
#
# Requires: params.json from optimiser.py
# ============================================================

import pandas as pd
import numpy as np
import yfinance as yf
import pandas_ta as ta
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, precision_recall_curve
import json
import warnings
warnings.filterwarnings("ignore")

# ============================================================
# SETTINGS
# ============================================================
TICKERS        = ["JNJ", "WMT", "XOM", "JPM", "MSFT"]
START_DATE     = "2019-01-01"
END_DATE       = "2024-12-31"
SPLIT_DATE     = "2023-01-01"
STARTING_CAP   = 100_000.00
MAX_TRADES_DAY = 3
MAX_POSITIONS  = 5
COST_PER_TRADE = 0.0015
S4_RUNNER_DAYS = 30

FEATURE_COLUMNS = [
    "return_1d", "return_5d", "return_10d",
    "close_vs_sma20", "close_vs_sma50",
    "rsi_14", "macd", "macd_signal", "macd_hist",
    "bb_position", "volume_ratio",
    "volatility_10d", "volatility_20d",
]


# ============================================================
# DATA
# ============================================================
def download_and_process(ticker, start, end):
    try:
        df = yf.download(
            ticker, start=start, end=end,
            auto_adjust=True, progress=False
        )
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if len(df) < 200:
            return None

        df["return_1d"]  = df["Close"].pct_change(1)
        df["return_5d"]  = df["Close"].pct_change(5)
        df["return_10d"] = df["Close"].pct_change(10)
        df["sma_20"]     = ta.sma(df["Close"], length=20)
        df["sma_50"]     = ta.sma(df["Close"], length=50)
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
        df["bb_upper"]    = bbands.iloc[:, 0]
        df["bb_lower"]    = bbands.iloc[:, 2]
        df["bb_position"] = (
            (df["Close"] - df["bb_lower"]) /
            (df["bb_upper"] - df["bb_lower"])
        )

        df["volume_sma_20"]  = ta.sma(df["Volume"], length=20)
        df["volume_ratio"]   = df["Volume"] / df["volume_sma_20"]
        df["volatility_10d"] = df["return_1d"].rolling(10).std()
        df["volatility_20d"] = df["return_1d"].rolling(20).std()
        df["atr_14"]         = ta.atr(
            df["High"], df["Low"], df["Close"], length=14
        )

        df["future_return_5d"] = df["Close"].pct_change(5).shift(-5)
        df["target"] = (
            df["future_return_5d"] > 0.01
        ).astype(int)

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
# MODEL
# ============================================================
def train_model(train_df):
    model = RandomForestClassifier(
        n_estimators=200, max_depth=4,
        min_samples_split=40, min_samples_leaf=20,
        class_weight="balanced", random_state=42,
        n_jobs=-1
    )
    model.fit(train_df[FEATURE_COLUMNS], train_df["target"])
    return model


def find_threshold(model, df):
    probs = model.predict_proba(df[FEATURE_COLUMNS])[:, 1]
    precisions, recalls, thresholds = precision_recall_curve(
        df["target"], probs
    )
    best_t, best_p = 0.5, 0
    for p, r, t in zip(precisions, recalls, thresholds):
        if r >= 0.20 and p > best_p:
            best_p, best_t = p, t
    return best_t


# ============================================================
# BRACKET POSITION
# ============================================================
class BracketPosition:
    """
    Manages one open trade with 4 trailing stops.

    Capital accounting:
      - Entry:  pool decreases by position_size
      - Exit:   pool increases by slice_capital + pnl
                (pnl can be negative on losses)

    This ensures equity = pool + deployed at all times
    and profits/losses flow correctly through the system.
    """

    def __init__(self, ticker, entry_price, entry_date,
                 position_size, params):
        self.ticker        = ticker
        self.entry_price   = entry_price
        self.entry_date    = entry_date
        self.position_size = position_size
        self.params        = params

        # Master stop based on ATR
        atr_val  = params.get("atr_at_entry", entry_price * 0.02)
        atr_pct  = params["master_atr_mult"] * (atr_val / entry_price)
        self.master_stop = entry_price * (1 - atr_pct)

        # 4 slices — each manages its own trail independently
        self.slices = {
            1: {
                "trail":        params["trail_1"],
                "alloc":        params["alloc_1"],
                "active":       False,
                "high":         entry_price,
                "closed":       False,
                "days_no_high": 0,
                "pnl":          0.0,
            },
            2: {
                "trail":        params["trail_2"],
                "alloc":        params["alloc_2"],
                "active":       False,
                "high":         entry_price,
                "closed":       False,
                "days_no_high": 0,
                "pnl":          0.0,
            },
            3: {
                "trail":        params["trail_3"],
                "alloc":        params["alloc_3"],
                "active":       False,
                "high":         entry_price,
                "closed":       False,
                "days_no_high": 0,
                "pnl":          0.0,
            },
            4: {
                "trail":        params["trail_4"],
                "alloc":        params["alloc_4"],
                "active":       False,
                "high":         entry_price,
                "closed":       False,
                "days_no_high": 0,
                "pnl":          0.0,
            },
        }

        self.realized_pnl    = 0.0
        self.is_bonus_runner = False
        self.fully_closed    = False
        self.exit_log        = []

    def deployed_capital(self):
        """Returns original capital still at risk in open slices."""
        return sum(
            self.position_size * s["alloc"]
            for s in self.slices.values()
            if not s["closed"]
        )

    def update(self, current_price, current_date):
        """
        Process one day's price update.
        Returns amount to ADD BACK to pool:
          = slice_capital + pnl  (profit flows back to pool)
          = slice_capital - loss (loss reduces pool)
        """
        released = 0.0

        for sid, s in self.slices.items():
            if s["closed"]:
                continue

            slice_capital = self.position_size * s["alloc"]

            # ── Update high watermark ────────────────────────
            if current_price > s["high"]:
                s["high"]         = current_price
                s["days_no_high"] = 0
            else:
                s["days_no_high"] += 1

            # ── Tighten stop 4 after 30 days no new high ────
            trail = s["trail"]
            if sid == 4 and s["days_no_high"] >= S4_RUNNER_DAYS:
                trail = min(trail, 0.10)

            # ── Calculate current trail level ────────────────
            trail_level = s["high"] * (1 - trail)

            # ── Activation: trail crosses above entry price ──
            if not s["active"]:
                if trail_level > self.entry_price:
                    s["active"] = True

            # ── Master stop (only while trail not yet active) ─
            if not s["active"]:
                if current_price <= self.master_stop:
                    gross  = (current_price - self.entry_price) \
                             / self.entry_price
                    net    = gross - COST_PER_TRADE
                    pnl    = slice_capital * net
                    s["pnl"]           = pnl
                    self.realized_pnl += pnl
                    s["closed"]        = True
                    # Return capital + profit (profit is negative here)
                    released += slice_capital + pnl
                    self.exit_log.append({
                        "stop":       "MASTER",
                        "sid":        sid,
                        "date":       current_date,
                        "exit_price": current_price,
                        "pnl":        pnl,
                        "return_pct": gross,
                    })
                continue

            # ── Trailing stop hit ────────────────────────────
            if current_price <= trail_level:
                gross  = (current_price - self.entry_price) \
                         / self.entry_price
                net    = gross - COST_PER_TRADE
                pnl    = slice_capital * net
                s["pnl"]           = pnl
                self.realized_pnl += pnl
                s["closed"]        = True
                # Return capital + profit
                released += slice_capital + pnl
                self.exit_log.append({
                    "stop":       f"S{sid}",
                    "sid":        sid,
                    "date":       current_date,
                    "exit_price": current_price,
                    "pnl":        pnl,
                    "return_pct": gross,
                })

        # ── Bonus runner check ───────────────────────────────
        open_sids = [
            sid for sid, s in self.slices.items()
            if not s["closed"]
        ]
        if open_sids == [4]:
            self.is_bonus_runner = True

        # ── Fully closed check ───────────────────────────────
        if all(s["closed"] for s in self.slices.values()):
            self.fully_closed = True

        return released


# ============================================================
# LEVEL 1 — BACKTESTER
# ============================================================
def run_backtest(full_df, model, threshold,
                 all_params, start, end, label="Backtest"):
    """
    Full equity curve backtest with:
      - Real capital pool starting at $100,000
      - Max 3 new trades per day
      - Max 5 active positions (runners excluded)
      - Capital + profit returns to pool on every exit
      - Equity tracked daily as pool + deployed
    """
    print(f"\n{'='*55}")
    print(f"  LEVEL 1 — BACKTEST: {label}")
    print(f"  Period: {start} to {end}")
    print(f"{'='*55}")

    # Filter data to period
    period_df = full_df[
        (full_df.index >= pd.Timestamp(start)) &
        (full_df.index <= pd.Timestamp(end))
    ].copy()

    if len(period_df) == 0:
        print("  ❌ No data for this period")
        return None

    # Price pivot for daily price lookups
    price_pivot = period_df.pivot_table(
        index=period_df.index,
        columns="ticker",
        values="Close",
        aggfunc="last"
    )

    # Capital pool — this is the source of truth
    pool           = STARTING_CAP
    active         = []    # BracketPosition — counting toward limit
    runners        = []    # BracketPosition — bonus runners, free
    all_closed     = []    # All fully closed positions
    equity_curve   = []

    all_dates      = sorted(period_df.index.unique())
    prev_day       = None
    day_trade_count= 0

    for current_date in all_dates:

        # ── Reset daily trade counter ────────────────────────
        cur_day = current_date.date()
        if cur_day != prev_day:
            day_trade_count = 0
            prev_day        = cur_day

        # ── Update active positions ──────────────────────────
        still_active = []
        for pos in active:
            try:
                px = float(price_pivot.loc[current_date, pos.ticker])
            except Exception:
                still_active.append(pos)
                continue

            released = pos.update(px, current_date)
            pool    += released   # Capital + profit flows back

            if pos.fully_closed:
                all_closed.append(pos)
            elif pos.is_bonus_runner:
                runners.append(pos)
            else:
                still_active.append(pos)

        active = still_active

        # ── Update bonus runners ─────────────────────────────
        still_running = []
        for pos in runners:
            try:
                px = float(price_pivot.loc[current_date, pos.ticker])
            except Exception:
                still_running.append(pos)
                continue

            released = pos.update(px, current_date)
            pool    += released   # Profit from runners flows back

            if pos.fully_closed:
                all_closed.append(pos)
            else:
                still_running.append(pos)

        runners = still_running

        # ── Check for new signals ────────────────────────────
        active_tickers = {p.ticker for p in active}

        try:
            todays_rows = period_df.loc[[current_date]]
        except Exception:
            todays_rows = pd.DataFrame()

        for _, row in todays_rows.iterrows():
            # Hard limits
            if len(active) >= MAX_POSITIONS:
                break
            if day_trade_count >= MAX_TRADES_DAY:
                break

            ticker = row.get("ticker")
            if ticker not in TICKERS:
                continue

            # No duplicate active positions on same ticker
            if ticker in active_tickers:
                continue

            # Get model signal probability
            try:
                feats = row[FEATURE_COLUMNS].values.reshape(1, -1)
                prob  = model.predict_proba(feats)[0][1]
            except Exception:
                continue

            if prob < threshold:
                continue

            # Entry price
            try:
                entry_px = float(price_pivot.loc[
                    current_date, ticker
                ])
            except Exception:
                continue

            # Position size = equal share of total capital
            # Using STARTING_CAP for consistent sizing
            # not current pool (avoids compounding issues)
            pos_size = STARTING_CAP / MAX_POSITIONS
            if pool < pos_size * 0.5:
                continue   # Not enough capital

            # Get ATR at entry
            atr_val = float(row.get("atr_14", entry_px * 0.02))

            # Load optimised params for this ticker
            p = dict(all_params.get(ticker, {}))
            p["atr_at_entry"] = atr_val

            # Open position
            pos = BracketPosition(
                ticker        = ticker,
                entry_price   = entry_px,
                entry_date    = current_date,
                position_size = pos_size,
                params        = p,
            )

            pool             -= pos_size   # Deduct from pool
            active.append(pos)
            active_tickers.add(ticker)
            day_trade_count  += 1

        # ── Record daily equity ──────────────────────────────
        # Equity = available cash pool
        #        + all capital currently deployed in open slices
        # Note: realized profits already flowed back into pool
        deployed = (
            sum(p.deployed_capital() for p in active) +
            sum(p.deployed_capital() for p in runners)
        )
        equity_curve.append({
            "date":   current_date,
            "equity": pool + deployed,
        })

    # ── Force close all remaining positions at period end ────
    last_date = all_dates[-1]
    for pos in active + runners:
        for sid, s in pos.slices.items():
            if not s["closed"]:
                try:
                    lp = float(price_pivot.loc[last_date, pos.ticker])
                except Exception:
                    lp = pos.entry_price

                gross  = (lp - pos.entry_price) / pos.entry_price
                net    = gross - COST_PER_TRADE
                pnl    = pos.position_size * s["alloc"] * net
                pos.realized_pnl += pnl
                s["closed"]        = True
                # Return capital + profit to pool
                pool += pos.position_size * s["alloc"] + pnl

        pos.fully_closed = True
        all_closed.append(pos)

    # Update final equity point with post-close pool value
    if equity_curve:
        equity_curve[-1]["equity"] = pool

    # ── Calculate performance metrics ────────────────────────
    eq_df = pd.DataFrame(equity_curve).set_index("date")
    eq_df["ret"] = eq_df["equity"].pct_change().fillna(0)

    final_eq  = eq_df["equity"].iloc[-1]
    total_ret = (final_eq - STARTING_CAP) / STARTING_CAP

    sharpe = (
        eq_df["ret"].mean() / eq_df["ret"].std() * np.sqrt(252)
        if eq_df["ret"].std() > 0 else 0.0
    )

    roll_max = eq_df["equity"].expanding().max()
    drawdown = (eq_df["equity"] - roll_max) / roll_max
    max_dd   = drawdown.min()

    n_trades   = len(all_closed)
    win_trades = sum(1 for p in all_closed if p.realized_pnl > 0)
    win_rate   = win_trades / n_trades if n_trades > 0 else 0

    total_pnl  = sum(p.realized_pnl for p in all_closed)

    # Buy and hold comparison
    bh_list = []
    for ticker in TICKERS:
        col = price_pivot.get(ticker)
        if col is None:
            continue
        col = col.dropna()
        if len(col) > 1:
            bh_list.append(
                (col.iloc[-1] - col.iloc[0]) / col.iloc[0]
            )
    buy_hold = float(np.mean(bh_list)) if bh_list else 0.0

    # Stop type breakdown
    all_exits = []
    for pos in all_closed:
        all_exits.extend(pos.exit_log)
    exits_df    = pd.DataFrame(all_exits) if all_exits else \
                  pd.DataFrame(columns=["stop"])
    stop_counts = exits_df["stop"].value_counts() \
                  if not exits_df.empty else pd.Series()

    # Per-stock breakdown
    per_stock = {}
    for ticker in TICKERS:
        stock_positions = [
            p for p in all_closed if p.ticker == ticker
        ]
        if stock_positions:
            st_pnl  = sum(p.realized_pnl for p in stock_positions)
            st_wins = sum(
                1 for p in stock_positions if p.realized_pnl > 0
            )
            per_stock[ticker] = {
                "trades": len(stock_positions),
                "pnl":    st_pnl,
                "win_rate": st_wins / len(stock_positions),
            }

    # ── Print results ────────────────────────────────────────
    print(f"\n  Starting Capital  : ${STARTING_CAP:>12,.2f}")
    print(f"  Final Equity      : ${final_eq:>12,.2f}")
    print(f"  Total Return      : {total_ret:>10.2%}")
    print(f"  Total PnL         : ${total_pnl:>12,.2f}")
    print(f"  Buy & Hold        : {buy_hold:>10.2%}")
    print(f"  Sharpe Ratio      : {sharpe:>10.2f}")
    print(f"  Max Drawdown      : {max_dd:>10.2%}")
    print(f"  Total Trades      : {n_trades}")
    print(f"  Win Rate          : {win_rate:.2%}")
    print(f"  Bonus Runners     : {len(runners)}")

    print(f"\n  Stop Breakdown:")
    for st in ["MASTER", "S1", "S2", "S3", "S4"]:
        c = stop_counts.get(st, 0)
        print(f"    {st:<8}: {c:>4} exits")

    print(f"\n  Per Stock:")
    print(f"  {'Ticker':<8} {'Trades':>7} "
          f"{'PnL':>10} {'WinRate':>9}")
    print(f"  {'-'*38}")
    for ticker, st in per_stock.items():
        print(f"  {ticker:<8} {st['trades']:>7} "
              f"${st['pnl']:>9,.0f} {st['win_rate']:>8.1%}")

    # ── Quant Assassin ───────────────────────────────────────
    print(f"\n  ⚔️  Quant Assassin:")
    checks = 0

    if total_ret > 0:
        print(f"  ✅ Positive return    : {total_ret:.2%}")
        checks += 1
    else:
        print(f"  ❌ Negative return    : {total_ret:.2%}")

    if total_ret > buy_hold:
        print(f"  ✅ Beats buy & hold   : "
              f"{total_ret:.2%} vs {buy_hold:.2%}")
        checks += 1
    else:
        print(f"  ❌ Trails buy & hold  : "
              f"{total_ret:.2%} vs {buy_hold:.2%}")

    if sharpe >= 1.0:
        print(f"  ✅ Sharpe above 1.0   : {sharpe:.2f}")
        checks += 1
    else:
        print(f"  ❌ Sharpe below 1.0   : {sharpe:.2f}")

    if max_dd >= -0.15:
        print(f"  ✅ Drawdown under 15% : {max_dd:.2%}")
        checks += 1
    else:
        print(f"  ❌ Drawdown over 15%  : {max_dd:.2%}")

    print(f"\n  VERDICT: {checks}/4 checks passed")

    return {
        "eq_df":      eq_df,
        "total_ret":  total_ret,
        "total_pnl":  total_pnl,
        "buy_hold":   buy_hold,
        "sharpe":     sharpe,
        "max_dd":     max_dd,
        "n_trades":   n_trades,
        "win_rate":   win_rate,
        "checks":     checks,
        "all_closed": all_closed,
    }


# ============================================================
# LEVEL 2 — WALK FORWARD
# ============================================================
def run_walk_forward(full_df, all_params):
    print(f"\n{'='*55}")
    print(f"  LEVEL 2 — WALK FORWARD")
    print(f"{'='*55}")

    windows = [
        ("2019-01-01", "2021-01-01",
         "2021-01-01", "2022-01-01", "2021 Bull"),
        ("2019-01-01", "2022-01-01",
         "2022-01-01", "2023-01-01", "2022 Bear"),
        ("2019-01-01", "2023-01-01",
         "2023-01-01", "2024-01-01", "2023 Recovery"),
        ("2019-01-01", "2024-01-01",
         "2024-01-01", "2024-12-31", "2024 AI Rally"),
    ]

    wf_results = []

    for ts, te, vs, ve, label in windows:
        train_w = full_df[
            (full_df.index >= pd.Timestamp(ts)) &
            (full_df.index <  pd.Timestamp(te))
        ]
        if len(train_w) < 100:
            continue

        model_w   = train_model(train_w)
        threshold = find_threshold(model_w, train_w)

        result = run_backtest(
            full_df, model_w, threshold,
            all_params, vs, ve, label=label
        )

        if result is None:
            continue

        passed = (
            result["total_ret"] > 0 and
            result["checks"] >= 2
        )
        icon = "✅" if passed else "❌"
        print(f"\n  {icon} {label:<20} "
              f"Return: {result['total_ret']:.2%} | "
              f"Sharpe: {result['sharpe']:.2f} | "
              f"DD: {result['max_dd']:.2%}")

        wf_results.append({
            "label":  label,
            "ret":    result["total_ret"],
            "sharpe": result["sharpe"],
            "dd":     result["max_dd"],
            "passed": passed,
        })

    passed_count = sum(1 for w in wf_results if w["passed"])
    total        = len(wf_results)

    print(f"\n  Walk Forward: {passed_count}/{total} passed")
    if passed_count == total:
        print(f"  ✅ Robust across all market regimes")
    elif passed_count >= 3:
        print(f"  ⚠️  Works in most regimes — investigate failure")
    else:
        print(f"  ❌ Not regime robust")

    return wf_results


# ============================================================
# LEVEL 3 — MONTE CARLO
# ============================================================
def run_monte_carlo(all_closed, n=10000):
    print(f"\n{'='*55}")
    print(f"  LEVEL 3 — MONTE CARLO ({n:,} simulations)")
    print(f"{'='*55}")

    if not all_closed:
        print("  ❌ No trades to simulate")
        return None

    real_rets = np.array([p.realized_pnl for p in all_closed])

    if len(real_rets) < 5:
        print("  ❌ Too few trades for Monte Carlo")
        return None

    real_total  = real_rets.sum()
    real_sharpe = (
        real_rets.mean() / real_rets.std() * np.sqrt(252)
        if real_rets.std() > 0 else 0.0
    )

    print(f"\n  Real Strategy:")
    print(f"  Total PnL    : ${real_total:,.2f}")
    print(f"  Sharpe       : {real_sharpe:.3f}")
    print(f"  Trades       : {len(real_rets)}")
    print(f"\n  Simulating {n:,} random portfolios...")

    rng         = np.random.default_rng(42)
    sim_sharpes = []
    sim_totals  = []

    for _ in range(n):
        shuffled = rng.choice(
            real_rets, size=len(real_rets), replace=True
        )
        sim_totals.append(shuffled.sum())
        sim_sharpes.append(
            shuffled.mean() / shuffled.std() * np.sqrt(252)
            if shuffled.std() > 0 else 0.0
        )

    sim_sharpes = np.array(sim_sharpes)
    sim_totals  = np.array(sim_totals)

    sharpe_pct = (sim_sharpes < real_sharpe).mean() * 100
    return_pct = (sim_totals  < real_total ).mean() * 100
    p_value    = 1 - sharpe_pct / 100

    print(f"\n  Real Sharpe        : {real_sharpe:.3f}")
    print(f"  Random 50th pct    : "
          f"{np.percentile(sim_sharpes, 50):.3f}")
    print(f"  Random 95th pct    : "
          f"{np.percentile(sim_sharpes, 95):.3f}")
    print(f"  Random 99th pct    : "
          f"{np.percentile(sim_sharpes, 99):.3f}")
    print(f"\n  Beats random       : {sharpe_pct:.1f}% of simulations")
    print(f"  Return beats random: {return_pct:.1f}% of simulations")
    print(f"  p-value            : {p_value:.3f}")

    passed = sharpe_pct >= 95
    print(f"\n  ⚔️  Quant Assassin:")
    if passed:
        print(f"  ✅ Statistically significant (p={p_value:.3f})")
        print(f"     Edge is likely real — not random luck")
    elif sharpe_pct >= 85:
        print(f"  ⚠️  Marginal (p={p_value:.3f}) — needs more trades")
    else:
        print(f"  ❌ Not significant (p={p_value:.3f})")
        print(f"     Could be random luck")

    return {
        "real_sharpe": real_sharpe,
        "real_total":  real_total,
        "sharpe_pct":  sharpe_pct,
        "p_value":     p_value,
        "passed":      passed,
        "sim_p95":     np.percentile(sim_sharpes, 95),
    }


# ============================================================
# FINAL REPORT
# ============================================================
def final_report(bt, wf, mc):
    print(f"\n\n{'='*55}")
    print(f"  Q-ALPHA | EXP-0006 | FINAL REPORT")
    print(f"{'='*55}")

    bt_pass = bt is not None and bt["checks"] >= 3
    wf_pass = (wf is not None and
               sum(1 for w in wf if w["passed"]) >= 3)
    mc_pass = mc is not None and mc["passed"]

    print(f"\n  LEVEL 1 — BACKTEST")
    print(f"  {'─'*45}")
    if bt:
        print(f"  Starting Capital : ${STARTING_CAP:>10,.2f}")
        print(f"  Final Equity     : ${bt['eq_df']['equity'].iloc[-1]:>10,.2f}")
        print(f"  Total Return     : {bt['total_ret']:>10.2%}")
        print(f"  Total PnL        : ${bt['total_pnl']:>10,.2f}")
        print(f"  Buy & Hold       : {bt['buy_hold']:>10.2%}")
        print(f"  Sharpe Ratio     : {bt['sharpe']:>10.2f}")
        print(f"  Max Drawdown     : {bt['max_dd']:>10.2%}")
        print(f"  Win Rate         : {bt['win_rate']:>10.2%}")
        print(f"  Total Trades     : {bt['n_trades']:>10}")
        print(f"  Result           : "
              f"{'✅ PASS' if bt_pass else '❌ FAIL'} "
              f"({bt['checks']}/4)")

    print(f"\n  LEVEL 2 — WALK FORWARD")
    print(f"  {'─'*45}")
    if wf:
        for w in wf:
            icon = "✅" if w["passed"] else "❌"
            print(f"  {icon} {w['label']:<22} "
                  f"Ret: {w['ret']:>7.2%}  "
                  f"Sharpe: {w['sharpe']:>5.2f}")
        passed_wf = sum(1 for w in wf if w["passed"])
        print(f"  Result           : "
              f"{'✅ PASS' if wf_pass else '❌ FAIL'} "
              f"({passed_wf}/{len(wf)})")

    print(f"\n  LEVEL 3 — MONTE CARLO")
    print(f"  {'─'*45}")
    if mc:
        print(f"  Beats random     : {mc['sharpe_pct']:.1f}%")
        print(f"  p-value          : {mc['p_value']:.3f}")
        print(f"  Result           : "
              f"{'✅ PASS' if mc_pass else '❌ FAIL'}")

    print(f"\n  {'═'*45}")
    all_pass = bt_pass and wf_pass and mc_pass
    if all_pass:
        print(f"\n  ✅ ALL LEVELS PASSED")
        print(f"  APPROVED FOR PAPER TRADING")
    else:
        print(f"\n  ❌ NOT ALL LEVELS PASSED")
        print(f"  Review failures and iterate")
        if bt and not bt_pass:
            print(f"  → Backtest needs work")
        if wf and not wf_pass:
            print(f"  → Walk forward not regime robust")
        if mc and not mc_pass:
            print(f"  → Edge not statistically proven")

    print(f"\n{'='*55}")


# ============================================================
# RUN
# ============================================================
if __name__ == "__main__":

    # Load optimised params
    params_path = "experiments/EXP-0006/params.json"
    try:
        with open(params_path, "r") as f:
            all_params = json.load(f)
        print(f"✅ Loaded params from {params_path}")
        print(f"\n  Optimised parameters:")
        for ticker, p in all_params.items():
            print(f"  {ticker}: "
                  f"master={p['master_atr_mult']}× ATR | "
                  f"trails={p['trail_1']:.0%}/"
                  f"{p['trail_2']:.0%}/"
                  f"{p['trail_3']:.0%}/"
                  f"{p['trail_4']:.0%} | "
                  f"alloc={p['alloc_1']:.0%}/"
                  f"{p['alloc_2']:.0%}/"
                  f"{p['alloc_3']:.0%}/"
                  f"{p['alloc_4']:.0%}")
    except FileNotFoundError:
        print(f"❌ params.json not found")
        print(f"   Run optimiser.py first")
        exit()

    # Download full dataset
    print("\n📥 Downloading full dataset...\n")
    full_df = build_dataset(TICKERS, START_DATE, END_DATE)

    # Train on training period only
    train_df = full_df[full_df.index < pd.Timestamp(SPLIT_DATE)]

    print(f"\n🤖 Training model on training period...")
    model     = train_model(train_df)
    threshold = find_threshold(model, train_df)
    print(f"✅ Threshold: {threshold:.3f}")

    # ── Level 1: Backtest on unseen test data ────────────────
    bt = run_backtest(
        full_df, model, threshold, all_params,
        SPLIT_DATE, END_DATE
    )

    # ── Level 2: Walk Forward across all regimes ─────────────
    wf = run_walk_forward(full_df, all_params)

    # ── Level 3: Monte Carlo significance test ───────────────
    mc = None
    if bt and bt["all_closed"]:
        mc = run_monte_carlo(bt["all_closed"])
    else:
        print("\n  ⚠️  Skipping Monte Carlo — no closed trades")

    # ── Final Report ─────────────────────────────────────────
    final_report(bt, wf, mc)
