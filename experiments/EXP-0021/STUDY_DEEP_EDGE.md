# EXP-0021 — Deep-edge studies (1→6)

**Generated:** 2026-09-05T12:01:48.264137-04:00
**Runtime:** 23.0s
**Corpus:** `corpus_htf_universe.csv` · OOS cut `2026-08-11` · slots=2

## Executive findings

### 1) Score-term ablation
- Baseline v1: n=720 WR=10.0% exp=0.284 capture=20.8%
- Largest **hurt** when removed (by Δexp): **`no_prior`** (Δexp=-0.012, Δcap=-0.7%)
- Impact order (|Δexp|): no_prior, no_scan_term, no_room, no_launch, no_vol

| Variant | n | WR | Exp R | Capture | Δexp | Δcap |
|---|---:|---:|---:|---:|---:|---:|
| baseline_v1 | 720 | 10.0% | 0.284 | 20.8% | +0.000 | +0.0% |
| no_peak | 720 | 10.0% | 0.284 | 20.8% | +0.000 | +0.0% |
| no_bar | 720 | 10.4% | 0.287 | 21.5% | +0.003 | +0.7% |
| no_room | 720 | 9.2% | 0.276 | 23.4% | -0.008 | +2.6% |
| no_bounce | 720 | 9.9% | 0.284 | 20.3% | +0.000 | -0.5% |
| no_vol | 720 | 10.1% | 0.279 | 20.6% | -0.005 | -0.2% |
| no_prior | 720 | 9.2% | 0.271 | 20.1% | -0.012 | -0.7% |
| no_launch | 720 | 10.7% | 0.290 | 22.2% | +0.006 | +1.4% |
| no_scan_term | 720 | 10.4% | 0.293 | 21.5% | +0.009 | +0.7% |
| no_soft_extension | 720 | 10.0% | 0.284 | 20.8% | +0.000 | +0.0% |

### 2) Lookback / room horizon
- Best by expectancy: **`room_20d_plus_52w_avg`**

| Variant | n | WR | Exp R | Capture | corr(room,mfe) |
|---|---:|---:|---:|---:|---:|
| room_20d | 720 | 10.0% | 0.284 | 20.8% | 0.085 |
| room_52w | 720 | 9.9% | 0.269 | 22.2% | 0.123 |
| above_sma200 | 720 | 8.1% | 0.252 | 19.9% | 0.088 |
| room_20d_plus_52w_avg | 720 | 9.9% | 0.284 | 21.7% | — |

### 3) Vol / scan on taken slots
- Admit corr(scan,mfe)=0.027 · corr(vol,mfe)=-0.074
- Best policy by exp: **`baseline`**

**Taken by scan**

| Band | n | WR | Exp | med MFE | med MAE |
|---|---:|---:|---:|---:|---:|
| scan_<45 | 601 | 10.0% | 0.285 | 1.35% | 1.33% |
| scan_45_55 | 119 | 10.1% | 0.277 | 1.54% | 1.54% |
| scan_55_75 | 0 | 0.0% | 0.000 | 0.00% | 0.00% |
| scan_ge_75 | 0 | 0.0% | 0.000 | 0.00% | 0.00% |

**Taken by vol**

| Band | n | WR | Exp | med MFE | med MAE |
|---|---:|---:|---:|---:|---:|
| vol_<0.5 | 204 | 16.2% | 0.208 | 1.55% | 2.12% |
| vol_0.5_1 | 337 | 5.3% | 0.321 | 1.31% | 1.10% |
| vol_1_2 | 156 | 11.5% | 0.281 | 1.30% | 1.30% |
| vol_ge_2 | 23 | 13.0% | 0.431 | 2.26% | 1.69% |

**Policies**

| Policy | n | WR | Exp | Capture |
|---|---:|---:|---:|---:|
| baseline | 720 | 10.0% | 0.284 | 20.8% |
| no_scan_penalty | 720 | 10.0% | 0.284 | 20.8% |
| reward_high_vol | 720 | 10.0% | 0.282 | 20.6% |
| no_vol_dead_penalty | 720 | 9.7% | 0.277 | 20.6% |
| reward_high_vol_no_ext_pen | 720 | 10.0% | 0.282 | 20.6% |

### 4) News / catalyst
- INCONCLUSIVE — full HTF bakeoff ran with news/social off. Need powered rebuild before catalyst claims. Pilot alone underpowered.
- HTF social_missing rate: 100%
- Pilot: n=332 news>0=47 WR with=0.0425531914893617 without=0.09473684210526316

### 5) TF-mix proxies
- Best by exp then capture: **`A_1H_only`**

| Variant | n_admit | taken | WR | Exp | Capture |
|---|---:|---:|---:|---:|---:|
| A_1H_only | 3353 | 720 | 10.0% | 0.284 | 20.8% |
| B_1H_above_sma50 | 3312 | 718 | 9.6% | 0.282 | 20.6% |
| C_1H_hh_hl_20 | 3211 | 714 | 9.5% | 0.276 | 19.2% |
| D_1H_above_sma200 | 2413 | 684 | 9.6% | 0.272 | 18.2% |
| E_1H_not_far_below_sma200 | 2820 | 702 | 8.8% | 0.271 | 18.0% |

### 6) Fitted logistic + importance

- Fitted slots exp=0.267 cap=25.9% · v1 exp=0.284 cap=20.8%
- Beats v1 on exp: **False** · on capture: **True**

| Feature | coef |
|---|---:|
| hour | -0.865 |
| bar_range_pct | +0.674 |
| dist_20d_high_pct | +0.376 |
| ticker_prior_hit1r_rate | +0.253 |
| dist_52w_high_pct | -0.233 |
| close_vs_sma50 | +0.222 |
| scan_score | +0.219 |
| close_vs_sma200 | -0.179 |
| bs_green | -0.153 |
| bs_orange | +0.120 |
| peak_hour | +0.115 |
| dollar_vol_1h | -0.113 |
| vol_ratio_20 | -0.099 |
| launch_score | -0.083 |
| htf_score | +0.052 |

## What this means for the deep-look design

1. Equal 1H admit stays; rank terms should be kept only if ablation shows lift.
2. Longer lookback (52w / SMA200) is a candidate upgrade over pure 20d room — see study 2.
3. Soft-punishing hot scan/vol is **not** clearly justified — see study 3 policies.
4. Catalyst claims stay blocked until a powered news corpus exists.
5. Daily structure filters (SMA200 / HH-HL) change the list shape — trade capture vs quality.
6. Fitted importance surfaces metrics beyond the heuristic weights.

## FAIL / PASS labels

- Study 1 ablation: **PASS** (completed on corpus)
- Study 2 lookback: **PASS** (completed; best=`room_20d_plus_52w_avg`)
- Study 3 vol/scan: **PASS** (completed; best policy=`baseline`)
- Study 4 news: **FAIL / INCONCLUSIVE** (data missing on HTF corpus)
- Study 5 TF mix: **PASS** (completed; best=`A_1H_only`)
- Study 6 fitted: **PASS**
