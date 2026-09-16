# Equal-signal Peak Hour counterfactual — 2026-09-14 (ET)

**Research / report only.** Live scheduler, continuation_score v1.6, case LLM,
and take rules on `main` are unchanged. Do not merge this as a live enablement.

- Overlay version: `research_v1`
- Take cap used: **`MAX_NEW_ENTRIES_PER_SCAN = 2`** (live, 2 NEW / hour)
- Hard-extension (primary): **ON** — `scan >= 75` still excluded
- Soft stage demotion: **neutralized** (phase −15, scan-band terms, launch-score beauty,
  phase→`bar_state=extended` hard-block leak)
- Case: **rules-only** (`rules_equal_signal`). Live LLM ENTER confidences are **not** replayed.
- Trails: out of scope (unchanged).

## Methodology

1. Start from 1H signal passers (`buy_signal` / `early_bull`).
2. Admit every passer equally unless hard-extension fires.
3. Rank with popularity / momentum / RS / vol / room / tape / HTF — **not** LAUNCH vs EXTENSION.
4. Rebuild attention (top-K ∪ tradable-popular ∪ soft-extension *admission* lane).
5. Rules case decides ENTER/WAIT/REJECT; **scan ≤ 55 is not required** for ENTER.
6. `select_enter_rows` still requires tradable popularity and the 2/hour cap.

Live v1.6 still **grades** stage (`DESIGN.md`: “do not equalize all bars”). This file tests
the opposite philosophy without shipping it.

### Hard-extension vs the live leak

Live `classify_bar_state()` returns `extended` when `phase == EXTENSION` **or** `scan >= 75`.
`evaluate_1h_symbol` then rejects `extension_hard`. Soft EXTENSION (scan 65–74 + trend ≥ 0.7)
therefore becomes a **hard block**, even though comments say only scan≥75 is hard.
This counterfactual keeps scan≥75 and uses **OHLC-only** bar_state for tape points.

### Data limits (this cloud clone)

- Scan funnels found for 2026-09-14: **0**
- `missed_ledger.json`: **no** (gitignored; absent here)
- HTF cache / `POLYGON_API_KEY`: not available for a full-universe bar replay
- Actual takes below are **operator-supplied (AARON)** unless a funnel is present

## Actual day baseline

| Hour ET | Actual take (queued) | Fill | Evidence | Notes |
|---:|---|---|---|---|
| 05 | TARS | — | AARON | Take queued (fill not documented in repo) |
| 06 | HOOD | — | AARON | Take queued (fill not documented in repo) |
| 07 | — | — | UNKNOWN | No take named in operator baseline or repo artifacts |
| 08 | **(aborted — no scan)** | — | AARON+CODE | Hour 8 aborted — DUE 08:15, TICK END exit=-1 at 08:23:45, no SCAN. Counterfactual cannot invent passers without a scan or bar replay. |
| 09 | IRD | IRD FILLED 3.63 | AARON | IRD FILLED +$3.63 (operator). Repo ops audit did not independently see IRD. |
| 10 | SNDK | — | AARON | Take queued (fill not documented in repo) |
| 11 | — | — | UNKNOWN | Afternoon unauditable from repo |
| 12 | — | — | UNKNOWN | Afternoon unauditable from repo |
| 13 | — | — | UNKNOWN | Afternoon unauditable from repo |
| 14 | — | — | UNKNOWN | Afternoon unauditable from repo |
| 15 | — | — | UNKNOWN | Afternoon unauditable from repo |

Carry / ops (not new Peak Hour takes): ATRC residual + NX trail exit morning of 09-14
(ops audit). L2 logger used HOOD/MSTR/TARS as depth symbols — not entry evidence.

## Hour-by-hour actual vs equal-signal counterfactual

Primary table = **hard-extension ON**. Case source = rules-only.

