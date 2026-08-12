# EXP-0013 Results — Small/Mid-Cap Universe + LightGBM + Option D Label

**Status:** PENDING

## Settings
- Starting Capital  : $3,000
- Universe          : Small/mid-cap momentum candidates (~60 stocks)
- Model             : LightGBM
- Target Variable   : Option D (2R before stop within 5 days)
- Train             : 2020-01-01 to 2022-12-31
- Simulate          : 2023-01-01 to 2024-12-31

## Performance
| Metric            | Value |
|-------------------|-------|
| Final Equity      |       |
| Total Return      |       |
| Sharpe Ratio      |       |
| Max Drawdown      |       |
| Win Rate          |       |
| Total Trades      |       |
| Trades/Day        |       |

## Model Quality
| Metric              | Value  | Gate   |
|---------------------|--------|--------|
| Base Rate           |        |        |
| Precision @ 0.60    |        | >= 55% |
| Lift @ 0.60         |        | >= 1.5x|
| EV per trade @ 0.60 |        | > 0    |

## Walk Forward
| Window        | Return | Sharpe | Result |
|---------------|--------|--------|--------|
| 2022          |        |        |        |
| 2023          |        |        |        |
| 2024 H1       |        |        |        |
| 2024 H2       |        |        |        |

## vs EXP-0012 Baseline
| Metric       | EXP-0012 | EXP-0013 | Change |
|--------------|----------|----------|--------|
| Sharpe       | 1.64     |          |        |
| Max DD       | -3.17%   |          |        |
| Win Rate     | 39.76%   |          |        |

## Verdict
**PASS / FAIL / INVESTIGATE**

Reasoning:
