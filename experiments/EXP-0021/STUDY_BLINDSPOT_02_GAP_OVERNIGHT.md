# EXP-0021 — Blind-spot #2: Gap + overnight

**Generated:** 2026-09-05T23:26:59.853398-04:00
**Runtime:** 1.6s (Modal)
**Recommendation:** **HARD**

Best real passer: **skip_gap_ge_5pct**. Hard filter only if you approve.

## Coverage

- admits: 3353
- gap_filled_rate: 43.9%
- overnight_led_rate: 47.1%

## How this maps to Peak Hour

- `gap_pct` is already computed but not a first-class v1.1 term.
- Literature: mid gaps + volume continue; filled / extreme gaps fade;
  overnight-led vs grind-from-open can differ.

## Strata (admits)

### Gap %

| Bucket | n | WR | Exp R | med MFE |
|---|---:|---:|---:|---:|
| gap_<=-2% | 302 | 6.6% | 0.251 | 0.012 |
| gap_-2_-0.5% | 795 | 3.0% | 0.240 | 0.011 |
| gap_flat | 1166 | 5.4% | 0.257 | 0.011 |
| gap_0.5_2.5% | 906 | 8.2% | 0.273 | 0.013 |
| gap_2.5_5% | 157 | 19.1% | 0.225 | 0.016 |
| gap_>=5% | 27 | 18.5% | 0.088 | 0.024 |

### Intraday return since open

| Bucket | n | WR | Exp R | med MFE |
|---|---:|---:|---:|---:|
| id_red_<=-1% | 177 | 11.9% | 0.279 | 0.015 |
| id_flat | 2123 | 5.8% | 0.245 | 0.011 |
| id_green_1_3% | 863 | 5.2% | 0.257 | 0.012 |
| id_green_>=3% | 190 | 14.2% | 0.315 | 0.019 |

### Gap already filled by signal

| Bucket | n | WR | Exp R | med MFE |
|---|---:|---:|---:|---:|
| 0 | 1882 | 7.2% | 0.257 | 0.011 |
| 1 | 1471 | 5.4% | 0.249 | 0.012 |

### Overnight-led

| Bucket | n | WR | Exp R | med MFE |
|---|---:|---:|---:|---:|
| 0 | 1774 | 5.5% | 0.256 | 0.012 |
| 1 | 1579 | 7.5% | 0.251 | 0.012 |

### Gap + vol confirmation

| Bucket | n | WR | Exp R | med MFE |
|---|---:|---:|---:|---:|
| 0 | 3153 | 6.3% | 0.252 | 0.012 |
| 1 | 200 | 9.0% | 0.278 | 0.012 |

## Bakeoff vs v1.1

| Variant | n | WR | Exp R | Capture | OOS Exp | PASS? |
|---|---:|---:|---:|---:|---:|---|
| v11 | 720 | 10.4% | 0.297 | 42.8% | 0.357 | — |
| boost_mid_gap | 720 | 9.4% | 0.285 | 37.8% | 0.336 | no |
| boost_overnight_led | 720 | 11.0% | 0.294 | 42.2% | 0.339 | no |
| demote_filled_gap | 720 | 10.4% | 0.291 | 43.3% | 0.346 | no |
| boost_gap_with_vol | 720 | 10.4% | 0.296 | 42.2% | 0.352 | no |
| combo_gap_quality | 720 | 9.4% | 0.284 | 37.2% | 0.334 | no |
| skip_gap_ge_5pct | 719 | 10.4% | 0.301 | 42.8% | 0.357 | YES |
| skip_gap_le_neg1 | 689 | 9.1% | 0.278 | 36.7% | 0.344 | no |
| skip_filled_up_gap | 718 | 10.3% | 0.290 | 42.2% | 0.350 | no |
| skip_not_mid_gap | 495 | 7.9% | 0.267 | 21.1% | 0.309 | no |
| prefer_overnight_led_only | 605 | 9.8% | 0.263 | 32.2% | 0.269 | no |

## Decision needed

- Reply: **ADD soft / ADD hard / HOLD**
- No live rewrite from this run.

## Notes

- gap_pct already in corpus; overnight_led = |gap|>=|id_ret| and |gap|>=0.5%.
- gap_filled = up-gap traded back to prior close (or down-gap to prior) by signal bar.
- Vacuous identical-to-v11 winners excluded from recommendation.
