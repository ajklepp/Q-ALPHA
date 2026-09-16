# Peak Hour paper shadows

Live Peak Hour exits stay on **keep-profit v1** (T1 hard-bank ~**+2% of entry** + existing software trail / structure kill). Shadows log a **counterfactual** on the same open book. **No broker orders.**

## B_arm4_lock (arm-after-MFE)

Study follow-up to PR #19. Verdict was WATCH; this is the durable **paper** logger, **not** a live gate flip.

| Knob | Value | Language |
|------|-------|----------|
| Mode | `B_arm4_lock` | paper only |
| Arm | **+4% of entry** path MFE | % of entry, not R |
| Lock width | **70% of ticker MAE-p50**, floor **1% off high** | % off high |
| Kill | ratchets **up** with the trail after arm | % of entry below entry |
| Live A | unchanged T1 bank ~+2% of entry | — |

### How to run

```text
# Manual / EOD (reads tsd_book_state.json, writes dated results)
py -3 candidates/tsd_scan_pipeline/php_arm_after_mfe_shadow.py --write
py -3 candidates/tsd_scan_pipeline/php_arm_after_mfe_shadow.py --eod
py -3 candidates/tsd_scan_pipeline/php_arm_after_mfe_shadow.py --book path/to/book.json --asof 20260916

# Optional Polygon 1H if h1_bar_cache misses (0.12s/ticker)
py -3 candidates/tsd_scan_pipeline/php_arm_after_mfe_shadow.py --write --fetch-1h
```

Automatic hooks (paper, `try/except` so live never fails):

1. **Each trail tick** — after live `save_state` in `tsd_trail_monitor.run_monitor`. Same quote as the 3R shadow. Does not call `place_tsd_exit`.
2. **:15 after 1H LAUNCH** — `scheduler._tick_body` after the scan is marked ran. Does not change ranking, entries, or exits.

Artifacts:

- `candidates/tsd_scan_pipeline/results/php_arm_after_mfe_shadow_YYYYMMDD.json`
- `candidates/tsd_scan_pipeline/results/php_arm_after_mfe_shadow_YYYYMMDD.md`
- Runtime book (gitignored): `candidates/tsd_arm4_lock_shadow_book.json`

### How to turn off (revert)

```powershell
# Session / Task Scheduler environment
$env:PHP_ARM_AFTER_MFE_SHADOW = "0"
```

Or omit the trail/scheduler hooks by setting that env var in the Trail Monitor and Scheduler task definitions. Live keep-profit / kill / entries do not read this flag.

See also `REVERT.md`.

## 3R multi-target (existing)

`tsd_shadow_multi_target.py` — same fills, software banks at 0.35/0.50/0.90R. Unrelated to B_arm4_lock. Dashboard tab **3R Paper**.
