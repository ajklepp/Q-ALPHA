# Microstructure logger (research only — Features A + B)

Read-only L2 book + tape feature logger for Peak Hour **watchlist symbols**.

This is **not** a Peak Hour / TSD strategy module. It does not import those
packages, does not write their state files, and **never places orders**.

Strategy Finder references (Aaron’s machine, naming only):

- `BestStrategyFinder/novelty/L2_TAPE_FEATURE_LOG_PLAN.md` (v0 A+B)
- `BestStrategyFinder/novelty/TEST01_IMBALANCE_TAPE_BURST.md`

**Deferred:** Feature C (confirms) and Feature D (options).

Cloud / this VM: **code only**. Do not connect TWS from the cloud agent.

---

## Hard pins

| Item | Value |
|------|--------|
| Host / port | `127.0.0.1:7497` paper TWS only |
| clientId | **72** (not 71 options bridge; not 1/5/75/76/85/86/88/89/93–99) |
| Live port 7496 | refused |
| Cloud Gateway / IBKR secrets | not used |
| Peak Hour / TSD | **untouched** (JSON artifacts read-only) |

IB `readonly=True`. `placeOrder` / `cancelOrder` / `reqGlobalCancel` are
patched to raise. The asyncio event loop runs **on the IB worker thread**
(same lesson as the options bridge). All IB waits are bounded.

---

## Depth honesty — `PARTIAL_IEX_SMART`

Paper SMART depth is **not** Nasdaq TotalView.

Expect incomplete IEX/smart-routed book. IB **Error 2152** (missing
NASDAQ/BATS/ARCA/NYSE depth) is possible and **logged**. Every row carries:

- `depth_source` = `PARTIAL_IEX_SMART` (never “TotalView”)
- `levels_n` = levels actually received (0 if quote/depth missing)

---

## Output

Repo-root layout (`Documents/Q-ALPHA`):

```
logs/microstructure/YYYYMMDD/{SYMBOL}.jsonl
```

UTC date in the folder name. One file per symbol.

### TEST-01 required keys (every row)

Every JSONL object **must** include at least:

`ts_utc`, `symbol`, `bid1`, `ask1`, `bid_sz1`, `ask_sz1`, `mid`,
`imbalance_l1`, `spread_bps`, `tape_burst_z`, `print_imb_5s`

Warm-up values for `tape_burst_z` / `print_imb_5s` are `null` until enough
tape exists. **Keys are always present.**

### Full A+B row

Also always present:

| Group | Fields |
|-------|--------|
| Identity | `session_tag` (`RTH` / `PRE` / `POST` / `CLOSED`), `source` (`qalpha_l2` \| `qalpha_tape`) |
| A book (1s) | `microprice`, `imbalance_l3` (null if depth &lt; 3), `book_pressure_delta_1s`, `book_pressure_delta_5s`, `quote_flicker` |
| B tape | `print_vwap_5s`, `large_print_flag` (k=5× median), `uptick_ratio_30s` |
| Provenance | `depth_source`, `levels_n` |

Each 1s snapshot writes **two** rows (same features, different `source`) so
downstream can filter book vs tape cadence without dropping TEST-01 keys.

Tape classification: **Lee-Ready** when mid is known, else **tick rule**.
`tape_burst_z` is a 5s volume z-score vs a ~20 minute baseline (null for
the first ~60s).

---

## Universe

Default: top **8** (`--top N`) liquid Peak Hour names, merged from local
artifacts if present (read-only JSON, no TSD imports):

1. `candidates/tsd_scan_pipeline/results/last_1h_launch.json`
2. `candidates/tsd_watch_queue.json`
3. `candidates/tsd_scan_pipeline/results/last_watchlist.json`

Heuristic: dollar volume first (`dollar_vol_1h` / 20d / generic), then
continuation / launch / scan score; rank is a small tie-break.

`--symbols AAPL,MSFT` overrides artifacts entirely (still capped by `--top`).

If all artifacts are missing/empty, fallback **dry-structure** names:

`SPY QQQ IWM AAPL MSFT NVDA TSLA AMD`

These are **not** Peak Hour signals — documented on stdout as fallback.

---

## Session

**RTH-primary.** Default: subscribe only 09:30–16:00 ET weekdays; idle
(stay alive, poll) outside RTH. `--allow-extended` also streams PRE
(04:00–09:30) and POST (16:00–20:00).

---

## Ops (Aaron’s Windows box, TWS paper open)

```powershell
# start (venv python, PID + daily log under candidates/logs)
.\candidates\start_microstructure_logger.ps1
.\candidates\start_microstructure_logger.ps1 -Top 8 -AllowExtended
.\candidates\start_microstructure_logger.ps1 -Symbols "AAPL,MSFT,NVDA" -Once

# stop
.\candidates\stop_microstructure_logger.ps1
```

Equivalent:

```powershell
$env:PYTHONPATH = (Get-Location)
.\venv\Scripts\python.exe -u -m candidates.microstructure_logger --top 8
.\venv\Scripts\python.exe -u -m candidates.microstructure_logger --no-connect --once
```

`--no-connect` writes null-schema rows (no TWS). Use it to verify path/schema
on a machine without TWS. Cloud agents must not pass a live connect.

PID file: `candidates/logs/microstructure_logger.pid`  
Daily starter log: `candidates/logs/microstructure_YYYY-MM-DD.log`

---

## Tests (no live IB)

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_microstructure_logger.py -q
```

Fakes cover feature math, session tags, TEST-01 schema, universe ranking,
and paper-endpoint guards.

---

## Isolation checklist

- [x] clientId **72** only
- [x] Paper `127.0.0.1:7497` only
- [x] No orders
- [x] No Peak Hour / TSD imports or mutations
- [x] `depth_source=PARTIAL_IEX_SMART`
- [x] TEST-01 keys on every row
- [x] Feature C / D not implemented
