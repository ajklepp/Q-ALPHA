"""
Spike — TWS scanner filters toward Aaron's "US Active (mod1)" (READ-ONLY).

Proves whether stockTypeFilter / marketCapAbove|Below / TagValue filters
strip ETFs at request time. No orders. clientId 97 · paper 7497.

Usage (TWS paper open, API on 7497):
  .\\venv\\Scripts\\python.exe candidates/tws_scan_pipeline/spike_scanner_filters.py

Does NOT change quality_score / scorer / EXP-0020 / agent wiring.
"""
from __future__ import annotations

import math
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from typing import Any

from ib_insync import IB, ScannerSubscription, TagValue, util

TWS_HOST = "127.0.0.1"
TWS_PORT = 7497
TWS_CLIENT_ID = 97

SCAN_CODES = ("TOP_PERC_GAIN", "MOST_ACTIVE", "HOT_BY_VOLUME")
LOCATION = "STK.US.MAJOR"
INSTRUMENT = "STK"
ROWS = 50

# Aaron US Active (mod1) market-cap band (dollars).
MCAP_ABOVE = 150e6
MCAP_BELOW = 300e9

# aboveVolume is NOT average volume — never treat them as the same.
# Documented explicitly in every summary print.


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


def _row_fields(sd) -> dict[str, Any]:
    cd = getattr(sd, "contractDetails", None)
    c = getattr(cd, "contract", None) if cd is not None else None
    return {
        "rank": getattr(sd, "rank", None),
        "symbol": str(getattr(c, "symbol", "") or "").upper() if c else "",
        "secType": getattr(c, "secType", None) if c else None,
        "primaryExchange": (
            getattr(c, "primaryExchange", None) or getattr(c, "exchange", None)
        ) if c else None,
        "longName": getattr(cd, "longName", None) if cd else None,
        "stockType": str(getattr(cd, "stockType", "") or "").upper() if cd else "",
        "distance": getattr(sd, "distance", None),
        "projection": getattr(sd, "projection", None),
    }


def _is_etf_like(stock_type: str, long_name: str | None) -> bool:
    st = (stock_type or "").upper()
    if st in {"ETF", "ETN", "ETP", "FUND"}:
        return True
    name = (long_name or "").upper()
    for tok in (" ETF", " ETN", " ETFS", "FUND ", " LEVERAGED", " 2X ", " 3X "):
        if tok in f" {name} ":
            return True
    if name.endswith(" ETF") or name.endswith(" ETN"):
        return True
    return False


def _summarize_arm(label: str, rows: list[Any]) -> dict[str, Any]:
    fields = [_row_fields(sd) for sd in rows]
    symbols = [f["symbol"] for f in fields if f["symbol"]]
    types = Counter(f["stockType"] or "(blank)" for f in fields)
    etf_n = sum(
        1 for f in fields
        if _is_etf_like(f["stockType"], f.get("longName"))
    )
    etf_syms = [
        f["symbol"] for f in fields
        if f["symbol"] and _is_etf_like(f["stockType"], f.get("longName"))
    ]
    return {
        "label": label,
        "n": len(rows),
        "symbols": symbols,
        "types": dict(types),
        "etf_n": etf_n,
        "etf_syms": etf_syms,
        "sample": symbols[:15],
        "fields": fields,
    }


def _print_arm(summary: dict[str, Any], *, baseline_syms: set[str] | None = None) -> None:
    print(f"\n--- {summary['label']} ---")
    print(f"  rows={summary['n']}  ETF/ETN-like={summary['etf_n']}")
    print(f"  stockType counts: {summary['types']}")
    print(f"  sample: {', '.join(summary['sample']) or '(none)'}")
    if summary["etf_syms"]:
        print(f"  ETF sample: {', '.join(summary['etf_syms'][:12])}")
    if baseline_syms is not None:
        cur = set(summary["symbols"])
        overlap = sorted(cur & baseline_syms)
        only_new = sorted(cur - baseline_syms)
        only_base = sorted(baseline_syms - cur)
        print(
            f"  vs baseline: overlap={len(overlap)}  "
            f"only_filtered={len(only_new)}  only_baseline={len(only_base)}"
        )
        if only_base[:10]:
            print(f"    dropped vs baseline (sample): {', '.join(only_base[:10])}")
        if only_new[:10]:
            print(f"    new vs baseline (sample): {', '.join(only_new[:10])}")


