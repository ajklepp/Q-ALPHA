# EXP-0016 Results — Day 0 Close Entry + 300-Ticker Universe

**Status:** FAIL (4/8 gates)

**Run date:** 2026-08-11  
**Modal run:** https://modal.com/apps/ajklepp/main/ap-LxQ7nfMh7brUKLV6JSbIBM  
**Runtime:** 145s

---

## Hypothesis
Returning to Day 0 Close entry while keeping all 19 features and VIX filter will restore base rate to 35-42% while maintaining lift > 1.3x and Sharpe > 1.50. Expanding universe to 300 tickers will produce 80+ trades/year.

**Null hypothesis:** MOC entry + expanded universe does not restore base rate or model lift vs EXP-0015.

---

## Settings
- Starting Capital  : $3,000
- Universe          : **300 tickers** (screener tightened vol to 2M to cap pool)
- Entry             : Day 0 Close (MOC)
- Option D label    : Close[day_0], ATR[day_0], forward days +1 to +5
- Features          : 19 (F01-F19)
- VIX Filter        : SPY vol proxy (I:VIX unavailable)
- Train / Val / Test: 1,324 / 381 / 1,053 candidate rows

---

## Base Rate Analysis
| Metric                    | Value   | Gate     | Result |
|---------------------------|---------|----------|--------|
| Candidate rows            | 2,758   | —        | 2.1× EXP-0015 |
| Base rate (label=1)       | **22.0%** | 35-45% | **FAIL** |
| Positive samples          | 607     | —        |        |
| Validation base rate      | 22.05%  | —        |        |

**Critical finding:** Base rate did **not** restore to EXP-0014's 39%. Likely causes:
1. EXP-0014 labels used **Open[day_0]** entry (not Close) with **prior-day ATR** — inconsistent with sim but inflated base rate
2. EXP-0016 uses **Close[day_0] + ATR[day_0]** — methodologically cleaner but harder target
3. Expanded universe (300 names, higher vol filter) shifts candidate mix toward lower follow-through rates

---

## Model Quality (2022 Validation @ 0.45)
| Metric              | Value   | Gate   | Result |
|---------------------|---------|--------|--------|
| Precision @ 0.45    | 16.36%  | > 42%  | **FAIL** |
| Recall              | 10.71%  | —      |        |
| Lift                | 0.74x   | > 1.3x | **FAIL** |
| Test precision      | 24.70%  | —      | Below base |
| Test lift           | 1.07x   | —      | Marginal |

Model **underperforms** validation base rate at threshold 0.45. EXP-0015's lift signal (1.40x) did **not** survive universe expansion + MOC label alignment.

**Gap-day hypothesis:** `body_ratio_d0` ranked **#1** — gap-day features confirmed useful for ranking.

---

## Performance Metrics (OOS Simulation 2023-2024)
| Metric            | Value     | Gate        | Result |
|-------------------|-----------|-------------|--------|
| Final Equity      | $3,992.55 | —           |        |
| Total Return      | +33.08%   | > 0         | PASS   |
| Sharpe Ratio      | **1.56**  | > 1.50      | PASS   |
| Max Drawdown      | -6.10%    | < 10%       | PASS   |
| Win Rate          | 44.19%    | —           |        |
| Total Trades      | **86**    | > 80        | PASS   |
| Trades/Day        | ~0.17     | —           | Goal met |

Simulation metrics improved vs EXP-0015 on trade count and maintained strong Sharpe — but model gates failed badly.

---

## Walk-Forward
| Window | Return  | Sharpe | Trades | Result |
|--------|---------|--------|--------|--------|
| 2021   | +23.51% | 1.10   | 31     | PASS   |
| 2022   | -15.71% | -3.44  | 36     | FAIL   |
| 2023   | +8.31%  | 0.60   | 44     | PASS   |
| 2024   | +4.59%  | 0.59   | 47     | PASS   |

**Walk-forward: 3/4 PASS** — maintained from EXP-0015. 2022 bear window remains the failure mode (-15.7%).

---

