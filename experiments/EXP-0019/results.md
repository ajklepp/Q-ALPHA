# EXP-0019 Results — Capacity Ablation (Threshold vs Rank vs Full 0017)

**Status:** NOT RUN

**Hypothesis:** EXP-0017’s high sim Sharpe (~1.87) vs EXP-0018’s collapse (~0.86) is explained by **capacity allocation** (who fills limited slots), not Option-D lift. Ablate the EXP-0017 ScoreCard into two mechanisms on an otherwise identical stack.

**Null / stop rule:** If ARM C (FULL_0017) does not approximately reproduce published EXP-0017 OOS Sharpe ~1.87 / ~103 trades → **implementation drift** — stop and diff vs `experiment17.py` before new ideas.

---

## Arms (one Modal download, three sims)

| Arm | Name | Threshold (`score_pts ≥ 5`) | Capacity tiebreak |
|-----|------|-----------------------------|-------------------|
| **A** | `THRESHOLD_ONLY` | ON | **Ticker A–Z** (not score rank) |
| **B** | `RANK_ONLY` | OFF (all catalyst days) | **`score_pts` descending** (ScoreCard verbatim) |
| **C** | `FULL_0017` | ON | **`score_candidate` composite score** desc (exact 0017 policy) |

External baseline (not re-run): **EXP-0018** Sharpe 0.864 / 93 trades / +13.41% / WF 2/4 / MC p=0.504.

Control target: **EXP-0017** Sharpe 1.87 / 103 trades / +27.20% / WF 2/4.  
Drift band: Sharpe ±0.25 of 1.87; trades ±25 of 103.

---

## ScoreCard cutoff / formula (citation: EXP-0017)

Copied verbatim from `experiments/EXP-0017/experiment17.py`:

- **Cutoff:** `MIN_SCORE = 5`, `SCORECARD_MAX = 11` → gate is `score_pts ≥ 5` (normalized `prob ≥ 5/11 ≈ 0.4545`).
- **Rules (max 11 pts):**
  1. `body_ratio_d0` > 0.60 → 3; > 0.40 → 1
  2. `close_vs_range_d0` > 0.75 → 2; > 0.55 → 1
  3. `volume_ratio_d0` > 4.0 → 2; > 2.5 → 1
  4. `gap_pct_d0` in [3%, 8%] → 2; (8%, 12%] → 1
  5. `bbw_percentile_52w` < 20 → 1
  6. `rs_vs_spy_20d` > 2% → 1
- **ARM C rank note:** EXP-0017 sorts by `score_candidate(row, prob)` (prob buckets + RSI/MACD/vol/BB/profile adj), **not** raw `score_pts`. ARM B isolates raw ScoreCard points; ARM C matches published 0017 policy.

---

## Shared stack (identical to 0017/0018)

- Sacred: `BracketPosition`, `classify_profile()`, `get_regime()` from EXP-0012 (verbatim)
- Universe: EXP-0016/0017 `build_dynamic_universe` (~300 tickers, screen 2024-01-03)
- Catalyst: gap ≥ 3% AND vol_ratio ≥ 2×
- Entry/label: `MOC_CLOSE_ATR_D0` (Option D)
- Costs: `COST_PER_TRADE = 0.0015`
- WF: 4 windows; MC: 5000
- Modal app: `q-alpha-exp019`

---

## Success interpretation (fill after run)

| Pattern | Meaning |
|---------|---------|
| ARM B ≈ 0017 **and** ARM A ≈ 0018 | Edge is **ranking/tiebreak** under capacity |
| ARM A ≈ 0017 **and** ARM B ≈ 0018 | Edge is **threshold filter** |
| ARM C not ≈ 0017 | **Implementation drift** — stop; diff `experiment17.py` |
| Mixed | Report clearly; do not overclaim |

Hard gates reported per arm (FAIL loudly): Sharpe ≥ 1.5, DD ≥ −0.15, +return, beats B&H, WF ≥ 3/4, MC p &lt; 0.05. Precision@0.60 = N/A.

---

## Results tables

*(empty until Modal run)*

### Per-arm OOS (2023–2024)

| Arm | Return | Sharpe | Max DD | Trades | WF | MC p | Gates | Verdict |
|-----|--------|--------|--------|--------|----|------|-------|---------|
| A THRESHOLD_ONLY | — | — | — | — | — | — | — | — |
| B RANK_ONLY | — | — | — | — | — | — | — | — |
| C FULL_0017 | — | — | — | — | — | — | — | — |
| Baseline 0018 | +13.41% | 0.864 | −5.36% | 93 | 2/4 | 0.504 | 2/6 | FAIL |
| Baseline 0017 | +27.20% | 1.87 | −5.52% | 103 | 2/4 | — | — | FAIL (lift) |

**Ablation code:** NOT RUN  
**Promotion:** Do not promote to `/candidates`.

---

## How to run (awaiting approval)

```powershell
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'
.\venv\Scripts\modal.exe run experiments/EXP-0019/experiment19.py
```
