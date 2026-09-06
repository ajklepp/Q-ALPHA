# EXP-0021 — Blind-spot #5: Event distance / earnings

**Generated:** 2026-09-06T00:21:08.706030-04:00
**Runtime:** 14.7s (Modal)
**Recommendation:** **ADD soft (user 2026-09-06)**

Live: `continuation_score` **v1.4** soft-boosts `catalyst_type=earnings` by +6. Filing-window hard filters **not** shipped.

## Coverage

`{"symbols": 267, "filings_ok": 229, "fetch_errors": 0, "admit_rows": 3353, "event_labeled": 2922, "event_coverage": 0.8714583954667462, "pre_earn_rate": 0.018789144050104383, "post_earn_rate": 0.014017297942141366, "catalyst_earn_n": 54, "source": "Polygon quarterly filing_date proxy (no Benzinga calendar)"}`

## How this maps to Peak Hour

- Pre-event anticipation vs post-print digest/PEAD timing.
- No live Benzinga calendar — filing-date proxy only.
- Live v1.3 has no event-distance term today.

## Strata

### Days since last filing (proxy earn)

| Bucket | n | WR | Exp R | med MFE |
|---|---:|---:|---:|---:|
| since_0_3 | 47 | 6.4% | 0.325 | 0.021 |
| since_4_10 | 222 | 9.5% | 0.329 | 0.013 |
| since_11_30 | 1016 | 5.4% | 0.243 | 0.010 |
| since_31_60 | 612 | 7.4% | 0.229 | 0.012 |
| since_>=61 | 1025 | 7.0% | 0.263 | 0.012 |

### Days to estimated next filing

| Bucket | n | WR | Exp R | med MFE |
|---|---:|---:|---:|---:|
| to_1_5 | 63 | 7.9% | 0.406 | 0.018 |
| to_6_15 | 190 | 6.3% | 0.274 | 0.013 |
| to_16_45 | 656 | 7.5% | 0.253 | 0.011 |
| to_>=46 | 1726 | 6.1% | 0.255 | 0.012 |

### Pre-earn window (≤5d to est)

| Bucket | n | WR | Exp R | med MFE |
|---|---:|---:|---:|---:|
| 0 | 2859 | 6.7% | 0.252 | 0.012 |
| 1 | 63 | 7.9% | 0.406 | 0.018 |

### Post-earn window (≤3d since)

| Bucket | n | WR | Exp R | med MFE |
|---|---:|---:|---:|---:|
| 0 | 2875 | 6.7% | 0.254 | 0.012 |
| 1 | 47 | 6.4% | 0.325 | 0.021 |

### Corpus catalyst_type=earnings

| Bucket | n | WR | Exp R | med MFE |
|---|---:|---:|---:|---:|
| 0 | 3299 | 6.3% | 0.252 | 0.012 |
| 1 | 54 | 13.0% | 0.370 | 0.014 |

## Bakeoff vs v1.3

| Variant | n | WR | Exp R | Capture | OOS Exp | PASS? |
|---|---:|---:|---:|---:|---:|---|
| v13 | 720 | 10.4% | 0.299 | 42.8% | 0.357 | — |
| demote_pre_earn | 720 | 10.4% | 0.299 | 42.2% | 0.358 | no |
| boost_post_earn | 720 | 10.4% | 0.294 | 42.8% | 0.354 | no |
| boost_catalyst_earn | 720 | 10.6% | 0.302 | 43.9% | 0.367 | YES |
| combo_event | 720 | 10.4% | 0.294 | 42.2% | 0.355 | no |
| skip_pre_earn_5d | 715 | 10.3% | 0.296 | 41.1% | 0.358 | no |
| skip_post_earn_3d | 717 | 10.6% | 0.297 | 42.8% | 0.359 | no |
| skip_since_0_3 | 717 | 10.6% | 0.297 | 42.8% | 0.359 | no |
| prefer_post_1_10 | 188 | 9.6% | 0.335 | 10.0% | 0.397 | no |

## Decision needed

- **ADD soft (done)** — earnings catalyst +6 in live v1.4.
- Pre/post filing-window hard filters: **not** shipped (proxy calendar only).
- Blind-spot series #1–#5 complete for this pass.
- No further rewrite from this note.

## Notes

- Benzinga earnings calendar not on Polygon plan; TWS fundamentals blocked.
- Proxy = quarterly filing_date; days_to_earn_est from median filing gap.
- Baseline = live v1.3 (gap soft-skip + RS soft).