def run_scanner(
    ib: IB,
    scan_code: str,
    *,
    rows: int = ROWS,
    market_cap_above: float | None = None,
    market_cap_below: float | None = None,
    stock_type_filter: str = "",
    filter_options: list[TagValue] | None = None,
) -> list[Any]:
    kwargs: dict[str, Any] = {
        "instrument": INSTRUMENT,
        "locationCode": LOCATION,
        "scanCode": scan_code,
        "numberOfRows": rows,
    }
    if market_cap_above is not None:
        kwargs["marketCapAbove"] = float(market_cap_above)
    if market_cap_below is not None:
        kwargs["marketCapBelow"] = float(market_cap_below)
    if stock_type_filter:
        kwargs["stockTypeFilter"] = stock_type_filter

    sub = ScannerSubscription(**kwargs)
    filt = filter_options or []
    tag = (
        f"mcap=[{market_cap_above},{market_cap_below}] "
        f"stockTypeFilter={stock_type_filter!r} "
        f"filterOpts={[f'{t.tag}={t.value}' for t in filt]}"
    )
    print(f"\n=== SCAN {scan_code}  {tag} ===")
    try:
        data = ib.reqScannerData(
            sub,
            scannerSubscriptionFilterOptions=filt,
        ) or []
    except Exception as exc:
        print(f"  FAIL: {type(exc).__name__}: {exc}")
        return []
    print(f"  rows returned={len(data)}")
    return list(data)


def _harvest_filter_tags(xml_text: str) -> dict[str, Any]:
    """
    Probe reqScannerParameters XML for TagValues related to avg volume / change%.
    Returns candidate tag names + evidence snippets.
    """
    interest = (
        "averagevolume", "avgvolume", "average_volume", "avg_volume",
        "changepercent", "percentchange", "changeperc", "percchange",
        "pricechange", "chgperc", "volumeavg", "avvol",
    )
    hits: dict[str, list[str]] = {k: [] for k in interest}
    # Also collect any Filter / AbstractField code attributes that look useful.
    codes: list[str] = []
    if not xml_text:
        return {"hits": hits, "codes": codes, "raw_len": 0}

    # Case-insensitive string harvest (XML can be huge / oddly namespaced).
    lower = xml_text.lower()
    for key in interest:
        for m in re.finditer(re.escape(key), lower):
            start = max(0, m.start() - 80)
            end = min(len(xml_text), m.end() + 80)
            snippet = xml_text[start:end].replace("\n", " ")
            if len(hits[key]) < 5:
                hits[key].append(snippet)

    try:
        root = ET.fromstring(xml_text)
        for el in root.iter():
            tag = (el.tag or "").split("}")[-1]
            text = (el.text or "").strip()
            attrs = el.attrib or {}
            blob = " ".join([tag, text] + [f"{k}={v}" for k, v in attrs.items()])
            blob_l = blob.lower()
            if any(k in blob_l for k in interest):
                code = (
                    attrs.get("code")
                    or attrs.get("name")
                    or attrs.get("colId")
                    or text
                    or tag
                )
                if code and code not in codes:
                    codes.append(str(code))
            # Common IB schema: <AbstractField type="..." code="avgVolume"> etc.
            for attr, val in attrs.items():
                if "code" in attr.lower() or "name" in attr.lower():
                    vl = str(val).lower()
                    if any(k in vl for k in interest) and val not in codes:
                        codes.append(str(val))
    except ET.ParseError as exc:
        print(f"  XML parse partial fail: {exc}")

    return {
        "hits": {k: v for k, v in hits.items() if v},
        "codes": codes[:40],
        "raw_len": len(xml_text),
    }


