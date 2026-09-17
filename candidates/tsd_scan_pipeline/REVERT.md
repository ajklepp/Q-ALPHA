# Peak Hour revert notes

Two independent live gates. Revert only the section you need.

---

# Peak Hour equal-signal — revert note

**Date:** 2026-09-15  
**Owner:** Peak Hour / Aaron  
**Flag:** `PHP_EQUAL_SIGNAL` (default **ON**)

## What changed

Live Peak Hour take path (attention / continuation / list admission) treats
every valid 1H signal candle as equal. After admission, picks still use
popularity, momentum, room, tape, and case — no new ranking philosophy.

- **Soft stage equalized.** Phase −15, scan-band beauty (`scan_term` /
  scan>55 extra −10), and launch-score “sweet-spot” beauty no longer grade
  LAUNCH vs EXTENSION vs NEUTRAL.
- **Phase → `bar_state=extended` leak fixed.** `classify_bar_state()` no
  longer maps `phase == EXTENSION` to `extended`. Soft EXTENSION (scan ~65–74)
  is no longer `extension_hard`. That leak is why OKTA / WIX / COIN-class
  names never reached attention on 2026-09-14.
- **Hard-extension stays ON.** `scan >= 75` is still excluded.
- **Trails / keep-profit / structure stops:** unchanged.
- **Take cap:** unchanged (2 NEW / hour).
- **Popularity:** unchanged (hard veto vs boost not flipped).
- **LLM case:** still live. Rules ENTER no longer refuses `scan > 55` when
  the flag is ON — constructive room + momentum + tradable popularity can
  ENTER at any scan `< 75`. Soft stage is not a veto. LLM ENTER is not
  removed.

## One-step revert (no commit hunting)

Set **`PHP_EQUAL_SIGNAL=0`** (also accepts `false` / `off` / `no`) and
restart the Peak Hour scheduler so the next `:15` tick inherits it.

1. Preferred: add `PHP_EQUAL_SIGNAL=0` to the repo **`.env`** (same file as
   `POLYGON_API_KEY`). The flag reads env first, then `.env`.
2. Or set a Windows **User** environment variable `PHP_EQUAL_SIGNAL=0`
   (Task Scheduler inherits the user env on the laptop).
3. Restart **QAlpha TSD Scheduler** (or wait for the next 5-min tick after
   the env is visible). Do **not** restart trail monitor — trails did not
   change.

Every live 1H path (`scheduler.py --tick --live`, `--launch --live`, and
direct `tsd_1h_launch_scan.py`) calls `apply_php_equal_signal_env()` at
process start so a later patch based on `main` cannot silently print
`score=v1.6` without `equal_signal=…`. Blank `PHP_EQUAL_SIGNAL=` is treated
as unset (default ON).

Confirm the mode on the next SCAN:

- Scheduler + 1H log: `score=v1.6+equal_signal  equal_signal=ON` (or OFF)
- Telegram: `equal_signal=OFF score=v1.6` when reverted

To turn it back on: delete the line / unset the var (default is ON), or set
`PHP_EQUAL_SIGNAL=1`. Telegram / log should show
`equal_signal=ON score=v1.6+equal_signal`.

Flag OFF restores pre-change list admission, continuation grading, the
phase→extended hard-block leak, and the rules ENTER `scan <= 55` refuse
band.

## Git fallback

- Parent of this change (main at branch): `0cc11c2cfd5beffbae6c0abbabf67c5f3bd286eb`
- After merge: revert the equal-signal live PR (do not revert trails).

Research-only counterfactual (not this product): PR
https://github.com/ajklepp/Q-ALPHA/pull/10

---

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
