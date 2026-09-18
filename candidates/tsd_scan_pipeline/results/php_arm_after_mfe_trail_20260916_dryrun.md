# Peak Hour arm-after-MFE trail study (2026-09-16)

PAPER-ONLY. Zero edits to live `tsd_keep_profit` / `tsd_trail` / `tsd_trail_monitor` / `tsd_kill` / entry gates.

- Universe: **fixture** · n=6 entries / 6 tickers
- Bar source: **fixture**
- Recommendable sample (n_bars≥3): **6** · n_bars>=3 (6/6)
- Runtime: **0.014s**
- Language: % of entry and % off high. “+1R” in older notes = +5.00% of entry (live fallback kill).
- Prior paper: PR #16 trail-from-entry MAE-p50 lost on rippers — do not prefer that B over A. This study arms **after** MFE.

## Recommendation (live later — not a patch)

- **Verdict:** `MECHANISM_ONLY`
- **Prefer B for live later:** **False**
- **Least-bad B on rippers:** `B_arm3_lock` (rippers mean 4.75%, left-on-table 1.65%)
- A all-paths mean: 0.35% · A rippers: 2.58%
- Why: Designed fixture (not live 1H): arm-after-MFE keeps the ripper that from-entry loses. Least-bad B on rippers = B_arm3_lock at 4.75% of entry vs A 2.58%. Do **not** prefer B for live until a book / h1_bar_cache / Polygon replay confirms the same gate.
- **Invite human review before any live Peak Hour patch.**

## Mode book (same entries)

| Mode | Book $ | Mean % of entry | Median % | GTL | Left on table vs MFE | Score |
|------|-------:|----------------:|---------:|----:|---------------------:|------:|
| A live php_keep_profit_v1 (T1 bank +2% of entry) | +1.68 | 0.35% | -0.24% | 33.33% | 3.88% | 0.000167 |
| B_arm3 arm-after-MFE +3% of entry · MAE-p50 trail · kill follows trail | +8.47 | 1.76% | 1.27% | 16.67% | 2.47% | 0.01598 |
| B_arm4 arm-after-MFE +4% of entry · MAE-p50 trail · kill follows trail | +5.69 | 1.18% | 0.70% | 33.33% | 3.05% | 0.008514 |
| B_arm3_lock arm-after-MFE +3% · lock width (70% of MAE-p50) | +10.12 | 2.11% | 1.95% | 16.67% | 2.13% | 0.019411 |
| B_arm4_lock arm-after-MFE +4% · lock width (70% of MAE-p50) | +6.79 | 1.41% | 1.05% | 33.33% | 2.82% | 0.01081 |
| C PR #16 Mode B contrast: trail-from-entry MAE-p50 (arm=0) | -0.29 | -0.06% | 0.14% | 16.67% | 2.53% | -0.002278 |

## Slice — All paths

| Mode | n | Mean % of entry | Median % | GTL | Left on table |
|------|--:|----------------:|---------:|----:|--------------:|
| A | 6 | 0.35% | -0.24% | 33.33% | 3.88% |
| B_arm3 | 6 | 1.76% | 1.27% | 16.67% | 2.47% |
| B_arm4 | 6 | 1.18% | 0.70% | 33.33% | 3.05% |
| B_arm3_lock | 6 | 2.11% | 1.95% | 16.67% | 2.13% |
| B_arm4_lock | 6 | 1.41% | 1.05% | 33.33% | 2.82% |
| C | 6 | -0.06% | 0.14% | 16.67% | 2.53% |

## Slice — Rippers (path MFE≥4.00%)

| Mode | n | Mean % of entry | Median % | GTL | Left on table |
|------|--:|----------------:|---------:|----:|--------------:|
| A | 3 | 2.58% | 2.35% | 33.33% | 3.82% |
| B_arm3 | 3 | 4.29% | 2.05% | 0.00% | 2.11% |
| B_arm4 | 3 | 4.29% | 2.05% | 0.00% | 2.11% |
| B_arm3_lock | 3 | 4.75% | 2.74% | 0.00% | 1.65% |
| B_arm4_lock | 3 | 4.75% | 2.74% | 0.00% | 1.65% |
| C | 3 | 0.45% | 0.10% | 33.33% | 2.41% |

## Slice — Grinders (path MFE<4.00%)

| Mode | n | Mean % of entry | Median % | GTL | Left on table |
|------|--:|----------------:|---------:|----:|--------------:|
| A | 3 | -1.88% | -0.96% | 33.33% | 3.95% |
| B_arm3 | 3 | -0.76% | -0.35% | 33.33% | 2.82% |
| B_arm4 | 3 | -1.92% | -2.70% | 66.67% | 3.98% |
| B_arm3_lock | 3 | -0.53% | -0.35% | 33.33% | 2.60% |
| B_arm4_lock | 3 | -1.92% | -2.70% | 66.67% | 3.98% |
| C | 3 | -0.58% | 0.19% | 0.00% | 2.64% |

## Slice — Green-then-lost

| Mode | n | Mean % of entry | Median % | GTL | Left on table |
|------|--:|----------------:|---------:|----:|--------------:|
| A | 2 | -0.96% | -0.96% | 100.00% | 4.66% |
| B_arm3 | 1 | -0.35% | -0.35% | 100.00% | 2.95% |
| B_arm4 | 2 | -1.52% | -1.52% | 100.00% | 4.42% |
| B_arm3_lock | 1 | -0.35% | -0.35% | 100.00% | 2.95% |
| B_arm4_lock | 2 | -1.52% | -1.52% | 100.00% | 4.42% |
| C | 1 | -0.79% | -0.79% | 100.00% | 2.39% |

## Per-ticker MAE–MFE widths (prior analogs only)

| Ticker | Status | n analogs | MAE-p50 | Trail % off high | Lock % off high | Kill floor |
|--------|--------|----------:|--------:|-----------------:|----------------:|-----------:|
| CHOPKILL | OK | 8 | 2.20% | 2.20% | 1.54% | 2.55% |
| FADE4 | OK | 8 | 2.20% | 2.20% | 1.54% | 2.55% |
| GRINDER | OK | 8 | 2.20% | 2.20% | 1.54% | 2.55% |
| GTL | OK | 8 | 2.20% | 2.20% | 1.54% | 2.55% |
| LOCKDEMO | OK | 8 | 2.20% | 2.20% | 1.54% | 2.55% |
| RIPPER | OK | 8 | 2.20% | 2.20% | 1.54% | 2.55% |

## How to re-run

```text
py -3 candidates/tsd_scan_pipeline/php_arm_after_mfe_trail_study.py --write
py -3 candidates/tsd_scan_pipeline/php_arm_after_mfe_trail_study.py --dry-run
cd candidates && python -m tsd_scan_pipeline.php_arm_after_mfe_trail_study --dry-run
```

Needs `POLYGON_API_KEY` in `.env` for fresh 1H bars. Without a key the
script uses fixture / cached `results/h1_bar_cache` / profiles /
EXP-0021 path facts (MFE+MAE 2-bar reconstruction — degrade).

Degrade notes: dry-run fixture (no Polygon)

Fixture / dry-run / 2-bar numbers are not a live Sharpe claim.
Do not change live kill/trail gates from this file.

