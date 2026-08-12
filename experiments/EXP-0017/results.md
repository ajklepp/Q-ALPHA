# EXP-0017 Results — Rules ScoreCard (replaces LightGBM)

**Status:** FAIL (4/8 gates)

**Run date:** 2026-08-11  
**Modal run:** https://modal.com/apps/ajklepp/main/ap-iog8aLtdsrZ5qIOF3s1fFd  
**Runtime:** 253s

---

## Hypothesis
A simple 6-rule scorecard based on gap-day candle quality and pre-gap setup will produce lift > 1.0x (beat base rate) and maintain Sharpe > 1.50 on the 300-ticker universe, proving the signal is in identifiable candle structure, not complex ML patterns.

**Null hypothesis:** Rules scorecard does not beat base rate on qualifying trades.

---

## Settings
- Universe          : 300 tickers (same screener as EXP-0016)
- Selector          : **Rules ScoreCard** (6 rules, max 11 pts, min 5 to qualify)
- Entry             : Day 0 Close (MOC)
- Threshold         : 5/11 pts = 0.4545 normalized
- VIX Filter        : SPY vol proxy

### Scorecard Rules
| Rule | Feature | Max Pts |
|------|---------|---------|
| 1 | body_ratio_d0 > 0.60 / 0.40 | 3 / 1 |
| 2 | close_vs_range_d0 > 0.75 / 0.55 | 2 / 1 |
| 3 | volume_ratio_d0 > 4.0 / 2.5 | 2 / 1 |
| 4 | gap_pct_d0 3-8% / 8-12% | 2 / 1 |
| 5 | bbw_percentile_52w < 20 | 1 |
| 6 | rs_vs_spy_20d > 2% | 1 |

---

## Base Rate Analysis
| Metric              | Value  | Gate     | Result |
|---------------------|--------|----------|--------|
| Candidate rows      | 2,761  | —        |        |
| Base rate           | 22.0%  | 18-28%   | **PASS** |
| Validation qualify  | 206/382 (53.9%) | — |        |
| Test qualify        | 612/1054 (58.1%) | — |        |

---

## Scorecard Quality
| Metric                    | Validation | OOS Test | Gate   |
|---------------------------|------------|----------|--------|
| Scorecard precision       | 22.8%      | 22.9%    | >25% FAIL |
| Base rate                 | 22.0%      | 23.1%    |        |
| Lift                      | **1.04x**  | **0.99x**| >1.10x FAIL |
| Monotonicity (7 vs 5 pts) | FAIL       | FAIL     | FAIL   |

### Win Rate by Score Bucket (Validation)
| Min Score | n   | Win Rate |
|-----------|-----|----------|
| >= 3      | 345 | 21.7%    |
| >= 5      | 206 | 22.8%    |
| >= 6      | 140 | 21.4%    |
| >= 7      | 91  | **15.4%** |
| >= 8      | 49  | **14.3%** |

**Critical finding:** Win rate **decreases** at higher scores — monotonicity is **inverted**. Higher scorecard points correlate with **worse** outcomes, not better. The rules capture visible "strong candle" patterns that may already be priced in or mark exhaustion.

Validation lift 1.04x is marginal; OOS test lift **0.99x** — scorecard **does not beat** base rate out of sample.

---

## Performance Metrics (OOS Simulation 2023-2024)
| Metric            | Value     | Gate        | Result |
|-------------------|-----------|-------------|--------|
| Final Equity      | $3,815.94 | —           |        |
| Total Return      | +27.20%   | —           |        |
| Sharpe Ratio      | **1.87**  | > 1.50      | **PASS** |
| Max Drawdown      | -5.52%    | < 10%       | PASS   |
| Win Rate          | 43.69%    | —           |        |
| Total Trades      | **103**   | > 60        | PASS   |

**Best simulation Sharpe in the EXP-0013→17 chain** — but driven by catalyst filter + bracket, not scorecard lift.

---

