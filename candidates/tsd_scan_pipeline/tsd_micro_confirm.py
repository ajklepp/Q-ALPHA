"""
Peak Hour micro-confirmation (post case-ENTER, pre-BUY).

1H LAUNCH nominates; case says ENTER; this module watches 1-min structure
for a short window and either CONFIRMs the buy or ABORTs if price is
immediately dumping through signal/structure.

Gates (Peak Hour — not gap Lane A):
  - min_wait 2 minutes after signal
  - ABORT if low pierces structure (or −1.5% from signal close)
  - CONFIRM if holding near signal close with constructive 1m close
  - Do not chase: wait for pullback if extended >+1.5% above signal
  - Dead-tape RVOL skip when enough 1m bars exist
  - TIMEOUT → SKIP (do not chase); EH/sparse may use live TWS last to hold-confirm
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta
from typing import Any, Literal

import pytz

from tsd_scan_pipeline.universe_tsd import POLYGON_BASE, load_polygon_key, polygon_get

ET = pytz.timezone("America/New_York")

MIN_WAIT_MIN = 2
MAX_WAIT_MIN = 10  # wall clock after queue (RTH)
MAX_WAIT_MIN_EH = 20  # extended hours: sparse 1m bars, give more time
DUMP_PCT = 0.015  # −1.5% from signal = abort
STRUCTURE_BUFFER = 0.002  # 0.2% under structure
HOLD_PCT = 0.003  # must be within −0.3% of signal to confirm
CHASE_PCT = 0.015  # >+1.5% above signal → wait for pullback (no chase)
MIN_MICRO_RVOL = 0.40  # post-signal vol vs prior 1m median (enough bars only)
MICRO_RVOL_MIN_BARS = 5
SPARSE_BARS_MAX = 2  # ≤ this → prefer live quote over TIMEOUT when holding
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


def is_extended_hours(now: datetime | None = None) -> bool:
    """True outside RTH 09:30–16:00 ET weekdays (pre/AH/overnight)."""
    dt = now or datetime.now(ET)
    if dt.tzinfo is None:
        dt = ET.localize(dt)
    else:
        dt = dt.astimezone(ET)
    if dt.weekday() >= 5:
        return True
    t = dt.time()
    return not (dtime(9, 30) <= t < dtime(16, 0))


def post_queue_max_wait(now: datetime | None = None) -> float:
    """Wall-clock minutes after queue before TIMEOUT."""
    return float(MAX_WAIT_MIN_EH if is_extended_hours(now) else MAX_WAIT_MIN)


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


def micro_rvol(
    all_bars: list[MicroBar],
    window: list[MicroBar],
    *,
    signal_end: datetime,
) -> float | None:
    """
    Post-signal avg 1m volume / median of prior 20 one-minute bars.

    Returns None when not enough history to judge (do not gate).
    """
    if len(window) < MICRO_RVOL_MIN_BARS:
        return None
    prior = [b for b in all_bars if b.ts <= signal_end][-20:]
    if len(prior) < 10:
        return None
    vols = sorted(float(b.volume or 0) for b in prior)
    mid = vols[len(vols) // 2]
    if mid <= 0:
        return None
    avg_win = sum(float(b.volume or 0) for b in window) / len(window)
    return avg_win / mid


def pullback_limit_price(
    signal_close: float,
    *,
    last_close: float | None = None,
    structure_level: float | None = None,
) -> float:
    """
    Passive BUY limit: prefer near signal, not chase of last print.

    If extended above signal, limit sits just above signal (wait for dip).
    """
    sig = float(signal_close)
    if sig <= 0:
        return 0.0
    last = float(last_close) if last_close and last_close > 0 else sig
    # Cap: never pay more than +0.2% over signal on the limit
    cap = round(sig * 1.002, 2)
    if last > sig * (1.0 + CHASE_PCT * 0.5):
        # Extended — sit at signal + 0.1% and wait for pullback fill
        lmt = round(sig * 1.001, 2)
    else:
        lmt = round(min(last, sig) * 1.002, 2)
    if structure_level and float(structure_level) > 0:
        # Do not place limit under structure (would be a thesis break buy)
        floor = round(float(structure_level) * (1.0 + STRUCTURE_BUFFER), 2)
        lmt = max(lmt, floor)
    return min(lmt, cap) if cap > 0 else lmt


def evaluate_micro_confirm(
    *,
    signal_close: float,
    structure_level: float | None,
    bars_after: list[MicroBar],
    minutes_elapsed: float,
    min_wait: float = MIN_WAIT_MIN,
    max_wait: float = MAX_WAIT_MIN,
    micro_rvol_ratio: float | None = None,
    vol_ratio_20: float | None = None,
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
    chase_ceil = signal_close * (1.0 + CHASE_PCT)
    if last.close < hold_floor:
        if minutes_elapsed >= max_wait:
            return "TIMEOUT", f"weak_hold_close={last.close:.4f}"
        return "PENDING", f"below_hold_close={last.close:.4f}"

    # No chase: wait for pullback into band
    if last.close > chase_ceil:
        if minutes_elapsed >= max_wait:
            return "TIMEOUT", f"chase_skip_close={last.close:.4f}"
        return "PENDING", f"waiting_pullback_close={last.close:.4f}"

    # Dead tape: skip when we have enough 1m evidence (or daily vol_ratio soft)
    if micro_rvol_ratio is not None and micro_rvol_ratio < MIN_MICRO_RVOL:
        if minutes_elapsed >= max_wait:
            return "TIMEOUT", f"dead_tape_rvol={micro_rvol_ratio:.2f}"
        return "PENDING", f"waiting_rvol={micro_rvol_ratio:.2f}"
    if vol_ratio_20 is not None and 0 < vol_ratio_20 < 0.45:
        if minutes_elapsed >= max_wait:
            return "TIMEOUT", f"dead_vol_ratio_20={vol_ratio_20:.2f}"
        return "PENDING", f"waiting_vol_ratio_20={vol_ratio_20:.2f}"

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


def _live_last_hold_ok(
    *,
    signal_close: float,
    structure_level: float | None,
    live_last: float,
) -> bool:
    """True if TWS/live last is holding (not dumping) near signal."""
    if live_last <= 0 or signal_close <= 0:
        return False
    dump_floor = signal_close * (1.0 - DUMP_PCT)
    if structure_level and structure_level > 0:
        dump_floor = max(dump_floor, float(structure_level) * (1.0 - STRUCTURE_BUFFER))
    if live_last <= dump_floor:
        return False
    hold_floor = signal_close * (1.0 - HOLD_PCT)
    chase_ceil = signal_close * (1.0 + CHASE_PCT)
    return hold_floor <= live_last <= chase_ceil


def confirm_peak_hour_entry(
    row: dict[str, Any],
    *,
    now: datetime | None = None,
    bars: list[MicroBar] | None = None,
    api_key: str | None = None,
    live_last: float | None = None,
) -> tuple[MicroVerdict, str, dict[str, Any]]:
    """
    Evaluate one WATCHING Peak Hour row.

    Expects: symbol, signal_close/htf_1h_close, structure_level,
    htf_1h_bar_hour (close hour), queued_at/scan_at.
    Optional live_last: TWS last used on sparse/TIMEOUT to avoid false skips.
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

    try:
        vol_ratio_20 = float(row.get("vol_ratio_20")) if row.get("vol_ratio_20") is not None else None
    except (TypeError, ValueError):
        vol_ratio_20 = None

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
    queue_max = post_queue_max_wait(now_et)
    signal_max = 15.0 + queue_max

    if bars is None:
        try:
            all_bars = fetch_1m_bars(sym, day=day, api_key=api_key)
        except Exception as exc:
            if elapsed >= queue_max:
                # Sparse EH: live quote can still confirm hold
                if live_last is not None and _live_last_hold_ok(
                    signal_close=signal, structure_level=structure_f, live_last=live_last,
                ):
                    meta = {
                        "symbol": sym,
                        "elapsed_min": round(elapsed, 2),
                        "n_bars": 0,
                        "live_last": live_last,
                        "verdict": "CONFIRM",
                        "reason": f"tws_hold_after_fetch_fail:{exc}",
                        "limit_price": pullback_limit_price(
                            signal, last_close=live_last, structure_level=structure_f,
                        ),
                    }
                    return "CONFIRM", meta["reason"], meta
                return "TIMEOUT", f"bar_fetch_fail:{exc}", {}
            return "PENDING", f"bar_fetch_retry:{exc}", {}
    else:
        all_bars = bars

    # Include pre-scan tape (bar close → now) so dump between :00 and :15 aborts
    window = bars_since(all_bars, after=signal_end, until=now_et)
    elapsed_from_signal = (now_et - signal_end).total_seconds() / 60.0
    rvol = micro_rvol(all_bars, window, signal_end=signal_end)

    verdict, reason = evaluate_micro_confirm(
        signal_close=signal,
        structure_level=structure_f,
        bars_after=window,
        minutes_elapsed=elapsed_from_signal,
        min_wait=MIN_WAIT_MIN,
        max_wait=signal_max,
        micro_rvol_ratio=rvol,
        vol_ratio_20=vol_ratio_20,
    )
    # If PENDING only because waiting constructive but we have 15m tape, re-eval
    if verdict == "PENDING" and elapsed_from_signal >= 15.0 and window:
        verdict2, reason2 = evaluate_micro_confirm(
            signal_close=signal,
            structure_level=structure_f,
            bars_after=window,
            minutes_elapsed=max(elapsed_from_signal, MIN_WAIT_MIN + 0.1),
            min_wait=MIN_WAIT_MIN,
            max_wait=signal_max,
            micro_rvol_ratio=rvol,
            vol_ratio_20=vol_ratio_20,
        )
        verdict, reason = verdict2, reason2

    if verdict == "PENDING" and elapsed >= queue_max:
        # Wall-clock post-queue timeout
        if window:
            last = window[-1]
            hold_floor = signal * (1.0 - HOLD_PCT)
            chase_ceil = signal * (1.0 + CHASE_PCT)
            if hold_floor <= last.close <= chase_ceil:
                # Re-check dead tape at timeout
                if rvol is not None and rvol < MIN_MICRO_RVOL:
                    verdict, reason = "TIMEOUT", f"post_queue_dead_tape_rvol={rvol:.2f}"
                elif vol_ratio_20 is not None and 0 < vol_ratio_20 < 0.45:
                    verdict, reason = "TIMEOUT", f"post_queue_dead_vol_ratio_20={vol_ratio_20:.2f}"
                else:
                    verdict, reason = "CONFIRM", f"timeout_hold_close={last.close:.4f}"
            else:
                verdict, reason = "TIMEOUT", f"post_queue_timeout_close={last.close:.4f}"
        else:
            verdict, reason = "TIMEOUT", "post_queue_no_bars"

    # Sparse / no-bars TIMEOUT → TWS last hold (do not loosen dump abort)
    sparse = len(window) <= SPARSE_BARS_MAX
    if (
        verdict == "TIMEOUT"
        and live_last is not None
        and (sparse or "no_bars" in reason or "bar_fetch" in reason)
        and _live_last_hold_ok(
            signal_close=signal, structure_level=structure_f, live_last=live_last,
        )
    ):
        # Still require no dump in whatever bars we have
        dump_ok = True
        abort_px = signal * (1.0 - DUMP_PCT)
        if structure_f and structure_f > 0:
            abort_px = max(abort_px, structure_f * (1.0 - STRUCTURE_BUFFER))
        for b in window:
            if b.low <= abort_px:
                dump_ok = False
                break
        if dump_ok:
            verdict, reason = "CONFIRM", f"tws_hold_last={live_last:.4f}"

    last_close = window[-1].close if window else live_last
    limit_px = pullback_limit_price(
        signal, last_close=last_close, structure_level=structure_f,
    ) if signal > 0 else None

    meta = {
        "symbol": sym,
        "elapsed_min": round(elapsed, 2),
        "elapsed_from_signal_min": round(elapsed_from_signal, 2),
        "n_bars": len(window),
        "last_close": window[-1].close if window else None,
        "live_last": live_last,
        "micro_rvol": round(rvol, 3) if rvol is not None else None,
        "vol_ratio_20": vol_ratio_20,
        "signal_end": signal_end.isoformat(),
        "queue_max_wait": queue_max,
        "limit_price": limit_px,
        "verdict": verdict,
        "reason": reason,
    }
    return verdict, reason, meta
