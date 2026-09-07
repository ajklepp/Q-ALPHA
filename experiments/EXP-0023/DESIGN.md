# EXP-0023 — High-math probability edge hunt (standalone)

Research only. **Does not modify** Peak Hour / TSD live code.

## Mission
Run a wide battery of probability / stochastic-signal families on Polygon
daily bars. Keep only rules that beat **always-long after costs** on temporal
walk-forward. Fail loudly if nothing clears gates.

## Cost model
`COST_PER_TRADE = 0.0015` charged on **position flips** (enter/exit), not every
long day. Always-long pays one entry cost at the start of each test window.

## Families under test
1. Momentum / trend (baselines)
2. Vol filter / EVT skip
3. Conditional Markov (bounce after shock, streak)
4. 2-state Gaussian HMM (bull vs bear; low-vol vs high-vol)
5. Bayesian P(up) shrinkage
6. Z-score mean reversion
7. RS vs SPY
8. Rolling entropy + trend
9. CUSUM regime
10. Kalman local trend
11. Train-only mutual-information lag pick
12. Ensemble of train-passing rules

## Gates (all required for PROMISING)
- Mean test Sharpe > always-long + 0.15
- Mean test return > always-long
- ≥55% of names beat always-long Sharpe
- Non-vacuous: mean position rate in [0.15, 0.95]
- Pass ≥ half of walk-forward windows on average
