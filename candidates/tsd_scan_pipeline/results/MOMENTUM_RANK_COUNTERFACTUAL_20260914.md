# Momentum-rank Peak Hour counterfactual — 2026-09-14 (ET)

**Live ranking change is separate** (`PHP_MOMENTUM_RANK`, default ON). This file
is the evidence pack: actual takes vs would-have-ranked under three policies.
Trails / keep-profit / take cap are out of scope.

- Overlay version: `live_v1`
- Take cap: **`MAX_NEW_ENTRIES_PER_SCAN = 2`**
- Hard-extension: **ON** (`scan >= 75`)
- Case: **rules-only** (no invented LLM ENTER confidences)
- Equal-signal admission: assumed ON for policies 2 and 3 (PR #11)

## What the live day did

Hour-8 abort plus operator-named takes (same baseline as PR #10). Cloud clone
has **0** `php_scan_20260914_*.json` files.

| Hour ET | Actual take (queued) | Evidence | Notes |
|---:|---|---|---|
| 05 | TARS | AARON | Take queued (fill not documented in repo) |
| 06 | HOOD | AARON | Take queued (fill not documented in repo) |
| 07 | — | UNKNOWN | No take named in operator baseline or repo artifacts |
| 08 | (aborted — no scan) | AARON+CODE | Hour 8 aborted — no SCAN. Cannot invent passers. |
| 09 | IRD | AARON | IRD FILLED +$3.63 (operator) |
| 10 | SNDK | AARON | Take queued (fill not documented in repo) |
| 11 | — | UNKNOWN | Afternoon unauditable |
| 12 | — | UNKNOWN | Afternoon unauditable |
| 13 | — | UNKNOWN | Afternoon unauditable |
| 14 | — | UNKNOWN | Afternoon unauditable |
| 15 | — | UNKNOWN | Afternoon unauditable |

Public same-day move (NOT missed_ledger — confirms who ripped):

| Symbol | High vs prior close | Close vs prior | Prior-close room vs 20d high |
|---|---:|---:|---:|
| OKTA | 13.95% | 11.98% | 7.2% |
| WIX | 11.33% | 8.81% | 20.9% |
| COIN | 10.25% | 9.24% | 10.5% |
| TARS | 1.17% | 0.81% | 13.4% |
| HOOD | 3.35% | 1.56% | 10.1% |
| IRD | 2.98% | -1.16% | 14.6% |
| SNDK | -3.15% | -4.98% | 9.6% |

TARS closed **+0.81%** (high **+1.17%**). OKTA / WIX / COIN closed
**+12% / +8.8% / +9.2%** with highs **+14% / +11% / +10%**.

## Nearby sessions (scan JSON on this clone)

| Date | Artifacts |
|---|---|
| 2026-09-11 | 0 scan JSON files |
| 2026-09-12 | 0 scan JSON files |
| 2026-09-15 | 0 scan JSON files |
| 2026-09-14 | 0 scan JSON files |

Without laptop `php_scan_*.json`, nearby-day would-takes stay UNKNOWN. Re-run
on the laptop next to the scan folder to fill them.

## Illustrative watchlist (labeled)

ILLUSTRATIVE — scan/tape fields constructed from public session + PR #10
assumed EXTENSION-class scans for OKTA/WIX/COIN and LAUNCH-class for the
names that were actually queued. **Not** live funnel passers.

### Policy 1 — legacy v1.6 (equal-signal OFF, momentum-rank OFF)

Would-take (cap 2, popularity + confidence-first): **IRD, TARS**

| Symbol | scan | phase | cont | case | tape_hot | slow_pop | overlay Δ |
|---|---:|---|---:|---|---:|---:|---:|
| IRD | 36.0 | LAUNCH | 89.06 | ENTER | 1 | 0 | 14.0 |
| TARS | 38.0 | LAUNCH | 86.98 | ENTER | 0 | 1 | -18.0 |
| HOOD | 40.0 | NEUTRAL | 83.49 | ENTER | 0 | 1 | -18.0 |
| COIN | 64.0 | NEUTRAL | 79.83 | WAIT | 1 | 0 | 36.0 |
| SNDK | 42.0 | NEUTRAL | 67.99 | WAIT | 0 | 1 | -26.0 |
| WIX | 66.0 | EXTENSION | 31.03 | WAIT | 1 | 0 | 36.0 |
| OKTA | 68.0 | EXTENSION | 25.11 | WAIT | 1 | 0 | 36.0 |

Soft-EXTENSION names (WIX/COIN/OKTA at scan 64–68) are often
`extension_hard` via the phase→extended leak, so they never rank.

### Policy 2 — equal-signal only (momentum-rank OFF)

Would-take: **WIX, COIN**

| Symbol | scan | phase | cont | case | tape_hot | slow_pop | overlay Δ |
|---|---:|---|---:|---|---:|---:|---:|
| WIX | 66.0 | EXTENSION | 98.63 | ENTER | 1 | 0 | 36.0 |
| COIN | 64.0 | NEUTRAL | 94.83 | ENTER | 1 | 0 | 36.0 |
| OKTA | 68.0 | EXTENSION | 92.71 | WAIT | 1 | 0 | 36.0 |
| IRD | 36.0 | LAUNCH | 74.96 | ENTER | 1 | 0 | 14.0 |
| TARS | 38.0 | LAUNCH | 74.28 | ENTER | 0 | 1 | -18.0 |
| HOOD | 40.0 | NEUTRAL | 72.19 | ENTER | 0 | 1 | -18.0 |
| SNDK | 42.0 | NEUTRAL | 58.09 | WAIT | 0 | 1 | -26.0 |

Admission is equal, so WIX/COIN can ENTER. Tape terms already in equal-signal
can rank them above TARS **when confidence is ignored**. Live take sort is
still **case-confidence first** — that is the remaining miss.

### Policy 3 — equal-signal + momentum-rank (proposed live)

Would-take: **WIX, COIN**

| Symbol | scan | phase | cont | case | tape_hot | slow_pop | overlay Δ |
|---|---:|---|---:|---|---:|---:|---:|
| WIX | 66.0 | EXTENSION | 134.63 | ENTER | 1 | 0 | 36.0 |
| COIN | 64.0 | NEUTRAL | 130.83 | ENTER | 1 | 0 | 36.0 |
| OKTA | 68.0 | EXTENSION | 128.71 | WAIT | 1 | 0 | 36.0 |
| IRD | 36.0 | LAUNCH | 88.96 | ENTER | 1 | 0 | 14.0 |
| TARS | 38.0 | LAUNCH | 56.28 | ENTER | 0 | 1 | -18.0 |
| HOOD | 40.0 | NEUTRAL | 54.19 | ENTER | 0 | 1 | -18.0 |
| SNDK | 42.0 | NEUTRAL | 32.09 | WAIT | 0 | 1 | -26.0 |

Same-day tape/room/RS pull WIX/COIN further ahead (WIX 135 vs TARS 56, a
**79-pt** gap vs ~24 pts under equal-signal only). TARS is marked
slow-popular and demoted. OKTA still WAITs on rules (prior-close room
7.2% < 10% constructive floor) — case remains a surgical veto. Hard
`scan>=75` is still blocked (not in this set).

### Confidence stress (why the overlay is not optional)

Live LLM often rates the slow popular name “safer.” Force TARS
confidence **0.92** vs WIX **0.70** (same ENTER set):

| Policy | Would-take (cap 2) |
|---|---|
| Equal-signal only (confidence-first) | **TARS, COIN** |
| Equal-signal + momentum-rank (score-first) | **WIX, COIN** |

That is the Sep 14 failure mode after equal admission: TARS can still
consume a slot because it looks popular and the model is confident.

## Reading

| Question | Answer |
|---|---|
| Did equal-signal alone prefer rippers over TARS? | By score, often yes in this set. By **live take sort**, no — high TARS confidence still wins a slot. |
| Does momentum-rank pick WIX/COIN over TARS? | Yes, including the 0.92-vs-0.70 confidence stress. |
| Does it grab every runner? | No. OKTA WAITs on room. Cap stays 2. Hard-extension stays ON. |
| Are trails changed? | No. |

Same-day high vs prior close: WIX **+11.3%** and COIN **+10.3%** versus TARS
**+1.2%**. That is the miss the overlay is built to stop repeating.

## How to finish this on the laptop

```text
py -3 candidates/tsd_scan_pipeline/php_momentum_rank_counterfactual.py --date 2026-09-14 --write
```

Expects `candidates/tsd_scan_pipeline/results/peak_hour_scans/php_scan_YYYYMMDD_*.json`.
When those files exist, the hour table fills from live passers (still
rules-only case). Until then the illustrative table is the mechanism proof.

### Verify before next RTH

1. `git pull` this branch on the laptop.
2. Leave `PHP_MOMENTUM_RANK` unset (default ON) or set `=1` in `.env`.
3. Run `py -3 -m unittest tests.test_php_momentum_rank tests.test_php_equal_signal -v`.
4. Next `:15` log / Telegram must show
   `equal_signal=ON  momentum_rank=ON  score=v1.6+equal_signal+momentum_rank`.
5. Revert (ranking only): `PHP_MOMENTUM_RANK=0` in `.env`. See `REVERT.md`.
   Do not restart trail monitor.
