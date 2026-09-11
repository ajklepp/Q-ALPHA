"""
Peak Hour Performers — scan funnel persistence (research, not Live Status).

Writes per-scan JSON + daily NDJSON under results/peak_hour_scans/.
Dashboard may read latest summary caption only (no reject spam).
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytz

PIPELINE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = PIPELINE_DIR / "results" / "peak_hour_scans"
ET = pytz.timezone("America/New_York")

REJECT_SAMPLE_CAP = 5  # symbols per reject reason


def ensure_scan_dir() -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    return RESULTS_DIR


def _normalize_reject_reason(row: dict[str, Any]) -> str:
    reason = str(row.get("reject_reason") or "").strip()
    if not reason:
        if row.get("pass"):
            return ""
        return "unknown_reject"
    # Collapse hour_not_allowed:11 → hour_not_allowed
    if reason.startswith("hour_not_allowed"):
        return "hour_not_allowed"
    if reason in ("not_1h_launch", "no_1h_buy", "polygon_empty", "no_bars"):
        return reason if reason != "not_1h_launch" else "no_1h_buy"
    return reason


def build_reject_summary(
    rows: list[dict[str, Any]],
    *,
    sample_cap: int = REJECT_SAMPLE_CAP,
) -> tuple[dict[str, int], dict[str, list[dict[str, str]]]]:
    """Histogram + capped samples for non-pass rows."""
    counts: Counter[str] = Counter()
    samples: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        if row.get("pass"):
            continue
        reason = _normalize_reject_reason(row)
        counts[reason] += 1
        bucket = samples.setdefault(reason, [])
        if len(bucket) < sample_cap:
            bucket.append({
                "symbol": str(row.get("symbol") or "").upper(),
                "reason": str(row.get("reject_reason") or reason),
            })
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))), samples


def build_scan_funnel_doc(
    *,
    now_et: datetime,
    bar_source: str,
    hours: list[int] | tuple[int, ...],
    htf_pass_count: int,
    symbols_scanned: int,
    all_rows: list[dict[str, Any]],
    ranked: list[dict[str, Any]],
    take: list[dict[str, Any]],
    queue_results: list[dict[str, Any]] | None = None,
    entry_results: list[dict[str, Any]] | None = None,
    runtime_sec: float = 0.0,
    live: bool = False,
) -> dict[str, Any]:
    """Assemble durable research payload (no full reject symbol dump)."""
    reject_summary, reject_samples = build_reject_summary(all_rows)
    q_results = queue_results or []
    e_results = entry_results or []

    queue_admitted = [
        {
            "symbol": str(r.get("symbol") or "").upper(),
            "status": r.get("status"),
            "reason": r.get("reason"),
        }
        for r in q_results
        if str(r.get("status") or "").upper() in {"ADDED", "UPDATED", "WATCHING"}
    ]
    queue_skipped = [
        {
            "symbol": str(r.get("symbol") or "").upper(),
            "status": r.get("status"),
            "reason": r.get("reason"),
        }
        for r in q_results
        if str(r.get("status") or "").upper() not in {"ADDED", "UPDATED", "WATCHING"}
    ]
    entered = [
        {
            "symbol": str(r.get("symbol") or "").upper(),
            "status": r.get("status"),
            "shares": r.get("shares"),
            "fill_price": r.get("fill_price"),
            "kind": r.get("kind"),
        }
        for r in e_results
        if str(r.get("status") or "").upper() == "FILLED"
    ]

    launches = []
    for i, r in enumerate(ranked, 1):
        launches.append({
            "symbol": str(r.get("symbol") or "").upper(),
            "hour": r.get("htf_1h_bar_hour"),
            "htf_score": r.get("htf_score") or r.get("htf_rank_score"),
            "launch_score": r.get("launch_score"),
            "rank": r.get("combined_rank_score"),
            "rank_order": i,
            "1h_close": r.get("htf_1h_close") or r.get("close"),
            "phase_3h": r.get("phase_3h") or r.get("phase"),
            "structure_mode": r.get("structure_mode"),
            "taken": any(
                str(t.get("symbol") or "").upper() == str(r.get("symbol") or "").upper()
                for t in take
            ),
            # Decision-time context (taken-vs-missed autopsy)
            "rs_spy_1h": r.get("rs_spy_1h"),
            "rs_spy_5d": r.get("rs_spy_5d"),
            "dollar_vol_1h": r.get("dollar_vol_1h"),
            "dollar_vol_1h_vs_20d": r.get("dollar_vol_1h_vs_20d"),
            "float_shares": r.get("float_shares"),
            "micro_dead_tape": r.get("micro_dead_tape"),
            "options_call_share": r.get("options_call_share"),
            "options_score_lite": r.get("options_score_lite"),
            "vol_ratio_20": r.get("vol_ratio_20"),
            "bar_state": r.get("bar_state"),
        })

    bar_hour = None
    for r in ranked[:1] or take[:1] or all_rows:
        if r.get("htf_1h_bar_hour") is not None:
            bar_hour = r.get("htf_1h_bar_hour")
            break
    if bar_hour is None:
        # Scan window hour (07:15 → bar hour 7)
        bar_hour = now_et.hour if now_et.minute < 30 else now_et.hour

    return {
        "strategy": "Peak Hour Performers",
        "version": "3.0",
        "et": now_et.isoformat(),
        "bar_hour": bar_hour,
        "hours": list(hours),
        "bar_source": bar_source,
        "live": bool(live),
        "htf_pass_count": int(htf_pass_count),
        "symbols_scanned": int(symbols_scanned),
        "launches_n": len(ranked),
        "take_n": len(take),
        "launches": launches,
        "queue_admitted": queue_admitted,
        "queue_skipped": queue_skipped,
        "entered": entered,
        "entered_n": len(entered),
        "take_to_entered_rate": (
            round(len(entered) / len(take), 4) if take else None
        ),
        "reject_summary": reject_summary,
        "reject_samples": reject_samples,
        "runtime_sec": round(float(runtime_sec), 1),
    }


def write_scan_funnel(doc: dict[str, Any], *, now_et: datetime | None = None) -> Path:
    """Write php_scan_YYYYMMDD_HHMM.json + append daily NDJSON line."""
    ensure_scan_dir()
    when = now_et or datetime.now(ET)
    if when.tzinfo is None:
        when = ET.localize(when)
    else:
        when = when.astimezone(ET)

    stamp = when.strftime("%Y%m%d_%H%M")
    path = RESULTS_DIR / f"php_scan_{stamp}.json"
    path.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")

    ndjson_path = RESULTS_DIR / f"php_funnel_{when.strftime('%Y%m%d')}.ndjson"
    line = {
        "et": doc.get("et"),
        "bar_hour": doc.get("bar_hour"),
        "htf": doc.get("htf_pass_count"),
        "launches_n": doc.get("launches_n"),
        "take_n": doc.get("take_n"),
        "entered_n": doc.get("entered_n"),
        "take_to_entered_rate": doc.get("take_to_entered_rate"),
        "reject_summary": doc.get("reject_summary") or {},
        "live": doc.get("live"),
        "scan_file": path.name,
    }
    with ndjson_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, default=str) + "\n")

    print(f"  Funnel -> {path}")
    print(f"  Funnel NDJSON append -> {ndjson_path.name}")
    return path


def latest_scan_funnel() -> dict[str, Any] | None:
    """Most recent php_scan_*.json for dashboard caption."""
    ensure_scan_dir()
    files = sorted(RESULTS_DIR.glob("php_scan_*.json"), reverse=True)
    for path in files[:5]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


def funnel_caption(doc: dict[str, Any] | None = None) -> str | None:
    """One-line Live Status caption: HTF · launches · take · entered (+ rate)."""
    doc = doc if doc is not None else latest_scan_funnel()
    if not doc:
        return None
    take_n = int(doc.get("take_n") or 0)
    entered_n = int(doc.get("entered_n") or 0)
    rate = doc.get("take_to_entered_rate")
    if rate is None and take_n:
        rate = entered_n / take_n
    rate_bit = f" ({100 * float(rate):.0f}%)" if rate is not None and take_n else ""
    return (
        f"HTF {int(doc.get('htf_pass_count') or 0)} · "
        f"launches {int(doc.get('launches_n') or 0)} · "
        f"take {take_n} · entered {entered_n}{rate_bit}"
    )


def list_scan_funnels_since(days: int = 7) -> list[Path]:
    """php_scan_*.json modified/named within the last N calendar days."""
    ensure_scan_dir()
    cutoff = datetime.now(ET) - timedelta(days=days)
    out: list[Path] = []
    for path in sorted(RESULTS_DIR.glob("php_scan_*.json")):
        try:
            # php_scan_YYYYMMDD_HHMM.json
            stamp = path.stem.replace("php_scan_", "")
            dt = ET.localize(datetime.strptime(stamp, "%Y%m%d_%H%M"))
            if dt >= cutoff:
                out.append(path)
        except ValueError:
            continue
    return out
