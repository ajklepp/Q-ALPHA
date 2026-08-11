# EXP-0009 Results — Full Universe Polygon Pipeline

## Status: STRONG PARTIAL PASS

## Infrastructure

- Data source  : [Polygon.io](http://Polygon.io) Developer

- Compute      : Modal cloud

- Runtime      : 69 seconds

- Universe     : 161 liquid stocks

## Performance

- Starting Capital : $100,000

- Final Equity     : $136,800

- Total Return     : +36.80%

- Buy and Hold     : +46.92%

- Sharpe Ratio     :   1.74

- Max Drawdown     :  -6.91%

- Total Trades     :  49

- Win Rate         :  51.02%

## Walk Forward — 4/4 ALL PASSED

- 2021 Bull     : +13.24%  Sharpe 1.39

- 2022 Bear     : +13.56%  Sharpe 1.87

- 2023 Recovery : +20.97%  Sharpe 1.26

- 2024 AI Rally : +16.35%  Sharpe 1.27

## Quant Assassin

- Positive return    : PASS

- Beats buy and hold : FAIL (36.80% vs 46.92%)

- Sharpe above 1.0   : PASS

- Drawdown under 15% : PASS

- Monte Carlo        : FAIL (only 49 trades)

## Root Cause

Only 49 trades from 17,741 signals

MAX_POSITIONS=5 too restrictive

Capital locked in positions blocks new trades

## Key Achievement

First experiment to pass Walk Forward 4/4

with Polygon data and full pipeline

Strategy profitable in ALL market regimes

## Next Steps

EXP-0010: Increase MAX_POSITIONS to 10

          Risk-based position sizing

          Target 150+ trades for MC validity

