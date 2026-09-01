"""
Q-ALPHA UTS v2 — shared entry gates (five-gate system, Phase 1).

No order unless all gates pass. Used by watch queue admission and
future setup_watch_agent entry executor.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytz

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

from state_paths import is_trading_day, state_path
from tsd_scan_pipeline.tsd_capacity import load_state, open_symbols
from tsd_scan_pipeline.tsd_entry import classify_session

ET = pytz.timezone("America/New_York")

# Phase 1 entry gate thresholds
SCAN_SCORE_ENTRY_MIN = 70.0
WT_GAP_MIN = 3.0
ENTRY_WINDOW_START = time(9, 35)
ENTRY_WINDOW_END = time(14, 0)


def is_entry_window(now: datetime | None = None) -> bool:
    """True when ET is weekday RTH between 09:35 and 14:00 inclusive."""
    dt = _as_et(now)
    if dt.weekday() >= 5 or not is_trading_day(dt.date()):
        return False
    if classify_session(dt) != "RTH":
        return False
    t = dt.time()
    return ENTRY_WINDOW_START <= t <= ENTRY_WINDOW_END


def _as_et(now: datetime | None) -> datetime:
    dt = now or datetime.now(ET)
    if dt.tzinfo is None:
        return ET.localize(dt)
    return dt.astimezone(ET)


def _prior_trading_day(d: date) -> date:
    """Most recent trading day strictly before *d*."""
    cur = d - timedelta(days=1)
    for _ in range(10):
        if is_trading_day(cur):
            return cur
        cur -= timedelta(days=1)
    return cur


def leg_eligible_for_day2_tighten(
    leg: dict[str, Any],
    trail_doc: dict[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    """
    Day-2 structure tighten only after at least one full trading day since entry.

    Skips legs opened on the prior trading day (first RTH session after fill).
    """
    day = int(trail_doc.get("trading_day") or 1)
    if day < 2:
        return False

    opened_raw = leg.get("time") or trail_doc.get("opened_at")
    if not opened_raw:
        return False

    opened = datetime.fromisoformat(str(opened_raw))
    if opened.tzinfo is None:
        opened = ET.localize(opened)
    else:
        opened = opened.astimezone(ET)

    now_dt = _as_et(now)
    # Too fresh if opened on or after the previous trading day
    if opened.date() >= _prior_trading_day(now_dt.date()):
        return False
    return True


def gap_open_symbols() -> set[str]:
    """Open symbols in gap-agent paper_trades.json book."""
    try:
        from paper_trader import OPEN_STATUSES, PaperTradesStore

        store = PaperTradesStore()
        data = store.load()
        return {
            str(t.get("ticker", "")).upper()
            for t in data.get("trades") or []
            if str(t.get("status", "")).upper() in {s.upper() for s in OPEN_STATUSES}
        }
    except Exception:
        path = state_path("paper_trades.json")
        if not path.exists():
            return set()
        data = json.loads(path.read_text(encoding="utf-8"))
        open_statuses = {"OPEN", "T1_HIT", "T2_HIT", "T3_TRAIL", "PENDING_MOC"}
        return {
            str(t.get("ticker", "")).upper()
            for t in data.get("trades") or []
            if str(t.get("status", "")).upper() in open_statuses
        }


def occupied_symbols() -> set[str]:
    """Cross-book dedup: TSD open legs ∪ gap paper open trades."""
    tsd = set(open_symbols(load_state()))
    return tsd | gap_open_symbols()


def fetch_regime_bull(*, polygon_key: str | None = None) -> tuple[bool, str, dict[str, Any]]:
    """
    SPY >= SMA50 regime gate via Polygon.

    Returns (is_bull, regime_label, details).
    """
    key = polygon_key or os.environ.get("POLYGON_API_KEY", "")
    if not key:
        return False, "NO_KEY", {}
    try:
        from pre_market_scanner import fetch_spy_regime

        reg = fetch_spy_regime(key)
        bull = reg.get("spy_regime") == "BULL"
        return bull, str(reg.get("spy_regime", "?")), reg
    except Exception as exc:
        return False, f"ERR:{exc}", {}


def evaluate_entry_gates(
    candidate: dict[str, Any],
    *,
    regime_bull: bool | None = None,
    require_rth_window: bool = False,
    now: datetime | None = None,
) -> tuple[bool, dict[str, bool], list[str]]:
    """
    Evaluate Phase 1 entry gates for a scan candidate.

    Returns (all_passed, gate_map, reject_reasons).
    """
    sym = str(candidate.get("symbol", "")).upper()
    gates: dict[str, bool] = {
        "scan_score": float(candidate.get("scan_score") or 0) >= SCAN_SCORE_ENTRY_MIN,
        "wt_gap": float(candidate.get("wt_gap") or 0) >= WT_GAP_MIN,
        "regime": regime_bull is not False,
        "dedup": sym not in occupied_symbols() if sym else False,
        "rth_window": is_entry_window(now),
    }
    if regime_bull is None:
        bull, _, _ = fetch_regime_bull()
        gates["regime"] = bull

    reasons: list[str] = []
    if not gates["scan_score"]:
        reasons.append(f"scan_score<{SCAN_SCORE_ENTRY_MIN:.0f}")
    if not gates["wt_gap"]:
        reasons.append(f"wt_gap<{WT_GAP_MIN:.0f}")
    if not gates["regime"]:
        reasons.append("regime_bear")
    if not gates["dedup"]:
        reasons.append("cross_book_occupied")
    if require_rth_window and not gates["rth_window"]:
        reasons.append("outside_entry_window")

    core = ("scan_score", "wt_gap", "regime", "dedup")
    passed = all(gates[k] for k in core)
    if require_rth_window:
        passed = passed and gates["rth_window"]
    return passed, gates, reasons


def infer_signal_lane(candidate: dict[str, Any]) -> str:
    """
    Lane A: fresh WT cross (tight wt_gap). Lane B: hold-above-cross swing.
    """
    if candidate.get("buy_signal") and float(candidate.get("wt_gap") or 99) < 15.0:
        return "A"
    return "B"
