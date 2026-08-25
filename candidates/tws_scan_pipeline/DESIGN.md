# TWS Scan Pipeline — Design (hybrid morning path)

**Status:** Phase 2 wired (`QALPHA_USE_TWS_SCAN=1`). Production funnel: ~50×3 → watch 10 / trade 3.  
**Owners:** Aaron + research director (architecture lock).  
**Workspace:** Q-ALPHA only (not Q-ALPHA-READONLY).

---

## Why this exists

Today’s pain (2026-08-25 context):

1. **Polygon CS universe** misses some listings (e.g. NYSE American names like PMI) — phone-book hole.
2. Agent tried **JEM**; IB rejected (**closing-only / customer ineligible**).
3. TWS “US Active” movers ≠ our Polygon opening-gap scan; ops want a **TWS-driven shortlist**.
4. Paper MD works (Error 420 gone); L2 still `PARTIAL_IEX_SMART`.

**Polygon stays source of truth** for historical analyses, Strategy Lab SIM, and experiments.  
This path only changes how the **live agent** builds its morning candidate list (Phase 2+).

---

## Target architecture (locked — production funnel)

```
  ~09:20–09:25 ET
       │
       ▼
 ┌─────────────────────────────────────────────────────────┐
 │ 1) LIST — TWS scanners                                  │
 │    TOP_PERC_GAIN + MOST_ACTIVE + HOT_BY_VOLUME          │
 │    SCAN_ROWS_PER_CODE ≈ 50 each → union+dedupe          │
 │    TARGET_UNIVERSE ≈ 100 hunting set                    │
 └───────────────────────────┬─────────────────────────────┘
                             │
                             ▼
 ┌─────────────────────────────────────────────────────────┐
 │ 2) GATES                                                │
 │    IGNORE mcap < $50M | LEARN $50–150M | TRADE ≥ $150M  │
 │    No $5 floor; ban ETF/lev/RIGHT/junk; no CS-cache req │
 └───────────────────────────┬─────────────────────────────┘
                             │
                             ▼
 ┌─────────────────────────────────────────────────────────┐
 │ 3) SCORE full post-gate shortlist (~100)                │
 │    PM: TWS first, Polygon fallback                      │
 │    History: Polygon ticker_profiler only                │
 └───────────────────────────┬─────────────────────────────┘
                             │
              ┌──────────────┴──────────────┐
              ▼                             ▼
 ┌────────────────────────┐   ┌────────────────────────────┐
 │ 4) WATCH top 10        │   │ 5) TRADE top 3             │
 │    by score (TRADE +   │   │    TRADE lane only         │
 │    LEARN allowed)      │   │    MAX_TRADES_DAY = 3      │
 │    Telegram/dashboard  │   │    Never bracket LEARN     │
 └────────────────────────┘   └────────────────────────────┘
                             │
                             ▼
 ┌─────────────────────────────────────────────────────────┐
 │ 6) OPTIONAL ~09:40 refill — OFF (stub)                  │
 └─────────────────────────────────────────────────────────┘
```

**Constants:** `SCAN_ROWS_PER_CODE=50`, `TARGET_UNIVERSE=100`, `WATCH_TOP_N=10`, `TRADE_TOP_N=3` (`MAX_TRADES_DAY=3`).

---

## Locked decisions (Phase 2 — Aaron 2026-08-25)

| Topic | Decision |
|-------|----------|
| **TRADE** | `marketCap >= $150M` → entry shortlist; take **top 3** (`TRADE_TOP_N` / `MAX_TRADES_DAY`) |
| **LEARN** | `$50M <= marketCap < $150M` → score + persist learn file; may appear in **watch 10**; **NO IB orders** |
| **IGNORE** | `marketCap < $50M` → drop |
| Funnel size | `SCAN_ROWS_PER_CODE=50` × 3 → `TARGET_UNIVERSE≈100` after dedupe (not spike’s 25) |
| Watch | `WATCH_TOP_N=10` by score across TRADE+LEARN |
| Price | **No $5 hard floor** for TRADE/LEARN. Sub-$5 ALLOWED when mcap lane says so. Still apply pool max-affordable for entries. |
| Safety | Ban leveraged/ETF/funds (`universe_filter` / IB `stockType`); reject RIGHT/preferred junk. **CS-cache membership NOT required** for TWS-sourced names. |
| Premarket | TWS extended hours first; Polygon snapshot fallback |
| History | Polygon `ticker_profiler` only |
| IB rejects | closing-only / Customer Ineligible / No Trading Permission → session skip, Telegram, **not** a fill |
| 09:40 refill | **OFF** (stub) |
| Flag | `QALPHA_USE_TWS_SCAN` default **1**; set `0` to revert to Polygon `full_market_scan` |
| Lab SIM | Polygon `live_forward` unchanged |
| Client IDs | Agent **5**; scan-only/spike **97**; connector **1** |

