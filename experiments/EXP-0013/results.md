# EXP-0013 Results — Small/Mid-Cap Universe + LightGBM + Option D Label

**Status:** FAIL — under investigation

## Run 1 (2026-08-11) — Initial

| Metric | Value |
|--------|-------|
| Sharpe | 0.99 |
| Max Drawdown | -6.08% |
| Win Rate | 44.44% |
| Total Trades | 99 |
| Base Rate | 8.41% |
| Precision @ 0.60 | 11.37% |
| Lift @ 0.60 | 1.35x |
| Walk Forward | 2/4 PASS |
| Final Equity | nan (bug) |

**Root cause:** 47% universe load failure (hardcoded dead tickers) + missing catalyst filter + NaN equity bug in `run_simulation()`.

**Fix applied:**
- Dynamic Polygon.io universe screener (price $5–$200, vol > 500K, mcap < $10B)
- Catalyst pre-filter: Option D labels only when `gap_pct >= 4%` OR `catalyst_tier >= 1`
- Polygon news API for `catalyst_tier` tagging
- Pool guard + finite final equity fix in simulation loop

**Re-run pending**

---

## Settings
- Starting Capital  : $3,000
- Universe          : Dynamic small/mid-cap (Polygon screener)
- Model             : LightGBM
- Target Variable   : Option D (2R before stop within 5 days, catalyst rows only)
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
**FAIL — under investigation**

Reasoning: Run 1 failed on model quality (8.41% base rate), walk-forward (2/4), and NaN equity bug. Fixes applied; awaiting re-run.