## Walk-Forward
| Window | Return  | Sharpe | Trades | Result |
|--------|---------|--------|--------|--------|
| 2021   | +32.50% | 1.69   | 58     | PASS   |
| 2022   | -8.61%  | -1.59  | 60     | FAIL   |
| 2023   | +30.71% | 1.77   | 52     | PASS   |
| 2024   | -5.81%  | -0.78  | 76     | FAIL   |

**Walk-forward: 2/4** — regressed from EXP-0016's 3/4. 2022 and 2024 both fail.

---

## VIX Regime Breakdown
| Regime   | Trades | P&L      |
|----------|--------|----------|
| LOW_VOL  | 59     | +$311.32 |
| NORMAL   | 44     | +$504.62 |
| ELEVATED | 0      | $0.00    |
| EXTREME  | 0      | $0.00    |

---

## Experiment Progression
| Metric     | EXP-0012 | EXP-0014 | EXP-0015 | EXP-0016 | EXP-0017 |
|------------|----------|----------|----------|----------|----------|
| Sharpe     | 1.64     | 1.10     | 1.76     | 1.56     | **1.87** |
| Max DD     | -3.17%   | -8.87%   | -4.31%   | -6.10%   | **-5.52%** |
| Trades     | 83       | 85       | 52       | 86       | **103**  |
| Base Rate  | —        | 39.06%   | 25.14%   | 22.0%    | 22.0%    |
| Precision  | —        | 37.29%   | 38.46%   | 16.36%   | 22.8%    |
| Lift       | —        | 1.15x    | 1.40x    | 0.74x    | **1.04x** |
| WF Pass    | —        | 2/4      | 3/4      | 3/4      | **2/4**  |
| Selector   | RF       | LGBM     | LGBM     | LGBM     | **Rules** |

---

## Success Gates Summary
| Gate                    | Result |
|-------------------------|--------|
| base_rate_18_28         | PASS (22.0%) |
| scorecard_precision_25  | FAIL (22.8%) |
| lift_1_10               | FAIL (1.04x val / 0.99x test) |
| sharpe_1_50             | PASS (1.87) |
| max_dd_10               | PASS (-5.52%) |
| wf_3_4                  | FAIL (2/4) |
| trades_60               | PASS (103) |
| score_monotonic         | FAIL (inverted) |

**Gates passed: 4/8**

---

## Quant Assassin Verdict
**FAIL**

### What worked
- **Simulation edge persists:** Sharpe **1.87** (best in chain), +27.2%, 103 trades, max DD -5.5%
- **Base rate gate passes** at 22% on 300-ticker universe
- **Transparent selector** — no calibration/overfitting risk from ML
- **Trade count** exceeds 60 gate comfortably

### What failed
- **Hypothesis rejected:** Lift 1.04x validation, **0.99x OOS** — scorecard does not beat random sampling
- **Monotonicity inverted:** Score >= 7 win rate **15.4%** vs score >= 5 at **22.8%** — rules actively mis-rank
- **Precision 22.8% < 25% gate** — barely above base, not meaningfully selective
- **Walk-forward 2/4** — worse than EXP-0016 (3/4); 2022/2024 bear/chop windows fail
- **Replacing LightGBM did not fix selection** — the problem isn't ML vs rules, it's that post-gap candle "quality" features don't predict forward 2R hits at this base rate

### Key insight (confirms EXP-0016 diagnosis)
Simulation Sharpe 1.5–1.9 is **stable across RF, LightGBM, and Rules** with wildly different lift metrics. The **catalyst filter (gap≥3% + vol≥2x) + bracket mechanics** carry the edge. Neither ML nor rules scorecard reliably improves trade selection on the 300-ticker universe at 22% base rate.

`body_ratio_d0` ranking #1 in LightGBM meant the model *weighted* it — but using it directly in rules does not translate to higher win rate. Strong gap-day bodies may mark **exhaustion**, not continuation.

---

## Next Steps
1. **Do not promote** — selection layer adds no OOS lift
2. Consider **catalyst-only** sim (no scorecard/ML filter) as ablation baseline
3. If continuing selection work: invert or remove rules 1-2 (body/close-range), test MIN_SCORE 6-7 with monotonicity check first
4. Walk-forward 2022/2024 failure suggests regime filter needs real VIX data, not proxy
