# TWS Scan Pipeline — Design (hybrid morning path)

**Status:** Phase 1 (design + spike). **Not wired** into `autonomous_agent.py`.  
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

## Target architecture (locked)

```
  ~09:25 ET
       │
       ▼
 ┌─────────────────────────────────────────────────────────┐
 │ 1) LIST — TWS scanners (ib_insync ScannerSubscription)  │
 │    TOP_PERC_GAIN / MOST_ACTIVE / HOT_BY_VOLUME (best     │
 │    available). Union + dedupe → raw symbol list.         │
 └───────────────────────────┬─────────────────────────────┘
                             │
                             ▼
 ┌─────────────────────────────────────────────────────────┐
 │ 2) SAFETY HARD GATES (before any score)                 │
 │    • price >= $5                                        │
 │    • not leveraged/ETF  → reuse universe_filter         │
 │      (EXCLUDE_SYMBOLS + is_leveraged_or_fund)           │
 │    • min market cap     → $50M (see decision below)     │
 │    • skip if IB marks closing-only / no trade permission│
 │      when detectable                                    │
 └───────────────────────────┬─────────────────────────────┘
                             │
                             ▼
 ┌─────────────────────────────────────────────────────────┐
 │ 3) SCORE shortlist (not full-market Polygon scan)       │
 │    Premarket: prefer TWS live / extended-hours bars+    │
 │               quotes; Polygon fallback if TWS no-quote  │
 │    History:   Polygon ticker_profiler ONLY              │
 │               (do not rebuild history on IB)            │
 └───────────────────────────┬─────────────────────────────┘
                             │
                             ▼
 ┌─────────────────────────────────────────────────────────┐
 │ 4) SELECT — rank by score → top MAX_TRADES_DAY (= 3)    │
 │    Agent only. Lab SIM Polygon path unchanged this phase│
 └───────────────────────────┬─────────────────────────────┘
                             │
                             ▼
 ┌─────────────────────────────────────────────────────────┐
 │ 5) OPTIONAL ~09:40 second scanner pass                  │
 │    Fill remaining slots (CRML-style post-open).         │
 │    v1: design + stub unless spike is clean.             │
 └─────────────────────────────────────────────────────────┘
```

---

## Locked decisions

| Topic | Decision |
|-------|----------|
| Market-cap floor | **$50M** — kills JEM-class (~$3M) while keeping true small/mid momentum names; $100M reserved if paper still floods microcaps. Documented here; constant in Phase 2 code. |
| Universe filter | Reuse `candidates/universe_filter.py` name/deny rules. **Do not** require Polygon `cs_universe_cache` membership for TWS-sourced names (that membership check is the PMI hole). Phase 2: gate = price + fund/ETF name + mcap + IB tradability; CS-cache is optional enrichment only. |
| History / analogs | Polygon `ticker_profiler` only. |
| Lab SIM | Unchanged in Phase 1–2 except docs cross-links. |
| Agent `MAX_TRADES_DAY` | Remains **3**. |
| Client IDs | Spike uses a free id (**97**). Reserved: connector `1`, agent `5`, MD probe `98`/`99`. |

---

## Phase plan

| Phase | Deliverable | Gate to next |
|-------|-------------|--------------|
| **1 (now)** | `DESIGN.md` + `spike_tws_scanners.py` — connect paper 7497, pull scanners, try extended snapshot/hist on a few symbols, report IB errors | Spike PASS + Aaron review |
| **2** | Module: list → hard gates → score helpers; unit tests; still not default agent path | Aaron approve wiring |
| **3** | Wire agent morning path behind flag; keep Polygon scan as fallback | Live paper day PASS |
| **4** | Optional 09:40 refill if Phase 1–2 show clean scanner+MD | Explicit approve |

**Do not** promote to live agent path until Phase 2 approval after spike PASS.

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
