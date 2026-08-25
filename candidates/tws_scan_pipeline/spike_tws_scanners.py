"""
Phase 1 spike — TWS scanners + extended-hours MD probe (READ-ONLY).

Connects to paper TWS on 7497 with a free clientId (97 — not agent 5 /
connector 1 / MD probes 98–99). Pulls scanner snapshots, prints top 25,
then tries snapshot + short extended-hours historical on a few symbols.

No orders. Not wired into autonomous_agent.py.

Usage (TWS paper open, API enabled, DUR857496 logged in):
  py -3 candidates/tws_scan_pipeline/spike_tws_scanners.py
"""
from __future__ import annotations

import math
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any

from ib_insync import IB, ScannerSubscription, Stock, util

TWS_HOST = "127.0.0.1"
TWS_PORT = 7497
TWS_CLIENT_ID = 97  # free; do not collide with 1 / 5 / 98 / 99

# Primary scans for morning shortlist exploration.
SCAN_CODES = (
    "TOP_PERC_GAIN",
    "MOST_ACTIVE",
)
# Optional third — only if reqScannerParameters lists it.
OPTIONAL_SCAN_CODES = ("HOT_BY_VOLUME", "HOT_BY_VOLUME_AND_PRICE", "TOP_VOLUME_RATE")

LOCATION = "STK.US.MAJOR"
INSTRUMENT = "STK"
TOP_N_PRINT = 25
LIQUID_FALLBACK = "SPY"


def _finite(val: Any) -> float | None:
    try:
        if val is None:
            return None
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _fmt_px(val: Any) -> str:
    f = _finite(val)
    return f"{f:.4f}" if f is not None else "nan"


def _scan_codes_from_params(xml_text: str) -> set[str]:
    """Parse scanCode values from IB scanner-parameters XML (best-effort)."""
    codes: set[str] = set()
    if not xml_text:
        return codes
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        # Fallback: crude string harvest if XML is huge/odd.
        for token in (
            "TOP_PERC_GAIN",
            "MOST_ACTIVE",
            "HOT_BY_VOLUME",
            "HOT_BY_VOLUME_AND_PRICE",
            "TOP_VOLUME_RATE",
            "TOP_OPEN_PERC_GAIN",
            "TOP_TRADE_COUNT",
        ):
            if token in xml_text:
                codes.add(token)
        return codes
    for el in root.iter():
        tag = (el.tag or "").lower()
        if tag.endswith("scancode") or tag == "scancode":
            if el.text:
                codes.add(el.text.strip())
        # Some schemas put the code in an attribute.
        for attr, val in (el.attrib or {}).items():
            if "scancode" in attr.lower() and val:
                codes.add(val.strip())
    return codes


def _row_fields(sd) -> dict[str, Any]:
    cd = getattr(sd, "contractDetails", None)
    c = getattr(cd, "contract", None) if cd is not None else None
    return {
        "rank": getattr(sd, "rank", None),
        "symbol": getattr(c, "symbol", None) if c else None,
        "secType": getattr(c, "secType", None) if c else None,
        "currency": getattr(c, "currency", None) if c else None,
        "primaryExchange": getattr(c, "primaryExchange", None) if c else None,
        "exchange": getattr(c, "exchange", None) if c else None,
        "conId": getattr(c, "conId", None) if c else None,
        "longName": getattr(cd, "longName", None) if cd else None,
        "marketName": getattr(cd, "marketName", None) if cd else None,
        "stockType": getattr(cd, "stockType", None) if cd else None,
        "distance": getattr(sd, "distance", None),
        "benchmark": getattr(sd, "benchmark", None),
        "projection": getattr(sd, "projection", None),
        "legsStr": getattr(sd, "legsStr", None),
    }


def run_scanner(ib: IB, scan_code: str) -> list[Any]:
    sub = ScannerSubscription(
        instrument=INSTRUMENT,
        locationCode=LOCATION,
        scanCode=scan_code,
        numberOfRows=TOP_N_PRINT,
    )
    print(f"\n=== SCANNER  {scan_code}  location={LOCATION} ===")
    try:
        data = ib.reqScannerData(sub)
    except Exception as exc:
        print(f"  FAIL reqScannerData: {type(exc).__name__}: {exc}")
        return []
    print(f"  rows={len(data)}")
    for sd in data[:TOP_N_PRINT]:
        f = _row_fields(sd)
        print(
            f"  rank={f['rank']:>3}  {f['symbol']:<8}  "
            f"exch={f['primaryExchange'] or f['exchange'] or '?':<8}  "
            f"name={(f['longName'] or '')[:40]!r}  "
            f"dist={f['distance']!r}  proj={f['projection']!r}"
        )
    return list(data)


