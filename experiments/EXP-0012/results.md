# EXP-0012 Results — $3K Dynamic Sizing + Regime Filter

## Status: STRONG PARTIAL PASS — Ready for paper trading

## Settings

- Starting Capital    : $3,000

- Position Size       : 10% of pool (dynamic)

- Max Positions Bull  : 10

- Max Positions Bear  : 3

- Threshold Bull      : 0.532

- Threshold Bear      : 0.65

- Regime Filter       : SPY vs 50-day SMA

## Performance

- Final Equity        : $3,467.68

- Total Return        : +15.59%

- Sharpe Ratio        :   1.64

- Max Drawdown        :  -3.17%

- Total Trades        :  83

- Win Rate            :  39.76%

- Avg Trades/Day      :  0.17

## Regime Performance

- BULL days (403)     : +14.97%  Sharpe 1.83

- BEAR days (94)      : +0.54%   Sharpe 0.50

## Walk Forward

- 2021 Bull     : +10.28%  Sharpe 1.96  PASS

- 2022 Bear     : +3.44%   Sharpe 0.77  PASS

- 2023 Recovery : +16.69%  Sharpe 1.19  PASS

- 2024 AI Rally : -6.18%   Sharpe -1.25 FAIL

## Key Achievements

- Max drawdown only -3.17% over 2 years

- 2022 Bear market: +3.44% (was -7.41% without regime filter)

- Regime filter works perfectly

- Bull Sharpe 1.83 excellent

## Remaining Issues

- 2024 AI Rally fails (threshold too permissive)

- Monte Carlo 83 trades (need 100+)

## Decision

Proceed to paper trading with current settings.

Monitor 2024-style conditions in real trading.

