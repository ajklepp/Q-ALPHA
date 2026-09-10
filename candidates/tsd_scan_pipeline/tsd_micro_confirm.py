"""
Peak Hour micro-confirmation (post case-ENTER, pre-BUY).

1H LAUNCH nominates; case says ENTER; this module watches 1-min structure
for a short window and either CONFIRMs the buy or ABORTs if price is
immediately dumping through signal/structure.

Gates (Peak Hour — not gap Lane A):
  - min_wait 2 minutes after signal
  - ABORT if low pierces structure (or −1.5% from signal close)
  - CONFIRM if holding near signal close with constructive 1m close
  - TIMEOUT → SKIP (do not chase)
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

import pytz

from tsd_scan_pipeline.universe_tsd import POLYGON_BASE, load_polygon_key, polygon_get

ET = pytz.timezone("America/New_York")

MIN_WAIT_MIN = 2
MAX_WAIT_MIN = 10  # wall clock after queue; premarket may be sparse
DUMP_PCT = 0.015  # −1.5% from signal = abort
STRUCTURE_BUFFER = 0.002  # 0.2% under structure
HOLD_PCT = 0.003  # must be within −0.3% of signal to confirm
# Also inspect tape from 1H bar close → scan (:00→:15) before buying
PRE_SCAN_ABORT = True

MicroVerdict = Literal["PENDING", "CONFIRM", "ABORT", "TIMEOUT"]


@dataclass
class MicroBar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def fetch_1m_bars(
    symbol: str,
    *,
    day: str,
    api_key: str | None = None,
) -> list[MicroBar]:
    """Polygon 1-minute aggs for one calendar day (ET session)."""
    key = api_key or load_polygon_key()
    sym = symbol.upper()
    url = (
        f"{POLYGON_BASE}/v2/aggs/ticker/{sym}/range/1/minute/{day}/{day}"
    )
    out: list[MicroBar] = []
    params: dict[str, Any] = {"adjusted": "true", "sort": "asc", "limit": 50000}
    while url:
        data = polygon_get(url, params if "cursor" not in url else {}, key, timeout=30)
        time.sleep(0.12)
        for row in data.get("results") or []:
            ms = int(row.get("t") or 0)
            if ms <= 0:
                continue
            ts = datetime.fromtimestamp(ms / 1000.0, tz=ET)
            o = float(row.get("o") or 0)
            h = float(row.get("h") or 0)
            l = float(row.get("l") or 0)
            c = float(row.get("c") or 0)
            v = float(row.get("v") or 0)
            if c <= 0 or l <= 0:
                continue
            out.append(MicroBar(ts=ts, open=o, high=h, low=l, close=c, volume=v))
        nxt = data.get("next_url")
        url = nxt if nxt else ""
        params = {}
    return out


def bars_since(
    bars: list[MicroBar],
    *,
    after: datetime,
    until: datetime | None = None,
) -> list[MicroBar]:
    """Bars with ts > after and optionally <= until."""
    out: list[MicroBar] = []
    for b in bars:
        if b.ts <= after:
            continue
        if until is not None and b.ts > until:
            break
        out.append(b)
    return out


def evaluate_micro_confirm(
    *,
    signal_close: float,
    structure_level: float | None,
    bars_after: list[MicroBar],
    minutes_elapsed: float,
    min_wait: float = MIN_WAIT_MIN,
    max_wait: float = MAX_WAIT_MIN,
) -> tuple[MicroVerdict, str]:
    """
    Pure decision from post-signal 1-min bars.

    minutes_elapsed: wall time since signal/scan (not bar count).
    """
    if signal_close <= 0:
        return "ABORT", "invalid_signal_close"

    struct = float(structure_level) if structure_level and structure_level > 0 else None
    dump_floor = signal_close * (1.0 - DUMP_PCT)
    floors = [dump_floor]
    if struct is not None:
        floors.append(struct * (1.0 - STRUCTURE_BUFFER))
    # Higher floor = tighter long abort (near-turn structure wins when tighter than −1.5%)
    abort_px = max(floors)

    if not bars_after:
        if minutes_elapsed >= max_wait:
            return "TIMEOUT", "no_bars_timeout"
        return "PENDING", "waiting_bars"

    # Immediate dump through floor
    for b in bars_after:
        if b.low <= abort_px:
            return "ABORT", f"pierced_floor@{b.low:.4f}<= {abort_px:.4f}"

    if minutes_elapsed < min_wait:
        return "PENDING", f"min_wait_{minutes_elapsed:.1f}<{min_wait}"

    last = bars_after[-1]
    # Holding near signal
    hold_floor = signal_close * (1.0 - HOLD_PCT)
    if last.close < hold_floor:
        if minutes_elapsed >= max_wait:
            return "TIMEOUT", f"weak_hold_close={last.close:.4f}"
        return "PENDING", f"below_hold_close={last.close:.4f}"

    # Constructive tape: last bar green OR close >= prior close OR reclaim after dip
    constructive = last.close >= last.open
    if len(bars_after) >= 2:
        constructive = constructive or last.close >= bars_after[-2].close
    # Dip-then-reclaim: any bar low under signal then last close back above
    dipped = any(b.low < signal_close * 0.998 for b in bars_after[:-1])
    if dipped and last.close >= signal_close * 0.999:
        constructive = True

    if not constructive:
        if minutes_elapsed >= max_wait:
            return "TIMEOUT", "no_constructive_close"
        return "PENDING", "waiting_constructive"

    return "CONFIRM", f"hold_ok_close={last.close:.4f}"


def confirm_peak_hour_entry(
    row: dict[str, Any],
    *,
    now: datetime | None = None,
    bars: list[MicroBar] | None = None,
    api_key: str | None = None,
) -> tuple[MicroVerdict, str, dict[str, Any]]:
    """
    Evaluate one WATCHING Peak Hour row.

    Expects: symbol, signal_close/htf_1h_close, structure_level,
    htf_1h_bar_hour (close hour), queued_at/scan_at.
    """
    now_et = now or datetime.now(ET)
    if now_et.tzinfo is None:
        now_et = ET.localize(now_et)
    else:
        now_et = now_et.astimezone(ET)

    sym = str(row.get("symbol") or "").upper()
    signal = float(
        row.get("signal_close")
        or row.get("htf_1h_close")
        or row.get("close")
        or 0
    )
    structure = row.get("structure_level")
    try:
        structure_f = float(structure) if structure is not None else None
    except (TypeError, ValueError):
        structure_f = None

    queued_raw = row.get("queued_at") or row.get("scan_at") or row.get("added_at")
    try:
        queued = datetime.fromisoformat(str(queued_raw).replace("Z", "+00:00"))
        if queued.tzinfo is None:
            queued = ET.localize(queued)
        else:
            queued = queued.astimezone(ET)
    except Exception:
        queued = now_et

    # 1H bar close time (signal print) — tape from here already exists at :15 scan
    bar_hour = row.get("htf_1h_bar_hour")
    try:
        bh = int(bar_hour) if bar_hour is not None else queued.hour
    except (TypeError, ValueError):
        bh = queued.hour
    signal_end = queued.replace(hour=bh % 24, minute=0, second=0, microsecond=0)
    if signal_end > queued:
        signal_end -= timedelta(days=1)

    elapsed = (now_et - queued).total_seconds() / 60.0
    day = signal_end.strftime("%Y-%m-%d")

    if bars is None:
        try:
            all_bars = fetch_1m_bars(sym, day=day, api_key=api_key)
        except Exception as exc:
            if elapsed >= MAX_WAIT_MIN:
                return "TIMEOUT", f"bar_fetch_fail:{exc}", {}
            return "PENDING", f"bar_fetch_retry:{exc}", {}
    else:
        all_bars = bars

    # Include pre-scan tape (bar close → now) so dump between :00 and :15 aborts
    window = bars_since(all_bars, after=signal_end, until=now_et)
    # Elapsed for min_wait counts from signal bar end (not just queue)
    elapsed_from_signal = (now_et - signal_end).total_seconds() / 60.0

    verdict, reason = evaluate_micro_confirm(
        signal_close=signal,
        structure_level=structure_f,
        bars_after=window,
        minutes_elapsed=elapsed_from_signal,
        min_wait=MIN_WAIT_MIN + 0,  # already ~15m at scan; 2m after signal_end is trivial
        max_wait=15.0 + MAX_WAIT_MIN,  # :00→:15 plus post-queue watch
    )
    # At first scan moment, if already aborted on pre-scan tape — done
    # If PENDING only because waiting constructive but we have 15m tape, be stricter:
    if verdict == "PENDING" and elapsed_from_signal >= 15.0 and window:
        # Re-eval with min_wait satisfied using queue elapsed for timeout
        verdict2, reason2 = evaluate_micro_confirm(
            signal_close=signal,
            structure_level=structure_f,
            bars_after=window,
            minutes_elapsed=max(elapsed_from_signal, MIN_WAIT_MIN + 0.1),
            min_wait=MIN_WAIT_MIN,
            max_wait=15.0 + MAX_WAIT_MIN,
        )
        verdict, reason = verdict2, reason2

    if verdict == "PENDING" and elapsed >= MAX_WAIT_MIN:
        # Wall-clock post-queue timeout
        if window:
            last = window[-1]
            hold_floor = signal * (1.0 - HOLD_PCT)
            if last.close >= hold_floor:
                verdict, reason = "CONFIRM", f"timeout_hold_close={last.close:.4f}"
            else:
                verdict, reason = "TIMEOUT", f"post_queue_timeout_close={last.close:.4f}"
        else:
            verdict, reason = "TIMEOUT", "post_queue_no_bars"

    meta = {
        "symbol": sym,
        "elapsed_min": round(elapsed, 2),
        "elapsed_from_signal_min": round(elapsed_from_signal, 2),
        "n_bars": len(window),
        "last_close": window[-1].close if window else None,
        "signal_end": signal_end.isoformat(),
        "verdict": verdict,
        "reason": reason,
    }
    return verdict, reason, meta
