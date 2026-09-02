"""
Q-ALPHA UTS v2 — setup watch agent (Phase 3).

RTH loop: poll tsd_watch_queue.json WATCHING rows, confirm setup, place entries.

Usage (TWS paper open):
  py -3 candidates/setup_watch_agent.py --dry-run --once
  py -3 candidates/setup_watch_agent.py --loop
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any

import pytz
from ib_insync import IB, Stock, util

CANDIDATES_DIR = Path(__file__).resolve().parent
ROOT = CANDIDATES_DIR.parent
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

from setup_watch_confirmation import SessionQuote, build_session_quote, compute_rvol
from tsd_scan_pipeline.tsd_capacity import load_state, save_state
from tsd_scan_pipeline.tsd_entry import classify_session
from tsd_scan_pipeline.tsd_entry_gates import (
    evaluate_entry_gates,
    fetch_regime_bull,
    is_entry_window,
    is_watch_timeout,
)
from tsd_scan_pipeline.tsd_structure import RTH_MINUTES, RTH_OPEN, fetch_orb_bars, orb_high_low
from tsd_scan_pipeline.tsd_watch_queue import (
    execute_live_entries,
    get_watching_rows,
    queue_row_as_candidate,
    update_queue_row,
)

ET = pytz.timezone("America/New_York")
TWS_HOST = "127.0.0.1"
TWS_PORT = 7497
TWS_CLIENT_ID = 94
POLL_SEC = 30
BARS_PER_MINUTE = 60  # 1-sec bars from IB; we aggregate last minute


def _notify(msg: str) -> None:
    try:
        from autonomous_agent import send_telegram

        send_telegram(msg)
    except Exception:
        print(f"  [telegram] {msg}")


def _minutes_since_open(now: datetime) -> int:
    return max(0, (now.hour - 9) * 60 + now.minute - 30)


def _session_bars(ib: IB, contract, session_open: datetime) -> list:
    bars = ib.reqHistoricalData(
        contract,
        endDateTime="",
        durationStr="1 D",
        barSizeSetting="1 min",
        whatToShow="TRADES",
        useRTH=True,
        formatDate=1,
        keepUpToDate=False,
    )
    out = []
    for bar in bars or []:
        ts = bar.date
        if ts.tzinfo is None:
            ts = ET.localize(ts)
        else:
            ts = ts.astimezone(ET)
        if ts >= session_open:
            out.append(bar)
    return out


def _avg_daily_volume(ib: IB, contract) -> float:
    daily = ib.reqHistoricalData(
        contract,
        endDateTime="",
        durationStr="25 D",
        barSizeSetting="1 day",
        whatToShow="TRADES",
        useRTH=True,
        formatDate=1,
        keepUpToDate=False,
    )
    if not daily or len(daily) < 2:
        return 0.0
    hist = list(daily)[:-1]
    return sum(float(b.volume or 0) for b in hist[-20:]) / min(20, len(hist))


def build_session_quote(ib: IB, symbol: str, *, now: datetime | None = None) -> SessionQuote | None:
    """Build SessionQuote from IBKR 1-min RTH bars."""
    now_et = now or datetime.now(ET)
    if now_et.tzinfo is None:
        now_et = ET.localize(now_et)
    else:
        now_et = now_et.astimezone(ET)

    session_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    contract = Stock(symbol.upper(), "SMART", "USD")
    ib.qualifyContracts(contract)

    session_bars = _session_bars(ib, contract, session_open)
    if not session_bars:
        return None

    price = float(session_bars[-1].close)
    low = min(float(b.low) for b in session_bars)
    high = max(float(b.high) for b in session_bars)
    session_open_px = float(session_bars[0].open)
    session_vol = sum(float(b.volume or 0) for b in session_bars)

    if session_vol > 0:
        vwap = sum(float(b.close) * float(b.volume) for b in session_bars) / session_vol
    else:
        vwap = price

    recent = session_bars[-1:]
    up_vol = sum(float(b.volume or 0) for b in recent if float(b.close) >= float(b.open))
    dn_vol = sum(float(b.volume or 0) for b in recent if float(b.close) < float(b.open))

    first_min = session_bars[:1]
    fc_low = min(float(b.low) for b in first_min) if first_min else low
    fc_high = max(float(b.high) for b in first_min) if first_min else high

    orb_bars = fetch_orb_bars(ib, symbol, day=now_et.date())
    hl = orb_high_low(orb_bars)
    orb_high, orb_low = (hl if hl else (0.0, 0.0))

    avg_daily = _avg_daily_volume(ib, contract)
    mins = _minutes_since_open(now_et)
    rvol = compute_rvol(session_vol, avg_daily, mins, rth_minutes=RTH_MINUTES)

    was_below = any(float(b.close) < vwap for b in session_bars[:-1])

    return SessionQuote(
        price=price,
        low=low,
        high=high,
        session_open=session_open_px,
        vwap=vwap,
        orb_high=orb_high,
        orb_low=orb_low,
        rvol=rvol,
        up_vol=up_vol,
        dn_vol=dn_vol,
        first_candle_low=fc_low,
        first_candle_high=fc_high,
        minutes_since_open=mins,
        prev_close=None,
        was_below_vwap=was_below,
    )


def _same_et_day(iso_ts: str, now: datetime) -> bool:
    try:
        dt = datetime.fromisoformat(iso_ts)
        if dt.tzinfo is None:
            dt = ET.localize(dt)
        else:
            dt = dt.astimezone(ET)
        return dt.date() == now.date()
    except Exception:
        return False


def process_timeouts(now: datetime | None = None) -> list[dict[str, Any]]:
    """Mark today's WATCHING rows SKIPPED after 11:00 ET."""
    now_et = now or datetime.now(ET)
    if not is_watch_timeout(now_et):
        return []

    actions: list[dict[str, Any]] = []
    for row in get_watching_rows():
        sym = str(row["symbol"]).upper()
        added = str(row.get("added_at") or "")
        if not _same_et_day(added, now_et):
            continue
        reason = "timeout_1100"
        update_queue_row(sym, status="SKIPPED", reason=reason)
        msg = f"TSD {sym} SKIPPED\nSetup not confirmed by 11:00 ET"
        print(f"  TIMEOUT {sym}: {reason}")
        _notify(msg)
        actions.append({"symbol": sym, "status": "SKIPPED", "reason": reason})
    return actions


