# Track 100 moved

This study now lives in its **own repository**:

- Local: `C:\Users\ajkle\Documents\Track 100`
- GitHub: https://github.com/ajklepp/track-100

Open that folder as a separate Cursor project (Agents → Repositories).
It is intentionally **not** part of Q-ALPHA live paper. Q-ALPHA does
**not** place Track 100 IBKR orders.

## Dashboard display (Q-ALPHA read-only)

The Live Paper **Track 100** tab reads a paper ledger written by Track 100.
It never writes IBKR orders.

### Read path (first existing file wins)

1. `TRACK100_PAPER_BOOK` — explicit JSON file
2. `TRACK100_ROOT/results/paper_book.json`
3. Sibling clones next to `Q-ALPHA`:
   - `../Track 100/results/paper_book.json`
   - `../Track100/results/paper_book.json`
   - `../track-100/results/paper_book.json`

Missing file is a valid empty state: *No paper book yet — run Track 100
`scan_live`*. Dated `results/scan_live_*.json` artifacts may fill last-scan
status only.

### Writer schema (Track 100 `scan_live` / paper logger)

Playbook v12: deepest OS → retest **signal-low** → next **1H open**.
Exit book: **5% stop / 15% target**. No IBKR order fields.

```json
{
  "version": 1,
  "strategy": "Track100_v12",
  "updated_et": "2026-09-16T12:00:00-04:00",
  "last_scan": {"date_et": "2026-09-16", "ended": "12:00 ET", "exit_code": 0},
  "waiting_retest": [{"symbol": "MU", "signal_low": 0, "notes": ""}],
  "armed": [{"symbol": "LRCX", "signal_low": 0, "armed_et": "", "notes": ""}],
  "open": [{
    "symbol": "AMD", "entry": 0, "qty": 0,
    "stop_pct": 5, "target_pct": 15, "opened_et": ""
  }],
  "closed": [{
    "symbol": "NVDA", "entry": 0, "exit": 0,
    "pnl_pct": 0, "reason": "target|stop|time", "closed_et": ""
  }],
  "totals": {"realized_pnl_pct": 0, "n_closed": 0, "n_open": 0}
}
```