| Hour | Actual take | CF top attention | CF would-take (≤2) | OKTA | WIX | COIN |
|---:|---|---|---|---|---|---|
| 05 | TARS | UNKNOWN (no php_scan JSON) | UNKNOWN (no passer set) | UNKNOWN | UNKNOWN | UNKNOWN |
| 06 | HOOD | UNKNOWN (no php_scan JSON) | UNKNOWN (no passer set) | UNKNOWN | UNKNOWN | UNKNOWN |
| 07 | — | UNKNOWN (no php_scan JSON) | UNKNOWN (no passer set) | UNKNOWN | UNKNOWN | UNKNOWN |
| 08 | GAP (abort) | — | — | GAP | GAP | GAP |
| 09 | IRD | UNKNOWN (no php_scan JSON) | UNKNOWN (no passer set) | UNKNOWN | UNKNOWN | UNKNOWN |
| 10 | SNDK | UNKNOWN (no php_scan JSON) | UNKNOWN (no passer set) | UNKNOWN | UNKNOWN | UNKNOWN |
| 11 | — | UNKNOWN (no php_scan JSON) | UNKNOWN (no passer set) | UNKNOWN | UNKNOWN | UNKNOWN |
| 12 | — | UNKNOWN (no php_scan JSON) | UNKNOWN (no passer set) | UNKNOWN | UNKNOWN | UNKNOWN |
| 13 | — | UNKNOWN (no php_scan JSON) | UNKNOWN (no passer set) | UNKNOWN | UNKNOWN | UNKNOWN |
| 14 | — | UNKNOWN (no php_scan JSON) | UNKNOWN (no passer set) | UNKNOWN | UNKNOWN | UNKNOWN |
| 15 | — | UNKNOWN (no php_scan JSON) | UNKNOWN (no passer set) | UNKNOWN | UNKNOWN | UNKNOWN |

If you drop this script onto the laptop next to `results/peak_hour_scans/php_scan_20260914_*.json`,
re-run and the UNKNOWN cells fill from live passers (still rules-only case).

## Mechanism proof (same other factors, synthetic)

Two 1H passers identical except scan/trend (LAUNCH 35 vs EXTENSION 68 / 0.75).

| Name | scan | phase | live list? | leak hard-block? | live cont | equal cont |
|---|---:|---|---|---|---:|---:|
| LAUNCHY | 35.0 | LAUNCH | True | False | 89.94 | 75.14 |
| EXTENDY | 68.0 | EXTENSION | False | True | 7.54 | 75.14 |
| HARDX | 80.0 | EXTENSION | False | False | 7.54 | 68.14 |

Live continuation gap LAUNCH − EXTENSION = **82.4 pts** (stage, not tape/room/popularity).
Equal-signal gap = **0.0 pts** (should be ~0 given identical non-stage features).

- Soft EXTENSION live list: `False` — leak hard-block `True`
- Soft EXTENSION equal list: `True`
- Hard scan=80 live list: `False` equal list (hard ON): `False`
- Primary CF attention: LAUNCHY, EXTENDY
- Primary CF would-take: LAUNCHY, EXTENDY

### Sensitivity — hard-extension also off

Extra names vs primary: **HARDX** (ranked with hard off: LAUNCHY, EXTENDY, HARDX).
Primary tables keep hard-extension **ON**.

## OKTA / WIX / COIN

Aaron: big missed runners on the launch list, ~+10% ran-up, blamed on EXTENSION demotion /
attention / case+popularity. `missed_ledger.json` is **not in this clone**.

