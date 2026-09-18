# Microstructure logger (research only — Features A + B)

Read-only L2 book + tape feature logger for Peak Hour **watchlist symbols**.

This is **not** a Peak Hour / TSD strategy module. It does not import those
packages, does not write their state files, and **never places orders**.
It **never sends Telegram**.

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
| Concurrent `reqMktDepth` | **`--depth-max` default 3** (IB Error 309) |
| Telegram | **never** |

IB `readonly=True`. `placeOrder` / `cancelOrder` / `reqGlobalCancel` are
patched to raise. The asyncio event loop runs **on the IB worker thread**
(same lesson as the options bridge). All IB waits are bounded.

---

## Entitlements (verified on Aaron’s paper TWS)

| Venue | Depth |
|-------|--------|
| **ARCA, NYSE, IEX** | **present** — use these |
| **NASDAQ, BATS, BEX** | **not entitled** — Error 2152, **expected** |

**Do not require BATS/BEX.** The default L2 path is SMART depth that aggregates
the entitled venues (ARCA / NYSE / IEX). Missing NASDAQ TotalView / BATS / BEX
makes the book **partial vs the full market**, not a crash.

**Long-term:** add **NASDAQ TotalView** when Aaron can. That fills the NASDAQ
hole in the SMART aggregate. BATS/BEX remain optional and are **not** required
for this logger.

Error **309** (`max 3 concurrent market depth requests`) is why `--top 8`
previously opened too many `reqMktDepth` lines. Depth is now capped at 3;
L1/tape (`reqMktData`) can still cover more names.

---

## Depth path (no BATS/BEX)

Default, in order:

1. **SMART depth** (`isSmartDepth=True`) on the SMART contract — partial
   aggregate from **ARCA / NYSE / IEX**. Label: `PARTIAL_ARCA_NYSE_IEX`.
2. **IEX-native fallback** only if that SMART DOM has **zero levels** after a
   short wait. Label: `IEX_NATIVE`. This is a single entitled venue, not a
   reason to drop ARCA/NYSE when SMART is already populating.
3. Names beyond `--depth-max` keep **L1 + tape only**. Label: `L1_ONLY`.

Error **2152** for NASDAQ/BATS/BEX is logged **once per process** (then
suppressed, including ib_insync ERROR spam). It is **not** an alarm and is
**not** sent to Telegram. The JSONL label stays honest: PARTIAL entitled
venues, never TotalView.

The earlier tag `PARTIAL_IEX_SMART` understated ARCA/NYSE (those venues **are**
entitled). Rows from this revision use `PARTIAL_ARCA_NYSE_IEX`.

Every row still carries `levels_n` = levels actually received (0 if quote/depth
missing). `levels_n` varies by symbol; that is expected on a partial book.

### How to prefer `levels_n >= 5`

The logger does **not** probe TWS from the cloud. After a paper session:

1. Inspect `logs/microstructure/YYYYMMDD/{SYMBOL}.jsonl` for `levels_n`.
2. Pass names that actually delivered ≥5 levels as `--depth-symbols` (or
   `--symbols` when the depth set **is** the universe, e.g. live ops
   `HOOD,MSTR,TARS`).
3. If `--depth-symbols` is omitted, auto-select among the L1 universe prefers
   NYSE / ARCA / IEX **listings**, then original order — a prior only, not a
   live `levels_n` probe.

`--top` / `--symbols` size the **L1/tape** set. `--depth-max` / `--depth-symbols`
size the **depth** set. Depth never exceeds `--depth-max` (default 3).

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

`depth_source` values:

| Value | Meaning |
|-------|---------|
| `PARTIAL_ARCA_NYSE_IEX` | SMART aggregate of entitled venues; NASDAQ/BATS/BEX missing |
| `IEX_NATIVE` | IEX-only book after empty SMART DOM |
| `L1_ONLY` | no `reqMktDepth` (beyond `--depth-max`) |

Each 1s snapshot writes **two** rows (same features, different `source`) so
downstream can filter book vs tape cadence without dropping TEST-01 keys.

Tape classification: **Lee-Ready** when mid is known, else **tick rule**.
`tape_burst_z` is a 5s volume z-score vs a ~20 minute baseline (null for
the first ~60s).

---

## Universe

Default: top **8** (`--top N`) liquid Peak Hour names for **L1/tape**, merged
from local artifacts if present (read-only JSON, no TSD imports):

1. `candidates/tsd_scan_pipeline/results/last_1h_launch.json`
2. `candidates/tsd_watch_queue.json`
3. `candidates/tsd_scan_pipeline/results/last_watchlist.json`

Heuristic: dollar volume first (`dollar_vol_1h` / 20d / generic), then
continuation / launch / scan score; rank is a small tie-break.

`--symbols AAPL,MSFT` overrides artifacts entirely (still capped by `--top`).

`--depth-max 3` (default) is the L2 cap. `--depth-symbols HOOD,MSTR,TARS`
pins which names get `reqMktDepth` (still capped by `--depth-max`).

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
# start — defaults: -Top 8 (L1/tape) -DepthMax 3 (L2)
.\candidates\start_microstructure_logger.ps1
.\candidates\start_microstructure_logger.ps1 -Top 8 -DepthMax 3
.\candidates\start_microstructure_logger.ps1 -Symbols "HOOD,MSTR,TARS" -DepthMax 3
.\candidates\start_microstructure_logger.ps1 -Top 8 -DepthSymbols "HOOD,MSTR,TARS"
.\candidates\start_microstructure_logger.ps1 -AllowExtended
.\candidates\start_microstructure_logger.ps1 -Symbols "AAPL,MSFT,NVDA" -Once

# stop
.\candidates\stop_microstructure_logger.ps1
```

Equivalent:

```powershell
$env:PYTHONPATH = (Get-Location)
.\venv\Scripts\python.exe -u -m candidates.microstructure_logger --top 8 --depth-max 3
.\venv\Scripts\python.exe -u -m candidates.microstructure_logger --symbols HOOD,MSTR,TARS --depth-max 3
.\venv\Scripts\python.exe -u -m candidates.microstructure_logger --no-connect --once
```

`--no-connect` writes null-schema rows (no TWS). Use it to verify path/schema
on a machine without TWS. Cloud agents must not pass a live connect.

PID file: `candidates/logs/microstructure_logger.pid`  
Daily starter log: `candidates/logs/microstructure_YYYY-MM-DD.log`

Live ops workaround `--symbols HOOD,MSTR,TARS --top 3` is still valid (3 L1 +
3 depth). Prefer `--top 8 --depth-max 3` when you want tape on a wider set
without opening a fourth depth line.

---

## Tests (no live IB)

```powershell
.\venv\Scripts\python.exe tests\test_microstructure_logger.py
.\venv\Scripts\python.exe -m pytest tests/test_microstructure_logger.py -q
```

Fakes cover feature math, session tags, TEST-01 schema, universe ranking,
depth-max / 2152 gate, honest labels, and paper-endpoint guards.

---

## Isolation checklist

- [x] clientId **72** only
- [x] Paper `127.0.0.1:7497` only
- [x] No orders
- [x] No Peak Hour / TSD imports or mutations
- [x] No Telegram
- [x] `--depth-max` default **3** (never opens more concurrent `reqMktDepth`)
- [x] Error 2152 NASDAQ/BATS/BEX treated as expected / once-per-process
- [x] `depth_source` honest: `PARTIAL_ARCA_NYSE_IEX` / `IEX_NATIVE` / `L1_ONLY`
- [x] BATS/BEX entitlements **not required**
- [x] TEST-01 keys on every row
- [x] Feature C / D not implemented
