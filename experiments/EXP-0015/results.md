# EXP-0015 Results — Gap-Day Features + VIX Regime Filter

**Status:** INVESTIGATE (6/8 gates)

**Run date:** 2026-08-11  
**Modal run:** https://modal.com/apps/ajklepp/main/ap-GMQHBCVsbhZ4fvVLG6a59u  
**Runtime:** 157s

---

## Hypothesis
Adding 5 gap-day close behavior features and a VIX volatility regime filter will lift model precision above base rate (~39%) and improve walk-forward to 3/4 windows.

**Null hypothesis:** Gap-day features and VIX sizing do not improve precision or walk-forward vs EXP-0014.

---

## Settings
- Starting Capital  : $3,000
- Universe          : Dynamic Polygon screener (120 tickers)
- Model             : LightGBM (19 features: 14 pre-gap + 5 gap-day)
- Catalyst Filter   : gap_pct >= 3% AND volume_ratio_20d >= 2.0
- Entry             : Next-day Open (simulation + Option D labels)
- Target            : Option D (2R before 1×ATR stop, 5 days from entry)
- VIX Filter        : I:VIX sizing (0.5× ELEVATED, skip EXTREME) — **SPY proxy used (I:VIX unavailable)**
- Train             : 2019-01-01 to 2021-12-31 (665 rows)
- Validate          : 2022-01-01 to 2022-12-31 (164 rows)
- Test / Simulate   : 2023-01-01 to 2024-12-31 (464 rows)

---

## Base Rate Analysis
| Metric                    | Value   | Notes                                      |
|---------------------------|---------|--------------------------------------------|
| Total OHLCV rows          | 174,366 | Same universe as EXP-0014                  |
| Candidate rows            | 1,293   | Same filter rate (0.74%)                   |
| Label=1 rate (candidates) | **25.14%** | ↓ from EXP-0014's 39% (next-day entry label) |
| Validation base rate      | 27.44%  | Model evaluated here                       |

Base rate gate **FAIL** (25% < 35% floor). Next-day-open entry materially lowers Option D hit rate vs gap-day-open labeling in EXP-0014.

---

## Model Quality (2022 Validation @ 0.45)
| Metric              | Value   | Gate   | Result |
|---------------------|---------|--------|--------|
| Base Rate           | 27.44%  | 35-45% | FAIL (split base rate) |
| Precision @ 0.45    | 38.46%  | > 42%  | **FAIL** (absolute gate) |
| Recall              | 22.22%  | —      |        |
| Lift                | 1.40x   | > 1.15x| **PASS** |

**Key finding:** Precision (38.5%) **beats validation base rate (27.4%)** — model now adds ranking signal vs random, unlike EXP-0014 (37% precision vs 32% base). Absolute 42% gate still missed.

---

## Performance Metrics (OOS Simulation 2023-2024)
| Metric            | Value     | Gate        | Result |
|-------------------|-----------|-------------|--------|
| Final Equity      | $4,076.50 | —           |        |
| Total Return      | +35.88%   | > 0         | PASS   |
| Sharpe Ratio      | **1.76**  | > 1.10      | PASS   |
| Max Drawdown      | -4.31%    | < 12%       | PASS   |
| Win Rate          | 53.85%    | —           |        |
| Total Trades      | 52        | > 40        | PASS   |
| Trades/Day        | ~0.10     | —           | Fewer than EXP-0014 (85) |
| vs EXP-0014 Sharpe| 1.76 vs 1.10 | > 1.10 | PASS   |

---

## Walk-Forward
| Window | Return  | Sharpe | Trades | Result |
|--------|---------|--------|--------|--------|
| 2021   | -5.28%  | -1.01  | 22     | FAIL   |
| 2022   | +1.84%  | 0.50   | 18     | PASS   |
| 2023   | +24.00% | 1.29   | 29     | PASS   |
| 2024   | +9.17%  | 1.24   | 30     | PASS   |

**Walk-forward: 3/4 PASS** — primary hypothesis target **achieved** (EXP-0014 was 2/4).

Notable: 2022 now passes (+1.8%) where EXP-0014 failed (-7.3%). 2021 now fails where EXP-0014 passed.

---

## Feature Importance (all 19)
| Rank | Feature              | Importance | Gap-Day? |
|------|----------------------|------------|----------|
| 1    | up_down_vol_ratio    | 502        |          |
| 2    | volume_thrust_prior  | 475        |          |
| 3    | close_vs_range_pct   | 472        |          |
| 4    | price_location_20d   | 463        |          |
| 5    | volume_ratio_20d     | 452        |          |
| 6    | **body_ratio_d0**    | 419        | **YES**  |
| 7    | base_tightness       | 407        |          |
| 8    | obv_slope_10d        | 404        |          |
| 9    | gap_pct              | 399        |          |
| 10   | bbw_percentile_52w   | 376        |          |
| 11   | rsi_14               | 364        |          |
| 12   | **upper_wick_ratio_d0** | 339     | **YES**  |
| 13   | rs_vs_spy_20d        | 334        |          |
| 14   | atr_14_pct           | 291        |          |
| 15   | **close_vs_range_d0**| 229        | **YES**  |
| 16   | dist_from_20ma       | 210        |          |
| 17   | **gap_pct_d0**       | 127        | **YES**  |
| 18   | **volume_ratio_d0**  | 74         | **YES**  |
| 19   | ttm_squeeze_active   | 0          |          |

