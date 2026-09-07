# EXP-0023 — High-math probability edge hunt

**Generated:** 2026-09-07T03:55:56.584946+00:00
**Runtime:** 29.8s (Modal)
**Recommendation:** **FAIL**

No high-math family beat always-long after costs on temporal walk-forward with required breadth. Closest: evt_skip (edge=-0.01). Do not add a side agent or PHP weight from this hunt.

**Ship winners:** `[]`
**Gate winners:** `[]`

## Bakeoff vs always-long (flip costs)

| Strategy | n | Sharpe | Always | Edge | Ret | Ret A | Rate | Beat% | Gate | Ship |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| always_long | 78 | 0.57 | 0.57 | +0.00 | 1.119 | 1.119 | 100% | 0% | no | no |
| evt_skip | 78 | 0.57 | 0.57 | -0.01 | 1.082 | 1.119 | 99% | 36% | no | no |
| vol_high_skip | 78 | 0.35 | 0.57 | -0.23 | 0.554 | 1.119 | 73% | 8% | no | no |
| cusum_long | 78 | 0.30 | 0.57 | -0.27 | 0.503 | 1.119 | 57% | 9% | no | no |
| sma_100 | 78 | 0.29 | 0.57 | -0.28 | 0.534 | 1.119 | 59% | 8% | no | no |
| rs_spy_20 | 78 | 0.28 | 0.57 | -0.30 | 0.448 | 1.119 | 49% | 8% | no | no |
| mom_60 | 78 | 0.27 | 0.57 | -0.30 | 0.499 | 1.119 | 58% | 4% | no | no |
| kalman_up | 78 | 0.27 | 0.57 | -0.31 | 0.431 | 1.119 | 54% | 6% | no | no |
| hmm_lowvol | 78 | 0.25 | 0.57 | -0.33 | 0.431 | 1.119 | 78% | 3% | no | no |
| sma_50 | 78 | 0.25 | 0.57 | -0.33 | 0.414 | 1.119 | 56% | 3% | no | no |
| vol_low_20 | 78 | 0.24 | 0.57 | -0.34 | 0.330 | 1.119 | 50% | 6% | no | no |
| mom_20 | 78 | 0.23 | 0.57 | -0.35 | 0.383 | 1.119 | 55% | 1% | no | no |
| rs_spy_strong | 78 | 0.22 | 0.57 | -0.35 | 0.338 | 1.119 | 34% | 5% | no | no |
| mom_10 | 78 | 0.20 | 0.57 | -0.37 | 0.349 | 1.119 | 54% | 6% | no | no |
| hmm_bull | 78 | 0.18 | 0.57 | -0.39 | 0.234 | 1.119 | 52% | 8% | no | no |
| trend_vol_combo | 78 | 0.08 | 0.57 | -0.50 | 0.139 | 1.119 | 36% | 1% | no | no |
| hmm_bull_vol | 78 | 0.07 | 0.57 | -0.51 | 0.085 | 1.119 | 26% | 4% | no | no |
| bayes_pup | 78 | 0.06 | 0.57 | -0.51 | 0.101 | 1.119 | 11% | 8% | no | no |
| z_mr_neg | 78 | 0.05 | 0.57 | -0.52 | 0.087 | 1.119 | 18% | 8% | no | no |
| z_mr_deep | 78 | 0.04 | 0.57 | -0.54 | 0.043 | 1.119 | 8% | 4% | no | no |
| entropy_trend | 78 | -0.09 | 0.57 | -0.67 | 0.014 | 1.119 | 3% | 5% | no | no |
| cond_cont | 78 | -0.25 | 0.57 | -0.83 | -0.095 | 1.119 | 5% | 4% | no | no |
| cond_bounce | 78 | -0.28 | 0.57 | -0.85 | -0.090 | 1.119 | 5% | 1% | no | no |
| streak_bounce | 78 | -0.33 | 0.57 | -0.90 | -0.252 | 1.119 | 23% | 1% | no | no |
| mi_lag_rule | 78 | -0.41 | 0.57 | -0.98 | -0.387 | 1.119 | 50% | 0% | no | no |
| co_up | 78 | -0.61 | 0.57 | -1.19 | -0.553 | 1.119 | 37% | 0% | no | no |

## Ensemble

`{"members": ["vol_high_skip", "cusum_long", "sma_100"], "mean_sharpe": 0.315, "mean_sharpe_a": 0.575, "mean_ret": 0.5306, "mean_ret_a": 1.1188, "edge": -0.261, "frac_beat_always": 0.013, "note": "soft ensemble = average of member pooled metrics"}`

## Notes

- Long-only. No short entries.
- HMM/Bayes/CUSUM/MI/Kalman/EVT/RS/vol/momentum/MR battery.
- Costs on flips; always-long pays one entry cost per test window.
- Standalone from Peak Hour live code.
