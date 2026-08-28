# Strategy Lab + Dashboard freshness cadence

**Lab SIM = Polygon only** (15-min delay OK for marks).  
**Agent = IBKR + Supabase paper book** — never mix into Lab settle/mark.

## Cadence (US/Eastern, Mon–Fri)

| Time (ET) | Task | Action |
|-----------|------|--------|
| 09:35 | `QAlpha Strategy Lab` | `live_forward.py` ENTRY |
| **10:00–16:00 every 30m** | `QAlpha Strategy Lab Mark` | `live_forward.py --mark` |
| **~16:20** | `QAlpha Strategy Lab Settle` | `live_forward.py --settle` (primary EOD) |
| Agent | Modal ~30m + ~16:15 EOD | unchanged; separate Supabase tables |

**No 16:40 settle backup** — duplicate `--settle` sent identical Telegram twice (Aug 2026). One settle at 16:20 only.

**Why 30m marks:** Cloud dashboard autorefresh is ~90s; without mid-day Lab pushes, Strategy Lab stays on morning entry marks until 16:20. Polygon delay means 30m is enough (not 5m).

**Why EOD ~16:20:** Shortly after agent Modal EOD (~16:15).

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

## Register tasks (Aaron runs — agents do not create schtasks)

**Preferred — registers Entry + Mark + Settle, removes any 16:40 backup:**

```powershell
cd C:\Users\ajkle\OneDrive\Documents\Q-ALPHA
.\strategy_lab\register_lab_tasks.ps1
```

**Manual (unquoted `-File` paths — do NOT wrap path in extra quotes):**

```powershell
# Intraday marks — Mon–Fri, every 30 min from 10:00 for 6 hours
schtasks /Create /F /TN "QAlpha Strategy Lab Mark" /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\ajkle\OneDrive\Documents\Q-ALPHA\strategy_lab\start_lab_mark_scheduled.ps1" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 10:00 /RI 30 /DU 06:00 /RL LIMITED

# Primary EOD settle ~16:20
schtasks /Create /F /TN "QAlpha Strategy Lab Settle" /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\ajkle\OneDrive\Documents\Q-ALPHA\strategy_lab\start_lab_settle_scheduled.ps1" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 16:20 /RL LIMITED

# Morning entry 09:35
schtasks /Create /F /TN "QAlpha Strategy Lab" /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\ajkle\OneDrive\Documents\Q-ALPHA\strategy_lab\start_lab_scheduled.ps1" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 09:35 /RL LIMITED
```

## Verify

```powershell
.\venv\Scripts\python.exe strategy_lab\live_forward.py --mark
# Expect: [lab_state_sync] upserted strategy_lab_state ... force=True
# Cloud Strategy Lab caption updated_at should advance within ~90s (no redeploy)
```
