# Q-ALPHA MASTER CONTEXT
## Read this in full before starting any experiment

---

## WHAT THIS SYSTEM IS

Q-Alpha is a momentum trading system targeting small/mid-cap stocks
that show 8-20% gains over 2-5 days following a catalyst event.

The system has 6 layers:
1. Catalyst Scanner    — pre-market news scan (Polygon.io news API)
2. Technical Scorer    — 28-feature setup quality score
3. Entry Engine        — 4 intraday entry patterns
4. Bracket Stop        — 4-slice ATR trailing stop (ALREADY BUILT in EXP-0012)
5. Position Sizer      — % of pool, capped per regime
6. Reporting           — results.md + Telegram (future)

---

## WHAT HAS BEEN DECIDED (DO NOT REVISIT)

### Target Variable — OPTION D
```
Label = 1 if the trade hits entry_price + 2 × ATR_stop_distance
        within 5 trading days WITHOUT first hitting the stop

Label = 0 if:
  - Stop fires first (price hits entry_price - 1.5 × ATR_14)
  - 5 days pass without either target or stop being hit
```

### Model — LightGBM
```python
import lightgbm as lgb
model = lgb.LGBMClassifier(
    n_estimators=500,
    learning_rate=0.05,
    max_depth=6,
    num_leaves=31,
    min_child_samples=20,
    subsample=0.8,
    colsample_bytree=0.8,
    class_weight="balanced",
    random_state=42,
    n_jobs=-1
)
```

### Train / Validation / Test Split
```
Train    : 2020-01-01 to 2022-12-31
Validate : 2023-01-01 to 2023-12-31  (hyperparameter tuning)
Test     : 2024-01-01 to 2025-01-01  (NEVER TOUCH until final eval)
```

### Walk-Forward Windows
```
Window 1: Train 2020-2021 → Test 2022
Window 2: Train 2020-2022 → Test 2023
Window 3: Train 2020-2023 → Test 2024 H1
Window 4: Train 2020-2024 → Test 2024 H2
```

---

## THE CATALYST CLASSIFICATION (Session 0.1)

### Tier 1 — Sustained moves (institutional re-rating)
- EARNINGS_BEAT_GUIDANCE  : Beat EPS + Revenue + Raised guidance
- FDA_APPROVAL            : Full NDA/BLA approval
- MAJOR_CONTRACT          : Contract > 20% of annual revenue
- SHORT_SQUEEZE_CATALYST  : Real catalyst + high short interest (>20% float)

### Tier 2 — Moderate reliability
- CLINICAL_TRIAL_POSITIVE : Phase 2/3 positive data
- ACTIVIST_INVESTOR       : 13D filing, board seat demand
- ANALYST_UPGRADE_MAJOR   : Goldman, Morgan Stanley, JPM upgrade
- PARTNERSHIP_MAJOR       : Named partner + revenue disclosed

### Tier 3 — Traps (avoid)
- CRYPTO_PIVOT, NAME_CHANGE_AI, REVERSE_SPLIT, LOI_ONLY

### Detection keywords for Polygon.io news API
```python
TIER1_KEYWORDS = [
    "FDA approved", "NDA approved", "BLA approved",
    "beats estimates", "raises guidance", "raises full-year",
    "awarded contract", "government contract",
]
TIER2_KEYWORDS = [
    "phase 3 results", "phase 2 results", "13D", "activist",
    "upgraded", "price target raised", "partnership",
]
TIER3_KEYWORDS = [
    "blockchain", "crypto", "reverse split",
    "letter of intent", "LOI", "rebranding",
]
```

---

## THE 28 ENTRY FEATURES (Group A — no look-ahead)

### Category 1: Catalyst (calculated pre-market)
```
F01  catalyst_tier           int      1=Tier1, 2=Tier2, 3=Tier3
F02  catalyst_type_encoded   int      label-encoded type
F03  gap_pct_premarket        float    (premarket_price / prev_close) - 1
F04  premarket_vol_ratio      float    premarket_vol / avg_premarket_vol_20d
```

### Category 2: Float + Liquidity
```
F05  float_shares_log         float    log10(float_shares)
F06  float_bucket             int      0=micro(<15M), 1=small(15-50M),
                                       2=mid(50-100M), 3=large(100-200M)
F07  dollar_vol_20d_avg        float    mean(close × volume, 20d) — prior days only
F08  short_interest_ratio      float    short_interest / float_shares
```

