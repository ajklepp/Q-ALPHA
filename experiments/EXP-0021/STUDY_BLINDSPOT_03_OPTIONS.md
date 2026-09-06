# EXP-0021 — Blind-spot #3: Options / IV (TWS)

**Generated:** 2026-09-06T00:08:44.854683-04:00
**Runtime:** 1375.4s (local TWS)
**Recommendation:** **HOLD**

No TWS IV variant beat v1.2 on the ship gate. Keep options/IV research-only.

## Coverage

`{"symbols": 267, "symbols_ok": 246, "admit_rows": 3353, "opt_labeled_admits": 3123, "opt_coverage": 0.9314047121980316, "source": "TWS OPTION_IMPLIED_VOLATILITY + HISTORICAL_VOLATILITY"}`

## How this maps to Peak Hour

- Polygon options snapshots: not entitled.
- **TWS** supplies daily `OPTION_IMPLIED_VOLATILITY` + `HISTORICAL_VOLATILITY` on the stock.
- Features use **prior session** only (no look-ahead).
- Live has no IV term today; wire only if you approve after bakeoff.

## Strata (IV-labeled admits)

### IV rank (20d)

| Bucket | n | WR | Exp R | med MFE |
|---|---:|---:|---:|---:|
| iv_rank_<0.3 | 1139 | 6.1% | 0.262 | 0.012 |
| iv_rank_0.3_0.7 | 962 | 6.0% | 0.236 | 0.010 |
| iv_rank_>=0.7 | 1021 | 6.9% | 0.262 | 0.012 |

### IV − HV spread

| Bucket | n | WR | Exp R | med MFE |
|---|---:|---:|---:|---:|
| iv_below_hv | 1100 | 6.5% | 0.249 | 0.011 |
| iv_near_hv | 1055 | 5.8% | 0.257 | 0.011 |
| iv_rich_>0.10 | 968 | 6.6% | 0.256 | 0.012 |

### IV 5-session change

| Bucket | n | WR | Exp R | med MFE |
|---|---:|---:|---:|---:|
| iv_falling | 1047 | 6.5% | 0.246 | 0.012 |
| iv_flat | 1223 | 5.4% | 0.255 | 0.011 |
| iv_rising | 850 | 7.3% | 0.262 | 0.012 |

### Raw prior IV

| Bucket | n | WR | Exp R | med MFE |
|---|---:|---:|---:|---:|
| iv_<0.4 | 131 | 1.5% | 0.148 | 0.004 |
| iv_0.4_0.8 | 1897 | 4.2% | 0.247 | 0.011 |
| iv_>=0.8 | 1095 | 10.6% | 0.279 | 0.014 |

## Bakeoff vs v1.2

| Variant | n | WR | Exp R | Capture | OOS Exp | PASS? |
|---|---:|---:|---:|---:|---:|---|
| v12 | 720 | 10.4% | 0.299 | 42.8% | 0.357 | — |
| demote_high_iv_rank | 720 | 10.3% | 0.298 | 45.0% | 0.346 | no |
| demote_iv_rich | 720 | 9.2% | 0.283 | 39.4% | 0.331 | no |
| boost_rising_iv | 720 | 10.7% | 0.296 | 41.7% | 0.369 | no |
| combo_iv | 720 | 9.7% | 0.283 | 41.7% | 0.330 | no |
| skip_iv_rank_ge_0.85 | 679 | 10.2% | 0.304 | 37.8% | 0.342 | no |
| skip_iv_rich_ge_0.20 | 696 | 9.2% | 0.293 | 36.7% | 0.341 | no |
| skip_iv_ge_0.8 | 661 | 5.9% | 0.265 | 22.8% | 0.290 | no |

## Decision needed

- Reply: **ADD soft / ADD hard / HOLD**
- No live rewrite from this run.

## Notes

- Source: TWS local (not Modal — IB Gateway unreachable from cloud).
- Causal IV/HV = prior session close of IB historical series.
- OPTION_VOLUME / OI on underlying rejected by this IB account.
- Baseline = live v1.2 (extreme-gap soft demote included).
