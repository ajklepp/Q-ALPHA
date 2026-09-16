# Revert — live structure_stop / BE lock exits

Live Peak Hour default after this change: **hard structure BE sells are OFF**.

`PHP_STRUCTURE_STOP_EXITS` unset / `0` / `false` / `off` → no `structure_stop` /
`be_lock_1r` / `breakeven_ratchet` dumps. Protective path is the single broker
kill/trail stop (ratchets **UP only**) plus a tighter software trail after
~+3–4% MFE.

## Restore old Phase 2.5 structure BE dumps

On the Peak Hour laptop (Task Scheduler env for **QAlpha TSD Trail Monitor**,
or the shell that launches `tsd_trail_monitor.py`):

```
set PHP_STRUCTURE_STOP_EXITS=1
```

PowerShell:

```powershell
$env:PHP_STRUCTURE_STOP_EXITS = "1"
```

Then restart the trail monitor. `maybe_arm_be_lock_on_1r` will again write
`structure_stop` after +1R and the monitor will flatten with
`reason=structure_stop` on a breach. `maybe_ratchet_breakeven` resumes.

Keep-profit **T1 bank at +2%** is independent of this flag (unchanged).

## Not in this revert

- **Do not** set live 3R / multi-target hard banks. `tsd_shadow_multi_target`
  / MT3 remains **paper/shadow comparison only**.
- Kill stop must stay armed while shares remain. Reverting the flag does not
  cancel broker kills.

## Open longs that were migrated

`migrate_structure_stop_to_trail.py --apply` clears `structure_stop` on the
book. Restoring the flag does **not** automatically re-arm BE on those legs
until price again satisfies +1R arming (`one_r_locked` already true → arm is
a no-op). If you need the old BE stop back on a specific open, set
`structure_stop` / `structure_stop_reason=be_lock_1r` on that leg after
`PHP_STRUCTURE_STOP_EXITS=1`.
