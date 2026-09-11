"""
Q-ALPHA TSD pipeline — weekly 5-trading-day scorecard.

Aggregates scan snapshots, book state, pool, and trail monitor logs
for the last 5 US trading days.

Usage:
  py -3 candidates/tsd_scan_pipeline/tsd_scorecard.py
  py -3 candidates/tsd_scan_pipeline/tsd_scorecard.py --days 5 --write
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytz

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

from state_paths import is_trading_day, state_path  # noqa: E402
from tsd_scan_pipeline.tsd_capacity import load_state, open_symbols  # noqa: E402
from tsd_scan_pipeline.tsd_pool import load_pool  # noqa: E402

ET = pytz.timezone("America/New_York")
RESULTS_DIR = PIPELINE_DIR / "results"


def _trading_days_back(n: int, from_date: date | None = None) -> list[date]:
    """Last n trading days ending at from_date (default today ET)."""
    end = from_date or datetime.now(ET).date()
    out: list[date] = []
    d = end
    while len(out) < n:
        if is_trading_day(d):
            out.append(d)
        d -= timedelta(days=1)
        if (end - d).days > n * 3:
            break
    return sorted(out)


def _load_scan_snapshots(since: date) -> list[dict[str, Any]]:
    snaps: list[dict[str, Any]] = []
    for path in sorted(RESULTS_DIR.glob("scan_*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        scanned = str(doc.get("scanned_at") or "")[:10]
        if scanned and date.fromisoformat(scanned) >= since:
            doc["_path"] = str(path)
            snaps.append(doc)
    return snaps


def _load_trail_snapshots(since: date) -> list[dict[str, Any]]:
    snaps: list[dict[str, Any]] = []
    for path in sorted(RESULTS_DIR.glob("trail_monitor_*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        checked = str(doc.get("checked_at") or "")[:10]
        if checked and date.fromisoformat(checked) >= since:
            snaps.append(doc)
    return snaps


def build_scorecard(*, days: int = 5) -> dict[str, Any]:
    """Build scorecard dict for the last N trading days."""
    window_days = _trading_days_back(days)
    since = window_days[0] if window_days else datetime.now(ET).date()

    scans = _load_scan_snapshots(since)
    trails = _load_trail_snapshots(since)
    book = load_state()
    pool = load_pool()

    signals = sum(len(s.get("signal_candidates") or []) for s in scans)
    entries = sum(len(s.get("entry_results") or []) for s in scans)
    live_scans = sum(1 for s in scans if s.get("mode") == "LIVE")
    dry_scans = len(scans) - live_scans

    trail_actions = sum(len(t.get("actions") or []) for t in trails)
    exits = sum(
        1
        for t in trails
        for a in (t.get("actions") or [])
        if a.get("reason") in ("trail", "kill", "time_cap")
    )

    open_pos = open_symbols(book)
    closed_legs = 0
    for pos in book.get("positions") or []:
        for leg in pos.get("legs") or []:
            closed_legs += len(leg.get("exits") or [])

    # Peak Hour PHP funnel (live path) — separate from legacy scan_*.json card.
    php: dict[str, Any] = {
        "scans_run": 0,
        "launches_n": 0,
        "take_n": 0,
        "entered_n": 0,
        "take_to_entered_rate": None,
        "note": "Peak Hour live path (php_scan_*.json); legacy TSD scan_*.json may be 0",
    }
    try:
        from tsd_scan_pipeline.php_scan_funnel import list_scan_funnels_since

        php_paths = list_scan_funnels_since(days)
        take_n = 0
        entered_n = 0
        launches_n = 0
        for path in php_paths:
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            launches_n += int(doc.get("launches_n") or 0)
            take_n += int(doc.get("take_n") or 0)
            entered_n += int(doc.get("entered_n") or 0)
        php.update({
            "scans_run": len(php_paths),
            "launches_n": launches_n,
            "take_n": take_n,
            "entered_n": entered_n,
            "take_to_entered_rate": (
                round(entered_n / take_n, 4) if take_n else None
            ),
        })
    except Exception as exc:
        php["error"] = str(exc)

    return {
        "generated_at": datetime.now(ET).isoformat(),
        "window_trading_days": [d.isoformat() for d in window_days],
        "window_start": since.isoformat(),
        "scans_total": len(scans),
        "scans_live": live_scans,
        "scans_dry_run": dry_scans,
        "scans_note": (
            "Legacy TSD profiler scans (scan_*.json). "
            "Live Peak Hour activity is under php_peak_hour below — "
            "scans_total=0 is expected when only PHP is live."
        ),
        "signals_total": signals,
        "entries_total": entries,
        "php_peak_hour": php,
        "trail_passes": len(trails),
        "trail_actions": trail_actions,
        "trail_exits": exits,
        "book_open_symbols": open_pos,
        "book_open_count": len(open_pos),
        "book_closed_leg_exits": closed_legs,
        "pool": pool,
        "last_scan_at": book.get("last_scan_at"),
        "last_trail_monitor_at": book.get("last_trail_monitor_at"),
    }


def format_scorecard_md(card: dict[str, Any]) -> str:
    """Render scorecard as markdown."""
    php = card.get("php_peak_hour") or {}
    php_rate = php.get("take_to_entered_rate")
    php_rate_s = f"{100 * float(php_rate):.1f}%" if php_rate is not None else "n/a"
    lines = [
        "# TSD Pipeline Scorecard",
        "",
        f"**Generated:** {card.get('generated_at')}",
        f"**Window:** {card.get('window_start')} to today ({len(card.get('window_trading_days') or [])} trading days)",
        "",
        "## Peak Hour (live PHP)",
        f"- PHP scans: **{php.get('scans_run', 0)}**",
        f"- 1H launches: **{php.get('launches_n', 0)}**",
        f"- Take / entered: **{php.get('take_n', 0)}** / **{php.get('entered_n', 0)}** "
        f"(take->entered {php_rate_s})",
        f"- _Note: {php.get('note', '')}_",
        "",
        "## Legacy TSD scan activity",
        f"- Total scans: **{card.get('scans_total')}** (live={card.get('scans_live')}, dry={card.get('scans_dry_run')})",
        f"- _Note: {card.get('scans_note')}_",
        f"- Signals detected: **{card.get('signals_total')}**",
        f"- Live entries attempted: **{card.get('entries_total')}**",
        "",
        "## Trail monitor",
        f"- Monitor passes: **{card.get('trail_passes')}**",
        f"- Trail actions: **{card.get('trail_actions')}**",
        f"- Exits (trail/kill/time_cap): **{card.get('trail_exits')}**",
        "",
        "## Book & pool",
        f"- Open positions: **{card.get('book_open_count')}** {card.get('book_open_symbols')}",
        f"- Closed leg exits (book): **{card.get('book_closed_leg_exits')}**",
        f"- Pool: **${card.get('pool', {}).get('pool', 0):,.2f}** deployed=${card.get('pool', {}).get('deployed', 0):,.2f}",
        f"- Last scan: {card.get('last_scan_at')}",
        f"- Last trail monitor: {card.get('last_trail_monitor_at')}",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="TSD weekly 5-day scorecard")
    parser.add_argument("--days", type=int, default=5, help="Trading days to include")
    parser.add_argument("--write", action="store_true", help="Write results/scorecard_*.md")
    args = parser.parse_args()

    card = build_scorecard(days=args.days)
    md = format_scorecard_md(card)
    print(md)

    if args.write:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(ET).strftime("%Y%m%d")
        path = RESULTS_DIR / f"scorecard_{stamp}.md"
        path.write_text(md, encoding="utf-8")
        json_path = RESULTS_DIR / f"scorecard_{stamp}.json"
        json_path.write_text(json.dumps(card, indent=2, default=str), encoding="utf-8")
        print(f"Wrote {path}")
        print(f"Wrote {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
