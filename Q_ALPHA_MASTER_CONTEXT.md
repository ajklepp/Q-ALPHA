# Q-ALPHA MASTER CONTEXT
## Durable architecture & decisions — read before changing the system

*Last updated: 2026-08-24. Companion ops doc: `Q_ALPHA_HANDOFF.md`.*

---

## WHAT THIS SYSTEM IS

Q-Alpha is a momentum trading system for small/mid-cap catalyst gappers.
It has **two parallel tracks** that must not be confused:

| Track | Purpose | Broker / data |
|-------|---------|----------------|
| **Live agent** (`candidates/autonomous_agent.py`) | Autonomous paper trading via IBKR TWS | IBKR paper + Polygon backup |
| **Strategy Lab** (`strategy_lab/`) | IBKR-free Polygon paper **forward test of exit strategies** | Polygon only (SIM money) |

The lab answers: *Given a shared entry, does Strategy A (Trailing) beat Strategy B (Target) out of sample?* It does **not** place IBKR orders and does **not** share the agent's pool/book.

---

## STRATEGY LAB (core new subsystem)

### Purpose
- Forward-test **exit** designs on real Polygon bars (IBKR-free by design — Lab does not share the agent book).
- Dual independent **$3,000** SIM pools (A and B), same entry, different exits.
- Accumulate a **true out-of-sample** prediction log (profiler MFE p50 vs realized MFE) for rolling OOS R².

### Pipeline (lab-only; do not rewrite agent files for lab work)
```
collect_setups → fetch_history → fetch_premarket → batch_profile
  → strategy_a / strategy_b
  → matrix / matrix_pm → scoreboard → analyze_edge → entry_edge_study
  → oos_r2 → replay → live_forward → reset_forward
```

| Module | Role |
|--------|------|
| `collect_setups.py` | Build setup universe (profiles, scans, history) |
| `fetch_history.py` | Daily / context history cache |
| `fetch_premarket.py` | Premarket 1-min bars + median/VWAP stats |
| `batch_profile.py` | Analog MFE/MAE percentiles + confidence |
| `entry_models.py` | immediate, orb_reclaim, vwap_reclaim, sweep_reclaim, PM limits |
| `strategy_a.py` | Trailing exit (4 tranches) |
| `strategy_b.py` | Target / MFE-percentile scale-out |
| `matrix.py` / `matrix_pm.py` | Entry × exit backtest grids |
| `scoreboard.py` | Rank combos |
| `analyze_edge.py` | Edge breakdown (incl. MFE buckets) |
| `entry_edge_study.py` | Timing / filter / limit entry study |
| `oos_r2.py` | Temporal OOS R² (predicted vs actual MFE) |
| `replay.py` | Single-day dual-pool dry harness |
| `live_forward.py` | Live / replay forward runner + Telegram |
| `reset_forward.py` | **Only** way to reset pools to $3000 |
| `lab_state_sync.py` | Supabase `strategy_lab_state` (service write / anon read) |

### Strategy A vs Strategy B
**Shared entry:** currently **`immediate`** (09:30 first 1-min close). Both pools fork from the same fill.

**Strategy A — Trailing**
- 4 tranches **40 / 30 / 20 / 10** (auto-collapse if share count tiny)
- Kill-all hard stop ≈ `safe_max_stop_pct` (fallback ~7%)
- Per-tranche **ratcheting** trails (triggers at early / MFE p50 / p75 / p90; trail ≈ MAE p50)
- T4 = **runner** (no hard upside cap); time-cap / max-hold applies

**Strategy B — Target**
- Scale-out toward MFE-percentile / fixed-style targets
- Same kill + time rules family; designed for A/B comparison on identical entries

### Key findings (evidence-based — do not re-litigate without new data)
1. **Entry = `immediate`.** Across three tested dimensions (timing, filters, premarket-median / premarket-VWAP limits), entry logic showed **no reliable edge**. Lab live entry is locked to immediate; `sweep_reclaim` is a **quality tag only** (not a gate).
2. **Exit A likely > B**, driven by the **>25% MFE** tail bucket — but **provisional** (small, clustered calendar: ~21 days / limited independent regimes).
3. **AI / decision entry engine is DEFERRED** until we deliberately build on IBKR Level 2 / live order-flow (paper L2 is now partially available — see DATA REALITY). Do not build “smart entry v2” on delayed Polygon aggregates alone.

### OOS R² (Narang-style prediction quality)
- **Predicted:** profile `percentiles.mfe.p50` → percent  
- **Actual:** peak favorable excursion vs entry over Strategy A hold window (not truncated by stops) — same definition in `oos_r2.actual_mfe_pct`
- **Backtest artifact:** `strategy_lab/results/oos_r2_backtest.json`  
  - Temporal holdout OOS **R² ≈ −0.2367 (N=35)** — noisy baseline; **keep this file**
