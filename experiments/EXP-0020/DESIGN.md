# EXP-0020 Design — Ticker-Profiler Ranker (replace ScoreCard)

**Status:** Design + scaffold · **NOT RUN** · Do not promote to `/candidates`  
**App:** `q-alpha-exp020`

---

## 1. Why the EXP-0017 ScoreCard is wrong for Q-Alpha

The ScoreCard ranks gap days with **same-day candle cosmetics** (`body_ratio_d0`, `close_vs_range_d0`, wick/volume heuristics). That is a daily chart checklist, not the Q-Alpha research object.

Q-Alpha’s live/research path asks: *on analogous past gap days for this ticker, how far did price move against / for me after a morning entry?* That is **analog MAE/MFE**, not “did today’s candle look strong.”

EXP-0017 already showed ScoreCard **lift ≈ 1.0×** (no Option-D precision edge). EXP-0019 showed capacity policy (threshold or points rank) restores sim Sharpe vs A–Z — but that does **not** validate candle rules as a *prediction* engine. EXP-0020 replaces candle scoring with the **Ticker Profiler** machinery already used by the agent/Lab.

**Explicit ban for EXP-0020 ranking:** do **not** use `body_ratio_d0`, `close_vs_range_d0`, or other ScoreCard candle point rules for selection or tiebreak.

---

## 2. How `ticker_profiler` works (reuse, do not fork)

Source of truth: `candidates/ticker_profiler.py` — import `find_analog_days` / `build_ticker_profile` (mount into Modal; **no second copy** of profiler logic).

1. **Analogs:** In a ~2yr lookback (extend toward ~3yr if needed), find prior gap days with gap &gt; 3% and unusual volume (≥1.75× baseline), strictly before `as_of`, age ≥ 2 days.
2. **Per-analog path:** Pull RTH 1-min bars; entry proxy ≈ **09:33 ET** (`ENTRY_PROXY_MIN = 3`); measure **MAE%** / **MFE%** vs that proxy; session “held” = close &gt; entry.
3. **Aggregate:** Equal-weight percentiles of MAE/MFE across analogs.
4. **Informational bracket:** stops from MAE p50/p75/p90; **target = MFE p50**; **R:R ≈ target / safe_max_stop**. Warn if R:R &lt; 1.5.
5. **Confidence:** `HIGH` / `MEDIUM` / `LOW` / **`INSUFFICIENT`**.

Live risk: Lab `oos_r2_backtest` showed **negative OOS R²** for MFE p50 (~−0.24, N≈35). EXP-0020 tests **portfolio value of profiler ranking** vs 0018 A–Z and 0017 ScoreCard — not point-forecast calibration.

---

## 3. Ranker spec (v1 locked)

### Hard gates

| Gate | Rule |
|------|------|
| Catalyst | `gap_pct ≥ 3%` AND `volume_ratio_20d ≥ 2×` |
| Profile usable | `INSUFFICIENT_POLICY = SKIP` — no trade if INSUFFICIENT / not meaningful |
| R:R floor | **None** (`RR_MIN_GATE = None`) — rank-only |
| Premarket | **OFF** (`USE_PREMARKET_FEATURES = False`) |

Still **report** count/share of eligible candidates with R:R &lt; 1.5 (informational; not a gate).

### Ranking

Primary: **`reward_risk`** descending · Secondary: analog_count · Tertiary: ticker A–Z.

### Sacred stack

`BracketPosition` / `classify_profile` / `get_regime` (EXP-0012) · Option D · `COST_PER_TRADE = 0.0015` · WF 4 · MC 5000 · Polygon sleep 0.12s.

---

## 4. Cost / cache (v1 locked)

- **Pilot:** `PILOT_MAX_TICKERS = 50` (same screener, first 50 after tighten).
- **Full 300:** Phase-2 **after** pilot PASS/FAIL review — do not jump to 300 on first Modal run.
- **Cache:** Modal Volume `qalpha-exp020-profiles` mounted at `/cache/exp020_profiles`, JSON keyed by `ticker` + `as_of_date`. Commit volume after profile attach. No look-ahead (`as_of` = signal day).

---

## 5. Entry / label (v1 locked)

**`MOC_CLOSE_ATR_D0`** — isolates ranker vs EXP-0017–19. Known limitation: profiler analogs use ~09:33 entry proxy.

**Follow-up (not v1):** morning / ~09:33 label+sim alignment experiment after pilot results.

---

## 6. Director decisions (locked for v1)

| # | Decision |
|---|----------|
| 1 | Pilot **50** + Modal Volume cache; full 300 = later phase |
| 2 | Entry **MOC**; morning follow-up later |
| 3 | **INSUFFICIENT = SKIP** |
| 4 | **RR_MIN_GATE = None**; report R:R &lt; 1.5 share |
| 5 | Premarket features **OFF** |

---

## 7. Success / FAIL gates

| Gate | Threshold |
|------|-----------|
| Sharpe | ≥ 1.50 |
| Max DD | ≥ −0.15 |
| Positive return | yes |
| Beats buy-and-hold | yes |
| Walk-forward | ≥ 3/4 |
| Monte Carlo | p &lt; 0.05 |
| Precision@0.60 | N/A — trade count + base rate + R:R&lt;1.5 share |

Compare vs EXP-0017 Sharpe **1.87** and EXP-0018 Sharpe **0.86**. Negative Lab MFE R² does not auto-FAIL.

**Promotion:** Never auto-promote to `/candidates`.

---

## 8. Files

| Path | Role |
|------|------|
| `DESIGN.md` | This doc |
| `experiment20.py` | Modal scaffold |
| `results.md` | Stub — NOT RUN |

No Modal full pilot until plumbing smoke passes and you approve the run.

---

## 9. Plumbing incident (2026-08-24) — NOT an economic FAIL

Invalid ablation: **0/523 eligible**, attach **~0s**.

**Root cause:** `_import_ticker_profiler` evaluated `Path(__file__).parents[2]` while Modal mounts `experiment20.py` as `/root/experiment20.py` (only 2 parents) → **`IndexError`** on every row, swallowed as `confidence=ERROR`. Secondary hardening: install **`tzdata`** for `ZoneInfo` on debian_slim; refuse silent 0s attach via plumbing guard; log cache hits/misses + first errors.

**Smoke (passed):** local + Modal `--smoke` → MARA/RIOT/SMCI **3/3 HIGH eligible** (~7–14s each cold).
