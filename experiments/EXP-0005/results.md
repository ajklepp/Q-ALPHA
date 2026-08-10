# EXP-0005 Results — Full Validation Stack

## Status: FAIL — Critical issues found

## Results Summary

- Net Return    : 62.72%

- Buy & Hold    : 52.22%

- Sharpe Ratio  : 0.51

- Max Drawdown  : -73.82%

- Win Rate      : 52.13%

- Total Trades  : 445

## Walk Forward

- Window 1 2021 Bull     : 135.54% PASS

- Window 2 2022 Bear     :   8.43% PASS

- Window 3 2023 Recovery : 213.72% FAIL (accuracy 47.52%)

- Window 4 2024 AI Rally :  78.25% PASS

## Monte Carlo

- Beats random : 50.3%

- p-value      : 0.497

- Result       : FAIL — not statistically significant

## Root Causes

1. No position sizing — unlimited simultaneous trades

2. Cost drag of 66.75% from 445 trades

3. No stop loss — 73% max drawdown is unacceptable

4. Return calculation sums trades not equity curve

5. Window 3 had 870 trades — clearly overtrading

## Fixes For EXP-0006

1. Real equity curve starting at $100,000

2. Max 5 positions open at once (20% each)

3. Raise signal threshold to 0.60

4. Add 2% stop loss per trade

5. Report dollar P&L not just percentages

