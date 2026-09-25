# Peak Hour early-trail / kill-schedule study (2026-09-16)

PAPER-ONLY. Zero edits to live `tsd_keep_profit` / `tsd_trail` / `tsd_trail_monitor` / `tsd_kill` / entry gates.

- Universe: **fixture** · n=4 entries / 4 tickers
- Bar source: **fixture**
- Runtime: **0.144s**
- Language: % of entry and % off high. “+1R” in older notes = +5.00% of entry (live fallback kill).

## Mode book (same entries)

| Mode | Book $ | Mean % of entry | Capture (MFE≥1%) | Green-then-lost | Score |
|------|-------:|----------------:|-----------------:|----------------:|------:|
| A live php_keep_profit_v1 (T1 bank +2% of entry) | +8.32 | 0.80% | -90.64% | 50.00% | 0.003005 |
| B paper trail-everything + MAE-p50 trail (no hard bank) | -5.61 | -2.18% | -125.18% | 50.00% | -0.026765 |
| C paper per-ticker early-trail + kill schedule | +18.09 | 2.81% | -74.84% | 25.00% | 0.025601 |
| D shadow MT3 hard banks (1.75/2.5/4.5% of entry; +1R=+5%) | +3.34 | 0.19% | -82.91% | 25.00% | -0.000562 |

## Per-ticker proposed schedule (Mode C)

| Ticker | Status | n analogs | Trail % off high | Arm MFE % of entry | Kill 0/2/3/4 |
|--------|--------|----------:|-----------------:|-------------------:|--------------|
| BANK | OK | 10 | 1.00% | 3.00% | 2.0%/2.0%/2.0%/1.5% |
| CHOP | OK | 10 | 1.00% | 1.50% | 6.0%/5.6%/2.5%/1.5% |
| FADE | OK | 10 | 1.00% | 1.50% | 5.0%/2.5%/1.5%/1.0% |
| RUNR | OK | 10 | 3.00% | 1.50% | 2.0%/2.0%/2.0%/1.5% |

## How to re-run (laptop)

```text
py -3 candidates/tsd_scan_pipeline/php_early_trail_kill_schedule_study.py --write
py -3 candidates/tsd_scan_pipeline/php_early_trail_kill_schedule_study.py --dry-run
cd candidates && python -m tsd_scan_pipeline.php_early_trail_kill_schedule_study --dry-run
```

Needs `POLYGON_API_KEY` in `.env` for fresh 1H bars. Without a key the
script uses fixture / cached `results/h1_bar_cache` / `profiles/*_tsd_profile.json`
/ EXP-0021 path facts (MFE+MAE reconstruction).

Degrade notes: dry-run fixture (no Polygon)

Fixture / dry-run numbers are sanity dumps, not a live Sharpe claim.

