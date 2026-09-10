# Track 100 v2 — refined strategy lock

Window: 2026-06-11 -> 2026-09-10
Excluded levered/index: ['CONL', 'IWM', 'NVDL', 'QQQ', 'SOXL', 'SPY', 'TQQQ', 'TSLL']

## Top 10 variants

| Tag | N | Win% | Avg net | PF | QScore |
|---|---:|---:|---:|---:|---:|
| `NDX100|C_early|cd1|s60_t120_h40` | 762 | 48.8% | 0.48% | 1.30 | 10.44 |
| `NDX100|C_os53_green|cd1|s60_t120_h40` | 1807 | 49.4% | 0.44% | 1.27 | 10.38 |
| `NDX100|C_not_ext|cd1|s60_t120_h40` | 1761 | 49.3% | 0.44% | 1.27 | 10.36 |
| `NDX100|C_buy|cd1|s60_t120_h40` | 1045 | 49.9% | 0.42% | 1.25 | 10.33 |
| `NDX100|C_os53_green|cd8|s60_t120_h40` | 959 | 48.5% | 0.41% | 1.25 | 10.16 |
| `NDX100|C_early|cd6|s60_t120_h40` | 695 | 48.2% | 0.41% | 1.25 | 10.14 |
| `LIQUID120|C_buy|cd1|s60_t120_h40` | 1242 | 49.1% | 0.37% | 1.22 | 10.09 |
| `NDX100|C_os53_green|cd6|s60_t120_h40` | 1028 | 48.2% | 0.39% | 1.24 | 10.07 |
| `NDX100|C_not_ext|cd8|s60_t120_h40` | 937 | 48.1% | 0.39% | 1.24 | 10.07 |
| `LIQUID120|C_not_ext|cd1|s60_t120_h40` | 2092 | 48.7% | 0.38% | 1.22 | 10.05 |

## Locked Track100_v2

```
UNIVERSE: NDX100 (ex levered ETFs)
ENTRY: OS<=-53 + early only
COOLDOWN: 1 x 1H bars between signals (same symbol)
FILL: next bar open
STOP: 6.00%
TARGET: 12.00%
TIME CAP: 40 x 1H
COST: 0.15% RT
SIDE: LONG ONLY
```

N=762 win=48.8% avg_net=0.48% PF=1.30 avg_mfe=3.9%

### VERDICT: CONDITIONAL PASS
Positive edge after refine; still paper-only / needs walk-forward.

## What the TSLA chart got right

- Deep OS (<= -53) + green dot beats raw green dots (which lose money).
- Clustered greens without cooldown overtrade the same trough.
- Avg MFE ~4% argues for targets near 4-6%, not 10-12% first take.

## Not live

This folder does not modify Q-ALPHA Peak Hour / TSD live paper.
