# UTS v2 — LAUNCH Phase Course Correction

**Date:** 2026-09-01  
**Status:** IMPLEMENTED (gates + scoring + base-break exit)

## Problem

Phase 1 gates used `scan_score >= 70` as the primary Lane B filter. User edge is the **opposite**: low `scan_score` (~25–45) on a fresh `buy_signal` / `early_bull` (orange candle) = move **starting**. High `scan_score` (65+) = move **extended** — bad for new longs.

## Changes

### 1. `tsd_launch_score.py` (new)

| Function | Purpose |
|----------|---------|
| `compute_launch_phase()` | `LAUNCH` \| `EXTENSION` \| `NEUTRAL` |
| `compute_launch_score()` | 0–100 quality from chart rules |
| `signal_bar_red()` | `close < open` on signal 3H bar |
| `is_launch_candidate()` | Phase + score + trigger check |
| `enrich_launch_fields()` | Adds `launch_score`, `phase`, `signal_bar_red` |

**Constants:** `LAUNCH_SCORE_MIN=50`, `LAUNCH_SCAN_MAX=55`, `EXTENSION_SCAN_MIN=65`, `EXTENSION_TREND_MIN=0.7`, `EXTENSION_SCAN_AUTO=75`

### 2. `tsd_entry_gates.py` (Lane B rewrite)

**REMOVED:** `scan_score >= 70` as primary gate

**ADDED:**
- `launch_score >= 50`
- `scan_score <= 55`
- `buy_signal OR early_bull`
- Reject `phase == EXTENSION` (`scan>=75` OR `scan>=65 AND trend>=0.7`)
- Keep: `wt_gap >= 3`, regime BULL, cross-book dedup

### 3. `tsd_watch_queue.json` schema

New fields per row: `launch_score`, `phase`, `signal_bar_red`, `early_bull`, `buy_signal`. `entry_score` now tracks `launch_score`.

### 4. `tsd_base_break.py` + trail monitor

- `detect_3h_base(bars, lookback=6)` — sideways base on prior 6 bars
- `base_break_exit` — close below `base_low`
- Wired into `tsd_trail_monitor.py` **before** structure stop (`reason=base_break_down`)

### 5. `setup_watch_agent.py`

- PM/extended: LAUNCH rows may confirm and enter outside RTH (kill backstop)
- Loop mode polls LAUNCH during PRE/POST sessions

### 6. `tsd_scan_ibkr.py`

- Accept `buy_signal OR early_bull`; reject `EXTENSION`
- Rank by `launch_score` desc, `scan_score` asc

---

## Backtest Sanity

### WEAV 8/31 — EXTENSION (would NOT queue today)

Source: `scan_20260831_1226.json` signal at 11:00 ET

| Field | Value |
|-------|-------|
| scan_score | 77.99 |
| trend_strength | 0.67 |
| buy_signal | true |
| **phase** | **EXTENSION** (score >= 75 auto) |
| launch_score | ~35 (buy +25, high-scan penalty -20) |

Old system: **entered** (score 78 >= 70). New system: **rejected** at scan + queue (`extension_phase`).

### ZIP 9/1 — LAUNCH (example early cross)

Source: `scan_20260901_1559.json`

| Field | Value |
|-------|-------|
| scan_score | 34.0 |
| trend_strength | 0.16 |
| buy_signal | true |
| **phase** | **LAUNCH** |
| launch_score | ~57.5 (buy +25, sweet-spot ~22.5, scan<=55 +10) |

Note: `wt_gap=1.03` still fails `wt_gap>=3` gate — phase classification is correct; gate fail is separate.

---

## Gate Changes Summary (Chat A)

| Gate | Old (Phase 1) | New (LAUNCH) |
|------|---------------|--------------|
| Primary score | `scan_score >= 70` | `launch_score >= 50` |
| Scan cap | none | `scan_score <= 55` |
| Trigger | `buy_signal` | `buy_signal OR early_bull` |
| Extension block | none | `phase != EXTENSION` |
| PM admission | queue OK, RTH executor | queue OK; **LAUNCH executor OK PM** |
| Rank key | `scan_score` desc | `launch_score` desc |

---

## Tests

```
tests/test_tsd_launch_score.py   — phase, WEAV/ZIP sanity, scoring
tests/test_tsd_base_break.py     — base detect + break exit
tests/test_tsd_entry_gates.py    — LAUNCH gates (updated)
tests/test_tsd_watch_queue.py    — queue schema + extension skip
```

Run: `py -3 -m unittest tests.test_tsd_launch_score tests.test_tsd_base_break tests.test_tsd_entry_gates tests.test_tsd_watch_queue -v`
