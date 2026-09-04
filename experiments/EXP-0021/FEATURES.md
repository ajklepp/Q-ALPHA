# EXP-0021 Feature Dictionary — Continuation Ranker

**Causal rule:** every feature uses only data available at the **close of the signal 1H bar** (no look-ahead).  
**Target labels (path-first after entry):** `hit_1r` (MFE hits +5% before −5% kill), `mfe_ge_3`, `mfe_ge_5`, rest-of-day MFE.

Peak hours `{7,11,12,13}` are a **score feature**, not a hard list gate.

---

## A — Already in stack

| Column | Source | Formula / rule | Causal cutoff |
|---|---|---|---|
| `launch_score` | `tsd_launch_score.compute_launch_score` | 0–100 launch points | Signal bar OHLC + WT only |
| `bar_state` | OHLC body/range | `orange` body&lt;25%; `red` close&lt;open; `yellow` weak green; `green` strong | Signal bar |
| `scan_score` | `tsd_signals` | Wave/trend/MFI/vol composite | Completed bars ≤ signal |
| `wt1`, `wt2` | WaveTrend | Channel 10 / avg 21 | ≤ signal |
| `buy_signal`, `early_bull` | WT cross / early bull | Boolean trigger | ≤ signal |
| `htf_range_20d_pct` | daily | (H20−L20)/L20 | Prior daily closes (signal day OK if RTH close known; use **prior day** for intraday) |
| `htf_dist_sma50_pct` | daily | (close−SMA50)/SMA50 | Prior daily |
| `htf_sma20_slope_pct` | daily | SMA20 vs SMA20−10 | Prior daily |
| `analog_mae_p75`, `analog_mfe_p50` | profiler when present | Soft context | Built as-of &lt; signal date |
| `analog_win_rate` | profiler | Soft | Same |
| `news_headline_count_48h` | Polygon news | Count published_utc in 48h | &lt; signal timestamp |
| `print`, `outlook` | catalyst extract | Enums | Headlines &lt; signal |
| `guidance_cut` | outlook lowered/withdrawn | Bool | Same |
| `float_shares`, `short_interest_pct` | reference / tags | Soft | Latest known ≤ signal date |
| `structure_dist_pct` | 1H swing/area | (entry−structure)/entry | Bars **before** signal |

---

## B — Polygon tape / MTF (new)

| Column | Formula | Causal cutoff |
|---|---|---|
| `bar_range_pct` | (H−L)/C | Signal bar |
| `bar_body_pct` | abs(C−O)/max(H−L,eps) | Signal bar |
| `close_loc` | (C−L)/(H−L) | Signal bar |
| `vol_ratio_20` | vol / SMA(vol,20) on 1H | ≤ signal |
| `dollar_vol_1h` | C×vol | Signal bar |
| `bars_since_rth_open` | Count 1H bars from 09:30 ET | ≤ signal |
| `dist_hod_pct` | (session_high−C)/C | Session bars ≤ signal |
| `dist_lod_pct` | (C−session_low)/C | Same |
| `consec_green` | Consecutive C&gt;O ending at signal | ≤ signal |
| `gap_pct` | (today_open−prior_close)/prior_close | Prior close + today open |
| `hour` | bar close hour ET | Signal |
| `peak_hour` | hour ∈ {7,11,12,13} | Signal |
| `dist_20d_low_pct` | (C−min(L,20d))/C | Prior daily (+ signal day open only if needed; use prior close series) |
| `dist_20d_high_pct` | (max(H,20d)−C)/C **room left** | Prior daily highs |
| `dist_52w_high_pct` | (max(H,252)−C)/C | Prior daily |
| `close_vs_sma20` | (C−SMA20)/SMA20 | Prior daily |
| `close_vs_sma50` | (C−SMA50)/SMA50 | Prior daily |
| `close_vs_sma200` | (C−SMA200)/SMA200 | Prior daily |
| `hh_hl_20` | Higher-high and higher-low vs 20d ago swing | Prior daily |
| `ticker_prior_mfe_p50` | Median MFE of prior 1H buys on same ticker | Prior signals only |
| `ticker_prior_hit1r_rate` | Fraction hit_1r on prior 1H buys | Prior signals only |
| `dollar_vol_rank` | Rank of today's RVOL/dollar vol in HTF set | Same-day volume **up to signal** (use cumulative session vol) |

---

## C — News / fundamentals / unresolved

| Column | Formula | Causal cutoff |
|---|---|---|
| `catalyst_type` | earnings/FDA/contract/offering/lawsuit/analyst/other/none | Headlines &lt; signal |
| `unresolved` | 1 if PDUFA/vote/financing/going-concern language | Same |
| `days_to_event` | Parsed event date − signal date (nullable) | Same |
| `dilution_flag` | offering/dilution/ATM keywords | Same |
| `distress_flag` | bankruptcy/going concern/neg equity | Same |
| `mcap` | Polygon reference | Latest ≤ signal |
| `news_velocity_24h` | Headline count last 24h | &lt; signal |
| `news_velocity_72h` | Headline count last 72h | &lt; signal |

---

## D — Social

| Column | Source | Formula | Causal / failure mode |
|---|---|---|---|
| `st_msg_24h` | StockTwits | Message count ~24h | Non-blocking; 0 if fail |
| `st_bull_ratio` | StockTwits | bullish/(bull+bear) | Same |
| `x_posts_24h` | X API v2 if bearer | Count `$TICKER` OR `TICKER stock` | 0 + `social_missing=1` if down |
| `x_authors_24h` | X | Unique authors | Same |
| `x_engage_24h` | X | likes+reposts+replies sum | Same |
| `x_sent_lex` | X | (bull−bear lex)/n | Same |
| `social_missing` | — | 1 if all social failed | Never veto |

---

## E — Do not use as live gates (research only / banned)

- Future MAE/MFE of *this* trade  
- Nearest 10-bar swing as broker kill  
- Blue-box efficiency flatten  
- Discretionary chart labels without a numeric rule  

---

## Continuation score v1 (heuristic challenger)

Used before a fitted model has enough OOS data (see `lib/features.py`):

```
score =
  + 25 * peak_hour
  + bar_state_pts (yellow 12, red 8, green 5, orange 0)
  + room_to_20d_high term (chase penalty if already through highs)
  + 15 * clip(dist_20d_low_bounce, 0, 1)
  + vol_ratio term (dead-tape penalty if vol_ratio<0.5)
  + ticker prior hit_1r + prior MFE p50
  + news_velocity_24h + StockTwits bull ratio + optional X lex
  + 0.25 * launch_score + scan sweet-spot (25–45)
  - 25 * guidance_cut - 30 * dilution - 40 * distress
  - 20 * soft EXTENSION (scan>55)
```

**Challenger admit floors:** buy/early_bull; hour∈{5…15}; hard-block scan≥75 / extended; require `launch_score≥40` OR `scan≤55`.

Baseline **Peak Hour v0:** hour∈{7,11,12,13} AND buy/early AND scan≤55 AND not EXTENDED; rank by `continuation_score_v0`.
