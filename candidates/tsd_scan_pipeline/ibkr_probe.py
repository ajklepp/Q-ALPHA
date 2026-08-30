"""
Q-ALPHA TSD pipeline — Phase 1 IBKR probe (READ-ONLY).

Runs five tests against paper TWS while validating 3H bar + TSD math:
  1. 3H bar parity (TSLA, NVDA, SPY) + wt1/wt2/BUY crosses
  2. Extended hours (useRTH False vs True on TSLA)
  3. Pacing benchmark (100 / 200 / 300 symbol pulls)
  4. Live price snapshot (5 symbols)
  5. keepUpToDate on one symbol

Usage (TWS paper open, API on 7497):
  py -3 candidates/tsd_scan_pipeline/ibkr_probe.py

Results written to candidates/tsd_scan_pipeline/results/ibkr_probe.md
"""
from __future__ import annotations

import math
import sys
import time
from datetime import datetime
from pathlib import Path

import pytz
from ib_insync import IB, Stock, util

# Repo imports
PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

from tsd_scan_pipeline.build_3h_bars import bars_from_ibkr, bar_count_for_lookback
from tsd_scan_pipeline.tsd_signals import enrich_tsd, last_bar_summary, recent_buy_crosses

TWS_HOST = "127.0.0.1"
TWS_PORT = 7497
TWS_CLIENT_ID = 94  # TSD probe — not agent 5 / sync 96 / spike 97 / MD 98-99

PARITY_SYMBOLS = ("TSLA", "NVDA", "SPY")
LIVE_PRICE_SYMBOLS = ("TSLA", "NVDA", "SPY", "AAPL", "AMD")
KEEPUPDATE_SYMBOL = "SPY"
HIST_DURATION = bar_count_for_lookback(60)
BAR_SIZE = "3 hours"
ET = pytz.timezone("America/New_York")

# Liquid universe for pacing — top names + scanner fill later
PACE_BASE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA", "BRK.B", "UNH",
    "JPM", "V", "XOM", "LLY", "JNJ", "WMT", "MA", "PG", "AVGO", "HD", "CVX", "MRK",
    "ABBV", "COST", "PEP", "KO", "ADBE", "MCD", "CRM", "CSCO", "ACN", "TMO", "NFLX",
    "ABT", "LIN", "AMD", "DHR", "WFC", "DIS", "VZ", "PM", "TXN", "INTC", "CMCSA", "NEE",
    "RTX", "HON", "QCOM", "UPS", "IBM", "AMAT", "SPGI", "LOW", "INTU", "CAT", "GE",
    "BA", "AMGN", "SBUX", "DE", "GS", "BLK", "MDT", "GILD", "ISRG", "ADI", "BKNG",
    "SYK", "TJX", "VRTX", "MMC", "LRCX", "REGN", "CVS", "PFE", "C", "MO", "PLD",
    "SO", "DUK", "ZTS", "CI", "BSX", "SLB", "EOG", "EQIX", "BDX", "CL", "ITW",
    "APD", "NOC", "SHW", "CME", "USB", "PNC", "FCX", "MPC", "EMR", "GM", "F",
    "MU", "SNPS", "CDNS", "KLAC", "PANW", "CRWD", "SNOW", "UBER", "ABNB", "COIN",
    "MARA", "RIOT", "SMCI", "ARM", "PLTR", "SOFI", "HOOD", "DKNG", "RBLX", "NET",
    "DDOG", "ZS", "OKTA", "TEAM", "WDAY", "NOW", "SHOP", "SQ", "PYPL", "AFRM",
    "UPST", "RIVN", "LCID", "NIO", "XPEV", "LI", "BABA", "JD", "PDD", "BIDU",
    "TSM", "ASML", "SMH", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK", "ARKK",
    "SOXL", "TQQQ", "SQQQ", "SPXL", "SPXS", "UVXY", "VXX", "GLD", "SLV", "USO",
    "UNG", "TLT", "HYG", "LQD", "EEM", "EFA", "FXI", "KWEB", "XBI", "IBB",
    "SMMT", "SOUN", "IONQ", "RGTI", "QBTS", "JOBY", "ACHR", "RKLB", "ASTS", "LUNR",
    "CELH", "DUOL", "HIMS", "CVNA", "CAR", "ANF", "GAP", "ONON", "CROX", "DECK",
    "LULU", "NKE", "UAA", "VFC", "TPR", "RL", "SKX", "BOOT", "BIRK", "CAVA",
    "WING", "CMG", "DPZ", "YUM", "QSR", "WEN", "JACK", "SHAK", "EAT", "BLMN",
    "DRI", "TXRH", "CAKE", "CBRL", "DENN", "PLAY", "RRGB", "FWRG", "BROS", "SG",
    "CCL", "RCL", "NCLH", "MAR", "HLT", "H", "WH", "EXPE", "BKNG", "ABNB",
    "LVS", "WYNN", "MGM", "CZR", "PENN", "DKNG", "FLUT", "GENI", "RSI", "LNW",
    "CHDN", "BYD", "MTN", "SKYW", "ALK", "DAL", "UAL", "AAL", "LUV", "JBLU",
    "SAVE", "HA", "CPA", "GOL", "AZUL", "FDX", "UPS", "XPO", "ODFL", "JBHT",
    "KNX", "CHRW", "EXPD", "ZIM", "MATX", "DAC", "SBLK", "GOGL", "EGLE", "NMM",
    "STNG", "FRO", "TNK", "INSW", "DHT", "NAT", "ASC", "TNP", "OSG", "GLNG",
    "LNG", "KMI", "WMB", "OKE", "ET", "EPD", "MPLX", "PAA", "TRGP", "ENLC",
    "AM", "DTM", "HESM", "USAC", "AR", "RRC", "EQT", "SWN", "CHK", "AROC",
    "VAL", "NE", "DO", "RIG", "HP", "PTEN", "NBR", "LBRT", "PUMP", "WHD",
    "CHX", "OII", "DRQ", "CLB", "NOV", "FTI", "BKR", "HAL", "SLB", "WFRD",
    "CHRD", "MTDR", "PR", "DVN", "FANG", "PXD", "OVV", "CTRA", "APA", "MRO",
    "HES", "COP", "OXY", "VLO", "PSX", "MPC", "PBF", "DK", "DINO", "PARR",
    "CVI", "CLMT", "PBF", "SUN", "ET", "PAGP", "PAGS", "VIST", "PBR", "EC",
]


