# EXP-0022 — Daily Markov Chain Stock Study (standalone)

Research only. **Does not modify** Peak Hour / TSD live code.

## Question
On Polygon daily bars, do discrete return-state Markov transition probabilities
yield a long-only edge that survives temporal walk-forward **after costs**?

## Why daily
Literature + estimation tradeoff: daily is the best primary horizon for tradable
equity state chains (sample size, stationarity, costs). Tick/1m is microstructure
description; monthly HMM is regime allocation — different product.

## Design
- Universe: liquid US equities + SPY (hardcoded liquid list)
- Bars: Polygon adjusted daily OHLCV, 2019-01-01 → present
- States: 3-bin (Down / Flat / Up) and 5-bin (quintiles of **train-only** returns)
- Model: order-1 TPM; bakeoff order-2 where counts allow
- Decision (long-only): enter next day when `P(Up | state_t) ≥ τ` vs unconditional
- Cost: `COST_PER_TRADE = 0.0015` per round-trip day in market
- Walk-forward: rolling train 504 trading days → test 63 days (forward only)
- Baselines: always-long, SPY buy-and-hold, random-threshold scramble

## Success gates (all required)
- Beat always-long **and** SPY B&H on test Sharpe after costs
- Positive test return
- State-prediction accuracy materially > chance (3-state > 0.38; 5-state > 0.24)
- Edge not concentrated in a single WF window (pass ≥ 2/3 windows)

## Downstream use (if anything survives)
1. **Lookback weight for 1H PHP** — daily “Up-persistence” as soft prior
2. **Side agent** — separate daily Markov sleeve (not fused into Peak Hour)

## Fail loudly
If gates fail, label **FAIL** and do not propose live wiring.
