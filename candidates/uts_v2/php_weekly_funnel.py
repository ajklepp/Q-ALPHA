#!/usr/bin/env python3
"""
Peak Hour Performers — weekly scan funnel rollup (research).

Reads results/peak_hour_scans/php_scan_*.json from the last N days and writes:
  results/peak_hour_scans/php_weekly_YYYYMMDD.md

Usage:
  .\\venv\\Scripts\\python.exe candidates\\uts_v2\\php_weekly_funnel.py
  .\\venv\\Scripts\\python.exe candidates\\uts_v2\\php_weekly_funnel.py --days 7 --write
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = ROOT / "candidates"
sys.path.insert(0, str(CANDIDATES))

from tsd_scan_pipeline.php_scan_funnel import (  # noqa: E402
    RESULTS_DIR,
    list_scan_funnels_since,
)
from tsd_scan_pipeline.tsd_capacity import load_state  # noqa: E402

ET = pytz.timezone("America/New_York")


def _closed_outcomes(entered_syms: set[str]) -> list[dict[str, Any]]:
    """Match entered symbols to closed legs in local book (best-effort)."""
    book = load_state()
    out: list[dict[str, Any]] = []
    for pos in book.get("positions") or []:
        sym = str(pos.get("symbol") or "").upper()
        if sym not in entered_syms:
            continue
        for leg in pos.get("legs") or []:
            if str(leg.get("status") or "").upper() != "CLOSED":
                continue
            exits = leg.get("exits") or []
            pnl = 0.0
            entry = float((leg.get("trail") or {}).get("entry_price") or leg.get("price") or 0)
            reason = ""
            for ex in exits:
                sh = int(ex.get("shares") or 0)
                px = float(ex.get("exit_price") or 0)
                if sh and px and entry:
                    pnl += (px - entry) * sh
                reason = str(ex.get("reason") or reason)
            out.append({
                "symbol": sym,
                "pnl": round(pnl, 2),
                "exit_reason": reason,
                "status": "CLOSED",
            })
    # Still-open entered names
    for pos in book.get("positions") or []:
        sym = str(pos.get("symbol") or "").upper()
        if sym not in entered_syms:
            continue
        if str(pos.get("status") or "").upper() == "OPEN":
            if not any(o["symbol"] == sym and o["status"] == "CLOSED" for o in out):
                out.append({"symbol": sym, "pnl": None, "exit_reason": None, "status": "OPEN"})
    return out


def build_weekly_funnel(*, days: int = 7) -> dict[str, Any]:
    paths = list_scan_funnels_since(days)
    docs: list[dict[str, Any]] = []
    for path in paths:
        try:
            docs.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue

    reject_hist: Counter[str] = Counter()
    entered_syms: set[str] = set()
    total_htf = 0
    total_scanned = 0
    total_launches = 0
    total_entered = 0

    for doc in docs:
        total_htf += int(doc.get("htf_pass_count") or 0)
        total_scanned += int(doc.get("symbols_scanned") or 0)
        total_launches += int(doc.get("launches_n") or 0)
        total_entered += int(doc.get("entered_n") or 0)
        for reason, n in (doc.get("reject_summary") or {}).items():
            reject_hist[str(reason)] += int(n)
        for e in doc.get("entered") or []:
            entered_syms.add(str(e.get("symbol") or "").upper())

    scans = len(docs)
    launch_rate = (total_launches / total_scanned) if total_scanned else 0.0
    enter_rate = (total_entered / total_launches) if total_launches else 0.0

    return {
        "asof": datetime.now(ET).isoformat(),
        "days": days,
        "scans_run": scans,
        "total_htf_evals": total_htf,
        "total_symbols_scanned": total_scanned,
        "total_launches": total_launches,
        "total_entered": total_entered,
        "launch_rate": round(launch_rate, 4),
        "enter_rate": round(enter_rate, 4),
        "reject_histogram": dict(reject_hist.most_common()),
        "symbols_entered": sorted(entered_syms),
        "outcomes": _closed_outcomes(entered_syms),
        "scan_files": [p.name for p in paths],
    }


def format_weekly_md(card: dict[str, Any]) -> str:
    lines = [
        "# Peak Hour weekly funnel",
        "",
        f"**As of:** {card.get('asof')}",
        f"**Window:** last {card.get('days')} days",
        "",
        "## Funnel",
        f"- Scans run: **{card.get('scans_run')}**",
        f"- HTF-pass evals (sum): **{card.get('total_htf_evals')}**",
        f"- Symbols scanned (sum): **{card.get('total_symbols_scanned')}**",
        f"- 1H launches: **{card.get('total_launches')}** "
        f"(rate {100 * float(card.get('launch_rate') or 0):.2f}% of scanned)",
        f"- Entered: **{card.get('total_entered')}** "
        f"(rate {100 * float(card.get('enter_rate') or 0):.2f}% of launches)",
        "",
        "## Reject reason histogram",
    ]
    hist = card.get("reject_histogram") or {}
    if not hist:
        lines.append("_No reject data yet._")
    else:
        lines.append("| Reason | Count |")
        lines.append("|--------|------:|")
        for reason, n in hist.items():
            lines.append(f"| `{reason}` | {n} |")
    lines.extend(["", "## Entered symbols", ""])
    entered = card.get("symbols_entered") or []
    if not entered:
        lines.append("_None._")
    else:
        lines.append(", ".join(f"**{s}**" for s in entered))
    lines.extend(["", "## Outcomes (local book)", ""])
    outcomes = card.get("outcomes") or []
    if not outcomes:
        lines.append("_No book matches._")
    else:
        lines.append("| Symbol | Status | PnL | Exit |")
        lines.append("|--------|--------|----:|------|")
        for o in outcomes:
            pnl = o.get("pnl")
            pnl_s = f"${pnl:+.2f}" if pnl is not None else "—"
            lines.append(
                f"| {o.get('symbol')} | {o.get('status')} | {pnl_s} | "
                f"{o.get('exit_reason') or '—'} |"
            )
    lines.extend(["", "## Source scans", ""])
    for name in card.get("scan_files") or []:
        lines.append(f"- `{name}`")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Peak Hour weekly funnel rollup")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--write", action="store_true", help="Write php_weekly_*.md")
    args = parser.parse_args()

    card = build_weekly_funnel(days=args.days)
    md = format_weekly_md(card)
    print(md)
    if args.write:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(ET).strftime("%Y%m%d")
        path = RESULTS_DIR / f"php_weekly_{stamp}.md"
        path.write_text(md, encoding="utf-8")
        json_path = RESULTS_DIR / f"php_weekly_{stamp}.json"
        json_path.write_text(json.dumps(card, indent=2, default=str), encoding="utf-8")
        print(f"Wrote {path}")
        print(f"Wrote {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