- **Forward rolling R²:** grows from **LIVE** completed prediction pairs only
  (`forward_predictions.json` + state blob). Replay is isolated and does **not**
  write the live prediction log. Dashboard **MIN_N = 20** before showing forward R²
  or gap verdicts.
  - **Negative R²** = worse than predicting the **average** MFE every time.
  - Forward benchmark to beat is **R² = 0** (the mean). The backtest −0.24 number is
    **context only**, not the live pass/fail bar.

### Lab infra decisions
- Dual pools **compound** across live days: `$3000` is a **one-time** start (`reset_forward.py` only). `live_forward` LIVE mode **resumes** pools, closed trades, equity curve, and prediction log.
- Replay (`--replay`) is **isolated**: writes `forward_state_replay.json`, does **not** clobber live book / Cloud.
- Supabase table `strategy_lab_state`: **service key** upserts from runner; **anon key** + RLS SELECT for public Strategy Lab tab.
- Dashboard: **🧪 Strategy Lab** + **📖 Glossary** (renders root `GLOSSARY.md`). Glossary terms: MFE/MAE, R², tranches, SIM vs IBKR, etc.

---

## DATA REALITY (critical)

| Source | What works | Caveats |
|--------|------------|---------|
| **IBKR paper** (TWS **7497**, e.g. DUR857496) | **Streaming + historical + realtime bars + smart depth** — verified **2026-08-24 ~19:34 EDT** probe (SPY, clientId 98, read-only). `reqMktData` valid bid/ask/last; `reqHistoricalData` 1-min; `reqRealTimeBars` 5s; `reqMktDepth` 5 levels. **Error 420 not observed.** | Depth via smart/IEX path; IB **2152** flags missing depth perms on NASDAQ/BATS/ARCA/NYSE/BEX — L2 usable but may be **partial** vs full TotalView on paper. Informational **2104/2106/2158**; transient **2108** normal. |
| **Polygon / Massive Stocks Developer ($79/mo)** | 1-min aggregates, news, scans, **Strategy Lab** — see `strategy_lab/polygon_tier.py` | **15-min delayed** (not real-time; real-time is Stocks Advanced ~$199). Unlimited REST. WebSockets carry the **same** delay. |

**Prior IBKR paper state (historical):** live + delayed + realtime bars failed with **Error 420**; historical always worked. Recovery likely from live North America equity streaming + L2/Snapshot entitlements **shared to paper**.