### Category 3: Pre-catalyst Technical Setup
```
F09  base_duration_days        int      days since prior trend ended
F10  base_depth_pct            float    (base_high - base_low) / base_high
F11  bbw_percentile_52w        float    percentile rank of BB width, 252d lookback
F12  ttm_squeeze_days          int      consecutive days squeeze active
F13  obv_slope_base            float    linear regression slope of OBV during base
F14  up_down_vol_ratio         float    avg_vol_up_days / avg_vol_down_days in base
F15  cmf_20                    float    Chaikin Money Flow 20-day
F16  rs_vs_spy_20d             float    stock_return_20d - spy_return_20d
F17  rs_vs_sector_20d          float    stock_return_20d - sector_etf_return_20d
F18  price_location_base       float    (close - base_low) / (base_high - base_low)
F19  dist_from_52w_high        float    (close - high_52w) / high_52w (negative)
```

### Category 4: Entry Pattern (calculated at signal time)
```
F20  entry_pattern_encoded     int      0=ORB, 1=VWAP, 2=FLAG, 3=HOD
F21  time_of_entry_minutes     int      minutes after 9:30 AM
F22  orb_width_pct             float    (orb_high - orb_low) / orb_low
F23  vwap_distance_at_entry    float    (price - vwap) / vwap
F24  pullback_vol_ratio        float    pullback_volume / initial_run_volume
F25  hod_test_count            int      times HOD tested before break
F26  spy_5m_slope_at_entry     float    SPY 5-min trend at entry moment
F27  cumul_turnover_at_entry   float    cumulative_volume / float_shares
F28  atr_14_normalized         float    atr_14 / close (ATR as % of price)
```

### IMPORTANT: Feature calculation rules
- All features use `.shift(1)` or prior-day data — never today's close
- 20d averages: `df['vol'].rolling(20).mean().shift(1)` — NOT including today
- News timestamps: only use articles with `published_utc` before 09:25 AM ET
- BB width percentile: `scipy.stats.percentileofscore(bbw_52w, bbw_today)`

---

## THE 11 EXIT MONITORING FEATURES (Group B — post-entry only)

These feed the position management system, NOT the entry model.
```
E01  close_vs_range_pct        (close - low) / (high - low)  — daily
E02  exhaustion_score_eod      composite 0-20 exhaustion signal
E03  upper_wick_ratio          upper_wick / total_candle_range
E04  dist_from_20ma_pct        (close - ma20) / ma20
E05  cumul_turnover_eod        total vol since catalyst / float
E06  day_n_high_vs_prior       current day high vs prior day high
E07  vol_vs_prior_day          today volume / yesterday volume
E08  gap_fill_flag             1 if open > prev_close but price < prev_close within 30m
E09  vwap_hold_eod             1 if close > vwap
E10  r_multiple_current        (current_price - entry) / risk_per_share
E11  days_held                 calendar days since entry
```

---

## THE 4 ENTRY PATTERNS

### Pattern 1: ORB (Opening Range Breakout)
```
Wait:        First 15 min (9:30-9:45 AM)
Trigger:     Close above ORB high on candle with vol > 1.5× avg
Entry:       Close of breakout candle
Stop:        Below ORB low
Best for:    Gaps 5-15% on Tier 1 catalyst, tight ORB (< 3% wide)
Skip if:     ORB width > 5%, gap > 25%
```

### Pattern 2: VWAP Pullback
```
Wait:        First pullback after open (9:45-10:30 AM)
Trigger:     Price touches VWAP on declining volume, then closes above
Entry:       First green candle reclaiming VWAP
Stop:        Below VWAP - 0.5%
Best for:    Gaps 5-20%, orderly pullback, rising VWAP
Skip if:     Pullback volume increasing (real selling, not resting)
```

### Pattern 3: Bull Flag
```
Wait:        After 5%+ initial move from open
Setup:       3-10 candles of tight consolidation, declining volume
Trigger:     Break above flag top on vol > 1.5× avg flag volume
Entry:       Close of breakout candle
Stop:        Below flag low
Target:      Flag low + flagpole height (measured move)
Skip if:     Flag lasts > 60 min, flag slope > 45 degrees
```

### Pattern 4: HOD Break
```
Wait:        HOD must hold for 20+ min, tested 2-4 times
Trigger:     Candle closes above HOD on vol > 2× avg intraday vol
Entry:       Close of breakout candle
Stop:        Below last consolidation low or VWAP
Best for:    Mid-morning second leg (10:00-11:30 AM)
Skip if:     After 1 PM, HOD tested > 5 times
```