## Feature Importance (top 10)
| Rank | Feature              | Importance | Gap-Day? |
|------|----------------------|------------|----------|
| 1    | **body_ratio_d0**    | 682        | YES      |
| 2    | bbw_percentile_52w   | 648        |          |
| 3    | up_down_vol_ratio    | 567        |          |
| 4    | base_tightness       | 541        |          |
| 5    | atr_14_pct           | 509        |          |
| 6    | volume_ratio_20d     | 498        |          |
| 7    | close_vs_range_pct   | 494        |          |
| 8    | volume_thrust_prior  | 485        |          |
| 9    | dist_from_20ma       | 473        |          |
| 10   | gap_pct              | 468        |          |

**Gap-day in top 5:** YES — `body_ratio_d0` is #1. Hypothesis confirmed for feature value.

---

## VIX Regime Breakdown
| Regime   | Trades | P&L      |
|----------|--------|----------|
| LOW_VOL  | 56     | +$810.44 |
| NORMAL   | 30     | +$182.10 |
| ELEVATED | 0      | $0.00    |
| EXTREME  | 0      | $0.00    |

SPY vol proxy only — VIX filter still not truly exercised.

---

## Experiment Progression
| Metric        | EXP-0012 | EXP-0013 | EXP-0014 | EXP-0015 | EXP-0016 |
|---------------|----------|----------|----------|----------|----------|
| Sharpe        | 1.64     | 0.99     | 1.10     | 1.76     | **1.56** |
| Max DD        | -3.17%   | -6.08%   | -8.87%   | -4.31%   | **-6.10%** |
| Win Rate      | 39.76%   | 44.44%   | 42.86%   | 38.46%   | **44.19%** |
| Trades        | 83       | 99       | 85       | 52       | **86**   |
| Base Rate     | —        | 8.41%    | 39.06%   | 25.14%   | **22.01%** |
| Precision     | —        | 11.37%   | 37.29%   | 38.46%   | **16.36%** |
| Lift          | —        | 1.35x    | 1.15x    | 1.40x    | **0.74x** |
| WF Pass       | —        | 2/4      | 2/4      | 3/4      | **3/4**  |

---

## Success Gates Summary
| Gate              | Result |
|-------------------|--------|
| base_rate_35_45   | FAIL (22.0%) |
| precision_42      | FAIL (16.4%) |
| lift_1_3          | FAIL (0.74x) |
| sharpe_1_50       | PASS (1.56) |
| max_dd_10         | PASS (-6.10%) |
| wf_3_4            | PASS (3/4) |
| trades_80         | PASS (86) |
| beats_exp0015     | FAIL (1.56 vs 1.76) |

**Gates passed: 4/8**

---

## Quant Assassin Verdict
**FAIL**

### What worked
- **Trade count goal met:** 86 trades (vs 52 in EXP-0015) — statistical sample improved
- **Sharpe 1.56** clears 1.50 gate; simulation +33% return
- **Walk-forward 3/4** maintained
- **`body_ratio_d0` #1 feature** — gap-day close behavior is the strongest predictor (validates EXP-0015 diagnosis)
- **Win rate 44%** above 22% base rate in simulation — bracket/sim edge persists despite weak classifier

### What failed
- **Base rate 22%** — hypothesis rejected; MOC entry + day-0 ATR did not restore 35-45%
- **Model lift collapsed to 0.74x** on validation — expanded universe diluted signal; classifier worse than random at 0.45 threshold
- **Precision 16%** — catastrophic vs 42% gate
- **2022 walk-forward -15.7%** — bear regime still breaks strategy
- **EXP-0014's 39% base rate was partly an artifact** of Open-entry labels with prior-day ATR, not Close MOC

### Skeptic view
Strong simulation metrics (Sharpe 1.56, +33%) with lift 0.74x means **the model is not driving edge** — catalyst filter + bracket mechanics are. Do not promote. Next experiment should either:
1. Reconcile label definition (Open vs Close entry, ATR timing) with a controlled ablation, or
2. Keep 300-ticker universe but re-tune threshold/features on validation before any sim claims

---

## Next Steps
1. Run label ablation: Close+ATR_d0 vs Open+ATR_shift vs Close+ATR_shift on same 300-ticker universe
2. Threshold sweep 0.35–0.65 on validation (current 0.45 clearly too low for this base rate)
3. Fix I:VIX Polygon pull
4. Do not merge to candidates — model gates failed decisively