def _finite(val) -> bool:
    try:
        f = float(val)
        return not (math.isnan(f) or math.isinf(f))
    except (TypeError, ValueError):
        return False


def _fmt_px(val) -> str:
    try:
        f = float(val)
        if math.isnan(f):
            return "nan"
        return f"{f:.4f}"
    except (TypeError, ValueError):
        return f"nan ({val!r})"


def _fetch_3h_bars(ib: IB, symbol: str, use_rth: bool, duration: str = HIST_DURATION):
    """Pull 3H historical bars from IBKR."""
    contract = Stock(symbol, "SMART", "USD")
    ib.qualifyContracts(contract)
    bars = ib.reqHistoricalData(
        contract,
        endDateTime="",
        durationStr=duration,
        barSizeSetting=BAR_SIZE,
        whatToShow="TRADES",
        useRTH=use_rth,
        formatDate=1,
    )
    ib.sleep(0.5)
    return bars


def test_parity(ib: IB) -> list[str]:
    """Test 1: 3H bars + TSD signals for parity symbols."""
    lines = ["## Test 1 — 3H bar parity + TSD signals", ""]
    for sym in PARITY_SYMBOLS:
        lines.append(f"### {sym}")
        try:
            bars = _fetch_3h_bars(ib, sym, use_rth=False)
            df = bars_from_ibkr(bars)
            lines.append(f"- Bars received: **{len(df)}** (useRTH=False, {HIST_DURATION})")
            if df.empty:
                lines.append("- **FAIL** — empty bar set")
                continue
            enriched = enrich_tsd(df)
            summary = last_bar_summary(enriched)
            lines.append(f"- Last bar: `{summary.get('time')}` close={summary.get('close'):.2f}")
            lines.append(
                f"- wt1={summary.get('wt1')}, wt2={summary.get('wt2')}, "
                f"trend={summary.get('trend_strength')}, score={summary.get('scan_score')}"
            )
            crosses = recent_buy_crosses(enriched, n=5)
            if crosses.empty:
                lines.append("- Recent BUY crosses (last 5): none in window")
            else:
                lines.append("- Recent BUY crosses:")
                for ts, row in crosses.iterrows():
                    lines.append(
                        f"  - `{ts}` close={row['close']:.2f} "
                        f"wt1={row['wt1']:.2f} wt2={row['wt2']:.2f} score={row['scan_score']:.1f}"
                    )
            lines.append("- Last 5 bar timestamps:")
            for ts in enriched.tail(5).index:
                lines.append(f"  - `{ts}`")
        except Exception as exc:
            lines.append(f"- **FAIL**: {exc}")
        lines.append("")
    return lines