---

## Phase plan

| Phase | Deliverable | Gate to next |
|-------|-------------|--------------|
| **1** | DESIGN + spike — PASS 2026-08-25 | Done |
| **2 (now)** | `pipeline.py` + agent flag + LEARN files + ineligible skip | Paper morning |
| **3** | Harden after first live paper day | — |
| **4** | Optional 09:40 refill | Explicit approve |

---

## Spike results (2026-08-25 ~12:52–12:53 ET, paper DUR857496, clientId 97)

| Check | Result |
|-------|--------|
| Connect 7497 | OK — account DUR857496 |
| `reqScannerParameters` | 673 scanCodes; `HOT_BY_VOLUME` YES |
| `TOP_PERC_GAIN` | 25 rows — **includes PMI + JEM** (TWS list finds what Polygon CS missed / ineligible) |
| `MOST_ACTIVE` | 25 rows (NVDA, TSLL, TQQQ, BITO… — funds appear; hard gates required) |
| `HOT_BY_VOLUME` | 25 rows |
| Union dedupe | ~60 unique |
| Snapshot | OK |
| Hist 5m RTH + EXTENDED | OK (SPY/AIXI/NCPL: RTH≈41 bars, EXTENDED≈107 from 04:00 ET) |
| `marketCap` on ContractDetails | **Absent** — Phase 2: Polygon/fundamentals |
| `stockType` | Useful (ETF / RIGHT / ADR / COMMON) — gate in Phase 2 |
| Benign IB noise | 2104/2106/2158 farm OK; 162 scanner cancel on teardown; 300 cancelMktData |

**Verdict:** **SPIKE PASS.** Agent wiring still blocked until Aaron Phase 2 approve.

### Spike script (`spike_tws_scanners.py`)

- Connect TWS paper `127.0.0.1:7497`, `clientId=97`
- Request `TOP_PERC_GAIN`, `MOST_ACTIVE`; add `HOT_BY_VOLUME` if parameters list it
- Print top 25 + union/dedupe preview
- Probe ~3 symbols: snapshot + short RTH/extended historical + contractDetails
- No orders / no agent import / no Modal / no `reset_forward`

---

- Connect TWS paper `127.0.0.1:7497`, `clientId=97`
- Request 1–2 scanner types (`TOP_PERC_GAIN`, `MOST_ACTIVE`; try `HOT_BY_VOLUME` if parameters list it)
- Print top 25 symbols + key fields per scan
- For ~3 symbols (liquid + a mover): snapshot + short extended-hours historical
- Print what worked / error codes; exit non-zero only on connect failure

No orders. No agent import. No Modal. No `reset_forward`.

---

## Open risks (Phase 1)

1. **Paper scanner quirks** — paper may return empty/stale rows vs live TWS “US Active”; ranks may not match the GUI.
2. **Market-cap data source** — IB `ContractDetails` / fundamental data vs Polygon snapshot; latency and missing fields on thin names. Spike reports what IB returns; Phase 2 picks SoT.
3. **Extended-hours bars** — paper delayed MD may refuse or return RTH-only; need Polygon fallback for PM features (already in architecture).
4. **Closing-only / eligibility** — error text vs structured order-permission fields; may only surface at order time unless we probe `reqContractDetails` / order preview.
5. **CS-universe gate conflict** — today’s `passes_universe_safety_gate` fails closed when symbol ∉ Polygon CS cache (PMI). Phase 2 must split “fund/ETF ban” from “must be in Polygon CS phone book.”
6. **Scanner location** — `STK.US.MAJOR` may still omit some American listings; may need broader `STK.US` or secondary location after spike.
7. **ClientId collision** — never reuse 1 / 5 during agent session.
8. **Rate limits** — scanners + hist for many symbols; keep shortlist small before MD fan-out.
9. **L2** — still `PARTIAL_IEX_SMART`; scoring must not assume full depth.

---

## Cross-links

- Agent: `candidates/autonomous_agent.py` (wire later)
- Safety: `candidates/universe_filter.py`
- Lab SIM (unchanged): `strategy_lab/live_forward.py` / Polygon scan
- Ops: `Q_ALPHA_HANDOFF.md` (IBKR section; add Phase 2 bullet when wired)
- Prior MD probe: `test_ibkr_data.py` (clientId 99)

---

## Out of scope (this phase)

- Editing `autonomous_agent.py`
- Changing Lab entry/capacity path
- Modal runs
- `reset_forward.py`
- Live (non-paper) TWS
