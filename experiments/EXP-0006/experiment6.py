# ============================================================
# Q-ALPHA | EXP-0006 | experiment6.py  CLEAN REWRITE
# Bug fix: released = slice_capital + pnl on every exit
# ============================================================
import pandas as pd
import numpy as np
import yfinance as yf
import pandas_ta as ta
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import precision_recall_curve
import json
import warnings
warnings.filterwarnings("ignore")

TICKERS        = ["JNJ", "WMT", "XOM", "JPM", "MSFT"]
START_DATE     = "2019-01-01"
END_DATE       = "2024-12-31"
SPLIT_DATE     = "2023-01-01"
STARTING_CAP   = 100_000.0
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
        df["sma_20"]     = ta.sma(df["Close"], length=20)
        df["sma_50"]     = ta.sma(df["Close"], length=50)
        df["close_vs_sma20"] = (df["Close"]-df["sma_20"])/df["sma_20"]
        df["close_vs_sma50"] = (df["Close"]-df["sma_50"])/df["sma_50"]
        df["rsi_14"] = ta.rsi(df["Close"], length=14)
        macd = ta.macd(df["Close"], fast=12, slow=26, signal=9)
        df["macd"]        = macd["MACD_12_26_9"]
        df["macd_signal"] = macd["MACDs_12_26_9"]
        df["macd_hist"]   = macd["MACDh_12_26_9"]
        bbands = ta.bbands(df["Close"], length=20, std=2)
        df["bb_upper"]    = bbands.iloc[:, 0]
        df["bb_lower"]    = bbands.iloc[:, 2]
        df["bb_position"] = (df["Close"]-df["bb_lower"])/(df["bb_upper"]-df["bb_lower"])
        df["volume_sma_20"]  = ta.sma(df["Volume"], length=20)
        df["volume_ratio"]   = df["Volume"]/df["volume_sma_20"]
        df["volatility_10d"] = df["return_1d"].rolling(10).std()
        df["volatility_20d"] = df["return_1d"].rolling(20).std()
        df["atr_14"]         = ta.atr(df["High"],df["Low"],df["Close"],length=14)
        df["future_return_5d"] = df["Close"].pct_change(5).shift(-5)
        df["target"] = (df["future_return_5d"] > 0.01).astype(int)
        df["ticker"] = ticker
        df.dropna(inplace=True)
        return df
    except Exception as e:
        print(f"   ERROR {ticker}: {e}")
        return None

def build_dataset(tickers, start, end):
    all_data = []
    for ticker in tickers:
        df = download_and_process(ticker, start, end)
        if df is not None:
            all_data.append(df)
            print(f"   OK {ticker}: {len(df)} rows")
    combined = pd.concat(all_data)
    combined.sort_index(inplace=True)
    return combined

def train_model(train_df):
    model = RandomForestClassifier(
        n_estimators=200, max_depth=4,
        min_samples_split=40, min_samples_leaf=20,
        class_weight="balanced", random_state=42, n_jobs=-1)
    model.fit(train_df[FEATURE_COLUMNS], train_df["target"])
    return model

def find_threshold(model, df):
    probs = model.predict_proba(df[FEATURE_COLUMNS])[:, 1]
    precisions, recalls, thresholds = precision_recall_curve(
        df["target"], probs)
    best_t, best_p = 0.5, 0
    for p, r, t in zip(precisions, recalls, thresholds):
        if r >= 0.20 and p > best_p:
            best_p, best_t = p, t
    return best_t

