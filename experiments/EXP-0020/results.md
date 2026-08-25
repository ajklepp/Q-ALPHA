# EXP-0020 Results — Ticker-Profiler R:R Ranker

**Status:** NOT RUN

**Hypothesis:** Ranking catalyst days by Ticker-Profiler **reward_risk** improves OOS portfolio metrics vs EXP-0018 A–Z and can match/beat EXP-0017 ScoreCard **without** candle ScoreCard rules.

**Known risk:** Lab `oos_r2_backtest` negative OOS R² for MFE p50 — portfolio gates decide.

See `DESIGN.md` (director decisions locked for v1).

---

## v1 settings (locked)

| Item | Value |
|------|-------|
| Universe | Pilot **50** (same screener); full 300 = Phase-2 after pilot |
| Cache | Modal Volume `qalpha-exp020-profiles` @ `/cache/exp020_profiles` |
| Entry/label | `MOC_CLOSE_ATR_D0` (morning/~09:33 follow-up later) |
| Selector | Profiler `reward_risk` desc |
| INSUFFICIENT | **SKIP** |
| R:R floor | **None** (rank-only) |
| Premarket | **OFF** |
| Banned | `body_ratio_d0`, `close_vs_range_d0`, ScoreCard points |

---

## Metrics plan (fill after run)

### Hard gates

| Gate | Threshold | Result |
|------|-----------|--------|
| Sharpe | ≥ 1.50 | — |
| Max DD | ≥ −0.15 | — |
| Positive return | yes | — |
| Beats B&H | yes | — |
| WF | ≥ 3/4 | — |
| MC p | &lt; 0.05 | — |

### Transparency

| Metric | Value |
|--------|-------|
| Candidate rows | — |
| Base rate | — |
| Profiler-eligible | — |
| Eligible with R:R &lt; 1.5 (count / share) | — *(informational)* |
| OOS trades | — |
| Sharpe vs 0017 (1.87) / 0018 (0.86) | — |

**Promotion:** Do not promote to `/candidates`.

---

## How to run (after approval)

```powershell
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'
.\venv\Scripts\modal.exe run experiments/EXP-0020/experiment20.py
```