def _adr_count(fields: list[dict]) -> int:
    return sum(1 for f in fields if (f.get("stockType") or "").upper() == "ADR")


def main() -> int:
    util.startLoop()
    ib = IB()
    error_log: list[str] = []

    def on_error(reqId, errorCode, errorString, contract):
        line = f"IB ERROR {errorCode} reqId={reqId}: {errorString}"
        if contract:
            line += f"  contract={contract}"
        error_log.append(line)
        # 162 = scanner cancel noise on teardown — still print once.
        print(f"  !! {line}")

    ib.errorEvent += on_error

    print("=" * 72)
    print("Q-ALPHA TWS SCANNER FILTER SPIKE — READ-ONLY")
    print(f"Host={TWS_HOST} Port={TWS_PORT} clientId={TWS_CLIENT_ID}")
    print(f"Started {datetime.now().isoformat(timespec='seconds')}")
    print("NOTE: aboveVolume ≠ average volume — do not conflate.")
    print("=" * 72)

    try:
        ib.connect(TWS_HOST, TWS_PORT, clientId=TWS_CLIENT_ID, timeout=12)
    except Exception as exc:
        print(f"\nCONNECT FAILED: {exc}")
        print("Open TWS paper (DUR857496), enable API on 7497, free clientId 97.")
        return 1

    accounts = list(ib.managedAccounts() or [])
    print(f"\nCONNECTED  accounts={accounts}")

    # --- D) Probe scanner parameters XML for avg vol / change% tags ---
    xml_text = ""
    try:
        xml_text = ib.reqScannerParameters() or ""
        print(f"\nreqScannerParameters: {len(xml_text)} chars")
    except Exception as exc:
        print(f"\nreqScannerParameters FAIL: {type(exc).__name__}: {exc}")

    tag_probe = _harvest_filter_tags(xml_text)
    print("\n=== XML FILTER TAG PROBE (avg volume / change%) ===")
    if tag_probe["hits"]:
        for key, snippets in tag_probe["hits"].items():
            print(f"  hit '{key}' x{len(snippets)}:")
            for sn in snippets[:2]:
                print(f"    …{sn}…")
    else:
        print("  No string hits for averageVolume / changePercent family.")
    if tag_probe["codes"]:
        print(f"  candidate codes/attrs: {tag_probe['codes']}")
    else:
        print("  No AbstractField-like codes harvested for those families.")

    # Candidate TagValues to try if anything looked promising.
    # IB docs historically use tags like "avgVolumeAbove" / "changePercAbove"
    # — we try only names we found, plus a small documented guess list.
    guess_tags = []
    found_lower = " ".join(tag_probe["codes"]).lower()
    for cand in (
        "avgVolumeAbove", "averageVolumeAbove", "AvgVolumeAbove",
        "changePercAbove", "priceChangePercAbove", "changePercentAbove",
        "volumeAvgAbove",
    ):
        if cand.lower() in found_lower or any(
            cand.lower() in k for k in tag_probe["hits"]
        ):
            guess_tags.append(cand)
    # Always record what we will attempt (even if XML silent — may still work).
    attempt_filters: list[tuple[str, list[TagValue]]] = []
    if guess_tags:
        opts = []
        if any("vol" in t.lower() for t in guess_tags):
            vol_tag = next(t for t in guess_tags if "vol" in t.lower())
            opts.append(TagValue(vol_tag, "500000"))
        if any("change" in t.lower() or "perc" in t.lower() for t in guess_tags):
            chg_tag = next(
                t for t in guess_tags
                if "change" in t.lower() or "perc" in t.lower()
            )
            opts.append(TagValue(chg_tag, "3"))
        if opts:
            attempt_filters.append(("xml_matched_tags", opts))
    # Blind probes (document pass/fail) — common IB forum names.
    attempt_filters.append((
        "blind_avgVolumeAbove_500k",
        [TagValue("avgVolumeAbove", "500000")],
    ))
    attempt_filters.append((
        "blind_changePercAbove_3",
        [TagValue("changePercAbove", "3")],
    ))
    attempt_filters.append((
        "blind_avgVol_and_change",
        [
            TagValue("avgVolumeAbove", "500000"),
            TagValue("changePercAbove", "3"),
        ],
    ))

    # --- A) Baseline unfiltered ---
    print("\n" + "=" * 72)
    print("A) BASELINE — unfiltered (50 rows/code)")
    print("=" * 72)
    baseline_by_code: dict[str, dict[str, Any]] = {}
    baseline_union: set[str] = set()
    baseline_etf_total = 0
    for code in SCAN_CODES:
        rows = run_scanner(ib, code, rows=ROWS)
        summary = _summarize_arm(f"BASELINE {code}", rows)
        _print_arm(summary)
        baseline_by_code[code] = summary
        baseline_union |= set(summary["symbols"])
        baseline_etf_total += summary["etf_n"]
        time.sleep(0.4)

    print(
        f"\nBASELINE UNION unique={len(baseline_union)}  "
        f"ETF rows summed across codes={baseline_etf_total} "
        f"(not unique; per-code count)"
    )

    # --- B) Filtered: mcap + stockTypeFilter=CORP ---
    print("\n" + "=" * 72)
    print("B) FILTERED — marketCap 150M–300B + stockTypeFilter=CORP")
    print("=" * 72)
    corp_by_code: dict[str, dict[str, Any]] = {}
    corp_union: set[str] = set()
    corp_etf_total = 0
    corp_adr_total = 0
    for code in SCAN_CODES:
        rows = run_scanner(
            ib, code, rows=ROWS,
            market_cap_above=MCAP_ABOVE,
            market_cap_below=MCAP_BELOW,
            stock_type_filter="CORP",
        )
        summary = _summarize_arm(f"CORP {code}", rows)
        _print_arm(summary, baseline_syms=set(baseline_by_code[code]["symbols"]))
        corp_by_code[code] = summary
        corp_union |= set(summary["symbols"])
        corp_etf_total += summary["etf_n"]
        corp_adr_total += _adr_count(summary["fields"])
        time.sleep(0.4)

    print(
        f"\nCORP UNION unique={len(corp_union)}  "
        f"ETF rows summed={corp_etf_total}  ADR rows summed={corp_adr_total}"
    )

    # --- B2) Retry CORP,ADR if ADRs vanished ---
    corp_adr_by_code: dict[str, dict[str, Any]] = {}
    ran_corp_adr = False
    if corp_adr_total == 0:
        # Check whether baseline had ADRs (reason to retry).
        baseline_adr = sum(
            _adr_count(s["fields"]) for s in baseline_by_code.values()
        )
        print(
            f"\nB2) CORP returned 0 ADRs "
            f"(baseline ADR rows summed={baseline_adr}) → retry stockTypeFilter='CORP,ADR'"
        )
        ran_corp_adr = True
        for code in SCAN_CODES:
            rows = run_scanner(
                ib, code, rows=ROWS,
                market_cap_above=MCAP_ABOVE,
                market_cap_below=MCAP_BELOW,
                stock_type_filter="CORP,ADR",
            )
            summary = _summarize_arm(f"CORP,ADR {code}", rows)
            _print_arm(summary, baseline_syms=set(baseline_by_code[code]["symbols"]))
            corp_adr_by_code[code] = summary
            time.sleep(0.4)
        adr_after = sum(
            _adr_count(s["fields"]) for s in corp_adr_by_code.values()
        )
        etf_after = sum(s["etf_n"] for s in corp_adr_by_code.values())
        print(f"\nCORP,ADR: ADR rows summed={adr_after}  ETF rows summed={etf_after}")
    else:
        print("\nB2) CORP still has ADRs — skip CORP,ADR retry.")

    # --- D continued: try TagValue filters on MOST_ACTIVE only (cheap) ---
    print("\n" + "=" * 72)
    print("D) TagValue filter probes (MOST_ACTIVE only)")
    print("   Reminder: aboveVolume ≠ average volume.")
    print("=" * 72)
    tag_results: list[dict[str, Any]] = []
    for name, opts in attempt_filters:
        rows = run_scanner(
            ib, "MOST_ACTIVE", rows=ROWS,
            market_cap_above=MCAP_ABOVE,
            market_cap_below=MCAP_BELOW,
            stock_type_filter="CORP",
            filter_options=opts,
        )
        summary = _summarize_arm(f"TAG {name}", rows)
        _print_arm(summary, baseline_syms=set(baseline_by_code["MOST_ACTIVE"]["symbols"]))
        # Detect IB reject via error log growth / empty sudden fail.
        tag_results.append({
            "name": name,
            "opts": [f"{t.tag}={t.value}" for t in opts],
            "n": summary["n"],
            "etf_n": summary["etf_n"],
        })
        time.sleep(0.5)

    # --- Verdict ---
    print("\n" + "=" * 72)
    print("SPIKE SUMMARY / VERDICT")
    print("=" * 72)
    for code in SCAN_CODES:
        b = baseline_by_code[code]
        c = corp_by_code[code]
        print(
            f"  {code}: baseline n={b['n']} ETF={b['etf_n']}  →  "
            f"CORP n={c['n']} ETF={c['etf_n']}"
        )
        if ran_corp_adr and code in corp_adr_by_code:
            ca = corp_adr_by_code[code]
            print(
                f"         CORP,ADR n={ca['n']} ETF={ca['etf_n']} "
                f"ADR={_adr_count(ca['fields'])}"
            )

    # Decide verdict from ETF reduction without emptying scanners.
    base_etf = sum(baseline_by_code[c]["etf_n"] for c in SCAN_CODES)
    filt_etf = sum(corp_by_code[c]["etf_n"] for c in SCAN_CODES)
    filt_n = sum(corp_by_code[c]["n"] for c in SCAN_CODES)
    base_n = sum(baseline_by_code[c]["n"] for c in SCAN_CODES)

    print(f"\n  ETF rows (summed across codes): baseline={base_etf}  CORP={filt_etf}")
    print(f"  Total rows (summed): baseline={base_n}  CORP={filt_n}")
    print("  TagValue probes:")
    for tr in tag_results:
        print(f"    {tr['name']}: opts={tr['opts']} → n={tr['n']} ETF={tr['etf_n']}")

    if filt_n == 0:
        verdict = "fails — keep post-filter only"
        detail = "CORP+mcap returned zero rows (filters rejected or too tight)."
    elif base_etf > 0 and filt_etf == 0 and filt_n >= max(10, base_n // 4):
        verdict = "API stockTypeFilter works"
        detail = "ETFs gone at request time; scanners still return a usable list."
    elif base_etf > 0 and filt_etf < base_etf and filt_n > 0:
        verdict = "partial"
        detail = (
            f"ETF count dropped {base_etf}→{filt_etf} but some remain — "
            "keep passes_instrument_safety + name/ETF post-filter."
        )
    elif base_etf == 0:
        verdict = "partial"
        detail = (
            "Baseline already had 0 ETF/ETN by stockType "
            "(session may be quiet) — inconclusive; keep post-filter."
        )
    else:
        verdict = "fails — keep post-filter only"
        detail = "ETF count did not drop under stockTypeFilter=CORP."

    print(f"\nVERDICT: {verdict}")
    print(f"  {detail}")
    print(
        "  Plain check tomorrow: list should look more like TWS US Active (mod1), "
        "not unfiltered MOST_ACTIVE full of SOXL/NVDL."
    )

    if error_log:
        print(f"\n  IB errorEvent lines ({len(error_log)}), last 15:")
        for line in error_log[-15:]:
            print(f"    {line}")

    try:
        ib.disconnect()
    except Exception:
        pass

    # Exit 0 on works/partial (usable signal); 2 only if scanners empty.
    if filt_n == 0 and base_n == 0:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