def test_extended_hours(ib: IB) -> list[str]:
    """Test 2: useRTH False vs True on TSLA — premarket bar presence."""
    lines = ["## Test 2 — Extended hours (TSLA useRTH False vs True)", ""]
    sym = "TSLA"
    try:
        bars_ext = _fetch_3h_bars(ib, sym, use_rth=False)
        bars_rth = _fetch_3h_bars(ib, sym, use_rth=True)
        df_ext = bars_from_ibkr(bars_ext)
        df_rth = bars_from_ibkr(bars_rth)
        lines.append(f"- useRTH=False: **{len(df_ext)}** bars")
        lines.append(f"- useRTH=True:  **{len(df_rth)}** bars")
        lines.append(f"- Delta (extended-only bars): **{len(df_ext) - len(df_rth)}**")

        # Look for 07:00 ET bars (premarket anchor)
        premarket_hits = []
        for ts in df_ext.index:
            ts_et = ts.tz_localize("UTC").tz_convert(ET) if ts.tzinfo is None else ts.tz_convert(ET)
            if ts_et.hour == 7 and ts_et.minute == 0:
                premarket_hits.append(str(ts_et))
        if premarket_hits:
            lines.append(f"- 07:00 ET bars found (extended): **{len(premarket_hits)}**")
            for h in premarket_hits[-3:]:
                lines.append(f"  - `{h}`")
        else:
            lines.append("- 07:00 ET bars: **not found** in last window (check alignment)")
        lines.append("")
        lines.append("Sample extended-only timestamps (in ext not rth):")
        ext_set = set(df_ext.index)
        rth_set = set(df_rth.index)
        only_ext = sorted(ext_set - rth_set)[-5:]
        for ts in only_ext:
            lines.append(f"  - `{ts}`")
    except Exception as exc:
        lines.append(f"- **FAIL**: {exc}")
    lines.append("")
    return lines


def test_pacing(ib: IB) -> list[str]:
    """Test 3: time 100/200/300 symbol 3H pulls."""
    lines = ["## Test 3 — Pacing benchmark (3H historical per symbol)", ""]
    sizes = (100, 200, 300)
    symbols = list(dict.fromkeys(PACE_BASE))
    if len(symbols) < 300:
        lines.append(f"- **WARN**: only {len(symbols)} unique symbols in pace list (target 300)")

    for n in sizes:
        batch = symbols[:n]
        t0 = time.perf_counter()
        ok = 0
        fail = 0
        for i, sym in enumerate(batch, 1):
            try:
                bars = _fetch_3h_bars(ib, sym, use_rth=False, duration="30 D")
                if bars:
                    ok += 1
                else:
                    fail += 1
            except Exception:
                fail += 1
            if i % 25 == 0:
                elapsed = time.perf_counter() - t0
                print(f"  pacing n={n}: {i}/{n} ({elapsed:.1f}s)", flush=True)
            ib.sleep(0.35)  # conservative pacing for probe
        elapsed = time.perf_counter() - t0
        per_sym = elapsed / n if n else 0
        lines.append(
            f"- **N={n}**: {elapsed:.1f}s total, **{per_sym:.2f}s/symbol**, "
            f"ok={ok}, fail={fail}"
        )
        lines.append(f"  - Safe delay estimate: **{max(0.35, per_sym + 0.1):.2f}s** between symbols")
    lines.append("")
    return lines


def test_live_prices(ib: IB) -> list[str]:
    """Test 4: reqMktData snapshot on 5 symbols."""
    lines = ["## Test 4 — Live price snapshot (5 symbols)", ""]
    now_et = datetime.now(ET)
    lines.append(f"- Probe time (ET): `{now_et.strftime('%Y-%m-%d %H:%M:%S %Z')}`")
    lines.append("")
    for sym in LIVE_PRICE_SYMBOLS:
        contract = Stock(sym, "SMART", "USD")
        ib.qualifyContracts(contract)
        ticker = ib.reqMktData(contract, "", False, False)
        ib.sleep(2)
        ok = any(_finite(getattr(ticker, a, None)) for a in ("last", "close", "bid", "ask"))
        lines.append(
            f"- **{sym}**: last={_fmt_px(ticker.last)} bid={_fmt_px(ticker.bid)} "
            f"ask={_fmt_px(ticker.ask)} close={_fmt_px(ticker.close)} "
            f"→ {'OK' if ok else 'FAIL (all nan)'}"
        )
        try:
            ib.cancelMktData(contract)
        except Exception:
            pass
    lines.append("")
    return lines