def _bar_open(b) -> Any:
    # ib_insync versions differ: some use open_, some open.
    return getattr(b, "open_", None) if hasattr(b, "open_") else getattr(b, "open", None)


def pick_probe_symbols(scan_results: dict[str, list[Any]]) -> list[str]:
    """Liquid fallback + up to two common-stock movers from scanner ranks."""
    movers: list[str] = []
    for code in SCAN_CODES:
        for sd in scan_results.get(code) or []:
            f = _row_fields(sd)
            sym = (f.get("symbol") or "").upper()
            if not sym or sym == LIQUID_FALLBACK or sym in movers:
                continue
            # Prefer names that look like common stocks (skip obvious rights/preferreds).
            if sym.endswith(("R", "WS", "W", "U", "P")) and len(sym) > 4:
                # soft skip — KLXER-style rights often end in R; still allow short tickers
                if sym.endswith("R") and len(sym) >= 5:
                    continue
            movers.append(sym)
            if len(movers) >= 2:
                break
        if len(movers) >= 2:
            break
    out = [LIQUID_FALLBACK] + movers
    seen: set[str] = set()
    uniq: list[str] = []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq[:3]


def probe_symbol(ib: IB, symbol: str) -> dict[str, Any]:
    """Snapshot + short extended-hours historical; report errors."""
    report: dict[str, Any] = {"symbol": symbol, "ok": {}, "errors": []}
    print(f"\n--- MD probe {symbol} ---")
    contract = Stock(symbol, "SMART", "USD")
    try:
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            report["errors"].append("qualifyContracts returned empty")
            print("  qualify: EMPTY")
            return report
        contract = qualified[0]
        print(
            f"  qualify: conId={contract.conId}  "
            f"primaryExchange={contract.primaryExchange}  "
            f"exchange={contract.exchange}"
        )
        report["ok"]["qualify"] = True
        report["conId"] = contract.conId
        report["primaryExchange"] = contract.primaryExchange
    except Exception as exc:
        msg = f"qualify: {type(exc).__name__}: {exc}"
        report["errors"].append(msg)
        print(f"  {msg}")
        return report

    # Snapshot (default live type — paper may still be delayed entitlement).
    try:
        ticker = ib.reqMktData(contract, "", True, False)  # snapshot=True
        ib.sleep(2.0)
        last = _finite(ticker.last)
        close = _finite(ticker.close)
        bid = _finite(ticker.bid)
        ask = _finite(ticker.ask)
        print(
            f"  snapshot: last={_fmt_px(ticker.last)}  close={_fmt_px(ticker.close)}  "
            f"bid={_fmt_px(ticker.bid)}  ask={_fmt_px(ticker.ask)}"
        )
        if any(v is not None for v in (last, close, bid, ask)):
            report["ok"]["snapshot"] = True
        else:
            report["errors"].append("snapshot all-nan")
        ib.cancelMktData(contract)
    except Exception as exc:
        msg = f"snapshot: {type(exc).__name__}: {exc}"
        report["errors"].append(msg)
        print(f"  {msg}")

    # Short historical — RTH then extended (useRTH=False).
    end = ""
    for label, use_rth in (("RTH", True), ("EXTENDED", False)):
        try:
            bars = ib.reqHistoricalData(
                contract,
                endDateTime=end,
                durationStr="1 D",
                barSizeSetting="5 mins",
                whatToShow="TRADES",
                useRTH=use_rth,
                formatDate=1,
            )
            n = len(bars or [])
            print(f"  hist 5m {label} useRTH={use_rth}: bars={n}")
            if n:
                b0, b1 = bars[0], bars[-1]
                print(
                    f"    first={b0.date} o={_bar_open(b0)} c={b0.close}  "
                    f"last={b1.date} o={_bar_open(b1)} c={b1.close}"
                )
                report["ok"][f"hist_{label}"] = n
            else:
                report["errors"].append(f"hist_{label} empty")
        except Exception as exc:
            msg = f"hist_{label}: {type(exc).__name__}: {exc}"
            report["errors"].append(msg)
            print(f"  {msg}")

    # Contract details — longName / industry (mcap rarely here; note absence).
    try:
        details = ib.reqContractDetails(contract)
        if details:
            d0 = details[0]
            print(
                f"  details: longName={getattr(d0, 'longName', None)!r}  "
                f"stockType={getattr(d0, 'stockType', None)!r}  "
                f"category={getattr(d0, 'category', None)!r}"
            )
            # marketCap is not a standard ContractDetails field — flag for Phase 2.
            mcap = getattr(d0, "marketCap", None)
            if mcap is not None:
                print(f"  marketCap field present: {mcap}")
                report["ok"]["marketCap"] = mcap
            else:
                print("  marketCap: NOT on ContractDetails (Phase 2: Polygon/fundamentals)")
            report["ok"]["contractDetails"] = True
            report["longName"] = getattr(d0, "longName", None)
        else:
            report["errors"].append("contractDetails empty")
    except Exception as exc:
        msg = f"contractDetails: {type(exc).__name__}: {exc}"
        report["errors"].append(msg)
        print(f"  {msg}")

    return report


