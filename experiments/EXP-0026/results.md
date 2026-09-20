# EXP-0026 — Peak Hour signals × Track 100 filter + C_ratchet ($5k / 10 seats)

**Status:** `ok`

## Question

If we take **every** Peak Hour valid signal (not only seats Cap took live) and apply the Track 100 paper stack (`paper_filter_winloss_v1` + `C_ratchet_struct`) on a **$5,000 / 10-seat** book, what is the P&L?

## Signal definition (from code)

Peak Hour equal-signal / LAUNCH-eligible 1H print: last completed 1H bar with (buy_signal OR early_bull) from tsd_signals.enrich_tsd, is_equal_signal_list_candidate (not hard-extended: scan_score < 75), close hour ET in ALLOWED_HOURS {5–15}, ≥80 prior 1H bars, not structure_too_wide (structure risk > 3.5%), and causal HTF-pass membership for that session (dailies strictly before signal date: 20d range 25–150%, close>SMA50, SMA20 rising, price≥$5, 20d dollar volume ≥$5M). Equal-signal ON. Case review, 2/scan cap, popularity, momentum-rank slot pick, and Cap-taken fills are NOT applied.

## Fill rule

Fill at the next 1H bar OPEN after the signal bar close (tsd_1h_signal bars are start-labeled; next row open is the first causal 1H-grid executable print after the :15 scan). Live Peak Hour additionally micro-confirms on 1m tape and rests a pullback Limit near signal close; this study does not replay 1m micro-confirm (TIMEOUT/ABORT not modeled).

## Walk-forward

- Target window: 2026-08-18 → 2026-09-19 (Aaron-locked mini WF; do not expand to 8 months)
- Used window: **2026-08-18 → 2026-09-18** (1.0 months, snapped to available bars)
- IS signal dates ≤ **2026-09-03**; OOS after
- Filter thresholds frozen on IS only (Track 100 `paper_filter.py` — not recut on OOS)
- Rank / report: **dollar equity** (occupancy matters)

## Stack

1. Peak Hour equal-signal print → next 1H open
2. Gate: `paper_filter_winloss_v1` (skip counts reported)
3. Exit: `C_ratchet_struct` only
4. Book: $5k, 10 concurrent seats, $500/seat, 0.15% RT

## P&L (dollar book — primary)

### Peak Hour signals + paper_filter_winloss_v1 + C_ratchet_struct
- Starting cash: **$5,000.00**
- Final $: **$4,598.70**
- Total P&L: **$-401.30**
- IS-end $: **$4,982.23**
- OOS $ (book at end): **$4,598.70**
- OOS P&L (end − IS-end): **$-383.53**
- Win%: **43.2%** (132 closed)
- Max DD (equity): **-12.4%**
- n signals: **8232**
- filter pass / skip: **1443 / 6789**
- skip reasons: `{'d_px_ema200': 5504, 'h1_ema50_chg_5': 1053, 'd_sma50_200_spread': 147, 'd_sma200_chg_5': 65, 'd_ema200_chg_5': 20}`
- n fills: **132**
- occupancy skips (full / already-open / cash): **258 / 127 / 926**
- avg occupancy (at fills): **8.36 / 10**

## Side row (a) — same signals + C_ratchet, **no** Track 100 filter

### Unfiltered
- Starting cash: **$5,000.00**
- Final $: **$4,725.71**
- Total P&L: **$-274.29**
- IS-end $: **$4,878.00**
- OOS $ (book at end): **$4,725.71**
- OOS P&L (end − IS-end): **$-152.29**
- Win%: **45.8%** (107 closed)
- Max DD (equity): **-8.2%**
- n signals: **8232**
- filter pass / skip: **8232 / 0**
- skip reasons: `{}`
- n fills: **107**
- occupancy skips (full / already-open / cash): **2338 / 133 / 5654**
- avg occupancy (at fills): **8.71 / 10**

## Side row (b) — Peak Hour live-style exits

Skipped (not cheap / not the question). Live keep-profit stays on the Peak Hour book; this study does not replay it.

## Window notes

- end snapped to last available bar date 2026-09-18

**Runtime:** 837.0s

## Honesty

- No look-ahead on features, HTF membership, or fills.
- Filter medians were **not** recut on OOS.
- Live Peak Hour gates were **not** changed. No IBKR orders.
- Runner: **local Polygon** (`study_php_t100_wf_5k_local.py`) — same engine/window/IS cut as the Modal study. Modal CLI had no `MODAL_TOKEN_*` on this host; P&L is from live Polygon bars, not invented.