class BracketPosition:
    def __init__(self, ticker, entry_price, entry_date,
                 position_size, params):
        self.ticker        = ticker
        self.entry_price   = entry_price
        self.entry_date    = entry_date
        self.position_size = position_size
        atr_val  = params.get("atr_at_entry", entry_price * 0.02)
        atr_pct  = params["master_atr_mult"] * (atr_val / entry_price)
        self.master_stop = entry_price * (1 - atr_pct)
        self.slices = {
            1: {"trail": params["trail_1"], "alloc": params["alloc_1"],
                "active": False, "high": entry_price, "closed": False,
                "days_no_high": 0, "pnl": 0.0},
            2: {"trail": params["trail_2"], "alloc": params["alloc_2"],
                "active": False, "high": entry_price, "closed": False,
                "days_no_high": 0, "pnl": 0.0},
            3: {"trail": params["trail_3"], "alloc": params["alloc_3"],
                "active": False, "high": entry_price, "closed": False,
                "days_no_high": 0, "pnl": 0.0},
            4: {"trail": params["trail_4"], "alloc": params["alloc_4"],
                "active": False, "high": entry_price, "closed": False,
                "days_no_high": 0, "pnl": 0.0},
        }
        self.realized_pnl    = 0.0
        self.is_bonus_runner = False
        self.fully_closed    = False
        self.exit_log        = []

    def deployed_capital(self):
        return sum(self.position_size * s["alloc"]
                   for s in self.slices.values()
                   if not s["closed"])

    def update(self, current_price, current_date):
        released = 0.0
        for sid, s in self.slices.items():
            if s["closed"]:
                continue
            slice_cap = self.position_size * s["alloc"]
            if current_price > s["high"]:
                s["high"] = current_price
                s["days_no_high"] = 0
            else:
                s["days_no_high"] += 1
            trail = s["trail"]
            if sid == 4 and s["days_no_high"] >= S4_RUNNER_DAYS:
                trail = min(trail, 0.10)
            trail_level = s["high"] * (1 - trail)
            if not s["active"]:
                if trail_level > self.entry_price:
                    s["active"] = True
            if not s["active"]:
                if current_price <= self.master_stop:
                    gross = (current_price - self.entry_price) / self.entry_price
                    net   = gross - COST_PER_TRADE
                    pnl   = slice_cap * net
                    s["pnl"] = pnl
                    self.realized_pnl += pnl
                    s["closed"] = True
                    released += slice_cap + pnl
                    self.exit_log.append({"stop": "MASTER", "sid": sid,
                        "date": current_date,
                        "exit_price": current_price, "pnl": pnl})
                continue
            if current_price <= trail_level:
                gross = (current_price - self.entry_price) / self.entry_price
                net   = gross - COST_PER_TRADE
                pnl   = slice_cap * net
                s["pnl"] = pnl
                self.realized_pnl += pnl
                s["closed"] = True
                released += slice_cap + pnl
                self.exit_log.append({"stop": f"S{sid}", "sid": sid,
                    "date": current_date,
                    "exit_price": current_price, "pnl": pnl})
        open_sids = [sid for sid, s in self.slices.items()
                     if not s["closed"]]
        if open_sids == [4]:
            self.is_bonus_runner = True
        if all(s["closed"] for s in self.slices.values()):
            self.fully_closed = True
        return released

