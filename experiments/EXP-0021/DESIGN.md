# EXP-0021 Design — Continuation Ranker

**Status:** Full-HTF bakeoff **PASS** — **live shipped** (hourly :15 through 15:15, 2 slots, continuation_score_v1)  
**App / path:** `experiments/EXP-0021/` + live wire in `candidates/tsd_scan_pipeline/`

## Problem

Peak Hour 1H WT buy at 07/11/12/13 misses most same-day expanders (Chat A: 55% of top-40 Sep-4 expanders had **no** peak-hour trigger). IREN-class names bounce daily support and run while signals fire off-hour or get blocked by `scan≤55`.

## Locked architecture

1. **List:** all RTH completed 1H bars with `buy_signal OR early_bull` (+ HTF/price floors).  
2. **Peak hour:** score bonus only.  
3. **Rank:** continuation features (see `FEATURES.md`).  
4. **Cap:** top 2 per scan slot (research simulates hourly :15).  
5. **EXTENSION:** soft penalty first (`scan>55`), not hard block in challenger.

## Modules

| File | Role |
|---|---|
| `FEATURES.md` | Column dictionary |
| `lib/features.py` | Causal feature pack A+B |
| `lib/social.py` | StockTwits + Polygon news velocity + X (optional) |
| `lib/corpus.py` | Build labeled signal rows from Polygon |
| `experiment21.py` | Bakeoff Peak Hour v0 vs all-hours + score |
| `results.md` | PASS/FAIL report |

## Labels

Path-first after 1H close entry, kill=5%:

- `hit_1r` if high reaches +5% before low −5%  
- `mfe`, `mfe_4`, `mfe_rest_day`

## Success (all required)

- Expander capture (top-decile day OH among HTF) higher than Peak Hour v0  
- Expectancy of taken slots not worse than v0 by &gt;20% relative  
- No Phase-2.5-style WR collapse (&lt;35% on challenger slots)  
- Else label **FAIL** — do not promote to live
