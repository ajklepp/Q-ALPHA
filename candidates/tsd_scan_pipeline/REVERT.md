# Revert notes — Peak Hour paper shadows

These flags **do not** change live T1 / trail / kill / entries. They only stop paper loggers.

## Turn off B_arm4_lock paper shadow

```text
PHP_ARM_AFTER_MFE_SHADOW=0
```

Set that on **QAlpha TSD Trail Monitor** and **QAlpha TSD Scheduler** (or the shell that launches them). Default is ON.

No need to revert `tsd_keep_profit.py`, `tsd_trail.py`, `tsd_kill.py`, or entry gates — this PR does not edit their behavior.

Optional: delete runtime `candidates/tsd_arm4_lock_shadow_book.json`. Dated `results/php_arm_after_mfe_shadow_*.json/md` are logs only.

## Turn off 3R multi-target paper shadow

There is no env flag today. The hook in `tsd_trail_monitor.py` (`tick_open_shadows`) and `mirror_live_fill` in `tsd_watch_queue.py` can be commented out. Live 4T keep-profit is independent.
