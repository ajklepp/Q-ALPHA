# Peak Hour revert notes

Independent live gates. Revert only the section you need.

---

# Peak Hour live overlays — equal-signal + momentum-rank

**Owner:** Peak Hour / Aaron  
**Flags:** `PHP_EQUAL_SIGNAL` (default **ON**, 2026-09-15) · `PHP_MOMENTUM_RANK` (default **ON**, 2026-09-16)

This file is the one-step revert path. Do **not** restart trail monitor for
either flag. Trails / keep-profit / 2 NEW per hour did not change.

---

## PHP_MOMENTUM_RANK (2026-09-16)

After equal-signal admission, scarce slots prefer same-day rippers
(momentum + room + tape) over slow multi-day popular names.

- **Score tilt.** Same-session RS, session return, volume, live-gainer+hot
  tape, and constructive room are boosted (ripper boost capped so we do not
  grab every runner). Slow popular (board membership without tape heat) and
  high hist-prior without tape are penalized.
- **Take sort.** Momentum-adjusted continuation first; case confidence is
  tiebreak only. A high-confidence slow popular name cannot beat a ripper.
- **Popularity is a lane, not the only filter.** Case ENTER + (popular **or**
  momentum_context + hot tape). First-day rippers can take without the
  10-day board.
- **Hard-extension stays ON.** `scan >= 75` is still excluded.
- **Case stays a veto.** Wreckage / toxic / no-ENTER still cannot buy.
- **Trails / take cap:** unchanged.

### One-step revert (ranking only)

Set **`PHP_MOMENTUM_RANK=0`** (also `false` / `off` / `no`) in repo `.env`
(same file as `POLYGON_API_KEY`) or as a Windows User env var. Next `:15`
tick picks it up. Do **not** restart trail monitor.

Confirm on the next SCAN:

- Log banner: `momentum_rank=OFF`
- Telegram: `momentum_rank=OFF`
- Score label drops `+momentum_rank` (e.g. `score=v1.6+equal_signal`)

Flag OFF restores: continuation score as computed by equal-signal / v1.6,
popularity-only take filter, and case-confidence-first sort.

To turn it back on: delete the line / unset (default ON), or set
`PHP_MOMENTUM_RANK=1`. Expect `momentum_rank=ON` and
`score=v1.6+equal_signal+momentum_rank` when equal-signal is also ON.

---

## PHP_EQUAL_SIGNAL (2026-09-15)

Live Peak Hour take path treats every valid 1H signal candle as equal.
Soft stage is not graded; hard `scan>=75` still excluded.

- **Soft stage equalized.** Phase −15, scan-band beauty, launch-score
  sweet-spot no longer grade LAUNCH vs EXTENSION vs NEUTRAL.
- **Phase → `bar_state=extended` leak fixed.** Soft EXTENSION (scan ~65–74)
  is no longer `extension_hard`. That leak is why OKTA / WIX / COIN-class
  names never reached attention on 2026-09-14.
- **Hard-extension stays ON.** `scan >= 75` is still excluded.
- **LLM case:** still live. Rules ENTER no longer refuses `scan > 55` when
  the flag is ON.

### One-step revert (admission / stage grading)

Set **`PHP_EQUAL_SIGNAL=0`**. Next `:15` tick inherits it.

Every live 1H path (`scheduler.py --tick --live`, `--launch --live`, and
direct `tsd_1h_launch_scan.py`) calls `apply_php_equal_signal_env()` at
process start so a later patch based on `main` cannot silently print
`score=v1.6` without `equal_signal=…`. Blank `PHP_EQUAL_SIGNAL=` is treated
as unset (default ON).

Confirm the mode on the next SCAN:

- Scheduler + 1H log: `score=v1.6+equal_signal…  equal_signal=ON|OFF`
  (plus `momentum_rank=…` when that overlay is present)
- Telegram: `equal_signal=OFF` when reverted (score may still show
  `+momentum_rank` if that flag stays ON)

Flag OFF restores pre-2026-09-15 list admission, continuation grading, the
phase→extended hard-block leak, and the rules ENTER `scan <= 55` refuse band.

To turn it back on: delete the line / unset (default ON), or set
`PHP_EQUAL_SIGNAL=1`.

---

## Laptop verify before next RTH

From the laptop (next to live `.env` + scan JSON):

```text
git pull
# confirm flags default ON (or unset)
py -3 -m unittest tests.test_php_momentum_rank tests.test_php_equal_signal -v
py -3 candidates/tsd_scan_pipeline/php_momentum_rank_counterfactual.py --date 2026-09-14
```

Next live `:15` log / Telegram must show:

```text
equal_signal=ON  momentum_rank=ON  score=v1.6+equal_signal+momentum_rank
```

If you see bare `score=v1.6` with no `momentum_rank=`, the tick is not on
this code.

## Git fallback

- Parent of equal-signal on main: `0cc11c2cfd5beffbae6c0abbabf67c5f3bd286eb`
- After merge: revert the momentum-rank PR to drop ranking only; revert
  the equal-signal PR to restore stage grading. Do not revert trails.

Research-only equal-signal counterfactual (not this product):
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