**Confirmed Polygon tier facts** (2026-08-23): plan `stocks_developer_79`, brand Massive (Polygon), `DELAY_MINUTES=15`, `REST_CALLS=unlimited`, minute/second aggregates, Snapshot, Corporate Actions, Flat Files — constants in `polygon_tier.py`. Docs: [massive.com](https://massive.com) (llms.txt/.md); client `massive-com/client-python`. API base still `api.polygon.io` unless Step 0 dashboard shows otherwise.

**ENTRY-CONVENTION LIMITATION:** SIM entries fill at the 09:30 1-min close, which on the 15-min delayed tier is not visible until ~09:45 — fills are therefore not executable at that price in real money. Real-money transition requires the real-time tier OR re-basing entry fills to the first executable visible bar. Deferred: latency-cost study quantifying the gap.

**Implication:** Agent **MAY** use IBKR live/streaming bars when we choose. Strategy Lab remains on **Polygon** (15-min delayed OK) — **do not mix** Lab SIM and IBKR agent books. Lab P0 wait-for-0930-bar stays **LOAD-BEARING** under Polygon delay.

---

## LIVE AGENT (durable rules — unchanged philosophy)

### Target variable — Option D only
```
Label = 1 if price hits entry + 2×ATR_stop before stop (within 5 days)
Label = 0 if stop first OR 5 days pass without 2R
```
Never use `future_return_5d > 0` as the label.

### Model for new experiments
- LightGBM (`LGBMClassifier`), not Random Forest (RF only in EXP-0012 and earlier)
- Temporal splits only; `COST_PER_TRADE = 0.0015`
- Sacred: `BracketPosition`, `classify_profile()`, `get_regime()`, walk-forward + MC patterns

### Universe (EXP-0013+)
Float 15M–200M, price $5–$200, min ~$5M dollar volume; live agent often tighter ($5–$50) for share count.

### 28 entry features (Group A) + 11 exit monitors (Group B)
See historical sections below — still the research contract for Modal experiments. Lab profilers use analog MFE/MAE percentiles separately.

### Sacred stack names
- Data: Polygon (`POLYGON_API_KEY`)
- Compute experiments: Modal + `modal.Secret.from_name("polygon-api-key")` / `q-alpha-secrets`
- Env names: exact spellings in `.cursorrules` (SUPABASE_*, TELEGRAM_*, etc.)

---

## DEFERRED / KNOWN DEBT

**(a) Entry-engine v2** — deferred until we choose to build on IBKR L2 / live order-flow (paper MD/L2 probe OK as of 2026-08-24; full NASDAQ TotalView on paper may still be partial — see **2152**).

**(b) SECURITY — Streamlit Cloud + service role**  
Most dashboard tabs still use **SUPABASE_SECRET_KEY** on a **public** Streamlit app. Strategy Lab tab correctly prefers **anon + RLS** for `strategy_lab_state`.  
**Plan:** migrate remaining reads to anon+RLS, then **drop secret key from Cloud**. Acceptable while paper/sim; **fix before real-account data** or during Modal/AWS move.

**(c) `use_container_width` deprecation** — Streamlit cosmetic warnings only.

**(d) Modal cron EDT vs EST** — adjust UTC cron strings when clocks change.

**(e) Agent vs Modal state sync** — local `candidates/` vs Modal volume; keep `sync_to_modal` discipline.

---

## CATALYST TIERS (Session 0.1 — still valid for research)

**Tier 1:** EARNINGS_BEAT_GUIDANCE, FDA_APPROVAL, MAJOR_CONTRACT, SHORT_SQUEEZE_CATALYST  
**Tier 2:** CLINICAL_TRIAL_POSITIVE, ACTIVIST_INVESTOR, ANALYST_UPGRADE_MAJOR, PARTNERSHIP_MAJOR  
**Tier 3 traps:** CRYPTO_PIVOT, NAME_CHANGE_AI, REVERSE_SPLIT, LOI_ONLY  

Keyword lists for Polygon news remain as previously defined in this project.

---

## THE 28 ENTRY FEATURES (Group A — no look-ahead)

### Catalyst
F01 catalyst_tier · F02 catalyst_type_encoded · F03 gap_pct_premarket · F04 premarket_vol_ratio  

### Float + liquidity
F05 float_shares_log · F06 float_bucket · F07 dollar_vol_20d_avg · F08 short_interest_ratio  

### Pre-catalyst setup
F09–F19: base_duration_days, base_depth_pct, bbw_percentile_52w, ttm_squeeze_days, obv_slope_base, up_down_vol_ratio, cmf_20, rs_vs_spy_20d, rs_vs_sector_20d, price_location_base, dist_from_52w_high  

### Entry pattern (at signal time)
F20–F28: entry_pattern_encoded, time_of_entry_minutes, orb_width_pct, vwap_distance_at_entry, pullback_vol_ratio, hod_test_count, spy_5m_slope_at_entry, cumul_turnover_at_entry, atr_14_normalized  

**Rules:** `.shift(1)` / prior-day only; news &lt; 09:25 ET; never look-ahead.

---

## GROUP B EXIT MONITORS (post-entry; not entry model)
E01–E11: close_vs_range_pct, exhaustion_score_eod, upper_wick_ratio, dist_from_20ma_pct, cumul_turnover_eod, day_n_high_vs_prior, vol_vs_prior_day, gap_fill_flag, vwap_hold_eod, r_multiple_current, days_held  

---

## CLASSIC ENTRY PATTERNS (research / agent — lab uses immediate)

ORB · VWAP pullback · Bull flag · HOD break — definitions unchanged from prior master context. Lab matrix tested reclaim/limit variants; **no reliable edge** → live lab entry = immediate.

---

## EXPERIMENT CONTRACT

```
Train 2020–2022 · Validate 2023 · Test 2024–2025
Walk-forward: 4 forward windows
Success: Sharpe ≥ 1.5, max DD ≥ −15%, WF ≥ 3/4, MC p < 0.05, precision@0.60 ≥ 55%
EXP-0012 = baseline (do not modify). New work = EXP-0013+
```

Reuse from EXP-0012: `process_stock()`, `BracketPosition`, `classify_profile()`, `get_regime()`. Replace scorer with LightGBM + Group A features for new exps.

---

## ABSOLUTE RULES (never violate)

1. No look-ahead bias  
2. Target = Option D only  
3. Temporal splits only  
4. Always apply transaction costs  
5. Do not modify `/candidates` agent paths for lab experiments without explicit approval  
6. Bracket stop system is sacred in the agent path  
7. Report failures as loudly as successes  
8. Ask before touching root `data_pipeline.py` / `features.py` / `model.py` if present  

---

## WHAT GOOD LOOKS LIKE

| Metric | EXP-0012 baseline | Bar for new systems |
|--------|-------------------|---------------------|
| Sharpe | ~1.64 | ≥ 1.5 |
| Max DD | ~−3% | ≥ −15% |
| Walk-forward | Strong / partial | ≥ 3/4 |
| Lab forward R² | — | Meaningful only at **N ≥ 20**; live bar is **R² ≥ 0** (beat the mean); −0.24 is backtest context only |

---

*Architecture doc — for day-to-day runbooks and Monday checklist see `Q_ALPHA_HANDOFF.md`.*
