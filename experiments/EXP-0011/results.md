# EXP-0011 Results — $3K Dynamic Sizing

## Status: STRONG PARTIAL PASS

## Settings

- Starting Capital : $3,000

- Position Size    : 10% of pool (dynamic)

- Max Positions    : 10

- Max Trades/Day   : 3

## Performance

- Final Equity     : $3,467.81

- Total Return     : +15.59%

- Sharpe Ratio     :   1.48

- Max Drawdown     :  -7.10%

- Total Trades     :  92

- Win Rate         :  41.30%

- Avg Trades/Day   :  0.19

## Walk Forward

- 2021 Bull     : +7.16%   Sharpe 1.24  PASS

- 2022 Bear     : -7.41%   Sharpe -0.97 FAIL

- 2023 Recovery : +8.92%   Sharpe 0.73  PASS

- 2024 AI Rally : +6.17%   Sharpe 0.71  PASS

- Result        : 3/4

## Key Improvements From EXP-0010

- Trades increased 4x (22 to 92)

- Walk Forward improved (1/4 to 3/4)

- Drawdown improved (-15% to -7%)

## Remaining Issue

- 2022 Bear market still fails

- Monte Carlo 92 trades (need 100+)

## Fix For EXP-0012

- Add market regime filter (SPY vs 50-day MA)

- Bull regime: MAX_POSITIONS=10, threshold=0.532

- Bear regime: MAX_POSITIONS=3, threshold=0.65

- Expected: 4/4 walk forward, MC passes

