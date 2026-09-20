# EXP-0026 — Peak Hour signals × Track 100 filter + C_ratchet ($5k / 10 seats)

**Status:** `blocked`

## Question

If we take **every** Peak Hour valid signal (not only seats Cap took live) and apply the Track 100 paper stack (`paper_filter_winloss_v1` + `C_ratchet_struct`) on a **$5,000 / 10-seat** book, what is the P&L?

## Signal definition (from code)

Peak Hour equal-signal / LAUNCH-eligible 1H print: last completed 1H bar with (buy_signal OR early_bull) from tsd_signals.enrich_tsd, is_equal_signal_list_candidate (not hard-extended: scan_score < 75), close hour ET in ALLOWED_HOURS {5–15}, ≥80 prior 1H bars, not structure_too_wide (structure risk > 3.5%), and causal HTF-pass membership for that session (dailies strictly before signal date: 20d range 25–150%, close>SMA50, SMA20 rising, price≥$5, 20d dollar volume ≥$5M). Equal-signal ON. Case review, 2/scan cap, popularity, momentum-rank slot pick, and Cap-taken fills are NOT applied.

## Fill rule

Fill at the next 1H bar OPEN after the signal bar close (tsd_1h_signal bars are start-labeled; next row open is the first causal 1H-grid executable print after the :15 scan). Live Peak Hour additionally micro-confirms on 1m tape and rests a pullback Limit near signal close; this study does not replay 1m micro-confirm (TIMEOUT/ABORT not modeled).

## Walk-forward

- Target window: 2026-08-18 → 2026-09-19 (Aaron-locked mini WF; do not expand to 8 months)
- Used window: **2026-08-18 → 2026-09-19** (1.1 months)
- IS signal dates ≤ **2026-09-03**; OOS after
- Filter thresholds frozen on IS only (Track 100 `paper_filter.py` — not recut on OOS)
- Rank / report: **dollar equity** (occupancy matters)

## Stack

1. Peak Hour equal-signal print → next 1H open
2. Gate: `paper_filter_winloss_v1` (skip counts reported)
3. Exit: `C_ratchet_struct` only
4. Book: $5k, 10 concurrent seats, $500/seat, 0.15% RT

## P&L

**No invented P&L.** This VM could not finish a live Polygon walk-forward.
Reason: `polygon_secret_or_track100_modules_not_on_this_vm`

Track 100 is a private sibling repo; this VM cannot vendor `features.py`.
Run on the laptop (Track 100 sibling + Modal Polygon secret):

```powershell
cd C:\Users\ajkle\Documents\Q-ALPHA
git fetch && git checkout cursor/exp0026-vendor-features-fa9c
py -3 experiments\EXP-0026\vendor_from_track100.py
.\venv\Scripts\python.exe -m modal run experiments/EXP-0026/study_php_t100_wf_5k_modal.py
```

That copy must include `features.py` (`paper_filter.py` does `from features import ...`).
Artifacts overwrite `experiments/EXP-0026/results.md` and `results.json`.

## Honesty

- No look-ahead on features, HTF membership, or fills.
- Filter medians were **not** recut on OOS.
- Live Peak Hour gates were **not** changed. No IBKR orders.

