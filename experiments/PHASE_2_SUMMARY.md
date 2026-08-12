# Phase 2 Complete — Executive Summary

**Verdict: Phase 2 INCONCLUSIVE for production. Simulation edge is real; trade selection is not.**

Five experiments (EXP-0013 → EXP-0017) tested one arc: fix labeling → add gap-day features → expand universe → replace ML with rules. **Simulation Sharpe stayed strong (1.1–1.9)** regardless of selector quality. **No experiment passed all model/selection gates.** Nothing should go to `/candidates` without explicit approval.

---

## Full Progression Table

| Metric | EXP-0012 (baseline) | EXP-0013 | EXP-0014 | EXP-0015 | EXP-0016 | EXP-0017 |
|--------|---------------------|----------|----------|----------|----------|----------|
| **Selector** | Random Forest | LightGBM | LightGBM | LightGBM | LightGBM | **Rules** |
| **Universe** | Large-cap | ~120 SMID | ~120 SMID | ~120 SMID | **300** | **300** |
| **Sharpe** | **1.64** | 0.99→~1.42* | 1.10 | **1.76** | 1.56 | **1.87** |
| **Max DD** | -3.17% | -6.08% | -8.87% | -4.31% | -6.10% | -5.52% |
| **Return (OOS)** | +15.6% | +23.5%* | +16.5% | +35.9% | +33.1% | +27.2% |
| **Trades** | 83 | 28–99 | 85 | 52 | 86 | 103 |
| **Win Rate** | 39.8% | 44.4% | 38.8% | 53.9% | 44.2% | 43.7% |
| **Base Rate** | — | 8%→8%* | **39.1%** | 25.1% | 22.0% | 22.0% |
| **Precision** | — | ~11% | 37.3% | 38.5% | 16.4% | 22.8% |
| **Lift** | — | ~1.35x | 1.15x | **1.40x** | 0.74x | **1.04x / 0.99x OOS** |
| **Walk-Fwd** | 3/4 | 2/4 | 2/4 | **3/4** | **3/4** | 2/4 |
| **Phase verdict** | PASS (partial) | FAIL | INVESTIGATE | INVESTIGATE | FAIL | FAIL |

\*EXP-0013: Run 1 failed; Run 2 fixed screener/sim bugs (~Sharpe 1.42) but `results.md` still shows Run 1 prominently.

---

## What Phase 2 Proved (in order of discovery)

### 1. Labeling was the first bug (EXP-0013 → EXP-0014)
- Labeling **all days** gave **8% base rate** — broken target definition in practice.
- Filtering **gap ≥3% + vol ≥2x before labeling** restored **~39% base rate** (EXP-0014).
- **Confirmed:** Option D works on catalyst days; the problem was *which rows* got labeled.

### 2. Pre-gap ML has weak ranking signal (EXP-0014)
- Precision **37% vs 32% base** — modest lift (1.15x), below gates.
- Simulation Sharpe **1.10** anyway → **bracket + filter** may carry more edge than the classifier.

### 3. Gap-day features help the model, not necessarily P&L (EXP-0015)
- Next-day-open entry dropped base rate to **25%** but validation lift hit **1.40x** (model beats base).
- Best sim Sharpe in chain at the time: **1.76** (52 trades — thin sample).
- `body_ratio_d0` ranked highly — gap-day close structure matters for ranking.

### 4. Universe expansion breaks ML, not simulation (EXP-0016)
- **300 tickers**, MOC entry, base rate **22%** (cleaner label math).
- LightGBM lift **collapsed to 0.74x** — model **hurts** vs random on expanded universe.
- Sim still strong: Sharpe **1.56**, 86 trades, WF **3/4**.

### 5. Rules scorecard doesn't fix selection (EXP-0017)
- Transparent 6-rule card: lift **1.04x val / 0.99x OOS**.
- **Monotonicity inverted** — higher scores → *lower* win rate.
- Sim Sharpe **1.87** (best in chain) with **no** selection edge.

---

## Stable Conclusions (high confidence)

| Finding | Evidence |
|---------|----------|
| **Catalyst filter has edge** | gap≥3% + vol≥2x → sim Sharpe 1.1–1.9 across 5 exps |
| **BracketPosition + SPY regime work** | Consistent positive OOS returns; bear windows still fragile |
| **ML/rules selection unproven at scale** | Lift fails on 300-ticker universe; precision near base rate |
| **Entry/label alignment matters** | Base rate swings 22%–39% depending on entry price + ATR timing |
| **VIX filter untested** | `I:VIX` never loaded; SPY proxy never hit ELEVATED/EXTREME |
| **2022 / 2024 are failure modes** | Bear/chop windows fail repeatedly in walk-forward |

---

## What Did NOT Work

- LightGBM precision gates (never cleared 42%+ on 300 names)
- Threshold calibration on expanded universe
- Rules scorecard using "strong candle" heuristics (inverted monotonicity)
- Next-day-open entry for momentum (misses gap-day move)
- Promoting any EXP-0013–17 config to production

---

## Recommended System Architecture (honest)

```
Layer 1 — Catalyst gate     ✅ PROVEN   gap≥3% + vol≥2x
Layer 2 — Trade selector    ❌ NOT PROVEN   (ML, rules, thresholds all failed OOS lift)
Layer 3 — Entry             ⚠️  MOC Close workable; label must match sim exactly
Layer 4 — Bracket stop      ✅ PROVEN   (EXP-0012 sacred baseline)
Layer 5 — Regime sizing     ✅ PARTIAL   SPY SMA50 works; VIX needs real data
```

**Best sim config in Phase 2:** EXP-0017 mechanics (300 tickers, MOC, catalyst filter, bracket, SPY regime) **without trusting the scorecard for ranking** — or ablation: **catalyst-only, no selector**.

---

## Phase 2 Final Verdict

| Criterion | Result |
|-----------|--------|
| Beat EXP-0012 Sharpe (1.64) in simulation? | **Yes** (EXP-0015/16/17) |
| Prove predictable 2R hit rate above base? | **No** |
| Walk-forward ≥3/4 consistently? | **No** (2–3/4 depending on exp) |
| Ready for `/candidates`? | **No** |
| Ready for paper trading as catalyst+bracket system? | **Investigate** — only after catalyst-only ablation |

---

## Suggested Phase 3 (if you want to continue)

1. **EXP-0018 ablation:** catalyst filter only, no ML/rules threshold — isolate bracket edge  
2. **Label reconciliation:** single canonical entry (MOC Close + ATR_d0) across label and sim  
3. **Fix I:VIX** on Polygon — actually test regime sizing  
4. **Do not add features** until lift > 1.1x OOS on 300 names  
