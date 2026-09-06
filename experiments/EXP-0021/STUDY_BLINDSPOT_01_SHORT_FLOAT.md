# EXP-0021 — Blind-spot #1: Short interest + float

**Generated:** 2026-09-05T23:22:29.338499-04:00
**Runtime:** 22.1s (Modal)
**Recommendation:** **HOLD (research)**

Bakeoff auto-flagged `skip_si_ge_35`, but it is a **no-op** (identical metrics to v1.1 — almost no admits had SI ≥ 35%). No soft/hard variant truly beat v1.1. Keep SI/float for research/thesis unless you override.

## Coverage

`{"symbols": 267, "admit_rows": 3353, "si_coverage": 1.0, "sv_coverage": 1.0, "si_pct_nonnull": 1.0, "fetch_errors": 0}`

## How this maps to Peak Hour

- Live ranker today ignores SI/float (tags exist in quality gate but corpus bakeoff was empty).
- Literature: high SI can fuel squeeze continuation; rising borrow/short volume often pressures longs;
  ultra-low float raises variance (size down, don't auto-boost).
- Causal SI uses biweekly FINRA settle + 10d publish lag; SV uses prior day ratio.

## Strata (admits)

### Short interest % of shares

| Bucket | n | WR | Exp R | med MFE |
|---|---:|---:|---:|---:|
| si_<5 | 896 | 4.2% | 0.232 | 0.010 |
| si_5_10 | 1107 | 6.1% | 0.260 | 0.012 |
| si_10_20 | 1114 | 7.5% | 0.256 | 0.012 |
| si_20_35 | 234 | 12.0% | 0.296 | 0.017 |

### Days to cover

| Bucket | n | WR | Exp R | med MFE |
|---|---:|---:|---:|---:|
| dtc_<1.5 | 387 | 10.1% | 0.226 | 0.013 |
| dtc_1.5_3 | 720 | 6.4% | 0.252 | 0.010 |
| dtc_3_5 | 992 | 4.8% | 0.244 | 0.012 |
| dtc_>=5 | 1254 | 6.6% | 0.271 | 0.012 |

### SI change vs prior report

| Bucket | n | WR | Exp R | med MFE |
|---|---:|---:|---:|---:|
| si_falling | 1058 | 5.3% | 0.244 | 0.011 |
| si_flat | 1229 | 6.4% | 0.272 | 0.012 |
| si_rising | 1066 | 7.6% | 0.242 | 0.012 |

### Shares outstanding (proxy float)

| Bucket | n | WR | Exp R | med MFE |
|---|---:|---:|---:|---:|
| float_<15M | 179 | 10.6% | 0.298 | 0.016 |
| float_15_50M | 590 | 8.5% | 0.246 | 0.011 |
| float_50_200M | 1708 | 5.4% | 0.248 | 0.012 |
| float_>=200M | 876 | 6.2% | 0.260 | 0.012 |

### Prior-day short volume ratio

| Bucket | n | WR | Exp R | med MFE |
|---|---:|---:|---:|---:|
| svr_<30 | 242 | 6.2% | 0.257 | 0.012 |
| svr_30_45 | 921 | 6.4% | 0.230 | 0.011 |
| svr_>=45 | 2190 | 6.5% | 0.263 | 0.012 |

## Bakeoff vs v1.1

| Variant | n | WR | Exp R | Capture | OOS Exp | PASS? |
|---|---:|---:|---:|---:|---:|---|
| v11 | 720 | 10.4% | 0.297 | 42.8% | 0.357 | — |
| boost_high_si | 720 | 11.2% | 0.296 | 45.6% | 0.361 | no |
| boost_rising_si | 720 | 10.1% | 0.281 | 44.4% | 0.342 | no |
| demote_high_svr | 720 | 10.6% | 0.285 | 45.0% | 0.340 | no |
| demote_low_float | 720 | 10.4% | 0.292 | 42.8% | 0.344 | no |
| combo_si_svr | 720 | 11.0% | 0.285 | 46.1% | 0.332 | no |
| skip_si_ge_35 | 720 | 10.4% | 0.297 | 42.8% | 0.357 | YES |
| skip_dtc_ge_5 | 671 | 9.7% | 0.288 | 33.3% | 0.346 | no |
| skip_svr_ge_45 | 583 | 7.4% | 0.238 | 26.1% | 0.289 | no |
| skip_float_lt_15m | 715 | 9.8% | 0.290 | 40.6% | 0.339 | no |
| prefer_si_10_20_only | 575 | 9.2% | 0.250 | 26.7% | 0.285 | no |

## Decision needed

- **HOLD research (user 2026-09-05)** — held off; not wired into live score.
- Soft boosts failed. Strata hint: SI 20–35% and float &lt;15M look *better* on raw admits (not slot bakeoff).
- Next: blind-spot #2 (gap + overnight).
- No live rewrite from this run.

## Notes

- Shares outstanding from ticker-details snapshot (not perfect PIT float).
- SI causal lag: settlement_date + 10d <= signal_date.
- Short volume uses prior calendar date < signal_date.
