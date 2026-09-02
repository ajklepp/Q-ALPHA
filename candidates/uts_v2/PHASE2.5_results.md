# UTS v2 Phase 2.5 — Kill-until-1R (Chat A validated)

**Shipped:** `8f41cd6` + parity pass

## Problem
ORB structure arming at RTH + day-2 tighten to `entry×0.99` stopped launches before they worked (~29% WR).

## Chat A backtest (Sep 2024–Sep 2026, $1k, 2 slots)
- END $2,760 | WR 68.3% | 41 trades | CAGR ~66% | beats SPY $1,380

## Exit (P0) — `tsd_structure.py`, `tsd_trail_monitor.py`
| Layer | Rule |
|-------|------|
| L1 Kill | Broker kill at `entry×(1-kill_pct)` — never cancelled |
| L2 Structure | **No ORB arm.** `structure_stop=None` until high ≥ +1R; then BE lock `entry×0.997` |
| L3 Trail | strategy_a 4-tranche (unchanged) |
| L4 Thesis | Day **5** force exit if no tranche trailing |

**Removed:** day-2 `entry×0.99` tighten, ORB bootstrap structure

## Entry (P1) — `tsd_entry_gates.py`, `tsd_htf_gates.py`, `tsd_1h_signal.py`
1. **3H** `buy_signal` + **1H** `buy_signal` + `is_launch_candidate` + red signal bar
2. No `scan_score>=60` floor in launch lane (profiler analogs aligned)
3. Phase ≠ EXTENSION
4. No entries at/after **15:00 ET**
5. HTF daily: 20d range ≥25%, close > SMA50, SMA20 rising
6. Rank by `combined_rank_score` = launch + HTF; max **2**/day, **2** slots
7. SPY regime = dashboard only
8. News never vetoes; analog WR gate demoted

## Disabled
- Setup confirmation bar (direct `htf_launch_direct`)
- SPY SMA50 entry veto
- Analog WR hard block
- Extended-hours LAUNCH bypass of RTH window

## Paper gate (P2)
```bash
python candidates/uts_v2/paper_gate.py
```
Target: 20 trades, WR ≥ 45% before sizing up.

## Tests
- `tests/test_tsd_structure.py` — kill-until-1R, day2 disabled
- `tests/test_tsd_entry_gates.py` — HTF gates, 15:00 cutoff
- `tests/test_tsd_htf_gates.py`

## Dashboard
- Kill $, Structure $ or **"KILL ONLY until +1R"**, trail levels
- `+1R` milestone on progress bar when not yet locked
