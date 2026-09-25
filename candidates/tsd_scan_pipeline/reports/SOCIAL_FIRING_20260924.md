# Peak Hour social — is it firing, and did it help?

**As of:** 2026-09-24 (Thursday). Window checked: the last few sessions with names in the repo (Wed 9/17 through Wed 9/23). Sep 22 has no named Peak Hour fill in committed notes.

## Short answer

**Is social firing on the live scan?** **Yes, inside the scan.** Polygon news and StockTwits are fetched while names are ranked, before case review and before any buy. **X (Twitter) is off**, even if a token is set. **The scan files you can open do not keep those fields**, which is why recent launch JSON looked like social never ran.

**Would social have helped pick movers vs duds over the last few days?** **No evidence it would.** This checkout has no `php_scan_*.json`, no paper book, and no Supabase or Polygon key, so decision-time news and StockTwits for those days were not saved. The names we can actually name from recent paper notes were mostly names that already moved (NUAI, GENB, GOLD). A “skip quiet social” rule cannot be scored on them, and it would have been a guess that risks cutting movers. Risk per trade was not changed in this check.

## What the live path actually does

1. The hourly job calls `run_1h_launch_scan` (`candidates/tsd_scan_pipeline/scheduler.py`).
2. That calls `rank_1h_launches` with social attachment left on (the default). Live and dry scans share this path (`tsd_1h_launch_scan.py`).
3. For every name that already passed the 1H launch check, `attach_social_to_rows` runs with **StockTwits on, X forced off, TWS news on if the broker connection works**.
4. Those numbers feed the rank score (news up to +10, StockTwits up to +8, dilution −30, distress −40, guidance cut −25). They do **not** change share size.
5. Dilution, distress, or a guidance cut can **reject** the name in case review. A missing social score does **not** reject it. The keyword `sentiment_score` on the quality gate is notes only; it never vetoes a name.
6. The Polygon key is the same key the scan already needs to start. If that key is missing, the scan stops before ranking. A scan that finishes was not running with a blank Polygon key. A failed news or StockTwits call still becomes zeros plus `social_missing=1`, and the name stays eligible.

X is not “on when a token exists.” The live call passes `include_x=False`. The helper also stays off unless a caller explicitly asks for X.

StockTwits is a live stream. Using it at scan time is fair for a live decision. Using it later to “replay” an old day looks ahead. Historical replays in `php_day_replay.py` / `php_range_replay.py` still request StockTwits; do not treat those replays as a clean backtest of social.

## Why the scan JSON looked empty

`attach_social` writes onto the in-memory row. Two files that get saved **dropped** those columns:

- `results/last_1h_launch.json` (the board)
- `results/peak_hour_scans/php_scan_*.json` (the funnel)

The local watch queue and the paper-book leg **do** keep news velocity, StockTwits, dilution, and `social_missing`. Supabase’s watch-queue row only uploads `sentiment_score`, not the StockTwits or news-velocity numbers. The position row uploads a short thesis paragraph, not the raw counts.

That matches the empty launches LUCA saw, and it matches EXP-0021’s old research corpus (`social_missing` about 96%). That corpus was a backfill with StockTwits turned off, not a readout of this week’s live scans.

**Small fix in this change:** the next scan JSON keeps the social columns. A blank means “never attached.” A zero means “fetched, nothing there.” Trading rules are unchanged. No size boost or caution overlay.

## Last few sessions — what we can actually name

Full admit / reject lists are **not in this repo**. `php_scan_*.json` and the paper book are gitignored and were not on this machine. Supabase could not be read (no key here).

Names that **are** written down in recent paper notes (PRs, not a full funnel):

| Day | Name | What the notes say | Public day move (Yahoo daily, high vs open / close vs open) | Mover or dud | Social at the decision |
|---|---|---|---|---|---|
| 2026-09-23 | NUAI | Live long. First slice banked at +2%, then the rest was sold while price was still near the high. | +5.3% / +2.3% | **Mover** (Peak Hour’s own +2% bank, and the day high) | Not saved |
| 2026-09-17 | GENB | Still open the next afternoon, 16 shares near 17.11 (that matches the 9/17 close). | +11.2% / +6.7% | **Mover** if that was the entry day | Not saved |
| 2026-09-17 | MLYS | Open 8 shares near 27.91. | +5.6% / +0.1% | **Flat-to-mild.** High got to about +6%; the close did not. | Not saved |
| by 2026-09-18 | GOLD | Closed by the +2% bank. Entry day is not in the notes. | 9/18 itself was about flat (−0.9% close) | **Mover** by the bank rule; we cannot see the entry day | Not saved |
| by 2026-09-18 | ATRC | Still open. Older entry (protective sell was near 53; price was near 58). | Not a fresh 9/17–9/23 entry | Older winner, not a new dud | Not saved |
| by 2026-09-18 | MMED | Still open, 2 shares, after a quantity mess. 9/18–9/23 daily closes were flat to down. | 9/18 −1.4% close; 9/21 −4.6% close | **Likely dud** if the entry was this week. Entry time not saved. | Not saved |

Sep 22: no Peak Hour name in the committed notes.

There is no list of names Peak Hour **rejected** those days. Missed-mover checks (did social bury a ripper?) cannot be done without the scan files.

## Would a simple social filter have helped?

Checked as a **skip / keep** rule only (same size if kept):

- dilution headline → skip
- distress headline → skip
- `social_missing` → skip
- no fresh Polygon headlines and no StockTwits messages → skip

On the six names above, every social input is missing, so the filter **cannot be applied**. It would not have a recorded reason to skip MMED, and it would not have a recorded reason to rank NUAI or GENB higher. Turning on “skip quiet social” from this sample would be a guess. NUAI and GENB were the movers; skipping them for missing social would have hurt, not helped.

What live already does, and what it does not:

- Dilution and distress **can** already knock a name out (score penalty and a case reject). That is a selection rule, already on, and we have **no saved dilution flag** from these days to show it blocked a dud or blocked a mover.
- Quiet news does **not** block a buy. It only withholds up to 10 rank points. StockTwits, when the fetch works, adds up to 8. That is too small, on its own, to explain who got the two slots versus who did not — and we cannot see the points because they were not stored.

## What to do next (no change to how big a trade is)

After the next live scan on the laptop, `php_scan_*.json` will include `news_velocity_24h`, `st_msg_24h`, `st_bull_ratio`, `social_missing`, `dilution_flag`, and `distress_flag`, plus a `social_audit` count (`news_gt0`, `st_ok`, `social_missing`). Re-run:

`python candidates/tsd_scan_pipeline/php_social_recent_study.py --days 7 --write`

If `social_missing` is high and `news_gt0` is near zero on a day the scheduler log also printed `social attach warn`, the fetch is failing. If `news_gt0` and `st_ok` are healthy, social is firing and the old JSON was just dropping the columns.

Do not use today’s StockTwits stream to judge last week. It has no “as of” time.
