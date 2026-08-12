# EXP-0014 Results — Catalyst Gap Filter + LightGBM + Option D

**Status:** INVESTIGATE (6/8 gates)

**Run date:** 2026-08-11  
**Modal run:** https://modal.com/apps/ajklepp/main/ap-fgzmJzboP1UhDbfsZgytlr  
**Runtime:** 118s

---

## Hypothesis
When a stock gaps up 3%+ at the open AND shows volume ratio > 2x on that day, a LightGBM model trained on pre-gap technical setup features can predict whether the move sustains (hits 2R before stop in 5 days) with precision > 45% at threshold 0.55, vs a base rate of 25-40%.

**Null hypothesis:** Catalyst-filtered gap days have no predictable edge above random (precision ≈ base rate, lift ≈ 1.0x).

---

## Settings
- Starting Capital  : $3,000
- Universe          : Dynamic Polygon screener (120 tickers, mcap < $10B)
- Model             : LightGBM (14 pre-gap features)
- Catalyst Filter   : gap_pct >= 3% AND volume_ratio_20d >= 2.0 (applied **before** labeling)
- Target            : Option D (2R before 1×ATR stop, days +1 to +5)
- Train             : 2019-01-01 to 2021-12-31 (665 rows)
- Validate          : 2022-01-01 to 2022-12-31 (164 rows)
- Test / Simulate   : 2023-01-01 to 2024-12-31 (464 rows)
- Threshold Bull    : 0.45
- Threshold Bear    : 0.55

---

## Base Rate Analysis
| Metric                    | Value   | Notes                          |
|---------------------------|---------|--------------------------------|
| Total OHLCV rows          | 174,366 | All universe days              |
| Candidate rows            | 1,293   | After gap≥3% + vol≥2x filter   |
| Candidate rate            | 0.74%   | ~1 in 135 days per ticker      |
| Label=1 rate (candidates) | 39.06%  | **Hypothesis confirmed** (EXP-0013 was 8%) |

---

## Model Quality (2022 Validation @ 0.45)
| Metric              | Value   | Gate   | Result |
|---------------------|---------|--------|--------|
| Base Rate           | 32.32%  | 15-40% | PASS   |
| Precision @ 0.45    | 37.29%  | > 40%  | **FAIL** |
| Recall              | 41.51%  | —      |        |
| Lift                | 1.15x   | > 1.3x | **FAIL** |

Model adds modest ranking signal but does not clear precision or lift gates.

---

## Performance Metrics (OOS Simulation 2023-2024)
| Metric            | Value    | Gate        | Result |
|-------------------|----------|-------------|--------|
| Final Equity      | $3,493.57| —           |        |
| Total Return      | +16.45%  | > 0         | PASS   |
| Sharpe Ratio      | 1.10     | > 1.0       | PASS   |
| Max Drawdown      | -8.87%   | < 15%       | PASS   |
| Win Rate          | 38.82%   | —           | Below base rate |
| Total Trades      | 85       | > 40        | PASS   |
| Trades/Day        | ~0.17    | —           | 85 trades / 501 sim days |
| vs EXP-0013 Sharpe| 1.10 vs 0.99 | > 0.99 | PASS   |

---

## Walk-Forward (train expanding → test 1yr sim)
| Window | Return  | Sharpe | Trades | Result |
|--------|---------|--------|--------|--------|
| 2021   | +11.61% | 1.70   | 43     | PASS   |
| 2022   | -7.33%  | -1.30  | 41     | FAIL   |
| 2023   | +21.03% | 1.10   | 42     | PASS   |
| 2024   | +1.80%  | 0.22   | 50     | FAIL   |

**Walk-forward score:** 2/4 PASS (gate: ≥2/4) — PASS

2022 bear-market window is the primary failure mode. 2024 positive but Sharpe near zero.

---

## Feature Importance (top 10)
| Rank | Feature              | Importance |
|------|----------------------|------------|
| 1    | volume_thrust_prior  | 604        |
| 2    | gap_pct              | 560        |
| 3    | obv_slope_10d        | 556        |
| 4    | up_down_vol_ratio    | 508        |
| 5    | base_tightness       | 481        |
| 6    | volume_ratio_20d     | 450        |
| 7    | rsi_14               | 447        |
| 8    | close_vs_range_pct   | 430        |
| 9    | atr_14_pct           | 428        |
| 10   | rs_vs_spy_20d        | 401        |

Volume and gap structure dominate; catalyst filter features (gap_pct, volume_ratio_20d) rank highly as expected.

---

## Success Gates Summary
| Gate              | Result |
|-------------------|--------|
| base_rate_15_40   | PASS   |
| precision_40      | FAIL   |
| lift_1_3          | FAIL   |
| sharpe_1          | PASS   |
| max_dd_15         | PASS   |
| wf_2_4            | PASS   |
| trades_40         | PASS   |
| beats_exp0013     | PASS   |

**Gates passed: 6/8**

---

## Quant Assassin Verdict
**INVESTIGATE**

### What worked
- **Labeling fix validated:** Filtering gap≥3% + vol≥2x **before** labeling raised base rate from EXP-0013's 8% to **39%**. The hypothesis that EXP-0013's low base rate was a labeling/universe problem — not a broken Option D label — is supported.
- **Simulation edge:** OOS Sharpe 1.10, +16.45% return, max DD -8.87%, beats EXP-0013 (0.99 Sharpe).
- **Sample size:** 1,293 candidate rows is thin but usable; 85 OOS trades meets activity gate.

### What failed / skeptic view
- **Model quality gates:** Precision 37.3% and lift 1.15x both miss targets. Null hypothesis not fully rejected — model ranking is weak.
- **Win rate 38.8% < base rate 39%:** Selected trades do not outperform random candidate sampling on hit rate.
- **Regime fragility:** 2022 walk-forward lost -7.3% (Sharpe -1.30). Strategy may be bull-biased; bear threshold 0.55 may be insufficient.
- **2024 decay:** +1.8% return, Sharpe 0.22 — edge may be fading or overfit to 2021/2023 bull windows.
- **Too-good-to-ignore check:** Sharpe 1.10 on 85 trades with weak model metrics suggests simulation/bracket mechanics may carry more edge than the classifier. Treat return numbers with caution until model gates pass.

### Bugs fixed this session
1. Walk-forward pivot used full `prices.index` on filtered slice → fixed to `price_slice.index`
2. Walk-forward used SIM-period-only prices (2023-24) → fixed to `prices_all` for all windows
3. Empty equity curve crash → early return when no sim dates

---

## Next Steps
1. **Threshold sweep** on validation set (0.40–0.65) to find precision/lift tradeoff; current 0.45 may be too loose.
2. **Regime analysis:** Separate 2022 bear-window feature importance vs bull windows; consider higher bear threshold or no-trade filter.
3. **Ablation:** Run simulation with random candidate selection (same filter, no model) to isolate bracket vs classifier edge.
4. **Do not promote to candidates** until precision > 40% and lift > 1.3x on validation, confirmed OOS.
