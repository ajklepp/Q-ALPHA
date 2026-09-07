# EXP-0022 — Daily Markov Chain Equity Study

**Generated:** 2026-09-07T03:25:53.991341+00:00
**Runtime:** 17.4s (Modal)
**Recommendation:** **FAIL**

No daily Markov long-only rule beat always-long after 0.15% costs on temporal walk-forward with required accuracy lift. Do not wire into Peak Hour or launch a side agent from this run.

## Design

`{"start": "2019-01-01", "end": "2026-09-05", "train_days": 504, "test_days": 63, "cost": 0.0015, "universe_n": 83, "ok_n": 83}`

## Coverage

- OK symbols: 83
- Failed: `[]`

## Bakeoff (walk-forward, after costs)

| Variant | n | Sharpe M | Sharpe Always | Ret M | Ret Always | Acc | Chance | Beat Always % | Gate | Ship |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| s3_o1_uncond | 83 | -0.43 | 0.58 | -0.413 | 1.105 | 34.3% | 33.3% | 0% | no | no |
| s3_o1_uncond_plus | 83 | -0.14 | 0.58 | -0.041 | 1.105 | 34.3% | 33.3% | 5% | no | no |
| s3_o1_fixed | 83 | -0.28 | 0.58 | -0.115 | 1.105 | 34.3% | 33.3% | 4% | no | no |
| s3_o2_uncond | 83 | -0.35 | 0.58 | -0.312 | 1.105 | 34.4% | 33.3% | 0% | no | no |
| s5_o1_uncond | 83 | -0.36 | 0.58 | -0.343 | 1.105 | 20.6% | 20.0% | 0% | no | no |
| s5_o1_uncond_plus | 83 | -0.21 | 0.58 | -0.085 | 1.105 | 20.6% | 20.0% | 1% | no | no |
| s5_o2_uncond | 83 | -0.38 | 0.58 | -0.370 | 1.105 | 20.5% | 20.0% | 0% | no | no |

## Best variant

- `s3_o1_uncond_plus`
- Gate: **False** · Ship: **False**
- Mean Sharpe Markov -0.139 vs always-long 0.576
- Acc 0.3434 vs chance 0.3333

### Top beaters (vs always-long)

| Symbol | Sharpe M | Sharpe A | Acc | Pass frac |
|---|---:|---:|---:|---:|
| DIS | 0.30 | -0.25 | 34.1% | 0% |
| UNH | 0.68 | 0.28 | 36.8% | 0% |
| SQ | 0.27 | -0.04 | 32.5% | 6% |
| CMCSA | -0.16 | -0.40 | 35.1% | 5% |
| PFE | 0.01 | -0.17 | 33.5% | 5% |
| ABNB | 0.59 | 0.47 | 31.9% | 7% |
| PEP | 0.00 | 0.02 | 33.5% | 0% |
| TXN | 0.46 | 0.49 | 35.8% | 9% |

## Diagnostics (in-sample TPM — descriptive only)

- SPY 3-state TPM: `[[0.30275229357798167, 0.26605504587155965, 0.43119266055045874], [0.2826086956521739, 0.34316770186335405, 0.37422360248447206], [0.2679296346414073, 0.3761840324763194, 0.35588633288227334]]` (rows=Down/Flat/Up → cols)
- SPY flat band: `[-0.003362192149281351, 0.003362192149281351]`
- Median stock Up→Up persistence: `0.34310850439882695`

## Implications

- **1H PHP lookback:** If Up→Up persistence >> uncond, use as soft prior weight on 1H continuation rank — only after a PROMISING/WEAK confirmed variant; never hard filter from FAIL.
- **Side agent:** A separate daily Markov sleeve is only justified on PROMISING. Keep capital and kill logic isolated from Peak Hour.

## Notes

- States/edges fit on train window only — no look-ahead into test.
- Decision at day-t close → earn day t+1 return; cost charged each long day.
- Long-only; no short entries.
- Standalone from TSD / Peak Hour codebase.