def run_backtest(full_df, model, threshold, all_params,
                 start, end, label="Backtest"):
    print(f"\n{'='*55}")
    print(f"  BACKTEST: {label}  |  {start} to {end}")
    print(f"{'='*55}")
    period_df = full_df[
        (full_df.index >= pd.Timestamp(start)) &
        (full_df.index <= pd.Timestamp(end))].copy()
    if len(period_df) == 0:
        print("  No data for this period")
        return None
    price_pivot = period_df.pivot_table(
        index=period_df.index, columns="ticker",
        values="Close", aggfunc="last")
    pool            = STARTING_CAP
    active          = []
    runners         = []
    all_closed      = []
    equity_curve    = []
    prev_day        = None
    day_trade_count = 0
    for current_date in sorted(period_df.index.unique()):
        cur_day = current_date.date()
        if cur_day != prev_day:
            day_trade_count = 0
            prev_day = cur_day
        still_active = []
        for pos in active:
            try:
                px = float(price_pivot.loc[current_date, pos.ticker])
            except Exception:
                still_active.append(pos)
                continue
            released = pos.update(px, current_date)
            pool += released
            if pos.fully_closed:
                all_closed.append(pos)
            elif pos.is_bonus_runner:
                runners.append(pos)
            else:
                still_active.append(pos)
        active = still_active
        still_running = []
        for pos in runners:
            try:
                px = float(price_pivot.loc[current_date, pos.ticker])
            except Exception:
                still_running.append(pos)
                continue
            released = pos.update(px, current_date)
            pool += released
            if pos.fully_closed:
                all_closed.append(pos)
            else:
                still_running.append(pos)
        runners = still_running
        active_tickers = {p.ticker for p in active}
        try:
            todays_rows = period_df.loc[[current_date]]
        except Exception:
            todays_rows = pd.DataFrame()
        for _, row in todays_rows.iterrows():
            if len(active) >= MAX_POSITIONS:
                break
            if day_trade_count >= MAX_TRADES_DAY:
                break
            ticker = row.get("ticker")
            if ticker not in TICKERS:
                continue
            if ticker in active_tickers:
                continue
            try:
                feats = row[FEATURE_COLUMNS].values.reshape(1, -1)
                prob  = model.predict_proba(feats)[0][1]
            except Exception:
                continue
            if prob < threshold:
                continue
            try:
                entry_px = float(price_pivot.loc[current_date, ticker])
            except Exception:
                continue
            pos_size = STARTING_CAP / MAX_POSITIONS
            if pool < pos_size * 0.5:
                continue
            atr_val = float(row.get("atr_14", entry_px * 0.02))
            p = dict(all_params.get(ticker, {}))
            p["atr_at_entry"] = atr_val
            pos = BracketPosition(
                ticker=ticker, entry_price=entry_px,
                entry_date=current_date,
                position_size=pos_size, params=p)
            pool -= pos_size
            active.append(pos)
            active_tickers.add(ticker)
            day_trade_count += 1
        deployed = (sum(p.deployed_capital() for p in active) +
                    sum(p.deployed_capital() for p in runners))
        equity_curve.append({"date": current_date,
                              "equity": pool + deployed})
    last_date = sorted(period_df.index.unique())[-1]
    for pos in active + runners:
        for sid, s in pos.slices.items():
            if not s["closed"]:
                try:
                    lp = float(price_pivot.loc[last_date, pos.ticker])
                except Exception:
                    lp = pos.entry_price
                gross = (lp - pos.entry_price) / pos.entry_price
                net   = gross - COST_PER_TRADE
                pnl   = pos.position_size * s["alloc"] * net
                pos.realized_pnl += pnl
                s["closed"] = True
                pool += pos.position_size * s["alloc"] + pnl
        pos.fully_closed = True
        all_closed.append(pos)
    if equity_curve:
        equity_curve[-1]["equity"] = pool
    eq_df = pd.DataFrame(equity_curve).set_index("date")
    eq_df["ret"] = eq_df["equity"].pct_change().fillna(0)
    final_eq  = eq_df["equity"].iloc[-1]
    total_ret = (final_eq - STARTING_CAP) / STARTING_CAP
    total_pnl = final_eq - STARTING_CAP
    sharpe = (eq_df["ret"].mean() / eq_df["ret"].std() * np.sqrt(252)
              if eq_df["ret"].std() > 0 else 0.0)
    roll_max = eq_df["equity"].expanding().max()
    drawdown = (eq_df["equity"] - roll_max) / roll_max
    max_dd   = drawdown.min()
    n_trades   = len(all_closed)
    win_trades = sum(1 for p in all_closed if p.realized_pnl > 0)
    win_rate   = win_trades / n_trades if n_trades > 0 else 0
    all_exits = []
    for pos in all_closed:
        all_exits.extend(pos.exit_log)
    exits_df = pd.DataFrame(all_exits) if all_exits \
               else pd.DataFrame(columns=["stop"])
    stop_counts = exits_df["stop"].value_counts() \
                  if not exits_df.empty else pd.Series()
    bh_list = []
    for ticker in TICKERS:
        try:
            col = price_pivot[ticker].dropna()
            if len(col) > 1:
                bh_list.append((col.iloc[-1]-col.iloc[0])/col.iloc[0])
        except Exception:
            pass
    buy_hold = float(np.mean(bh_list)) if bh_list else 0.0
    print(f"\n  Starting Capital : ${STARTING_CAP:>12,.2f}")
    print(f"  Final Equity     : ${final_eq:>12,.2f}")
    print(f"  Total Return     : {total_ret:>10.2%}")
    print(f"  Total PnL        : ${total_pnl:>12,.2f}")
    print(f"  Buy and Hold     : {buy_hold:>10.2%}")
    print(f"  Sharpe Ratio     : {sharpe:>10.2f}")
    print(f"  Max Drawdown     : {max_dd:>10.2%}")
    print(f"  Total Trades     : {n_trades}")
    print(f"  Win Rate         : {win_rate:.2%}")
    print(f"  Bonus Runners    : {len(runners)}")
    print(f"\n  Stop Breakdown:")
    for st in ["MASTER", "S1", "S2", "S3", "S4"]:
        print(f"    {st}: {stop_counts.get(st, 0)} exits")
    print(f"\n  Per Stock:")
    for ticker in TICKERS:
        sp = [p for p in all_closed if p.ticker == ticker]
        if sp:
            spnl = sum(p.realized_pnl for p in sp)
            swin = sum(1 for p in sp if p.realized_pnl > 0)
            print(f"    {ticker}: {len(sp)} trades  "
                  f"PnL={spnl:,.0f}  "
                  f"WR={swin/len(sp):.0%}")
    checks = 0
    print(f"\n  Quant Assassin:")
    if total_ret > 0:
        print(f"  PASS Positive return    : {total_ret:.2%}")
        checks += 1
    else:
        print(f"  FAIL Negative return    : {total_ret:.2%}")
    if total_ret > buy_hold:
        print(f"  PASS Beats buy and hold : {total_ret:.2%} vs {buy_hold:.2%}")
        checks += 1
    else:
        print(f"  FAIL Trails buy and hold: {total_ret:.2%} vs {buy_hold:.2%}")
    if sharpe >= 1.0:
        print(f"  PASS Sharpe above 1.0   : {sharpe:.2f}")
        checks += 1
    else:
        print(f"  FAIL Sharpe below 1.0   : {sharpe:.2f}")
    if max_dd >= -0.15:
        print(f"  PASS Drawdown under 15% : {max_dd:.2%}")
        checks += 1
    else:
        print(f"  FAIL Drawdown over 15%  : {max_dd:.2%}")
    print(f"\n  VERDICT: {checks}/4")
    return {"eq_df": eq_df, "total_ret": total_ret,
            "total_pnl": total_pnl, "buy_hold": buy_hold,
            "sharpe": sharpe, "max_dd": max_dd,
            "n_trades": n_trades, "win_rate": win_rate,
            "checks": checks, "all_closed": all_closed}