| Symbol | Ledger ran-up | Public same-day (best effort) | Prior-close room vs 20d high |
|---|---|---|---|
| OKTA | +13.95% high vs prior close (public daily; NOT missed_ledger) | close 186.45 (+11.98% vs prev) · high +13.95% | 7.2% |
| WIX | +11.33% high vs prior close (public daily; NOT missed_ledger) | close 83.33 (+8.81% vs prev) · high +11.33% | 20.9% |
| COIN | +10.25% high vs prior close (public daily; NOT missed_ledger) | close 191.45 (+9.24% vs prev) · high +10.25% | 10.5% |
| TARS | +1.17% high vs prior close (public daily; NOT missed_ledger) | close 79.95 (+0.81% vs prev) · high +1.17% | 13.4% |
| HOOD | +3.35% high vs prior close (public daily; NOT missed_ledger) | close 114.33 (+1.56% vs prev) · high +3.35% | 10.1% |
| IRD | +2.98% high vs prior close (public daily; NOT missed_ledger) | close 5.97 (-1.16% vs prev) · high +2.98% | 14.6% |
| SNDK | -3.15% high vs prior close (public daily; NOT missed_ledger) | close 1551.99 (-4.98% vs prev) · high -3.15% | 9.6% |

Public daily is **not** the Peak Hour missed-ledger definition (peak since signal ref).
It does confirm OKTA/WIX/COIN were the day’s large runners vs the names actually queued.

### Illustrative watchlist (assumed scans — labeled)

ILLUSTRATIVE watchlist — scan_score assumed EXTENSION-class for OKTA/WIX/COIN and LAUNCH-class for actual takes. Not live funnel.

| Symbol | live list | leak block | live cont | equal cont | attention case |
|---|---|---|---:|---:|---|
| WIX | False | True | 17.44 | 85.04 | ENTER (`rules_equal_signal`) reasons=top_continuation,recent_leaderboard,polygon_gainer_today,tws_scanner,soft_extension |
| IRD | True | False | 98.88 | 84.78 | ENTER (`rules_momentum+equal_signal`) reasons=top_continuation |
| TARS | True | False | 98.08 | 83.98 | ENTER (`rules_momentum+equal_signal`) reasons=top_continuation |
| COIN | False | True | 14.44 | 82.04 | ENTER (`rules_equal_signal`) reasons=top_continuation,recent_leaderboard,polygon_gainer_today,tws_scanner,soft_extension |
| HOOD | True | False | 94.48 | 81.78 | ENTER (`rules_momentum+equal_signal`) reasons=top_continuation,recent_leaderboard,tws_scanner |
| SNDK | True | False | 92.74 | 81.44 | WAIT (`rules_equal_signal_fail_closed`) reasons=top_continuation,recent_leaderboard |
| OKTA | False | True | 12.24 | 79.84 | WAIT (`rules_equal_signal_fail_closed`) reasons=recent_leaderboard,polygon_gainer_today,tws_scanner,soft_extension |

- Equal-signal would-take (cap 2, rules+popular): **WIX, IRD**
- Rules ENTER set: **WIX, IRD, TARS, COIN, HOOD**

In this assumed set, a name the live leak would hard-block (WIX) can consume a 2/hour
slot on room+momentum+popularity. That is the philosophy working — not a live fill.

**Hypothesis check:** neutralizing soft-EXTENSION (including the phase→extended leak)
does surface OKTA/WIX/COIN-class names into the list + attention in this construction;
live list gate often **excludes** them as `extension_hard` even when scan is 66–68.
Whether they **would have been taken** still depends on popularity + constructive room
(OKTA prior-close room **7.2% < 10%** → live/equal rules ENTER needs CONSTRUCTIVE_ROOM,
so OKTA may WAIT on rules-only even after equal admission). WIX room 20.9% and COIN 10.5%
can rules-ENTER if treated as tradable-popular. **Do not treat the would-take list as a
live fill list** — LLM case is missing and the passer set is assumed.

## What this does *not* prove

- Full-universe 09-14 take list (needs `php_scan_20260914_*.json` or Polygon HTF replay)
- Live LLM case verdicts / ENTER confidence
- Hour 8 passers (scan never ran)
- Trail P&L for counterfactual takes

## How to complete this on the laptop

```text
py -3 candidates/tsd_scan_pipeline/php_equal_signal_counterfactual.py --date 2026-09-14
```

Expects `candidates/tsd_scan_pipeline/results/peak_hour_scans/php_scan_20260914_*.json`
and optionally `missed_ledger.json`. Rewrites this markdown from real passers.

