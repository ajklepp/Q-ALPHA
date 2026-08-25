# EXP-0018 Results — Catalyst-Filter-Only Ablation (NO Selector)

**Status:** NOT RUN

**Hypothesis:** On the Phase-2 SMID/catalyst universe, the bracket + catalyst gate alone (`gap ≥ 3%` AND `vol_ratio ≥ 2×`) produces the sim Sharpe. ML (EXP-0016) and rules scorecard (EXP-0017) add no reliable OOS lift. EXP-0018 removes the selector entirely.

**Null:** Catalyst-only trading fails ≥1 of the hard gates below (or underperforms EXP-0017 sim Sharpe enough to reject “bracket+filter is enough”).

---

## Deliberately omitted (vs EXP-0017 / Phase-2)

| Component | EXP-0017 | EXP-0018 |
|-----------|----------|----------|
| Selector | 6-rule ScoreCard (min 5/11 pts) | **NONE** — every catalyst day is a candidate |
| LightGBM | Already removed in 0017 | Still none |
| Precision@0.60 | Scorecard precision / lift gates | **N/A** — replaced by trade-count + base-rate transparency |
| Capacity tiebreak | Score / pts | Deterministic **ticker A–Z** only |

Sacred stack unchanged: `BracketPosition`, `classify_profile()`, `get_regime()` (copied from EXP-0012), Option D labels, `COST_PER_TRADE = 0.0015`, temporal WF (4 windows), Monte Carlo 5000.

---

## Settings (pre-run)

- **Universe source:** Same dynamic screener as EXP-0016 / EXP-0017 (`build_dynamic_universe`): screen date `2024-01-03`, mcap &lt; $20B, price $3–$500, avg vol ≥ 300k, target ~300 (cap 400). Exact ticker count is printed at Modal runtime.
- **Catalyst filter:** `gap_pct ≥ 0.03` AND `volume_ratio_20d ≥ 2.0`
- **Entry / label convention:** `MOC_CLOSE_ATR_D0` — Day-0 **Close** (MOC) entry; stop = entry − 1×ATR_d0; target = entry + 2×ATR_d0; Option D looks forward days +1…+5 only. Chosen because Phase-2 (0016/0017) already aligned label and sim on this path (cleanest; avoids open/intraday mismatch).
- **Selector:** NONE
- **Costs:** 0.15% per trade
- **Validate window (base-rate):** 2022
- **OOS sim:** 2023–2024
- **Modal app:** `q-alpha-exp018`

---

## Success gates (report ALL; FAIL loudly)

| Gate | Threshold | Result |
|------|-----------|--------|
| Sharpe | ≥ 1.50 | — |
| Max DD | ≥ −0.15 | — |
| Positive return | yes | — |
| Beats buy-and-hold | yes | — |
| Walk-forward | ≥ 3/4 | — |
| Monte Carlo | p &lt; 0.05 | — |
| Precision@0.60 | N/A (no model) | — |

Info-only (not PASS/FAIL gates): base rate in ~18–28%; trade count &gt; 60.

---

## Trade count + base-rate transparency

*(filled after Modal run)*

| Metric | Value |
|--------|-------|
| Candidate rows | — |
| Option D positives | — |
| Base rate | — |
| OOS trades taken | — |

---

## Performance (OOS 2023–2024)

| Metric | Value | Gate | Result |
|--------|-------|------|--------|
| Total return | — | &gt; 0 | — |
| Buy & hold | — | beaten | — |
| Sharpe | — | ≥ 1.50 | — |
| Max DD | — | ≥ −0.15 | — |
| Trades | — | info &gt; 60 | — |
| Win rate | — | — | — |
| WF pass | — | ≥ 3/4 | — |
| MC p-value | — | &lt; 0.05 | — |

**Verdict:** NOT RUN

---

## How to run (awaiting approval)

```bash
modal run experiments/EXP-0018/experiment18.py
```

Do not promote to `/candidates` regardless of outcome until Phase-3 review.
