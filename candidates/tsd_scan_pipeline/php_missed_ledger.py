"""
Missed-move ledger — launches we saw but did not take, plus how far they ran.

Public dashboard shows Symbol / when / ran-up only (no scores, hours, reject reasons).
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytz
import requests

PIPELINE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = PIPELINE_DIR / "results" / "peak_hour_scans"
LEDGER_PATH = RESULTS_DIR / "missed_ledger.json"
ET = pytz.timezone("America/New_York")
POLYGON_BASE = "https://api.polygon.io"
RATE_SLEEP = 0.12


def _ensure_dir() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _load_ledger() -> dict[str, Any]:
    _ensure_dir()
    if not LEDGER_PATH.exists():
        return {"version": 1, "rows": []}
    try:
        doc = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        if not isinstance(doc.get("rows"), list):
            doc["rows"] = []
        return doc
    except Exception:
        return {"version": 1, "rows": []}


def _save_ledger(doc: dict[str, Any]) -> Path:
    _ensure_dir()
    doc["updated_at"] = datetime.now(ET).isoformat()
    LEDGER_PATH.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
    return LEDGER_PATH


def _row_key(symbol: str, signal_at: str) -> str:
    day = str(signal_at)[:10]
    return f"{symbol.upper()}|{day}"


def record_scan_outcomes(
    *,
    now_et: datetime,
    ranked: list[dict[str, Any]],
    taken_symbols: set[str] | list[str],
) -> int:
    """
    Upsert one ledger row per ranked launch not taken (missed) or taken.

    Returns number of rows written/updated.
    """
    taken = {str(s).upper() for s in taken_symbols}
    when = now_et.astimezone(ET) if now_et.tzinfo else ET.localize(now_et)
    signal_at = when.isoformat()
    doc = _load_ledger()
    by_key = {_row_key(r["symbol"], r["signal_at"]): r for r in doc["rows"] if r.get("symbol")}
    n = 0
    for r in ranked:
        sym = str(r.get("symbol") or "").upper()
        if not sym:
            continue
        ref = r.get("htf_1h_close") or r.get("close") or r.get("entry_price")
        try:
            ref_f = float(ref) if ref is not None else None
        except (TypeError, ValueError):
            ref_f = None
        if ref_f is None or ref_f <= 0:
            continue
        outcome = "TAKEN" if sym in taken else "MISSED"
        key = _row_key(sym, signal_at)
        prev = by_key.get(key)
        if prev and prev.get("outcome") == "TAKEN":
            # Never demote a fill to missed same day
            continue
        row = {
            "symbol": sym,
            "signal_at": signal_at,
            "signal_day": when.strftime("%Y-%m-%d"),
            "ref_price": round(ref_f, 4),
            "peak_price": prev.get("peak_price") if prev else None,
            "ran_up_pct": prev.get("ran_up_pct") if prev else None,
            "outcome": outcome,
            "marked_at": prev.get("marked_at") if prev else None,
        }
        by_key[key] = row
        n += 1
    doc["rows"] = sorted(
        by_key.values(),
        key=lambda x: (str(x.get("signal_at") or ""), str(x.get("symbol") or "")),
        reverse=True,
    )
    _save_ledger(doc)
    return n


def _polygon_key() -> str:
    key = os.environ.get("POLYGON_API_KEY", "")
    if key:
        return key
    env_path = PIPELINE_DIR.parents[1] / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("POLYGON_API_KEY") and "=" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _peak_since(symbol: str, start_day: str, key: str) -> float | None:
    """Max high from daily aggs since start_day (inclusive) through today."""
    end = datetime.now(ET).strftime("%Y-%m-%d")
    url = (
        f"{POLYGON_BASE}/v2/aggs/ticker/{symbol}/range/1/day/"
        f"{start_day}/{end}"
    )
    try:
        resp = requests.get(url, params={"adjusted": "true", "sort": "asc", "apiKey": key}, timeout=20)
        if resp.status_code != 200:
            return None
        results = (resp.json() or {}).get("results") or []
        highs = [float(b["h"]) for b in results if b.get("h") is not None]
        return max(highs) if highs else None
    except Exception:
        return None


def mark_ran_up(*, days: int = 14, only_missed: bool = True) -> int:
    """Refresh peak / ran_up_pct for recent ledger rows. Returns rows updated."""
    key = _polygon_key()
    if not key:
        return 0
    doc = _load_ledger()
    cutoff = (datetime.now(ET) - timedelta(days=days)).strftime("%Y-%m-%d")
    updated = 0
    for row in doc["rows"]:
        if only_missed and str(row.get("outcome") or "").upper() != "MISSED":
            continue
        day = str(row.get("signal_day") or row.get("signal_at") or "")[:10]
        if not day or day < cutoff:
            continue
        sym = str(row.get("symbol") or "").upper()
        ref = float(row.get("ref_price") or 0)
        if not sym or ref <= 0:
            continue
        peak = _peak_since(sym, day, key)
        time.sleep(RATE_SLEEP)
        if peak is None or peak <= 0:
            continue
        row["peak_price"] = round(peak, 4)
        row["ran_up_pct"] = round((peak / ref - 1.0) * 100.0, 2)
        row["marked_at"] = datetime.now(ET).isoformat()
        updated += 1
    _save_ledger(doc)
    return updated


def rows_since(days: int = 7, *, outcome: str | None = "MISSED") -> list[dict[str, Any]]:
    """Ledger rows in window, newest first. outcome=None returns all."""
    doc = _load_ledger()
    cutoff = (datetime.now(ET) - timedelta(days=days)).strftime("%Y-%m-%d")
    out: list[dict[str, Any]] = []
    for row in doc["rows"]:
        day = str(row.get("signal_day") or row.get("signal_at") or "")[:10]
        if not day or day < cutoff:
            continue
        if outcome and str(row.get("outcome") or "").upper() != outcome.upper():
            continue
        out.append(row)
    out.sort(key=lambda r: float(r.get("ran_up_pct") or -999), reverse=True)
    return out


def ledger_path() -> Path:
    return LEDGER_PATH
