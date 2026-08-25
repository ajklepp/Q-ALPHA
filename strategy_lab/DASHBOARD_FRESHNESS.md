# Strategy Lab + Dashboard freshness cadence

**Lab SIM = Polygon only** (15-min delay OK for marks).  
**Agent = IBKR + Supabase paper book** — never mix into Lab settle/mark.

## Cadence (US/Eastern, Mon–Fri)

| Time (ET) | Task | Action |
|-----------|------|--------|
| 09:35 | `QAlpha Strategy Lab` | `live_forward.py` ENTRY |
| **10:00–16:00 every 30m** | `QAlpha Strategy Lab Mark` | `live_forward.py --mark` |
| **~16:20** | `QAlpha Strategy Lab Settle` | `live_forward.py --settle` (primary EOD) |
| 16:40 | `QAlpha Strategy Lab Settle Backup` | same `--settle` (optional safety net) |
| Agent | Modal ~30m + ~16:15 EOD | unchanged; separate Supabase tables |

**Why 30m marks:** Cloud dashboard autorefresh is ~90s; without mid-day Lab pushes, Strategy Lab stays on morning entry marks until 16:20. Polygon delay means 30m is enough (not 5m).

**Why EOD ~16:20:** Shortly after agent Modal EOD (~16:15). 16:40 kept as optional backup only.

## What `--mark` does

Same engine as `--settle` (`settle_open_positions`):
- Refresh minute/daily bars for opens
- Update `mark_price` / `mark_usd` / `residual_tranche_ids` / `slots_open`
- Book closes if strategies fully exit
- Force-upsert `strategy_lab_state` (`updated_at` moves)

Quieter than settle: no Telegram unless a position **closes** during the mark.

## Dashboard

- Global `st_autorefresh` **90s** (was 5 min) so Live Status + Strategy Lab pick up new Supabase rows without manual rerun / redeploy.
- Strategy Lab tab still prefers anon `strategy_lab_state`, then local `forward_state.json`.

## Register tasks (YOU run — agents do not create schtasks)

```powershell
# Intraday marks — Mon–Fri, every 30 min from 10:00 for 6 hours
schtasks /Create /F `
  /TN "QAlpha Strategy Lab Mark" `
  /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"C:\Users\ajkle\OneDrive\Documents\Q-ALPHA\strategy_lab\start_lab_mark_scheduled.ps1`"" `
  /SC WEEKLY /D MON,TUE,WED,THU,FRI `
  /ST 10:00 /RI 30 /DU 06:00 `
  /RL LIMITED

# Primary EOD settle ~16:20
schtasks /Create /F `
  /TN "QAlpha Strategy Lab Settle" `
  /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"C:\Users\ajkle\OneDrive\Documents\Q-ALPHA\strategy_lab\start_lab_settle_scheduled.ps1`"" `
  /SC WEEKLY /D MON,TUE,WED,THU,FRI `
  /ST 16:20 `
  /RL LIMITED

# Optional backup 16:40
schtasks /Create /F `
  /TN "QAlpha Strategy Lab Settle Backup" `
  /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"C:\Users\ajkle\OneDrive\Documents\Q-ALPHA\strategy_lab\start_lab_settle_scheduled.ps1`"" `
  /SC WEEKLY /D MON,TUE,WED,THU,FRI `
  /ST 16:40 `
  /RL LIMITED
```

## Verify

```powershell
.\venv\Scripts\python.exe strategy_lab\live_forward.py --mark
# Expect: [lab_state_sync] upserted strategy_lab_state ... force=True
# Cloud Strategy Lab caption updated_at should advance within ~90s (no redeploy)
```
