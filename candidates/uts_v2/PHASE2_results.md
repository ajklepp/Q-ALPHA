# UTS v2 Phase 2 — Quality + History Gate

**Date:** 2026-09-01  
**Status:** IMPLEMENTED

## Problem

Original Phase 2 plan (`research_gate.py`) would have **vetoed** entries on low news/sentiment scores. User edge: 3H LAUNCH signals often fire **before** news (algo/insider tape). Many big movers have no catalyst at signal time.

**Phase 2 is NOT a research veto.** News is context only.

## `quality_history_gate.py`

### Hard block ONLY

| Check | Threshold |
|-------|-----------|
| `passes_instrument_safety` | must pass |
| `market_cap` | ≥ $300M (when known) |
| `dollar_vol_20d` | ≥ $5M (when known) |
| `price` | ≥ $5 |
| `analog_count` | ≥ 30 |
| `analog_win_rate` | ≥ 40% (WIN vs LOSS on 5d MAE/MFE) |
| `phase` | ≠ EXTENSION |
| `fundamental_distress` | SPEC lane only (size_mult 0.5), not hard block |

### NEVER block on

- `news_catalyst` false / `catalyst_tier` 0 / no headlines
- Negative sentiment at entry

### Soft tags (after pass)

| Condition | Tag | size_mult |
|-----------|-----|-----------|
| No news in 48h | `pre_catalyst` | 1.0 |
| Tier-1 catalyst present | `catalyst_confirmed` | +5 launch_score **display** only |
| Short interest ≥ 20% | `squeeze_candidate` | up to 1.15 |
| Float < 15M | `low_float_spec` | × 0.5 |
| Fundamental distress | `spec_lane` | × 0.5 |

### Queue context fields (post-pass)

`news_summary`, `catalyst_tier`, `sentiment_score`, `pre_catalyst`, `tags`, `size_mult`, `analog_count`, `analog_win_rate`

## Admission flow

```
LAUNCH gates (tsd_entry_gates)
  → quality_history_gate (hard)
  → fetch_news_context + apply_soft_tags (context only)
  → tsd_watch_queue.json
```

## Sample queue rows (Chat A)

### ZIP — pre-catalyst (no news, PASSES)

```json
{
  "symbol": "ZIP",
  "phase": "LAUNCH",
  "launch_score": 72.5,
  "scan_score": 34.0,
  "analog_count": 42,
  "analog_win_rate": 52.4,
  "pre_catalyst": true,
  "catalyst_tier": 0,
  "sentiment_score": 0.0,
  "news_summary": "🔀 No Catalyst: No news found — possible technical move",
  "tags": ["pre_catalyst"],
  "size_mult": 1.0,
  "status": "WATCHING"
}
```

### ROOT — catalyst confirmed (news present, PASSES)

```json
{
  "symbol": "ROOT",
  "phase": "LAUNCH",
  "launch_score": 68.0,
  "launch_score_display": 73.0,
  "scan_score": 38.0,
  "analog_count": 35,
  "analog_win_rate": 48.6,
  "pre_catalyst": false,
  "catalyst_tier": 1,
  "sentiment_score": 0.6,
  "news_summary": "📈 Earnings Beat: Q2 revenue beat, guidance raised",
  "tags": ["catalyst_confirmed"],
  "size_mult": 1.0,
  "status": "WATCHING"
}
```

Both pass if LAUNCH + quality gates OK. News difference affects **tags only**, not admission.

## Tests

```
py -3 -m unittest tests.test_quality_history_gate -v
py -3 -m unittest tests.test_tsd_watch_queue -v
```

## Supersedes

Phase 1 `PHASE1_results.md` "Next (Phase 2)" research veto plan — replaced by this gate.
