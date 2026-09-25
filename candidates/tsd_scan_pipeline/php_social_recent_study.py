#!/usr/bin/env python3
"""
Peak Hour social/sentiment firing check — selection only, same risk per trade.

Reads local php_scan_*.json when the laptop has them (gitignored). Does not
place orders, does not change size, and does not call StockTwits (live stream
has no historical as_of). Polygon news is causal only when a key is present
and the caller passes as_of; this script does not refetch. It scores fields
already stored on the scan row.

Usage:
  python candidates/tsd_scan_pipeline/php_social_recent_study.py
  python candidates/tsd_scan_pipeline/php_social_recent_study.py --days 7 --write
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz

from tsd_scan_pipeline.php_scan_funnel import RESULTS_DIR, list_scan_funnels_since
from tsd_scan_pipeline.tsd_social import social_audit_summary

ET = pytz.timezone("America/New_York")
REPORT_DIR = Path(__file__).resolve().parent / "reports"

# Same-day favorable excursion Peak Hour already treats as a rip (T1 / +2%).
MOVER_MFE = 0.02


def selection_flags(row: dict[str, Any]) -> dict[str, bool]:
    """
    Research filter flags. True means "would skip this name."

    Not a size overlay. social_missing and low news/ST overlap on purpose:
    quiet tape is the broad bucket; social_missing is the fetch-failed subset.
    """
    news = float(row.get("news_velocity_24h") or 0)
    st = float(row.get("st_msg_24h") or 0)
    missing = int(row.get("social_missing") or 0) == 1
    return {
        "dilution_flag": int(row.get("dilution_flag") or 0) == 1,
        "distress_flag": int(row.get("distress_flag") or 0) == 1,
        "social_missing": missing,
        "low_news_and_st": news <= 0 and st <= 0,
    }


def would_skip(row: dict[str, Any]) -> str:
    """First matching skip reason, or empty string if the filter would keep it."""
    flags = selection_flags(row)
    for key in ("dilution_flag", "distress_flag", "social_missing", "low_news_and_st"):
        if flags[key]:
            return key
    return ""


def outcome_label(*, mfe: float | None, close_ret: float | None = None) -> str:
    """mover if favorable excursion hit +2%; dud if it did not and close was flat/down."""
    if mfe is None:
        return "unknown"
    if mfe >= MOVER_MFE:
        return "mover"
    if close_ret is None or close_ret <= 0:
        return "dud"
    return "flat"


def _load_scans(days: int) -> list[tuple[Path, dict[str, Any]]]:
    out: list[tuple[Path, dict[str, Any]]] = []
    for path in list_scan_funnels_since(days):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append((path, doc))
    return out


def rows_from_scans(scans: list[tuple[Path, dict[str, Any]]]) -> list[dict[str, Any]]:
    """One row per launch on each saved scan. Social keys may be null on old files."""
    rows: list[dict[str, Any]] = []
    for path, doc in scans:
        et = str(doc.get("et") or "")
        day = et[:10]
        for launch in doc.get("launches") or []:
            row = dict(launch)
            row["scan_file"] = path.name
            row["signal_day"] = day
            row["taken"] = bool(launch.get("taken"))
            row["skip_reason"] = would_skip(row)
            rows.append(row)
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """How often the selection filter would have dropped taken vs not-taken names."""
    taken = [r for r in rows if r.get("taken")]
    missed = [r for r in rows if not r.get("taken")]

    def _n(bucket: list[dict[str, Any]], reason: str) -> int:
        return sum(1 for r in bucket if r.get("skip_reason") == reason)

    reasons = ("dilution_flag", "distress_flag", "social_missing", "low_news_and_st")
    return {
        "launches": len(rows),
        "taken": len(taken),
        "not_taken": len(missed),
        "social": social_audit_summary(rows),
        "skip_taken": {k: _n(taken, k) for k in reasons},
        "skip_not_taken": {k: _n(missed, k) for k in reasons},
        "fields_present": sum(1 for r in rows if r.get("social_missing") is not None),
    }


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "signal_day",
        "scan_file",
        "symbol",
        "taken",
        "rank_order",
        "news_velocity_24h",
        "st_msg_24h",
        "st_bull_ratio",
        "st_ok",
        "social_missing",
        "dilution_flag",
        "distress_flag",
        "x_ok",
        "tws_ok",
        "skip_reason",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser(description="Peak Hour social selection study")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    scans = _load_scans(args.days)
    rows = rows_from_scans(scans)
    summary = summarize(rows)
    print(
        f"scans={len(scans)} launches={summary['launches']} "
        f"fields_present={summary['fields_present']} "
        f"social={summary['social']}"
    )
    if args.write and rows:
        stamp = datetime.now(ET).strftime("%Y%m%d")
        path = REPORT_DIR / f"social_scan_rows_{stamp}.csv"
        write_csv(rows, path)
        print(f"wrote {path}")
    if not scans:
        print(
            "No php_scan_*.json in results/peak_hour_scans "
            "(folder is gitignored; laptop copy is the source)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
