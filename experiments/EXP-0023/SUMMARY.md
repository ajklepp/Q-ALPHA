# EXP-0023 — High-math probability edge hunt (master)

**Final recommendation: PROMISING (risk edge only)** — `ew_hmm_bull`

Do **not** auto-wire Peak Hour. Research-only side-quest result.

## What we tested

### Round 1 — Time-series timing (25 families)
HMM bull/low-vol, Bayes P(up), CUSUM, Kalman, MI-lag, EVT skip, momentum,
SMA, vol filters, z-MR, conditional bounce/continue, RS vs SPY, entropy…

**Result: FAIL.** Nothing beat always-long after flip costs. Closest ≈ always-long.

### Round 2 — Cross-section + SPY regime
Top-quintile mom/RS/MR/low-vol vs equal-weight universe; SPY HMM overlay.

**Result: FAIL on strict gates**, but clear near-misses:
- `ew_spy_bull`: Sharpe +0.22, MDD −17.5% vs −29.8%, return ~83% of EW
- `cs_mom60`: higher return/Sharpe but worse MDD and unstable windows

### Round 3 — Stress-test near-winners
Separated **RISK** vs **GROWTH** gates; monthly rebalance; SMA200 robustness.

**Result: PROMISING — `ew_hmm_bull` clears RISK gate.**

| Metric | EW universe | EW × SPY HMM bull |
|---|---:|---:|
| Sharpe | 1.01 | **1.23** (+0.22) |
| Max DD | −29.8% | **−17.5%** |
| Total ret (pooled WF) | 1.13 | 0.94 (~83% of EW) |
| % days invested | 100% | ~70% |

Growth-style cross-sectional momentum did **not** clear ship gates (drawdown /
window instability).

## What “true edge” means here
Not a next-day direction oracle. A **regime risk overlay**:
stay equal-weight long only when a 2-state Gaussian HMM on SPY returns
assigns ≥55% probability to the higher-mean state (fit on prior 504 days only).

## Implications for Q-Alpha
1. **Side sleeve:** possible daily EW (or PHP pool) risk-on/off from SPY HMM — isolated capital.
2. **Peak Hour:** optional *soft size weight* from SPY bull probability — **not** an entry filter; needs a separate PHP bakeoff before any live change.
3. **Do not** revisit plain daily Up/Flat/Down Markov for direction (EXP-0022 + R1 killed it).

## Honesty note
RISK gate thresholds were set in Round 3 after observing Round-2 near-misses
(ret≥80% EW, MDD +5pp). The economic story is coherent; still requires a
frozen-gate replication on a later sample before production.

## Artifacts
- `study_prob_edge_hunt_modal.py` + `results.md` (R1)
- `study_prob_edge_round2_modal.py` + `results_round2.md`
- `study_prob_edge_round3_modal.py` + `results_round3.md`
- metrics JSON beside each

## Live code
**Unchanged.** No Peak Hour / TSD edits from this hunt.