def test_keep_up_to_date(ib: IB) -> list[str]:
    """Test 5: keepUpToDate=True on SPY 3H bars."""
    lines = ["## Test 5 — keepUpToDate (SPY 3H)", ""]
    contract = Stock(KEEPUPDATE_SYMBOL, "SMART", "USD")
    ib.qualifyContracts(contract)
    updates: list = []

    def on_bar_update(bars, has_new_bar):
        updates.append({"has_new_bar": has_new_bar, "n_bars": len(bars), "last": str(bars[-1].date) if bars else None})

    try:
        bars = ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr="10 D",
            barSizeSetting=BAR_SIZE,
            whatToShow="TRADES",
            useRTH=False,
            formatDate=1,
            keepUpToDate=True,
        )
        bars.updateEvent += on_bar_update
        initial_n = len(bars)
        lines.append(f"- Initial bars: **{initial_n}**")
        lines.append("- Waiting 20s for historicalDataUpdate…")
        ib.sleep(20)
        lines.append(f"- updateEvent callbacks: **{len(updates)}**")
        if updates:
            for u in updates[-3:]:
                lines.append(f"  - has_new_bar={u['has_new_bar']} n={u['n_bars']} last={u['last']}")
            lines.append("- **OK** — historicalDataUpdate fired")
        else:
            lines.append("- **INCONCLUSIVE** — no update in 20s (may be normal between 3H closes)")
        ib.cancelHistoricalData(bars)
    except Exception as exc:
        lines.append(f"- **FAIL**: {exc}")
    lines.append("")
    return lines


def main() -> int:
    util.startLoop()
    results_dir = PIPELINE_DIR / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    out_path = results_dir / "ibkr_probe.md"

    header = [
        "# TSD Pipeline — IBKR Phase 1 Probe",
        "",
        f"**Run at:** {datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"**Host:** {TWS_HOST}:{TWS_PORT} clientId={TWS_CLIENT_ID}",
        "**Mode:** READ-ONLY (no orders)",
        "",
    ]
    all_lines = list(header)
    ib = IB()

    print("=" * 64)
    print("TSD IBKR PROBE — Phase 1")
    print(f"clientId={TWS_CLIENT_ID} (not agent 5)")
    print("=" * 64)

    try:
        ib.connect(TWS_HOST, TWS_PORT, clientId=TWS_CLIENT_ID, timeout=12)
    except Exception as exc:
        msg = f"CONNECT FAILED: {exc}"
        print(msg)
        all_lines.append(f"## CONNECT FAILED\n\n{exc}\n")
        out_path.write_text("\n".join(all_lines), encoding="utf-8")
        return 1

    accounts = list(ib.managedAccounts() or [])
    all_lines.append(f"**Accounts:** {accounts}")
    all_lines.append("")

    print("\n[1/5] Parity test…")
    all_lines.extend(test_parity(ib))

    print("[2/5] Extended hours…")
    all_lines.extend(test_extended_hours(ib))

    print("[3/5] Pacing (this takes several minutes)…")
    all_lines.extend(test_pacing(ib))

    print("[4/5] Live prices…")
    all_lines.extend(test_live_prices(ib))

    print("[5/5] keepUpToDate…")
    all_lines.extend(test_keep_up_to_date(ib))

    all_lines.extend(
        [
            "## Summary",
            "",
            "| Test | Status |",
            "|------|--------|",
            "| 3H parity + TSD | see per-symbol above |",
            "| Extended hours | see TSLA delta |",
            "| Pacing | see N=100/200/300 timings |",
            "| Live prices | see per-symbol OK/FAIL |",
            "| keepUpToDate | see callback count |",
            "",
            "*Next: Phase 1 dry-run `tsd_scan_ibkr.py` after Pine parity spot-check.*",
        ]
    )

    out_path.write_text("\n".join(all_lines), encoding="utf-8")
    print(f"\nWrote {out_path}")

    try:
        ib.disconnect()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
