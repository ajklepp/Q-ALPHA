# Entry-Timing Study — Q-ALPHA Self-Learning Layer

A **read-only, offline** research harness that replays how different entry-timing
rules *would have* performed on each day's scan candidates, using Polygon 1-minute
bars. It does **not** trade, place orders, or touch `paper_trades.json` /
`pool_state.json`. Safe to run anytime after the close.

Its purpose: build an **unbiased dataset** to answer *"which entry timing wins,
and which candidates should we skip?"* — measured from real data, not guessed.

---

## How to run

```powershell
# one day
python candidates\entry_study.py 2026-08-12

# a date range (weekdays only)
python candidates\entry_study.py 2026-08-11 2026-08-15

# most recent scan
python candidates\entry_study.py
```

Requires a `daily_scan_<date>.json` file for that day (the scanner's output) and
`POLYGON_API_KEY` in `.env`. Re-running a date is safe — it **replaces** that
date's rows in `rollup.csv` (no double-counting).

---

## What it produces

| File | Contents |
|------|----------|
| `entry_study/<date>.jsonl` | One row per candidate × per rule — the full detail (entry price, exit, R, MFE, reason). Taken AND skipped candidates, so no survivorship bias. |
| `entry_study/rollup.csv` | Excel-friendly per-rule daily summary. This is your decision dataset. |

Both are **generated artifacts** and are git-ignored (reproducible by re-running).

---

## The 5 entry rules

Each rule decides *when* to enter; all then use the **same single-bracket 2R exit**
(exit 100% at 2R target or at the structure stop, matching the live broker order).

| Rule | Entry trigger |
|------|--------------|
| `immediate` | Enter at 09:32 market (min-wait baseline). |
| `vwap_reclaim` | Enter on the first 1-min close back above VWAP after 09:32. |
| `orb_breakout` | Enter on a break above the first 5-minute high (opening-range breakout). |
| `pullback_go` | Enter on a reclaim after a dip back toward VWAP. |
| `live_logic` | **The baseline.** A 1-minute replica of the *actual* live `watch_and_enter` gate: gap-holding + above-VWAP + volume-confirming + not-dumping + structure intact + min-wait. Every other rule is judged "better/worse than what we do live now." |

---

## How to read `rollup.csv`

Columns:

| Column | Meaning |
|--------|---------|
| `date` | Trading day. |
| `rule` | One of the 5 rules above. |
| `n_candidates` | Candidates the rule evaluated that day. |
| `n_entered` | How many it actually entered. |
| `entry_rate` | `n_entered / n_candidates`. |
| `avg_r` | Average R across **entered** trades. |
| `expectancy_r` | Average R per entered trade (the number that matters for edge). |
| `win_rate` | Fraction of entered trades with R > 0. |
| `n_target` / `n_stop` / `n_time` | Exit-reason breakdown (hit 2R / stopped out / timed out). |
| `avg_mfe_r` | Average Maximum Favorable Excursion in R — how far in your favor trades got **before** exiting. Key for the 2R-vs-trailing decision: if `avg_mfe_r` is much higher than `avg_r`, a trailing stop might capture more. |
| `avg_entry_time` | Average entry clock time. |

---

## ⚠️ How to interpret it (read this before drawing conclusions)

1. **Do NOT conclude anything from 1–2 days.** A single day is one sample. You need
   roughly **2 weeks (~30–50 candidates)** before any rule comparison is meaningful.
2. **Compare rules against `live_logic`.** That's the current live behavior. A rule
   is only worth switching to if it beats `live_logic` on `expectancy_r` **and**
   isn't just luckier on a tiny sample.
3. **Segment before deciding.** Once you have data, split by market regime
   (BULL/BEAR) and by gap size / RVOL bucket — the best entry rule may differ by
   context. That segmentation is the actual "learning."
4. **This is a 1-minute approximation.** Live entries use 5-second TWS bars, so the
   study's absolute R won't perfectly match live fills. It's reliable for *relative*
   rule comparison (the question we're asking), not for predicting exact live P&L.

---

## Where this fits in the self-learning roadmap

- **Now:** run daily; let `rollup.csv` accumulate honest, bias-free data.
- **~2 weeks:** review `rollup.csv` → make the **first data-driven decision**
  (which entry rule; 2R vs. trailing via `avg_mfe_r`).
- **After IBKR data upgrade:** add a live 5-second "decision tape" (Track A) to close
  the 1-min-vs-5-sec gap and quantify how much the better feed helps.
- **Later:** let the agent *shadow-select* the winning rule and A/B it against the
  current gate **before** it ever changes live behavior.

The learning stays **advisory** until you review it and decide. The harness can
never silently change how the system trades.