def main() -> int:
    util.startLoop()
    ib = IB()
    error_log: list[str] = []

    def on_error(reqId, errorCode, errorString, contract):
        line = f"IB ERROR {errorCode} reqId={reqId}: {errorString}"
        if contract:
            line += f"  contract={contract}"
        error_log.append(line)
        print(f"  !! {line}")

    ib.errorEvent += on_error

    print("=" * 72)
    print("Q-ALPHA TWS SCANNER SPIKE (Phase 1) — READ-ONLY")
    print(f"Host={TWS_HOST} Port={TWS_PORT} clientId={TWS_CLIENT_ID}")
    print(f"Started {datetime.now().isoformat(timespec='seconds')}")
    print("=" * 72)

    try:
        ib.connect(TWS_HOST, TWS_PORT, clientId=TWS_CLIENT_ID, timeout=12)
    except Exception as exc:
        print(f"\nCONNECT FAILED: {exc}")
        print("Open TWS paper (DUR857496), enable API on 7497, free clientId 97.")
        return 1

    accounts = list(ib.managedAccounts() or [])
    print(f"\nCONNECTED  accounts={accounts}")
    if "DUR857496" in accounts:
        print("  Paper DUR857496 confirmed.")
    else:
        print("  NOTE: DUR857496 not in managedAccounts — check login.")

    # Discover optional scan codes.
    available: set[str] = set()
    try:
        xml_text = ib.reqScannerParameters()
        available = _scan_codes_from_params(xml_text or "")
        print(f"\nScanner parameters: parsed {len(available)} scanCode(s)")
        for opt in OPTIONAL_SCAN_CODES:
            print(f"  optional {opt}: {'YES' if opt in available else 'no'}")
    except Exception as exc:
        print(f"\nreqScannerParameters failed: {type(exc).__name__}: {exc}")

    scan_results: dict[str, list[Any]] = {}
    for code in SCAN_CODES:
        scan_results[code] = run_scanner(ib, code)
        time.sleep(0.5)

    for code in OPTIONAL_SCAN_CODES:
        if code in available:
            scan_results[code] = run_scanner(ib, code)
            time.sleep(0.5)
            break  # one optional is enough for the spike

    # Union + dedupe (preview of Phase 2 LIST step).
    union: list[str] = []
    seen: set[str] = set()
    for code, rows in scan_results.items():
        for sd in rows:
            sym = (_row_fields(sd).get("symbol") or "").upper()
            if sym and sym not in seen:
                seen.add(sym)
                union.append(sym)
    print(f"\n=== UNION (deduped) n={len(union)} ===")
    print("  " + ", ".join(union[:40]) + (" ..." if len(union) > 40 else ""))

    probes = pick_probe_symbols(scan_results)
    print(f"\n=== MD PROBES ({len(probes)}): {probes} ===")
    probe_reports = [probe_symbol(ib, s) for s in probes]

    # Summary
    print("\n" + "=" * 72)
    print("SPIKE SUMMARY")
    print("=" * 72)
    for code, rows in scan_results.items():
        print(f"  scanner {code}: {len(rows)} rows")
    print(f"  union unique symbols: {len(union)}")
    for r in probe_reports:
        ok = ",".join(sorted(r.get("ok") or {})) or "(none)"
        errs = "; ".join(r.get("errors") or []) or "(none)"
        print(f"  probe {r['symbol']}: ok=[{ok}]  errors=[{errs}]")
    if error_log:
        print(f"\n  IB errorEvent lines ({len(error_log)}):")
        for line in error_log[-20:]:
            print(f"    {line}")
    else:
        print("\n  IB errorEvent: (none captured)")

    scanners_ok = any(len(v) > 0 for v in scan_results.values())
    md_ok = any(bool(r.get("ok")) for r in probe_reports)
    if scanners_ok and md_ok:
        print("\nVERDICT: SPIKE PASS (scanners + at least one MD probe path worked)")
        verdict = 0
    elif scanners_ok:
        print("\nVERDICT: SPIKE PARTIAL — scanners OK, MD weak (see errors)")
        verdict = 0  # still useful for Phase 2 design
    else:
        print("\nVERDICT: SPIKE FAIL — no scanner rows")
        verdict = 2

    try:
        ib.disconnect()
    except Exception:
        pass
    return verdict


if __name__ == "__main__":
    sys.exit(main())
