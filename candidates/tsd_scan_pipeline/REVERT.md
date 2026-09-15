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

Confirm the mode on the next SCAN:

- Log banner: `equal_signal=OFF  score=v1.6`
- Telegram: `equal_signal=OFF score=v1.6`

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