def _synthetic_quote_for_dry_run(row: dict[str, Any]) -> SessionQuote:
    """Synthetic confirming quote for --dry-run when TWS is unavailable."""
    cross = float(row.get("cross_level") or row.get("close") or 10.0)
    return SessionQuote(
        price=cross * 1.02,
        low=cross * 1.001,
        high=cross * 1.03,
        session_open=cross * 0.99,
        vwap=cross * 0.995,
        orb_high=cross * 1.0,
        orb_low=cross * 0.98,
        rvol=1.1,
        up_vol=100_000,
        dn_vol=50_000,
        first_candle_low=cross * 0.97,
        first_candle_high=cross * 1.01,
        minutes_since_open=15,
        prev_close=cross * 0.98,
        was_below_vwap=True,
    )


def _is_launch_row(row: dict[str, Any]) -> bool:
    """LAUNCH lane B rows may confirm/enter outside RTH (kill backstop)."""
    return row.get("phase") == "LAUNCH" or (
        str(row.get("signal_lane", "")).upper() == "B"
        and float(row.get("launch_score") or 0) >= 50
    )


def process_watching_row(
    ib: IB | None,
    row: dict[str, Any],
    *,
    dry_run: bool,
    now: datetime | None = None,
    quote: SessionQuote | None = None,
    regime_bull: bool | None = None,
) -> dict[str, Any]:
    """Evaluate one WATCHING row; confirm and optionally enter."""
    sym = str(row["symbol"]).upper()
    now_et = now or datetime.now(ET)

    passed, gates, reasons = evaluate_entry_gates(
        queue_row_as_candidate(row),
        regime_bull=regime_bull,
        require_rth_window=not dry_run,
        now=now_et,
    )
    if not passed:
        return {"symbol": sym, "status": "WAIT", "reason": ";".join(reasons) or "gates"}

    if quote is None:
        if dry_run:
            quote = _synthetic_quote_for_dry_run(row)
        elif ib is None:
            return {"symbol": sym, "status": "WAIT", "reason": "no_quote"}
        else:
            quote = build_session_quote(ib, sym, now=now_et)
            if quote is None:
                return {"symbol": sym, "status": "WAIT", "reason": "bars_unavailable"}

    confirm_reason = "htf_launch_direct"

    if dry_run:
        return {
            "symbol": sym,
            "status": "CONFIRMED_DRY_RUN",
            "reason": confirm_reason,
            "price": quote.price,
            "lane": row.get("signal_lane"),
        }

    update_queue_row(sym, status="CONFIRMED", reason=confirm_reason)
    book = load_state()
    cand = queue_row_as_candidate(row)
    cand["close"] = quote.price
    results = execute_live_entries(ib, [cand], book)
    save_state(book)
    fill = results[0] if results else {}
    if fill.get("status") == "FILLED":
        _notify(
            f"TSD {sym} ENTERED\n{fill.get('shares')}sh @ ${fill.get('fill_price'):.2f}\n"
            f"{confirm_reason}"
        )
    return {"symbol": sym, "status": "ENTERED", "fill": fill, "reason": confirm_reason}


