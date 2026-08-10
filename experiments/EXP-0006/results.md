# EXP-0006 Results — Dynamic Bracket Orders

## Status: PARTIAL PASS

## Key Results

- Starting Capital  : $100,000

- Final Equity      : $113,855

- Total Return      : 13.85%

- Sharpe Ratio      : 0.84

- Max Drawdown      : -2.34%

- Win Rate          : 58.33%

- Total Trades      : 12

## Walk Forward — 4/4 PASS

- 2021 Bull     : +8.04%   Sharpe 1.42  DD -1.06%

- 2022 Bear     : +12.43%  Sharpe 1.36  DD -3.95%

- 2023 Recovery : +3.77%   Sharpe 0.83  DD -2.34%

- 2024 AI Rally : +8.14%   Sharpe 1.81  DD -0.19%

## Failures

- Does not beat buy and hold (8-13% vs 27-52%)

- Monte Carlo fails (only 12 trades - sample size issue)

- Sharpe 0.84 (just below 1.0 target)

## Key Achievements

- Profitable in all 4 market regimes including bear market

- Maximum drawdown -3.95% (exceptional capital protection)

- Bracket stop system working correctly

- Optimised parameters confirmed by math

## Root Cause Of Failures

Too few trades (12) due to small universe (5 stocks)

Not enough statistical power for Monte Carlo

## Next Steps

- Path A: Expand to 20 stocks

- Path B: Add market regime filter

- Run on Modal when infrastructure is ready

