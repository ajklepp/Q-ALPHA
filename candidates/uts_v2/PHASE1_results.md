# UTS v2 Phase 1 — Stop the Bleeding

**Commit baseline:** 3f80419+  
**Date:** 2026-09-01  
**Status:** SHIPPED (code + tests)

## Problem

TSD live entries bypassed research and entry gates. Eight closed trades, zero winners. Structure stops (3f80419) were live but entries fired on 3H scan without RTH window, regime, or cross-book checks.

## Changes

| Component | Change |
|-----------|--------|
| `tsd_scan_ibkr.py` | `--live` → `add_to_watch_queue()` (no direct IBKR entries) |
| `tsd_watch_queue.py` | New queue state `candidates/tsd_watch_queue.json` |
| `tsd_entry_gates.py` | Five-gate primitives (score, wt_gap, regime, dedup, RTH window) |
| `tsd_structure.py` | Day-2 tighten skipped if leg opened &lt; 1 trading day ago |
| Hunt list | Cross-book dedup vs `paper_trades.json` + TSD book |

## Entry Gates (Phase 1)

| Gate | Threshold | Enforced at |
|------|-----------|-------------|
| scan_score | ≥ 70 | Queue admission |
| wt_gap | ≥ 3 | Queue admission |
| Regime | SPY ≥ SMA50 (BULL) | Queue admission |
| Cross-book dedup | Not in TSD or gap open book | Queue admission + hunt list |
| RTH window | 09:35–14:00 ET | Recorded; executor in Phase 3 |

## Architecture

```
3H scan → rank → profiler → add_to_watch_queue (WATCHING)
                              ↓ (Phase 3)
                    setup_watch_agent → execute_live_entries
```

## Tests

```
py -3 -m unittest discover -s tests -p "test_tsd_entry_gates.py" -v
py -3 -m unittest discover -s tests -p "test_tsd_watch_queue.py" -v
py -3 -m unittest discover -s tests -p "test_tsd_structure.py" -v
```

## Metrics (pre-Phase 1 baseline)

| Metric | Value |
|--------|-------|
| Closed TSD trades | 8 |
| Win rate | 0% |
| Direct scan entries | Disabled |
| Queue-only live mode | Enabled |

## Next (Phase 2) — SHIPPED

See `candidates/uts_v2/PHASE2_results.md`. **`quality_history_gate.py`** — profiler analog depth/win rate + liquidity floors. News/sentiment are **context tags only**, never entry vetoes.

## Reply for Chat A

**Phase 1 shipped:** Scan `--live` no longer places orders; profiler-pass picks go to `tsd_watch_queue.json` after gates (score≥70, wt_gap≥3, BULL regime, cross-book dedup). Day-2 structure tighten will not fire on the first RTH session after entry (fixes MAGN/BMNR instant exit pattern). `execute_live_entries` preserved in `tsd_watch_queue.py` for Phase 3 setup_watch_agent.

**Blockers:** None for Phase 1 deploy. Restart trail monitor not required; next `--live` scan will queue only.

**Action for Aaron:** Confirm gap `paper_trades.json` open symbols are excluded from hunt list. Paper trade until Phase 5 validation (20 trades, WR≥45%).