def run_pass(*, dry_run: bool = False) -> dict[str, Any]:
    """Single setup-watch pass over WATCHING queue rows."""
    now = datetime.now(ET)
    mode = "DRY_RUN" if dry_run else "LIVE"
    session = classify_session(now)

    print("=" * 64)
    print(f"Q-ALPHA SETUP WATCH AGENT - {mode}")
    print(f"ET={now.strftime('%Y-%m-%d %H:%M:%S')} session={session} clientId={TWS_CLIENT_ID}")
    print("=" * 64)

    actions: list[dict[str, Any]] = []
    if not dry_run:
        actions.extend(process_timeouts(now))

    watching = get_watching_rows()
    print(f"WATCHING: {len(watching)}  symbols={[r['symbol'] for r in watching]}")

    if not watching:
        return {"mode": mode, "checked_at": now.isoformat(), "actions": actions}

    if not is_entry_window(now) and not dry_run:
        print("Outside entry window (09:35-15:00 ET) — skip entries")
        return {"mode": mode, "checked_at": now.isoformat(), "actions": actions}

    ib: IB | None = None
    if not dry_run:
        util.startLoop()
        ib = IB()
        try:
            ib.connect(TWS_HOST, TWS_PORT, clientId=TWS_CLIENT_ID, timeout=12)
        except Exception as exc:
            print(f"CONNECT FAILED: {exc}")
            return {"error": str(exc), "checked_at": now.isoformat()}

    bull, regime, _ = fetch_regime_bull()
    print(f"Regime: {regime} (bull={bull})")

    for row in watching:
        sym = row["symbol"]
        print(f"\n--- {sym} lane={row.get('signal_lane')} cross={row.get('cross_level')} ---")
        result = process_watching_row(
            ib, row, dry_run=dry_run, now=now, regime_bull=bull,
        )
        print(f"  -> {result}")
        actions.append(result)

    if ib is not None:
        try:
            ib.disconnect()
        except Exception:
            pass

    print("")
    print("=" * 64)
    print(f"Done — {len(actions)} action(s)")
    print("=" * 64)
    return {"mode": mode, "checked_at": now.isoformat(), "actions": actions}


def main() -> int:
    parser = argparse.ArgumentParser(description="UTS v2 setup watch agent")
    parser.add_argument("--once", action="store_true", help="Single pass (default)")
    parser.add_argument("--loop", action="store_true", help="RTH 30s loop until 14:00 ET")
    parser.add_argument("--dry-run", action="store_true", help="No orders; mock-friendly with injected quotes in tests")
    args = parser.parse_args()

    if args.loop:
        print("Loop mode: Ctrl+C to stop")
        while True:
            now = datetime.now(ET)
            session = classify_session(now)
            in_rth = session == "RTH" and now.time() <= dtime(14, 0)
            if not in_rth:
                # PM/extended: still poll LAUNCH rows (kill backstop on entry)
                run_pass(dry_run=args.dry_run)
                wait = 60 if session in ("PRE", "POST") else 300
                print(f"  session={session} — sleeping {wait}s")
                time.sleep(wait)
                continue
            run_pass(dry_run=args.dry_run)
            time.sleep(POLL_SEC)
    else:
        run_pass(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
