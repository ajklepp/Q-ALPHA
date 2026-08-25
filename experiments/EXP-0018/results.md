# EXP-0018 Results — Catalyst-Filter-Only Ablation (NO Selector)

**Status: FAIL (2/6 hard gates)**

**Run date:** 2026-08-24  
**Modal run:** https://modal.com/apps/ajklepp/main/ap-wklxcbLEboV3n1f4o8nyMN  
**Runtime:** 327s  
**App:** `q-alpha-exp018`

---

## Hypothesis
On the Phase-2 SMID/catalyst universe, the bracket + catalyst gate alone (`gap ≥ 3%` AND `vol_ratio ≥ 2×`) produces the sim Sharpe. ML (EXP-0016) and rules scorecard (EXP-0017) add no reliable OOS lift. EXP-0018 removes the selector entirely.

**Null:** Catalyst-only trading fails ≥1 of the hard gates below.

**Result: hypothesis REJECTED.** Removing the selector collapsed OOS Sharpe from EXP-0017’s **1.87** to **0.86**, failed buy-and-hold, failed walk-forward (≥3/4), and Monte Carlo is indistinguishable from random (p=0.504). Do **not** promote to `/candidates`.

---

## Deliberately omitted (vs EXP-0017 / Phase-2)

| Component | EXP-0017 | EXP-0018 |
|-----------|----------|----------|
| Selector | 6-rule ScoreCard (min 5/11 pts) | **NONE** — every catalyst day is a candidate |
| LightGBM | Already removed in 0017 | Still none |
| Precision@0.60 | Scorecard precision / lift gates | **N/A** — trade-count + base-rate transparency |
| Capacity tiebreak | Score / pts | Deterministic **ticker A–Z** only |

Sacred stack unchanged: `BracketPosition`, `classify_profile()`, `get_regime()` (verbatim EXP-0012), Option D, `COST_PER_TRADE = 0.0015`, temporal WF (4), MC 5000.

---

## Settings (as run)

- **Universe source:** EXP-0016/0017 `build_dynamic_universe` — screen `2024-01-03`, mcap &lt; $20B, price $3–$500, avg vol ≥ 300k (tightened at runtime to load **300** tickers)
- **Catalyst filter:** `gap_pct ≥ 0.03` AND `volume_ratio_20d ≥ 2.0`
- **Entry / label:** `MOC_CLOSE_ATR_D0` — Day-0 Close (MOC); stop = entry − 1×ATR_d0; target = entry + 2×ATR_d0; Option D on days +1…+5
- **Selector:** NONE
- **Validate (base-rate window):** 2022 (382 candidate rows)
- **OOS sim:** 2023–2024 (1,054 candidate rows)
- **VIX:** I:VIX unavailable → SPY vol proxy (same fallback pattern as Phase-2)

---

## Success gates (ALL reported — FAIL loudly)

| Gate | Threshold | Actual | Result |
|------|-----------|--------|--------|
| Sharpe | ≥ 1.50 | **0.864** | **FAIL** |
| Max DD | ≥ −0.15 | −5.36% | **PASS** |
| Positive return | yes | **+13.41%** | **PASS** |
| Beats buy-and-hold | yes | +13.41% vs B&H **+81.16%** | **FAIL** |
| Walk-forward | ≥ 3/4 | **2/4** | **FAIL** |
| Monte Carlo | p &lt; 0.05 | **p = 0.504** | **FAIL** |
| Precision@0.60 | N/A (no model) | — | N/A |

**Hard gates: 2/6 PASS → FAIL**

Info-only: base rate 22.0% (in 18–28% band); trades 93 (&gt; 60).

---

## Trade count + base-rate transparency

| Metric | Value |
|--------|-------|
| Total daily rows downloaded | 434,763 |
| Candidate rows (catalyst filter) | **2,761** |
| After feature dropna | 2,761 |
| Candidate rate | 0.64% |
| Option D positives | **607** |
| Base rate (all candidates) | **22.0%** |
| Validate rows (2022) | 382 |
| Test rows (2023–2024) | 1,054 |
| OOS trades taken (sim) | **93** |
| OOS win rate (bracket P&amp;L &gt; 0) | 43.01% |

