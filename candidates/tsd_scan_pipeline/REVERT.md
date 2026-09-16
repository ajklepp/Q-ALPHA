# Revert LIVE all-trailing (restore structure / BE dumps)

Peak Hour **LIVE** trail monitor no longer arms or fires software
`structure_stop` / `be_lock_1r` full-position sells. Runners stay on trail +
emergency kill. The broker kill is the single working protective SELL and
**only ratchets up**. “Lock profit” is a tighter software trail after ~+3–4% MFE
(ticker MAE/MFE priors when present), not a hard BE dump.

This is the revert for that gate. Do **not** use it to change Track 100 or
the 3R paper shadow.

## Canonical flag (laptop)

`TSD_LIVE_STRUCTURE_STOP` — default **OFF**. Laptop start script pins
`TSD_LIVE_STRUCTURE_STOP=0` **and** `PHP_STRUCTURE_STOP_EXITS=0`.

`PHP_STRUCTURE_STOP_EXITS` is an **alias** of the same gate (not a second
path). Either variable `=1` restores Phase 2.5 dumps.

## Restore Phase 2.5 BE / structure dumps

On the laptop that runs `tsd_trail_monitor`:

1. Set `TSD_LIVE_STRUCTURE_STOP=1` (or `PHP_STRUCTURE_STOP_EXITS=1`) in the
   trail-monitor process environment (User env, scheduled-task env, or a
   session `$env:TSD_LIVE_STRUCTURE_STOP=1` before launching the loop).
2. Restart the trail loop (`--loop --adaptive`).
3. Confirm the boot banner says
   `LIVE structure_stop/be_lock_1r ON (TSD_LIVE_STRUCTURE_STOP=1 — REVERT path…)`.
4. After +1R, the log should show `be_lock_1r ARMED at <price>`. A later print
   through that price can `STRUCTURE EXIT … reason=structure_stop`.

To go back to all-trailing: unset both vars (or set `0`) and restart the loop.
Open legs with a leftover `structure_stop` are disarmed on the next tick
(`live all-trailing: disarmed structure_stop from=be_lock_1r`).

Offline helper (ATRC already migrated on the laptop; keep for other opens):

```
py -3 candidates\tsd_scan_pipeline\migrate_structure_stop_to_trail.py --symbol ATRC
py -3 candidates\tsd_scan_pipeline\migrate_structure_stop_to_trail.py --symbol ATRC --apply
```

Dry-run by default. `--apply` writes book only (never removes kill). Then run
trail `--once` so `sync_kill_quantity` ratchets the working stop UP if needed.

## What this does **not** revert

| Path | Status |
|------|--------|
| Broker emergency kill | Always on; qty sync; ratchet **UP** only |
| Keep-profit T1 bank + T2–T4 trail | Unchanged |
| Paper **3R** multi-target shadow | Unchanged (not this flag) |
| Strategy B / any paper `B_arm4_lock` shadow | Paper-only; not 3R; not this flag |
| Track 100 | Untouched |

## Default

`TSD_LIVE_STRUCTURE_STOP` / `PHP_STRUCTURE_STOP_EXITS` default **OFF** (`0` / unset).