---

## THE EXHAUSTION SCORING SYSTEM

```python
def calc_exhaustion_score(row):
    score = 0
    # Signal 1: Climactic volume candle
    if (row['volume'] > 2 * row['vol_20d_avg'] and
        row['upper_wick'] > 0.4 * row['candle_range'] and
        row['close'] < row['low'] + 0.4 * row['candle_range']):
        score += 3
    # Signal 2: Parabolic extension
    if row['dist_from_20ma'] > 0.20: score += 2
    if row['dist_from_20ma'] > 0.35: score += 3  # additional
    # Signal 3: Float exhaustion
    if row['cumul_turnover'] > 1.0: score += 2
    if row['cumul_turnover'] > 1.5: score += 3  # additional
    # Signal 4: Lower high forming
    if row['day_high'] < row['prior_day_high']: score += 2
    if row['two_consecutive_lower_highs']:       score += 4  # additional
    # Signal 5: Gap and crap (immediate full exit)
    if row['gap_fill_flag']: score += 5
    return score

# Thresholds:
# score >= 3 → exit 50%, tighten trail
# score >= 5 → exit 75%, trail 25%
# score >= 7 → full exit
# gap_fill  → IMMEDIATE full exit (no waiting)
```

---

## FLOAT + VOLUME UNIVERSE RULES

```python
UNIVERSE_RULES = {
    "price_min":          5.0,
    "price_max":          200.0,
    "float_min":          15_000_000,
    "float_max":          200_000_000,
    "avg_daily_vol_min":  500_000,
    "dollar_vol_min":     5_000_000,    # price × volume daily avg
    "volume_ratio_min":   5.0,          # Day 1 must be 5× avg for signal
    "float_turnover_min": 0.30,         # Day 1 min turnover
    "float_turnover_max": 1.50,         # Day 1 max (exhaustion)
    "gap_pct_min":        0.05,         # Min 5% pre-market gap
}
```

---

## EXISTING CODE TO REUSE (copy from EXP-0012)

### 1. process_stock() — Polygon.io data fetcher
Copy this function verbatim. It handles rate limiting, column renaming,
and basic filtering. Only change: add new feature calculations.

### 2. BracketPosition class — 4-slice trailing stop
Copy this class verbatim. Never change the stop logic.

### 3. classify_profile() — ATR-based stock profiling
Copy verbatim. Used in BracketPosition initialization.

### 4. get_regime() — SPY vs SMA50 market regime
Copy verbatim. Used to set max positions and thresholds.

### 5. score_candidate() — REPLACE this
The old scorer used Random Forest prob + basic indicators.
New scorer uses LightGBM prob + 28 momentum features.

### 6. run_simulation() — EXTEND this
Keep the day-by-day loop structure.
Add: catalyst gate check before entering positions.
Add: float turnover check (cumul_turnover_at_entry < 1.5).

---

## EXPERIMENT NAMING CONVENTION

```
EXP-0012 : Current baseline (Random Forest, large caps, simple features)
EXP-0013 : New universe + LightGBM + Option D label (no catalyst filter yet)
EXP-0014 : Add catalyst scoring layer
EXP-0015 : Add intraday entry patterns
EXP-0016 : Add exhaustion exit scoring
EXP-0017 : Full system integration
```

Each experiment must beat EXP-0012's Sharpe of 1.64 to be worth pursuing.

---

## HOW TO RUN AN EXPERIMENT

```bash
# From Q-ALPHA project root in terminal:
modal run experiments/EXP-0013/experiment13.py

# Results saved to:
experiments/EXP-0013/results.md
```

---

## WHAT GOOD RESULTS LOOK LIKE

```
Metric              EXP-0012 (baseline)    Target for new system
─────────────────────────────────────────────────────────────────
Sharpe Ratio        1.64                   >= 1.5 (maintain)
Max Drawdown        -3.17%                 >= -15% (tighter preferred)
Total Return        +15.59%                > EXP-0012
Win Rate            39.76%                 > 40% (momentum typically higher)
Trades/Day          0.17                   0.1 - 0.5 (quality over quantity)
Walk Forward        STRONG PARTIAL PASS    3/4 or 4/4 pass
Monte Carlo         (check results.md)     p-value < 0.05
```
