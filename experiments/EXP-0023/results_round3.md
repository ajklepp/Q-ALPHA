# EXP-0023 Round-3 — Near-winner stress test

**Generated:** 2026-09-07T03:59:38.516117+00:00
**Runtime:** 13.2s
**Recommendation:** **PROMISING**

Ship candidates under RISK/GROWTH gates: ['ew_hmm_bull']. Research-only — candidate side sleeve or PHP risk overlay, not auto-wired.

**Winners:** `['ew_hmm_bull']`

Gates: `{'risk': 'sharpe>+0.15, MDD better by >=5pp, ret>=80% EW, sharpe-win windows>=35%', 'growth': 'sharpe>+0.15, ret>EW, both-win windows>=30%, MDD not >8pp worse'}`

| Strategy | Sharpe | Edge | Ret | MDD | Inv% | Risk | Growth | Ship |
|---|---:|---:|---:|---:|---:|---|---|---|
| cs_mom60_sma200 | 1.29 | +0.28 | 1.681 | -27.7% | 79% | no | no | no |
| cs_mom60_hmm | 1.25 | +0.24 | 1.499 | -21.0% | 70% | no | no | no |
| ew_hmm_bull | 1.23 | +0.22 | 0.940 | -17.5% | 70% | YES | no | YES |
| cs_mom60_d | 1.21 | +0.20 | 1.790 | -38.5% | 100% | no | no | no |
| cs_mom60_m21_hmm | 1.11 | +0.10 | 1.153 | -20.2% | 56% | no | no | no |
| ew_sma200 | 1.07 | +0.06 | 0.834 | -25.0% | 79% | no | no | no |
| cs_mom60_m21 | 1.02 | +0.01 | 1.507 | -35.6% | 100% | no | no | no |
| ew | 1.01 | +0.00 | 1.133 | -29.8% | 100% | no | no | no |
| cs_mom20_m21 | 0.86 | -0.14 | 1.242 | -46.4% | 100% | no | no | no |

## Notes

- R1 time-series FAIL; R2 found near-misses; R3 stress-tests them.
- Long-only. Standalone research.
