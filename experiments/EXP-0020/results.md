# EXP-0020 Results — Ticker-Profiler R:R Ranker (pilot 50)

**Status: FAIL (1/6 hard gates) — INVALID ABLATION (0 profiler-eligible rows)**

**Run date:** 2026-08-24  
**Modal run:** https://modal.com/apps/ajklepp/main/ap-BhKVsVEDNEhBtMRTDhpbu3  
**Runtime:** **42s** (STEP 1 daily download ~39s; STEP 2 profile attach ~**0–3s** for 523 rows)  
**App:** `q-alpha-exp020`  
**Universe:** Pilot **50** + SPY (screener then `PILOT_MAX_TICKERS=50`)

---

## Loud verdict

# FAIL — do not promote to `/candidates`

This run **does not** measure whether profiler R:R ranking beats EXP-0017/0018/0019.

| Red flag | Observation |
|----------|-------------|
| Profiler-eligible | **0 / 523** |
| OOS trades | **0** |
| Profile attach wall time | **≈0s** for 523 rows |
| Implication | Profiles were **not** built via Polygon 1-min analogs (that path cannot finish in &lt;1s cold). Likely every `load_or_build_profile` hit the exception path or returned non-meaningful / INSUFFICIENT without measurement — **scaffold/integration bug**, not a ranker economic result. |

**Next step before Phase-2 (300):** debug Modal import/`build_ticker_profile` errors (log `prof_error`), confirm Volume writes, print first failing traceback. Re-run pilot only after eligible count ≫ 0 and attach time reflects cache miss cost.

---

## v1 settings (as run)

| Item | Value |
|------|-------|
| Entry/label | `MOC_CLOSE_ATR_D0` |
| Selector | `PROFILER_RR` |
| INSUFFICIENT | SKIP |
| RR_MIN_GATE | None (rank-only) |
| Premarket | OFF |
| Cache | Modal Volume `qalpha-exp020-profiles` → `/cache/exp020_profiles` |
| Cache behavior | Progress `profiles N/523 [0s]` throughout — **no evidence of cold builds**; treat as miss/fail, not healthy hits |

---

## Transparency

| Metric | Value |
|--------|-------|
| Candidate rows | **523** |
| Base rate | **25.0%** (131/523 Option D ≈ 0.2505) |
| Profiler-eligible | **0 / 523 (0%)** |
| Eligible with R:R &lt; 1.5 | **0 / 0 (n/a)** — no eligible sample |
| OOS trades | **0** |
| Win rate | 0% |

---

## Hard gates (FAIL loudly)

| Gate | Threshold | Actual | Result |
|------|-----------|--------|--------|
| Sharpe | ≥ 1.50 | **0.00** | **FAIL** |
| Max DD | ≥ −0.15 | **0.00** (no trades) | **PASS*** |
| Positive return | yes | **0.00%** | **FAIL** |
| Beats buy-and-hold | yes | 0% vs B&H **+136.5%** | **FAIL** |
| Walk-forward | ≥ 3/4 | **0/4** | **FAIL** |
| Monte Carlo | p &lt; 0.05 | **n/a** (insufficient trades) | **FAIL** |

\*DD “PASS” with zero trades is **vacuous** — not evidence of risk control.

**Gates: 1/6 → FAIL**

---

## Walk-forward

| Window | Return | Sharpe | Trades | Result |
|--------|--------|--------|--------|--------|
| 2021 | 0% | 0.00 | 0 | **FAIL** |
| 2022 | 0% | 0.00 | 0 | **FAIL** |
| 2023 | 0% | 0.00 | 0 | **FAIL** |
| 2024 | 0% | 0.00 | 0 | **FAIL** |

---

## vs EXP-0017 / 0018 / 0019

| Experiment | Selector | Universe | Sharpe | Trades | Notes |
|------------|----------|----------|--------|--------|-------|
| EXP-0017 | ScoreCard FULL | 300 | **1.87** | 103 | Lift failed; sim Sharpe strong |
| EXP-0018 | A–Z catalyst-only | 300 | **0.86** | 93 | Hypothesis REJECTED |
| EXP-0019 A/B/C | Threshold / rank / FULL | 300 | **1.71 / 1.69 / 1.87** | 105 / 94 / 103 | MIXED; all FAIL B&H/WF/MC |
| **EXP-0020 pilot** | Profiler R:R | **50** | **0.00** | **0** | **Invalid — 0 eligible profiles** |

**Comparison:** EXP-0020 is **worse than every baseline** only in the trivial sense of taking no trades. It is **not** a fair head-to-head until profiler attach works.

---

## Quant Assassin notes

### What “worked”
- Modal app started; pilot cap 50 applied; daily STEP 1 completed (~39s).
- Volume mount path printed; hard-gate machinery ran (SKIP → empty book).

### What failed (loud)
- **Zero** profiler-eligible candidates  
- **Zero** trades / WF / MC  
- Attach timing proves **profiler path did not execute real analog builds**  
- Vacuous DD pass must not be spun as success  

### Promotion
**Do not promote to `/candidates`.** Do not scale to 300 until pilot produces eligible profiles and a non-empty trade set.

---

## How to re-run (after debug)

```powershell
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'
.\venv\Scripts\modal.exe run experiments/EXP-0020/experiment20.py
```
