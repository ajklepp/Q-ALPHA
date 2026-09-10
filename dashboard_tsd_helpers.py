"""
Pure helpers for UTS v2 TSD dashboard rendering (unit-testable).
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any

import pytz

ET = pytz.timezone("America/New_York")


def _sf(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def mark_age_minutes(
    value: Any,
    *,
    now: datetime | None = None,
) -> float | None:
    """Return non-negative age of a cloud mark timestamp in minutes."""
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = ET.localize(stamp)
        current = now or datetime.now(ET)
        if current.tzinfo is None:
            current = ET.localize(current)
        return max(0.0, (current.astimezone(ET) - stamp.astimezone(ET)).total_seconds() / 60)
    except (TypeError, ValueError):
        return None


def pct_from_entry(price: float, entry: float) -> float | None:
    """Fractional change from entry (0.019 = +1.9%)."""
    if entry <= 0 or price <= 0:
        return None
    return (price - entry) / entry


def fmt_pct_signed(pct: float | None, *, decimals: int = 1) -> str:
    """Format fractional pct as signed string (+1.9%)."""
    if pct is None or not math.isfinite(pct):
        return "—"
    return f"{pct * 100:+.{decimals}f}%"


def format_level(price: float, entry: float) -> str:
    """Primary stop level: $price (±X.X% entry)."""
    if price <= 0:
        return "—"
    pct = pct_from_entry(price, entry)
    if pct is None:
        return f"${price:.2f}"
    return f"${price:.2f} ({fmt_pct_signed(pct)} entry)"


def distance_from_current(level: float, current: float, *, label: str = "") -> str:
    """Metric delta: distance from current price to level."""
    if level <= 0 or current <= 0:
        return "—"
    gap = (current - level) / current
    if abs(gap) < 0.0005:
        return f"at {label}".strip() or "at level"
    if level < current:
        return f"{abs(gap):.1%} above {label}".strip() or f"{abs(gap):.1%} above"
    return f"{abs(gap):.1%} below {label}".strip() or f"{abs(gap):.1%} below"


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
    if tranche.get("closed"):
        return None
    armed = tranche.get("armed") or tranche.get("trailing")
    if not armed:
        return None
    stop = tranche.get("trail_stop")
    if stop is not None:
        return _sf(stop, 0.0) or None
    run_high = _sf(tranche.get("run_high"), 0.0)
    trail_pct = _sf(tranche.get("trail_pct"), 0.0)
    if run_high <= 0 or trail_pct <= 0:
        return None
    return round(run_high * (1.0 - trail_pct), 4)


def next_trail_stop(tranches: list[dict[str, Any]]) -> float | None:
    """Nearest (highest) armed trail stop among open trailing tranches."""
    stops = [s for t in tranches if (s := trail_stop_price(t)) is not None]
    return max(stops) if stops else None


def format_trail_stop_cell(tranche: dict[str, Any]) -> str:
    """Trail stop column: $7.02 (−5.4% off high $7.31, trail 5.4%)."""
    stop = trail_stop_price(tranche)
    if stop is None:
        return "—"
    run_high = _sf(tranche.get("run_high"), 0.0)
    trail_pct = _sf(tranche.get("trail_pct"), 0.0)
    if run_high > 0:
        off_high = (stop - run_high) / run_high
        trail_note = f", trail {trail_pct * 100:.1f}%" if trail_pct > 0 else ""
        return f"${stop:.2f} ({fmt_pct_signed(off_high)} off high ${run_high:.2f}{trail_note})"
    return f"${stop:.2f}"


def format_to_trigger_cell(
    trigger: float,
    current: float,
    *,
    armed: bool,
    closed: bool,
) -> str:
    """To-trigger column when tranche not yet armed."""
    if closed:
        return "closed"
    if armed:
        return "armed"
    if trigger <= 0 or current <= 0:
        return "—"
    move = (trigger - current) / current
    return f"{fmt_pct_signed(move)} from current"


def build_tranche_table_rows(
    tranches: list[dict[str, Any]],
    *,
    entry: float,
    current: float,
) -> list[dict[str, str]]:
    """T1–T4 table rows with price + % formatting."""
    rows: list[dict[str, str]] = []
    for t in tranches:
        trig = _sf(t.get("trigger_price"), 0.0)
        armed = bool(t.get("armed"))
        closed = bool(t.get("closed"))
        rows.append({
            "ID": str(t.get("id") or "?"),
            "Sh": str(t.get("shares") or "—"),
            "Trigger": format_level(trig, entry) if trig > 0 else "—",
            "Trail stop": format_trail_stop_cell(t),
            "To trigger": format_to_trigger_cell(
                trig, current, armed=armed, closed=closed,
            ),
        })
    return rows


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
    """
    Price-ordered milestones for progress bar fraction.

    Includes Kill, Structure, Entry, T1, T2 when available.
    """
    entry = _sf(row.get("entry_price"), 0.0)
    if entry <= 0:
        return []

    named: list[tuple[str, float]] = []
    kill = _sf(row.get("kill_price"), 0.0)
    kill_pct = _sf(row.get("kill_pct"), 0.0)
    if kill > 0:
        named.append(("Kill", kill))
    if entry > 0 and kill_pct > 0 and not row.get("one_r_locked"):
        one_r = round(entry * (1.0 + kill_pct), 4)
        named.append(("+1R", one_r))
    structure = _sf(row.get("structure_stop"), float("nan"))
    if math.isfinite(structure) and structure > 0:
        named.append(("Structure", structure))
    named.append(("Entry", entry))

    tranches = parse_tranche_json(row.get("tranche_json"))
    for tid in ("T1", "T2", "T3", "T4"):
        for t in tranches:
            if str(t.get("id")) == tid and not t.get("closed"):
                trig = _sf(t.get("trigger_price"), 0.0)
                if trig > 0:
                    named.append((tid, trig))
                break

    # Sort by price for bar scale; dedupe labels keeping first
    seen: set[str] = set()
    ordered: list[tuple[str, float]] = []
    for label, price in sorted(named, key=lambda x: x[1]):
        if label in seen:
            continue
        seen.add(label)
        ordered.append((label, price))
    return ordered


def progress_tick_labels(row: dict[str, Any]) -> str:
    """
    Tick labels: Kill −8.1% | Structure −1.0% | Entry 0% | T1 +1.9% | T2 +3.7%
    """
    entry = _sf(row.get("entry_price"), 0.0)
    if entry <= 0:
        return ""

    parts: list[str] = []
    kill = _sf(row.get("kill_price"), 0.0)
    if kill > 0:
        parts.append(f"Kill {fmt_pct_signed(pct_from_entry(kill, entry))}")

    structure = _sf(row.get("structure_stop"), float("nan"))
    if math.isfinite(structure) and structure > 0:
        parts.append(f"Structure {fmt_pct_signed(pct_from_entry(structure, entry))}")

    parts.append("Entry 0%")

    tranches = parse_tranche_json(row.get("tranche_json"))
    for tid in ("T1", "T2"):
        for t in tranches:
            if str(t.get("id")) == tid and not t.get("closed"):
                trig = _sf(t.get("trigger_price"), 0.0)
                if trig > 0:
                    parts.append(f"{tid} {fmt_pct_signed(pct_from_entry(trig, entry))}")
                break

    return " | ".join(parts)


def progress_fraction(current_price: float, milestones: list[tuple[str, float]]) -> float:
    """0–1 progress along milestone ladder."""
    if not milestones or current_price <= 0:
        return 0.0
    prices = [m[1] for m in milestones]
    lo, hi = min(prices), max(prices)
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (current_price - lo) / (hi - lo)))


def hold_time_display(opened_at: Any, closed_at: Any) -> str:
    """Human hold duration between leg open and close."""
    if not opened_at or not closed_at:
        return "—"
    try:
        o = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
        c = datetime.fromisoformat(str(closed_at).replace("Z", "+00:00"))
        if o.tzinfo is None:
            o = ET.localize(o)
        else:
            o = o.astimezone(ET)
        if c.tzinfo is None:
            c = ET.localize(c)
        else:
            c = c.astimezone(ET)
        delta = c - o
        if delta.total_seconds() < 0:
            return "—"
        days = delta.days
        hours = delta.seconds // 3600
        if days > 0:
            return f"{days}d {hours}h"
        mins = (delta.seconds % 3600) // 60
        if hours > 0:
            return f"{hours}h {mins}m"
        return f"{mins}m"
    except (ValueError, TypeError):
        return "—"


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
            "day5",
            "thesis_fail",
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


def remaining_open_shares(row: dict[str, Any]) -> int:
    """
    Shares still open on a cloud/local open leg.

    Prefer open tranche sizes; fall back to shares minus closed-tranche sizes,
    then to the raw shares field.
    """
    tranches = parse_tranche_json(row.get("tranche_json"))
    if tranches:
        open_sh = sum(
            int(_sf(t.get("shares"), 0.0))
            for t in tranches
            if not bool(t.get("closed"))
        )
        if open_sh > 0:
            return open_sh
        closed_sh = sum(
            int(_sf(t.get("shares"), 0.0))
            for t in tranches
            if bool(t.get("closed"))
        )
        raw = int(_sf(row.get("shares"), 0.0))
        if raw > 0 and closed_sh > 0:
            return max(0, raw - closed_sh)
    return max(0, int(_sf(row.get("shares"), 0.0)))


def partial_realized_from_open_row(row: dict[str, Any]) -> float:
    """
    Realized P&L from closed tranches still attached to an OPEN leg.

    Trail scale-outs (T1/T2) must count in scoreboard realized even before
    the whole symbol is flat.
    """
    entry = _sf(row.get("entry_price"), 0.0)
    if entry <= 0:
        return 0.0
    if row.get("partial_realized") is not None:
        return round(_sf(row.get("partial_realized"), 0.0), 2)
    pnl = 0.0
    for t in parse_tranche_json(row.get("tranche_json")):
        if not bool(t.get("closed")):
            continue
        sh = int(_sf(t.get("shares"), 0.0))
        px = _sf(t.get("exit_price"), 0.0)
        if sh > 0 and px > 0:
            pnl += (px - entry) * sh
    return round(pnl, 2)


def unrealized_from_open_row(row: dict[str, Any]) -> float:
    """Mark-to-market P&L on remaining open shares only."""
    entry = _sf(row.get("entry_price"), 0.0)
    mark = _sf(row.get("current_price"), 0.0)
    shares = remaining_open_shares(row)
    if entry <= 0 or mark <= 0 or shares <= 0:
        return 0.0
    return round((mark - entry) * shares, 2)


def format_tranche_levels_caption(row: dict[str, Any]) -> str:
    """
    Compact T1–T4 line for open cards: T1=$20.36 · T2=HIT · T3=$21.35 · T4=$21.75.

    HIT when the tranche trigger has fired (closed or trailing/armed).
    Omits missing labels (e.g. 2-tranche ATRC shows T1/T2 only).
    """
    parts: list[str] = []
    for t in parse_tranche_json(row.get("tranche_json")):
        tid = str(t.get("id") or t.get("tranche_id") or "").upper()
        if tid not in ("T1", "T2", "T3", "T4"):
            continue
        closed = bool(t.get("closed"))
        armed = bool(t.get("armed") or t.get("trailing"))
        if closed or armed:
            parts.append(f"{tid}=HIT")
            continue
        trig = _sf(t.get("trigger_price"), 0.0)
        if trig > 0:
            parts.append(f"{tid}=${trig:.2f}")
        else:
            parts.append(f"{tid}=—")
    return " · ".join(parts)


def open_tranche_ids(row: dict[str, Any]) -> list[str]:
    """Open tranche labels on a cloud/local open row (e.g. T1, T2)."""
    ids: list[str] = []
    for t in parse_tranche_json(row.get("tranche_json")):
        if bool(t.get("closed")):
            continue
        tid = str(t.get("id") or t.get("tranche_id") or "").upper()
        if tid:
            ids.append(tid)
    return ids


def is_t34_trailing_position(row: dict[str, Any]) -> bool:
    """
    True when only runner inventory remains (T3 and/or T4).

    Full open = T1 and/or T2 still intact (consumes a slot).
    Trailing = T1 and T2 gone; residual T3/T4 free the slot.
    """
    if bool(row.get("t4_only")) and remaining_open_shares(row) > 0:
        return True
    ids = open_tranche_ids(row)
    if not ids:
        return False
    return all(tid in ("T3", "T4") for tid in ids)


def is_full_slot_position(row: dict[str, Any]) -> bool:
    """True when an open name still consumes a full slot (T1/T2 intact)."""
    if remaining_open_shares(row) <= 0:
        return False
    return not is_t34_trailing_position(row)


def scoreboard_pnl(
    open_rows: list[dict[str, Any]],
    closed_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Consistent Peak Hour scoreboard P&L parts.

    Headline P&L = realized (closed + open partials) + unrealized (remaining).
    Open = full slots (T1/T2 intact). Trailing = T3/T4-only runners.
    """
    closed_realized = sum(_sf(r.get("pnl_dollars"), 0.0) for r in closed_rows)
    partial_realized = sum(partial_realized_from_open_row(r) for r in open_rows)
    realized = round(closed_realized + partial_realized, 2)
    unrealized = round(sum(unrealized_from_open_row(r) for r in open_rows), 2)
    open_mtm = round(
        sum(
            _sf(r.get("current_price"), 0.0) * remaining_open_shares(r)
            for r in open_rows
        ),
        2,
    )
    winners = sum(1 for r in closed_rows if _sf(r.get("pnl_dollars"), 0.0) > 0.005)
    losers = sum(1 for r in closed_rows if _sf(r.get("pnl_dollars"), 0.0) < -0.005)
    flats = len(closed_rows) - winners - losers
    decisive = winners + losers
    trailing = sum(1 for r in open_rows if is_t34_trailing_position(r))
    full_slots = sum(1 for r in open_rows if is_full_slot_position(r))
    return {
        "realized": realized,
        "closed_realized": round(closed_realized, 2),
        "partial_realized": round(partial_realized, 2),
        "unrealized": unrealized,
        "total_pnl": round(realized + unrealized, 2),
        "open_mtm": open_mtm,
        "winners": winners,
        "losers": losers,
        "flats": flats,
        "decisive": decisive,
        "win_rate": (winners / decisive) if decisive else None,
        "open_names": len({str(r.get("symbol") or "").upper() for r in open_rows if r.get("symbol")}),
        "full_slots": full_slots,
        "trailing_positions": trailing,
    }