**Gap-day features in top 5?** No — `body_ratio_d0` ranked #6 (closest). Hypothesis **partially supported**: gap-day features contribute (3 in top 12) but did not dominate top 5.

---

## VIX Regime Breakdown
| Regime   | Trades | P&L      | Notes                          |
|----------|--------|----------|--------------------------------|
| LOW_VOL  | 38     | +$763.96 | SPY vol proxy — most trades    |
| NORMAL   | 14     | +$312.54 |                                |
| ELEVATED | 0      | $0.00    | Proxy rarely hits 25-35 band   |
| EXTREME  | 0      | $0.00    | No entries skipped              |

**Caveat:** Polygon `I:VIX` pull failed; SPY 20d vol × 16 proxy used. VIX filter was **not truly tested** — all trades landed in LOW/NORMAL proxy buckets.

---

## vs Prior Experiments
| Metric       | EXP-0012 | EXP-0013 | EXP-0014 | EXP-0015 |
|--------------|----------|----------|----------|----------|
| Sharpe       | 1.64     | 0.99     | 1.10     | **1.76** |
| Max DD       | -3.17%   | -6.08%   | -8.87%   | **-4.31%** |
| Base Rate    | —        | 8.41%    | 39.06%   | 25.14%   |
| Precision    | —        | 11%      | 37.29%   | 38.46%   |
| Lift         | —        | 1.35x    | 1.15x    | **1.40x** |
| Walk-Fwd     | —        | 2/4      | 2/4      | **3/4**  |
| OOS Return   | —        | +23.5%   | +16.5%   | **+35.9%** |
| OOS Trades   | —        | 28       | 85       | 52       |

---

## Success Gates Summary
| Gate              | Result |
|-------------------|--------|
| base_rate_35_45   | FAIL (25.14%) |
| precision_42      | FAIL (38.46%) |
| lift_1_15         | PASS (1.40x) |
| sharpe_1_10       | PASS (1.76) |
| max_dd_12         | PASS (-4.31%) |
| wf_3_4            | PASS (3/4) |
| trades_40         | PASS (52) |
| beats_exp0014     | PASS |

**Gates passed: 6/8**

---

## Quant Assassin Verdict
**INVESTIGATE**

### What worked
- **Walk-forward 3/4** — primary EXP-0015 target hit; 2022 recovery vs EXP-0014 is meaningful.
- **Model beats base rate on validation** (38.5% vs 27.4%) — first experiment where classifier precision exceeds base rate.
- **Lift 1.40x** clears the 1.15x gate.
- **Simulation strong:** Sharpe 1.76, +35.9%, max DD -4.3% — best sim metrics in the EXP-0013/14/15 chain.
- **Gap-day features matter:** `body_ratio_d0` #6, `upper_wick_ratio_d0` #12 — not top 5 but clearly used.

### What failed / skeptic view
- **Base rate dropped to 25%** when labels use next-day open entry — the 39% base rate from EXP-0014 is not portable across entry timing. Gate fail is structural, not noise.
- **Precision 38.5% < 42% gate** — improvement vs base rate but misses absolute threshold.
- **VIX filter untested** — I:VIX unavailable; SPY proxy never triggered ELEVATED/EXTREME sizing. Cannot credit VIX filter for WF improvement.
- **2021 walk-forward fails** — opposite regime pattern vs EXP-0014; strategy still regime-sensitive.
- **Sharpe 1.76 skepticism:** Only 52 trades, next-day entry changes fill quality, win rate 54% with 38% model precision suggests bracket mechanics may dominate. Compare to random-entry ablation before promoting.
- **Too-good check:** +35.9% on 52 trades over 2 years with weak model precision gate — investigate whether next-day-open entry avoids gap-and-crap losses without model skill.

### Null hypothesis assessment
Partially rejected on walk-forward and lift; not rejected on absolute precision gate or base rate stability.

---

## Next Steps
1. **Fix I:VIX data pull** — verify Polygon ticker format / plan tier; re-run to actually test VIX sizing.
2. **Reconcile base rate** — document that next-day entry lowers Option D base rate ~25% vs gap-day ~39%; update gate expectations or normalize entry for comparison.
3. **Threshold sweep** — find precision/lift tradeoff at 0.50–0.65 on validation.
4. **Ablation:** sim with gap-day features only vs pre-gap only vs random selection (same filter).
5. **Do not promote to candidates** until precision > 42% and real VIX filter validated.
