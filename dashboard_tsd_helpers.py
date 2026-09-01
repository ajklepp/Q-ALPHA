"""
Pure helpers for UTS v2 TSD dashboard rendering (unit-testable).
"""
from __future__ import annotations

import json
import math
from typing import Any


def _sf(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def parse_tranche_json(raw: Any) -> list[dict[str, Any]]:
    """Parse tranche_json from Supabase row (dict or JSON string)."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [t for t in raw if isinstance(t, dict)]
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []
    return []


def trail_stop_price(tranche: dict[str, Any]) -> float | None:
    """Active trail stop for a trailing tranche."""
    if not tranche.get("trailing") or tranche.get("closed"):
        return None
    run_high = _sf(tranche.get("run_high"), 0.0)
    trail_pct = _sf(tranche.get("trail_pct"), 0.0)
    if run_high <= 0 or trail_pct <= 0:
        return None
    return round(run_high * (1.0 - trail_pct), 4)


def next_trail_stop(tranches: list[dict[str, Any]]) -> float | None:
    """Nearest (highest) armed trail stop among open trailing tranches."""
    stops = [s for t in tranches if (s := trail_stop_price(t)) is not None]
    return max(stops) if stops else None


def mfe_in_r(
    entry_price: float,
    peak_high: float,
    kill_price: float,
) -> float | None:
    """MFE expressed in initial risk (R) units."""
    if entry_price <= 0 or peak_high <= entry_price:
        return None
    risk = entry_price - kill_price
    if risk <= 0:
        return None
    return round((peak_high - entry_price) / risk, 2)


def progress_milestones(row: dict[str, Any]) -> list[tuple[str, float]]:
    """Ordered milestones for 3-layer progress bar."""
    entry = _sf(row.get("entry_price"), 0.0)
    if entry <= 0:
        return []
    out: list[tuple[str, float]] = [("Entry", entry)]
    structure = _sf(row.get("structure_stop"), float("nan"))
    if math.isfinite(structure) and structure > 0:
        out.append(("Structure", structure))
    tranches = parse_tranche_json(row.get("tranche_json"))
    for tid in ("T1", "T2"):
        for t in tranches:
            if str(t.get("id")) == tid and not t.get("closed"):
                trig = _sf(t.get("trigger_price"), 0.0)
                if trig > 0:
                    out.append((tid, trig))
                break
    return out


def progress_fraction(current_price: float, milestones: list[tuple[str, float]]) -> float:
    """0–1 progress along milestone ladder."""
    if not milestones or current_price <= 0:
        return 0.0
    prices = [m[1] for m in milestones]
    lo, hi = min(prices), max(prices)
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (current_price - lo) / (hi - lo)))


def map_exit_layer(reason: str | None) -> str:
    """Map raw exit_reason to Kill / Structure / Trail layer."""
    if not reason:
        return "—"
    r = str(reason).lower()
    if any(k in r for k in ("kill", "broker")):
        return "Kill"
    if any(
        k in r
        for k in (
            "structure",
            "orb",
            "day3",
            "base_break",
            "breakeven",
            "thesis",
        )
    ):
        return "Structure"
    if r.startswith("t") or "trail" in r:
        return "Trail"
    return str(reason)


def format_gate_summary(gates: Any) -> str:
    """Compact gate summary for watch queue table."""
    if not gates:
        return "—"
    if isinstance(gates, str):
        return gates[:40]
    if not isinstance(gates, dict):
        return "—"
    failed = [k for k, v in gates.items() if v is False]
    if failed:
        return "FAIL:" + ",".join(failed[:3])
    return "OK"