Precision@0.60 = **N/A** (no ML / no scorecard threshold).

---

## Performance (OOS Simulation 2023–2024)

| Metric | Value | Gate | Result |
|--------|-------|------|--------|
| Total return | **+13.41%** | &gt; 0 | **PASS** |
| Buy & hold (univ. mean) | **+81.16%** | beaten | **FAIL** |
| Sharpe | **0.864** | ≥ 1.50 | **FAIL** |
| Max DD | **−5.36%** | ≥ −0.15 | **PASS** |
| Trades | **93** | info &gt; 60 | OK |
| Win rate | 43.01% | — | — |
| WF pass | **2/4** | ≥ 3/4 | **FAIL** |
| MC p-value | **0.504** | &lt; 0.05 | **FAIL** |
| MC real Sharpe (trade bootstrap) | 1.803 | — | — |
| MC beats random | 49.6% | — | **FAIL** |

---

## Walk-Forward

| Window | Return | Sharpe | Trades | Result |
|--------|--------|--------|--------|--------|
| 2021 | +3.95% | 0.50 | 62 | **FAIL** |
| 2022 | −11.70% | −1.66 | 68 | **FAIL** |
| 2023 | +11.18% | 0.86 | 52 | PASS |
| 2024 | +6.44% | 0.72 | 52 | PASS |

**Walk-forward: 2/4 — FAIL** (same count as EXP-0017, worse Sharpe in every printed window vs 0017’s bull years).

---

## vs EXP-0012 / EXP-0017

| Metric | EXP-0012 | EXP-0017 | **EXP-0018** |
|--------|----------|----------|--------------|
| Selector | RF | Rules ScoreCard | **NONE** |
| Universe | legacy | 300 dynamic | **300** (same screener) |
| Entry | (baseline) | MOC Close | **MOC Close** |
| OOS Sharpe | **1.64** | **1.87** | **0.86 FAIL** |
| Max DD | −3.17% | −5.52% | −5.36% |
| OOS return | (partial pass era) | +27.20% | **+13.41%** |
| Beats B&amp;H | — | (passed sim gates set) | **FAIL** (B&amp;H +81%) |
| Trades | 83 | 103 | **93** |
| Base rate | — | 22.0% | **22.0%** |
| WF | 3/4 (partial) | 2/4 | **2/4 FAIL** |
| MC p | — | (not 0017 hard gate set) | **0.504 FAIL** |
| Verdict | STRONG PARTIAL PASS | FAIL (4/8; sim Sharpe OK) | **FAIL (2/6)** |

### Interpretation
- Same candidate pool / base rate as EXP-0017 (**2,761** rows, **22.0%** Option D).
- **Without** scorecard ranking, capacity slots fill by ticker A–Z → OOS edge **does not** match Phase-2 sim Sharpes.
- EXP-0017’s high Sharpe was **not** proven to be “catalyst+bracket alone”; under this ablation the selector-free book underperforms badly on Sharpe, B&amp;H, WF, and MC.
- Still **do not** treat EXP-0017 scorecard as a validated lift engine (0017 lift ~1.0×) — but raw “take every catalyst day” is worse for the portfolio sim under slot caps.

---

## Quant Assassin Verdict

# FAIL

### What passed
- Max DD −5.36% inside −15% gate
- Positive OOS return (+13.41%)
- Base rate healthy (22.0%); trade count 93 &gt; 60 info bar
- Sacred stack / Option D / costs / temporal protocol intact

### What failed (loud)
- **Sharpe 0.86 ≪ 1.50**
- **Does not beat buy-and-hold** (+13% vs +81%)
- **Walk-forward 2/4** (2021/2022 fail)
- **Monte Carlo p=0.504** — trade P&amp;L Sharpe not better than bootstrap noise

### Promotion
**Do not promote to `/candidates`.**

---

## How to re-run

```bash
.\venv\Scripts\modal.exe run experiments/EXP-0018/experiment18.py
```
