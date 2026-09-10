# Track 100 â€” Wave Cross trough study

**Generated:** 2026-09-10T11:19:27.793076-04:00
**Window (signals):** 2026-06-11 â†’ 2026-09-10
**Fetch warmup from:** 2026-04-27
**Universe:** 120 symbols | bars OK: 119
**Runtime:** 484.3s

## Thesis (from TSLA chart)

Long when Wave Cross (TSD) prints a **green buy/early-bull dot** at a
**deep oversold trough** (WT near/below âˆ’50), on 1H bars, universe =
Nasdaq-100 + liquid popular names. Long only.

## Setup ablation (default exit: âˆ’5% / +10% / 40Ã—1H bars, cost 0.15%)

| Setup | N | Win% | Avg net | Sum net | Avg MFE | Hit tgt | Hit stop | PF | Score |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `C_os53_green` Classic OSâ‰¤âˆ’53 + green | 2286 | 46.9% | 0.19% | 439.5% | 3.8% | 9% | 25% | 1.11 | 5.09 |
| `E_buy_cross_os` Buy cross only + OSâ‰¤âˆ’50 | 1596 | 46.1% | 0.09% | 148.6% | 3.8% | 9% | 26% | 1.05 | 4.92 |
| `B_os50_green` TSLA chart: OSâ‰¤âˆ’50 + green dot | 2727 | 45.8% | 0.10% | 273.4% | 3.8% | 9% | 26% | 1.06 | 4.92 |
| `F_early_os` Early-bull only + OSâ‰¤âˆ’50 | 1131 | 45.4% | 0.11% | 124.8% | 3.8% | 9% | 25% | 1.06 | 4.91 |
| `I_os_green_room` OSâ‰¤âˆ’50 + green + room to 20b high | 2678 | 45.7% | 0.10% | 255.5% | 3.8% | 9% | 26% | 1.05 | 4.91 |
| `D_trough_os_green` OSâ‰¤âˆ’50 + trough turn + green | 1793 | 45.3% | 0.06% | 111.0% | 3.7% | 9% | 25% | 1.04 | 4.84 |
| `A_green_any` Any green dot (buy|early_bull) | 12637 | 43.6% | -0.21% | -2676.2% | 3.6% | 7% | 27% | 0.89 | 4.41 |
| `H_os40_green_uptrend` OSâ‰¤âˆ’40 + green + SMA50 rising | 40 | 40.0% | 0.52% | 21.0% | 5.1% | 18% | 25% | 1.27 | 3.56 |
| `G_os_green_uptrend` OSâ‰¤âˆ’50 + green + SMA50 rising | 2 | 0.0% | -2.86% | -5.7% | 2.0% | 0% | 50% | 0.00 | -999.00 |

**Best setup by score:** `C_os53_green`

### Best setup detail

- Describe: Classic OSâ‰¤âˆ’53 + green
- N=2286 win_rate=46.9% expectancy=0.19% PF=1.11
- Top contributors: [('SOFI', 0.593731661803113), ('ZS', 0.41450036567795007), ('ARM', 0.400503158316005), ('ASML', 0.38002782361878984), ('DDOG', 0.3779542876483176), ('APP', 0.37308681457786036), ('KLAC', 0.3717684868023799), ('ADP', 0.3712262424840949), ('TMUS', 0.3586704664035694), ('AMAT', 0.3504485846442026)]
- Worst contributors: [('GEHC', -0.7120240644232345), ('NVDL', -0.6287606332572588), ('GFS', -0.548247027120257), ('QCOM', -0.40273971408212633), ('WDAY', -0.37254480846659943), ('MRVL', -0.36602898934574746), ('CTSH', -0.3432025318716197), ('MARA', -0.34161598334651966), ('CRWD', -0.31772594590207004), ('PDD', -0.3035979506332751)]

## Exit grid on best setup

**Best exit cell:** `stop6_tgt12_h40`

| Exit | N | Win% | Avg net | Sum net | PF | Score |
|---|---:|---:|---:|---:|---:|---:|
| `stop6_tgt12_h40` | 2286 | 48.5% | 0.34% | 767.5% | 1.19 | 5.36 |
| `stop5_tgt5_h40` | 2286 | 51.0% | 0.14% | 320.1% | 1.09 | 5.24 |
| `stop5_tgt10_h40` | 2286 | 46.9% | 0.19% | 439.5% | 1.11 | 5.09 |
| `stop5_tgt15_h48` | 2286 | 44.6% | 0.25% | 560.5% | 1.13 | 5.04 |
| `stop4_tgt8_h32` | 2286 | 46.5% | 0.11% | 240.4% | 1.07 | 4.96 |
| `stop4_tgt12_h40` | 2286 | 44.1% | 0.14% | 312.1% | 1.08 | 4.88 |
| `stop3_tgt6_h24` | 2286 | 44.0% | -0.03% | -61.7% | 0.98 | 4.66 |
| `stop3_tgt9_h40` | 2286 | 40.2% | 0.07% | 164.9% | 1.05 | 4.61 |

## Locked strategy (Track 100 v1)

```
UNIVERSE: Nasdaq-100 âˆª liquid popular (cap ~120)
TF: 1-hour Polygon aggs
ENTRY: Classic OSâ‰¤âˆ’53 + green
FILL: next bar open after signal close (causal)
STOP: 6.0%
TARGET: 12.0%
TIME: 40 Ã— 1H bars max
COST: 0.15% per round trip
SIDE: LONG ONLY
```

**Locked sample:** N=2286 | win=48.5% | avg_net=0.34% | PF=1.19

### VERDICT: WEAK PASS / NEEDS MORE
Positive but thin edge â€” extend window or tighten filters before live.

## Notes

- Isolated from Q-ALPHA live paper; no candidate pipeline edits.
- Red sell dots ignored for entries (long-only study).
- Same-bar stop-before-target conservative fill assumption.


---

## Update: see results_v2.md + STRATEGY.md

Track100_v2 locked on NDX100 early_bull + OS<=-53, stop 6% / target 12%.
Conditional PASS: +0.48% avg net, PF 1.30, N=762.
