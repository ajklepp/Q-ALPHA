# Revert LIVE all-trailing (restore structure / BE dumps)

Peak Hour **LIVE** trail monitor no longer arms or fires software
`structure_stop` / `be_lock_1r` full-position sells. Runners stay on trail +
emergency kill. The broker kill is the single working protective SELL and
**only ratchets up**.

This is the revert for that gate. Do **not** use it to change Track 100 or
the 3R paper shadow.

## Restore Phase 2.5 BE / structure dumps

On the laptop that runs `tsd_trail_monitor`:

1. Set `TSD_LIVE_STRUCTURE_STOP=1` in the trail-monitor process environment
   (User env, scheduled-task env, or a session `$env:TSD_LIVE_STRUCTURE_STOP=1`
   before launching the loop).
2. Restart the trail loop (`--loop --adaptive`).
3. Confirm the boot banner says
   `LIVE structure_stop/be_lock_1r ON (TSD_LIVE_STRUCTURE_STOP=1 — REVERT path…)`.
4. After +1R, the log should show `be_lock_1r ARMED at <price>`. A later print
   through that price can `STRUCTURE EXIT … reason=structure_stop`.

To go back to all-trailing: unset the var (or set `0`) and restart the loop.
Open legs with a leftover `structure_stop` are disarmed on the next tick
(`live all-trailing: disarmed structure_stop from=be_lock_1r`).

## What this does **not** revert

| Path | Status |
|------|--------|
| Broker emergency kill | Always on; qty sync; ratchet **UP** only |
| Keep-profit T1 bank + T2–T4 trail | Unchanged |
| Paper **3R** multi-target shadow | Unchanged (not this flag) |
| Strategy B / any paper `B_arm4_lock` shadow | Paper-only; not 3R; not this flag |
| Track 100 | Untouched |

## Default

`TSD_LIVE_STRUCTURE_STOP` defaults to **OFF** (`0` / unset).
