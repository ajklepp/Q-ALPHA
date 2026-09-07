# EXP-0024 — Power-law + fractal/Hurst study

**Generated:** 2026-09-07T04:08:49.074893+00:00
**Runtime:** 19.6s (Modal)
**Recommendation:** **FAIL_NEW** (no new power/fractal edge)

Power-law and fractal trading rules did NOT clear ship gates. Diagnostics confirm fat tails (Hill α≈2.2–2.8) and near-random Hurst (H≈0.5). EXP-0023 HMM risk overlay remains the only PROMISING rule in this bakeoff.

## Diagnostics (what the math says about the data)

`{
  "spy_hill_abs": 2.443855370238395,
  "spy_hill_left": 2.2240476258230726,
  "spy_hill_right": 2.666433944935297,
  "spy_hurst_rs": 0.5551573353933267,
  "spy_hurst_dfa": 0.4819649635814947,
  "median_stock_hill_abs": 2.7935003377051766,
  "median_stock_hurst_dfa": 0.5104457525342821,
  "frac_stocks_H_gt_055": 0.24358974358974358,
  "frac_stocks_H_lt_045": 0.0641025641025641,
  "frac_stocks_alpha_lt_3": 0.7051282051282052
}`

Interpretation: Hill α ≪ 4 ⇒ fat tails (variance may be infinite-ish for α≤2). Hurst H>0.5 ⇒ persistence; H<0.5 ⇒ mean-reversion tendency.

**HMM baseline (EXP-0023):** `{"sharpe": 1.225, "sharpe_ew": 1.008, "edge_sharpe": 0.217, "ret": 0.9399, "ret_ew": 1.1328, "mdd": -0.1749, "mdd_ew": -0.2985, "invested_frac": 0.703, "pass_frac_sharpe": 0.364, "risk_pass": true, "growth_pass": false, "ship_pass": true}`
**Beats HMM:** `[]`

## Bakeoff vs equal-weight

| Strategy | Sharpe | Edge | Ret | MDD | Inv% | Risk | Growth | Ship |
|---|---:|---:|---:|---:|---:|---|---|---|
| ew_hmm_bull | 1.23 | +0.22 | 0.940 | -17.5% | 70% | YES | no | YES |
| cs_low_alpha | 1.01 | +0.00 | 1.275 | -36.9% | 100% | no | no | no |
| ew | 1.01 | +0.00 | 1.133 | -29.8% | 100% | no | no | no |
| hurst_switch_mom_mr | 1.01 | +0.00 | 1.133 | -29.8% | 100% | no | no | no |
| cs_high_alpha | 0.82 | -0.18 | 1.054 | -30.6% | 100% | no | no | no |
| ew_fat_tail_skip | 0.64 | -0.37 | 0.492 | -23.3% | 55% | no | no | no |
| ew_thin_tail_only | 0.64 | -0.37 | 0.492 | -23.3% | 55% | no | no | no |
| hmm_and_thin_tail | 0.60 | -0.40 | 0.381 | -17.7% | 43% | no | no | no |
| ew_hurst_persist | 0.00 | -1.01 | 0.000 | 0.0% | 0% | no | no | no |
| ew_hurst_antipersist | 0.00 | -1.01 | 0.000 | 0.0% | 0% | no | no | no |
| cs_high_H | 0.00 | -1.01 | 0.000 | 0.0% | 0% | no | no | no |
| hmm_and_hurst | 0.00 | -1.01 | 0.000 | 0.0% | 0% | no | no | no |

## Notes

- Power law: Hill α on |returns| / left tail (α≈3–4 typical; lower = fatter).
- Fractal: DFA + R/S Hurst (H>0.5 persist, H<0.5 antipersist).
- Long-only. Standalone. No Peak Hour edits.
