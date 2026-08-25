# EXP-0020 Results — Ticker-Profiler R:R Ranker (pilot 50)

**Current status: INVESTIGATE (4/6 hard gates) — plumbing fixed; do not promote**

Pilot universe = **50** names (not 300). Compare Sharpe to 0017/0018/0019 with that caveat.

---

## Run B — post-plumbing pilot (VALID ablation) — 2026-08-24

**Modal:** https://modal.com/apps/ajklepp/main/ap-A1hJV1vaNktFSTTVZpDQ5l  
**Runtime:** **2718s** (~45 min) · Attach alone **2671.9s**  
**Cache:** misses=**523** hits=**0** bad=0 errs=0 (cold Volume after fix)

### Expectation checks

| Check | Result |
|-------|--------|
| Attach not ~0s on cold work | **PASS** — 2671.9s |
| Profiler-eligible ≫ 0 | **PASS** — **433 / 523 (82.8%)** |
| OOS trades > 0 | **PASS** — **66** |

### Loud gate table

| Gate | Threshold | Actual | Result |
|------|-----------|--------|--------|
| Sharpe | ≥ 1.50 | **2.303** | **PASS** |
| Max DD | ≥ −0.15 | **−4.28%** | **PASS** |
| Positive return | yes | **+46.42%** | **PASS** |
| Beats buy-and-hold | yes | +46.4% vs B&H **+136.5%** | **FAIL** |
| Walk-forward | ≥ 3/4 | **3/4** | **PASS** |
| Monte Carlo | p &lt; 0.05 | **p = 0.526** | **FAIL** |

**Verdict: INVESTIGATE (4/6)** — two hard fails (B&H, MC). **Do not promote to `/candidates`.**

### Transparency

| Metric | Value |
|--------|-------|
| Candidates | 523 · base rate **25.0%** |
| Confidence | HIGH 122 · MEDIUM 233 · LOW 78 · INSUFFICIENT 90 · ERROR 0 |
| Profiler-eligible | **433 / 523** |
| Eligible with R:R &lt; 1.5 | **394 / 433 (91.0%)** — informational |
| OOS trades / win rate | **66** / **50.0%** |
| as_of samples | `MARA@2019-02-11` Timestamp (DatetimeIndex OK) |

### Walk-forward

| Window | Return | Sharpe | Trades | Result |
|--------|--------|--------|--------|--------|
| 2021 | +8.83% | 1.19 | 41 | PASS |
| 2022 | −13.48% | −3.24 | 35 | **FAIL** |
| 2023 | +35.37% | 1.80 | 34 | PASS |
| 2024 | +5.09% | 0.70 | 37 | PASS |

### vs EXP-0017 / 0018 / 0019

| Experiment | Selector | Univ. | Sharpe | Trades | WF | Notes |
|------------|----------|-------|--------|--------|-----|-------|
| EXP-0017 | ScoreCard FULL | 300 | 1.87 | 103 | 2/4 | Lift failed |
| EXP-0018 | A–Z catalyst-only | 300 | 0.86 | 93 | 2/4 | REJECTED |
| EXP-0019 FULL | Threshold+rank | 300 | 1.87 | 103 | 2/4 | MIXED |
| **EXP-0020 Run B** | Profiler R:R | **50** | **2.30** | **66** | **3/4** | B&H+MC **FAIL**; pilot only |

**Reading:** On the **50-name pilot**, profiler R:R ranking produces strong OOS Sharpe (2.30) and WF 3/4 — better than 0018 and competitive with 0017/0019 on Sharpe — but **MC p≈0.53** (trade bootstrap indistinguishable from noise) and **does not beat univ. B&H**. Not a full-300 claim. **91%** of eligible rows have R:R &lt; 1.5 (rank-only policy kept them).

### Quant Assassin (Run B)

**PASS-ish sim edge on pilot; FAIL promotion bar.**  
Plumbing is fixed. Economic gates incomplete (B&H, MC). Pause scale-to-300 until you decide next experiment design.

---

## Run A — invalid plumbing FAIL (HISTORY) — 2026-08-24

**Status: FAIL (1/6) — INVALID ABLATION (0 profiler-eligible)**  
**Modal:** https://modal.com/apps/ajklepp/main/ap-BhKVsVEDNEhBtMRTDhpbu3 · **42s**

| Red flag | Observation |
|----------|-------------|
| Eligible | **0 / 523** |
| Trades | **0** |
| Attach | **≈0s** |
| Cause | Modal flat-mount `Path(__file__).parents[2]` → **IndexError** every row, swallowed as ERROR |

Kept for audit. Fixed in commit `c6d1f3a`. Not an economic reject of the ranker.

---

## Settings (both runs)

| Item | Value |
|------|-------|
| Entry | `MOC_CLOSE_ATR_D0` |
| INSUFFICIENT | SKIP |
| RR_MIN_GATE | None (rank-only) |
| Premarket | OFF |
| Pilot | 50 · Volume `qalpha-exp020-profiles` |

---

## How to re-run

```powershell
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'
.\venv\Scripts\modal.exe run experiments/EXP-0020/experiment20.py
```
