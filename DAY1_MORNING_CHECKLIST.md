# Q-ALPHA — Day 1 Morning Checklist (2026-08-18)

Everything below was fixed & verified the night before. This is what to
watch and what NOT to do at the open.

---

## ⛔ THE ONE RULE — do NOT run this
**Do NOT run `pre_market_scanner.py` locally before the open.**
It triggers the ~5,000-symbol universe rebuild (the scan-window landmine).
Tomorrow's Task Scheduler run uses `load_universe()` on the 300-name static
list — leave it alone and let it run.

---

## ✅ Pre-open (before 9:20 ET) — 2-minute sanity check
Run from project root with venv active:

```powershell
# 1. Confirm state is clean Day-1 on the Modal volume
python -m modal volume get qalpha-state pool_state.json -
#    expect: pool 3000.0, total_trades 0, open_positions 0

python -m modal volume get qalpha-state pending_approvals.json -
#    expect: candidates [], date 2026-08-18 (or empty) — NO APMD/MGTX/KEX

# 2. Confirm git is clean and pushed
git status   # -> "up to date with origin/main", "working tree clean"
```

---

## 👀 During the day — what healthy looks like on Telegram

**9:20 AM — Scan**
- Regime shows a REAL read (BULL today, or BEAR only if truly bear).
  - If it can't get SPY data it should ALERT, never silently say BEAR.
- Candidates have real scores (not all 0) and real gap/vol numbers.

**On any entry (9:32–11:00)**
- TWS receives: ONE 100% market buy + ONE 100% stop + ONE 100% limit @ 2R.
- Ledger agrees (single-bracket 2R model). No partial scale-out, no T3 trail.
- Position exits 100% at 2R OR 100% at stop OR 100% at time (5 days).

**4:15 PM — EOD report**
- Pool / positions / P&L match reality.
- NO phantom NEBX/NBIG/NBIL. NO fake STOP HITs on positions you don't hold.

---

## 🚩 Red flags — stop and investigate if you see:
- "Regime: BEAR" on a clearly-bull day  -> regime data failure (should alert now)
- Any position in NEBX / NBIG / NBIL / NBIS -> ban filter bypassed
- EOD report P&L that doesn't match open positions -> books/broker drift
- PGRST204 / "column ... schema cache" error -> a new field hit Supabase

---

## 📊 What we're collecting for the 2-week review
- **MFE (mfe_r, mfe_price)** logged per trade in `paper_trades.json`.
  In ~2 weeks, analyze MFE to decide 2R vs trailing-stop EMPIRICALLY.

---

## 🔧 Still open (NOT blockers for Day 1 — do later)
1. Scan-window overrun fix (real landmine in committed code, just not on
   today's path). Fix `refresh_universe` timing before ever using the
   Modal scanner (Route A) live.
2. Supabase has unused legacy columns (pnl vs pnl_dollars, updated_at vs
   last_updated) — reconcile eventually.
3. Decide if MFE should sync to Supabase for dashboard charting (needs
   ALTER TABLE + add to TRADE_FIELDS). Local JSON is fine for now.
4. Backups + migrations are OFF in Supabase — turn on before scaling.

---

## Tonight's 8 fixes (all pushed to origin/main @ ea9ca13)
1. c26b413  Fix silent BEAR regime when SPY data unavailable
2. 48a4de4  Reconcile Supabase trades schema (PGRST204)
3. 7f6187f  Add Day-1 state reset script
4. dc911c2  Block leveraged ETFs / funds with a real universe filter
5. 4cf87f5  Stop dropping throttled quotes / late-open entries
6. 97f7a8b  Refuse stale approvals instead of warning
7. c579cf2  Capture opening candle by timestamp; skip on incomplete bars
8. ea9ca13  Single-bracket 2R model: books match broker + MFE logging
