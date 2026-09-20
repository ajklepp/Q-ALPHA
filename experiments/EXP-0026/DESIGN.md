# EXP-0026 — Peak Hour signals × Track 100 $5k walk-forward

**Study only. No live gate changes. No IBKR orders.**

## Question

If we take **every Peak Hour valid signal** (not only seats Cap took live)
and apply the **Track 100 paper stack** (`paper_filter_winloss_v1` +
`C_ratchet_struct`) on a **$5,000 / 10-seat** book, what is the dollar P&L?

## Signal definition (code)

From `tsd_scan_pipeline` with `PHP_EQUAL_SIGNAL` ON (default):

A print is a Peak Hour **equal-signal / LAUNCH-eligible** 1H bar when **all** hold:

1. `buy_signal` OR `early_bull` — `tsd_signals.enrich_tsd` (WT cross / Pine early-bull)
2. `is_equal_signal_list_candidate` — not hard-extended (`scan_score >= 75`)
3. Bar **close hour ET** in `ALLOWED_HOURS` `{5–15}` (`tsd_1h_signal`)
4. ≥ `HTF_1H_BARS_MIN` (80) completed 1H bars before the signal bar
5. Not `structure_too_wide` — same reject as `evaluate_1h_buy_signal` (risk > 3.5%)
6. Symbol is in the **causal HTF-pass** set for that session: dailies with
   `date < signal_date` pass `compute_htf_metrics` (20d range 25–150%, close>SMA50,
   SMA20 rising, price≥$5) and 20d dollar volume ≥ $5M

**Not applied:** case review, 2/scan cap, popularity, momentum-rank slot pick,
micro-confirm ABORT/TIMEOUT, Cap-taken live fills only.

## Fill rule

**Next 1H open** after the signal bar (Polygon 1H start-labeled; next row `open`).

Live Peak Hour scans at `:15` and may rest a pullback Limit after 1m micro-confirm
near signal close. This study does not replay 1m tape. Next 1H open is the first
causal 1H-grid print after that scan window.

## Track 100 stack (frozen — do not recut)

- Filter: `paper_filter_winloss_v1` from track-100 `paper_filter.py` (IS medians
  already frozen; study book split IS ≤ **2026-09-03**)
- Exit: exact `C_ratchet_struct` from `paper_exit.py` / `trail_exits.py`
- Book: $5k, 10 seats, $500/seat, 0.15% RT, **dollar equity**

Vendor on the laptop: `py -3 experiments/EXP-0026/vendor_from_track100.py`

That copy **must** include `features.py` (paper_filter does `from features import ...`).
The Modal image mounts `vendor_track100/*.py`. Missing `features.py` is the
confirmed walk-forward crash after the Peak Hour scan.

## Window

- Aaron-locked: **2026-08-18 → 2026-09-19**
- IS / OOS cut: **2026-09-03** (mid-window mini walk-forward)
- Do **not** expand back to 8 months
- Filter medians stay frozen inside Track 100 `paper_filter.py` (not recut on OOS)

## Honesty

- Features, HTF membership, and fills use only data available at signal / fill time
- Filter thresholds are not recut on OOS
- No invented P&L if Polygon secret or Track 100 modules are missing
