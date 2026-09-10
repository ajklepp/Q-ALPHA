# Track 100 — Locked Strategy (v2)

**Status:** Isolated study only. Does **not** modify Q-ALPHA live paper.  
**Window measured:** 2026-06-11 → 2026-09-10 (3 months of 1H signals)  
**Data:** Polygon 1H aggs · cost 0.15% RT · fill = next bar open (causal)

---

## What the TSLA chart was showing

On TradingView **1H** with **3HR Trend Finder / Wave Cross (TSD)**:

1. Wave trough into deep oversold (**WT ≤ −53**)
2. **Green dot** = early-bull turn and/or buy cross
3. Circled TSLA dates (Aug 18, Aug 26) **reproduce** in this code as `OS≤−53 + green`

Raw green dots **without** deep OS **lose money** on this universe.

---

## Locked rules — Track100_v2

```
UNIVERSE : Nasdaq-100 only (no levered ETFs / index proxies)
TIMEFRAME: 1-hour bars
SIDE     : LONG ONLY

ENTRY    : early_bull AND min(wt1, wt2) <= -53
           (Pine early_bull: wt1 rising from trough while still below wt2)
FILL     : next 1H bar open after signal bar close

STOP     : 6.0% below entry
TARGET   : 12.0% above entry
TIME CAP : 40 x 1H bars (~1 week of sessions with EH bars)
COST     : 0.15% round-trip
```

### Study metrics (NDX100, N=762)

| Metric | Value |
|--------|------:|
| Win rate | 48.8% |
| Avg net / trade | **+0.48%** |
| Profit factor | **1.30** |
| Avg MFE | 3.9% |
| Hit 12% target | ~6% |
| Hit 6% stop | ~15% |

**Verdict:** CONDITIONAL PASS (study-level). Positive expectancy + PF≥1.3 on one 3-month window. Needs walk-forward / OOS before any live capital.

---

## Ablation lessons

| Variant | Result |
|---------|--------|
| Any green dot | **FAIL** (−0.21% avg) |
| OS≤−50 + green | Weak pass |
| OS≤−53 + green | Better |
| OS≤−53 + **early_bull only** on **NDX100** | **Best** |
| Liquid-120 + junk (NVDL/MARA/…) | Worse than NDX-only |
| Cooldown 6–12 bars | Slightly weaker than taking early turns |

Most trades never hit +12%; edge is a mix of small winners + occasional runners under a wide stop. Avg MFE ~4% means a **keep-profit / partial at +3–4%** is a natural v3 research step (not locked yet).

---

## How to re-run

```powershell
cd C:\Users\ajkle\Documents\Q-ALPHA
py -3 "Track 100\sanity_tsla.py"   # confirm TSLA circles
py -3 "Track 100\run_study.py"     # full ablation v1
py -3 "Track 100\refine_v2.py"     # NDX + exit grid
py -3 "Track 100\scan_live.py"     # print current NDX hits (read-only)
```

Artifacts: `Track 100/results/`