def run_walk_forward(full_df, all_params):
    print(f"\n{'='*55}")
    print(f"  LEVEL 2 - WALK FORWARD")
    print(f"{'='*55}")
    windows = [
        ("2019-01-01","2021-01-01","2021-01-01","2022-01-01","2021 Bull"),
        ("2019-01-01","2022-01-01","2022-01-01","2023-01-01","2022 Bear"),
        ("2019-01-01","2023-01-01","2023-01-01","2024-01-01","2023 Recovery"),
        ("2019-01-01","2024-01-01","2024-01-01","2024-12-31","2024 AI Rally"),
    ]
    wf_results = []
    for ts, te, vs, ve, label in windows:
        train_w = full_df[(full_df.index >= pd.Timestamp(ts)) &
                          (full_df.index <  pd.Timestamp(te))]
        if len(train_w) < 100:
            continue
        model_w   = train_model(train_w)
        threshold = find_threshold(model_w, train_w)
        result    = run_backtest(full_df, model_w, threshold,
                                 all_params, vs, ve, label=label)
        if result is None:
            continue
        passed = result["total_ret"] > 0 and result["checks"] >= 2
        print(f"\n  {'PASS' if passed else 'FAIL'} {label}: "
              f"Return={result['total_ret']:.2%}  "
              f"Sharpe={result['sharpe']:.2f}  "
              f"DD={result['max_dd']:.2%}")
        wf_results.append({"label": label,
                           "ret": result["total_ret"],
                           "sharpe": result["sharpe"],
                           "dd": result["max_dd"],
                           "passed": passed})
    passed_count = sum(1 for w in wf_results if w["passed"])
    print(f"\n  Walk Forward: {passed_count}/{len(wf_results)} passed")
    return wf_results

