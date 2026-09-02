"""
Q-ALPHA UTS v2 Phase 2.5 — strict HTF entry gates.

Lane B (LAUNCH): buy_signal + is_launch_candidate + red signal bar + HTF daily gates.
SPY regime is dashboard context only — never vetoes entry.
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
from tsd_scan_pipeline.tsd_htf_gates import evaluate_htf_daily_gates
from tsd_scan_pipeline.tsd_launch_score import (
    enrich_launch_fields,
    is_launch_candidate,
)

ET = pytz.timezone("America/New_York")

ENTRY_WINDOW_START = time(9, 35)
ENTRY_CUTOFF = time(15, 0)  # no new entries at or after 15:00 ET
WATCH_TIMEOUT = time(11, 0)


def is_entry_window(now: datetime | None = None) -> bool:
    """True when ET is weekday RTH between 09:35 and before 15:00 ET."""
    dt = _as_et(now)
    if dt.weekday() >= 5 or not is_trading_day(dt.date()):
        return False
    if classify_session(dt) != "RTH":
        return False
    t = dt.time()
    return ENTRY_WINDOW_START <= t < ENTRY_CUTOFF


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
    """DISABLED — Phase 2.5 removed day-2 structure tighten."""
    return False


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
    """SPY >= SMA50 — dashboard context only; does not gate entries."""
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


def infer_signal_lane(candidate: dict[str, Any]) -> str:
    """Phase 2.5: LAUNCH lane B only for new entries."""
    return "B"


def evaluate_launch_gates(
    candidate: dict[str, Any],
    *,
    regime_bull: bool | None = None,
    require_rth_window: bool = False,
    now: datetime | None = None,
    polygon_key: str | None = None,
) -> tuple[bool, dict[str, bool], list[str]]:
    """Strict HTF LAUNCH gates — no scan_score floor, no SPY veto."""
    row = enrich_launch_fields(candidate)
    sym = str(row.get("symbol", "")).upper()
    phase = row.get("phase", "NEUTRAL")

    htf_pass, htf_gates, htf_reasons, htf_score = evaluate_htf_daily_gates(
        row, polygon_key=polygon_key,
    )
    row["htf_score"] = htf_score

    gates: dict[str, bool] = {
        "buy_signal": bool(row.get("buy_signal")),
        "launch_candidate": is_launch_candidate(row),
        "signal_bar_red": bool(row.get("signal_bar_red")),
        "not_extension": phase != "EXTENSION",
        "htf_daily": htf_pass,
        "dedup": sym not in occupied_symbols() if sym else False,
        "entry_cutoff": _as_et(now).time() < ENTRY_CUTOFF,
        "rth_window": is_entry_window(now),
        **{f"htf_{k}": v for k, v in htf_gates.items()},
    }
    _, regime_label, _ = (
        fetch_regime_bull(polygon_key=polygon_key) if regime_bull is None else (regime_bull, "CTX", {})
    )
    gates["regime_context"] = True  # informational only; label in {regime_label}

    reasons: list[str] = []
    if phase == "EXTENSION":
        reasons.append("extension_phase")
    if not gates["buy_signal"]:
        reasons.append("no_buy_signal")
    if not gates["launch_candidate"]:
        reasons.append("not_launch_candidate")
    if not gates["signal_bar_red"]:
        reasons.append("signal_bar_not_red")
    reasons.extend(htf_reasons)
    if not gates["dedup"]:
        reasons.append("cross_book_occupied")
    if not gates["entry_cutoff"]:
        reasons.append("after_15:00_cutoff")
    if require_rth_window and not gates["rth_window"]:
        reasons.append("outside_entry_window")

    core = (
        "buy_signal",
        "launch_candidate",
        "signal_bar_red",
        "not_extension",
        "htf_daily",
        "dedup",
        "entry_cutoff",
    )
    passed = all(gates[k] for k in core)
    if require_rth_window:
        passed = passed and gates["rth_window"]
    return passed, gates, reasons


def evaluate_lane_a_gates(
    candidate: dict[str, Any],
    *,
    regime_bull: bool | None = None,
    require_rth_window: bool = False,
    now: datetime | None = None,
    polygon_key: str | None = None,
) -> tuple[bool, dict[str, bool], list[str]]:
    """Lane A disabled in Phase 2.5 — redirect to LAUNCH gates."""
    return evaluate_launch_gates(
        candidate,
        regime_bull=regime_bull,
        require_rth_window=require_rth_window,
        now=now,
        polygon_key=polygon_key,
    )


def evaluate_entry_gates(
    candidate: dict[str, Any],
    *,
    regime_bull: bool | None = None,
    require_rth_window: bool = False,
    now: datetime | None = None,
    polygon_key: str | None = None,
) -> tuple[bool, dict[str, bool], list[str]]:
    """Dispatch to strict HTF LAUNCH gates."""
    return evaluate_launch_gates(
        candidate,
        regime_bull=regime_bull,
        require_rth_window=require_rth_window,
        now=now,
        polygon_key=polygon_key,
    )


def is_watch_timeout(now: datetime | None = None) -> bool:
    """True when ET is at or past the 11:00 confirmation deadline."""
    dt = _as_et(now)
    if dt.weekday() >= 5 or not is_trading_day(dt.date()):
        return False
    return dt.time() >= WATCH_TIMEOUT
