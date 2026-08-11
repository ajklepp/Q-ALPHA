# EXP-0008 Results — Full Pipeline Backtest

## Status: PARTIAL PASS — 3/4 checks, MC fails

## Performance

- Starting Capital : $100,000

- Final Equity     : $134,357

- Total Return     : +34.36%

- Buy and Hold     : +51.14%

- Sharpe Ratio     :   1.80  PASS

- Max Drawdown     : -10.62% PASS

- Total Trades     :  38

- Win Rate         :  47.37%

- Runtime          :  72 seconds

## Key Wins

- Sharpe 1.80 — best result yet

- Profile filtering working perfectly

- Zero HIGH_GROWTH_TECH or HIGH_VOLATILITY trades

- All top 10 stocks 100% win rate

- 72 second runtime on Modal

## Failures

- Does not beat buy and hold (34% vs 51%)

- Monte Carlo fails — only 38 trades (need 100+)

- Too few trades from 8,007 signals

## Root Cause

MAX_POSITIONS=5, MAX_TRADES_DAY=3 too restrictive

Combined with profile penalty leaves too few trades

## Fixes For EXP-0009

1. MAX_POSITIONS=8, MAX_TRADES_DAY=5

2. Lower threshold to 0.52

3. Add walk forward across all 4 regimes

4. Target 100+ trades for Monte Carlo validity