def run_monte_carlo(all_closed, n=10000):
    print(f"\n{'='*55}")
    print(f"  LEVEL 3 - MONTE CARLO ({n:,} simulations)")
    print(f"{'='*55}")
    if not all_closed or len(all_closed) < 5:
        print("  Not enough trades")
        return None
    real_rets   = np.array([p.realized_pnl for p in all_closed])
    real_total  = real_rets.sum()
    real_sharpe = (real_rets.mean()/real_rets.std()*np.sqrt(252)
                   if real_rets.std() > 0 else 0.0)
    print(f"\n  Real PnL     : {real_total:,.2f}")
    print(f"  Real Sharpe  : {real_sharpe:.3f}")
    print(f"  Trades       : {len(real_rets)}")
    rng = np.random.default_rng(42)
    sim_sharpes = []
    for _ in range(n):
        s = rng.choice(real_rets, size=len(real_rets), replace=True)
        sim_sharpes.append(s.mean()/s.std()*np.sqrt(252)
                           if s.std() > 0 else 0.0)
    sim_sharpes = np.array(sim_sharpes)
    pct    = (sim_sharpes < real_sharpe).mean() * 100
    p_val  = 1 - pct / 100
    passed = pct >= 95
    print(f"\n  Random 50th  : {np.percentile(sim_sharpes,50):.3f}")
    print(f"  Random 95th  : {np.percentile(sim_sharpes,95):.3f}")
    print(f"  Beats random : {pct:.1f}%")
    print(f"  p-value      : {p_val:.3f}")
    print(f"\n  Result: {'PASS' if passed else 'FAIL'} p={p_val:.3f}")
    return {"real_sharpe": real_sharpe, "pct": pct,
            "p_value": p_val, "passed": passed}

if __name__ == "__main__":
    params_path = "experiments/EXP-0006/params.json"
    try:
        with open(params_path, "r") as f:
            all_params = json.load(f)
        print("Loaded params OK")
    except FileNotFoundError:
        print("params.json not found - run optimiser.py first")
        exit()
    print("\nDownloading data...\n")
    full_df   = build_dataset(TICKERS, START_DATE, END_DATE)
    train_df  = full_df[full_df.index < pd.Timestamp(SPLIT_DATE)]
    print("\nTraining model...")
    model     = train_model(train_df)
    threshold = find_threshold(model, train_df)
    print(f"Threshold: {threshold:.3f}")
    bt = run_backtest(full_df, model, threshold,
                      all_params, SPLIT_DATE, END_DATE)
    wf = run_walk_forward(full_df, all_params)
    mc = run_monte_carlo(bt["all_closed"]) \
         if bt and bt["all_closed"] else None
    print(f"\n{'='*55}")
    print(f"  FINAL REPORT")
    print(f"{'='*55}")
    bt_pass = bt and bt["checks"] >= 3
    wf_pass = wf and sum(1 for w in wf if w["passed"]) >= 3
    mc_pass = mc and mc["passed"]
    if bt:
        status = "PASS" if bt_pass else "FAIL"
        print(f"\n  Backtest : {status}")
        print(f"    Return   : {bt['total_ret']:.2%}")
        print(f"    PnL      : {bt['total_pnl']:,.2f}")
        print(f"    Sharpe   : {bt['sharpe']:.2f}")
        print(f"    Drawdown : {bt['max_dd']:.2%}")
    if wf:
        status = "PASS" if wf_pass else "FAIL"
        print(f"\n  Walk Fwd : {status}")
        for w in wf:
            ok = "PASS" if w["passed"] else "FAIL"
            print(f"    {ok} {w['label']}: {w['ret']:.2%}")
    if mc:
        status = "PASS" if mc_pass else "FAIL"
        print(f"\n  Monte Carlo: {status}")
        print(f"    Beats random: {mc['pct']:.1f}%  p={mc['p_value']:.3f}")
    all_ok = bt_pass and wf_pass and mc_pass
    if all_ok:
        print(f"\n  Overall: ALL PASSED - APPROVED FOR PAPER TRADING")
    else:
        print(f"\n  Overall: NOT ALL PASSED - REVIEW AND ITERATE")
    print(f"{'='*55}")
