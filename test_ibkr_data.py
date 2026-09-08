"""
Q-ALPHA | IBKR market-data subscription diagnostic (READ-ONLY).

Connects to TWS paper (same host/port as autonomous_agent) and probes:
  LIVE snapshot, DELAYED (type 3), REALTIME 5s bars, HISTORICAL daily bars.

No orders. Cancel subscriptions and disconnect on exit.

Usage (TWS paper open, API enabled, account DUR857496 logged in):
  py -3 test_ibkr_data.py
"""
from __future__ import annotations

import math
import sys
import time
from datetime import datetime

from ib_insync import IB, Stock, util

# Match autonomous_agent.py connection settings
TWS_HOST = "127.0.0.1"
TWS_PORT = 7497
# Distinct from agent clientId=5 so this diagnostic does not collide mid-session
TWS_CLIENT_ID = 99

SYMBOL = "SPY"
EXCHANGE = "SMART"
CURRENCY = "USD"


def _fmt_px(val) -> str:
    """Label a ticker field as a real number or nan."""
    try:
        if val is None:
            return "nan (None)"
        f = float(val)
        if math.isnan(f):
            return "nan"
        return f"{f:.4f} (real)"
    except (TypeError, ValueError):
        return f"nan ({val!r})"


def _snapshot_ok(ticker) -> bool:
    """True if at least one of last/close/bid/ask is a finite number."""
    for attr in ("last", "close", "bid", "ask"):
        try:
            f = float(getattr(ticker, attr, float("nan")))
            if not math.isnan(f):
                return True
        except (TypeError, ValueError):
            continue
    return False


def _print_ticker_fields(label: str, ticker) -> None:
    print(f"\n--- {label} ---")
    print(f"  last : {_fmt_px(getattr(ticker, 'last', None))}")
    print(f"  close: {_fmt_px(getattr(ticker, 'close', None))}")
    print(f"  bid  : {_fmt_px(getattr(ticker, 'bid', None))}")
    print(f"  ask  : {_fmt_px(getattr(ticker, 'ask', None))}")


def main() -> int:
    util.startLoop()
    ib = IB()
    live_ok = False
    delayed_ok = False
    rt_n = 0
    hist_ok = False
    last_bar = None

    print("=" * 64)
    print("Q-ALPHA IBKR MARKET DATA DIAGNOSTIC")
    print(f"Host={TWS_HOST} Port={TWS_PORT} clientId={TWS_CLIENT_ID}")
    print("(READ-ONLY — no orders)")
    print("=" * 64)

    try:
        ib.connect(TWS_HOST, TWS_PORT, clientId=TWS_CLIENT_ID, timeout=10)
    except Exception as exc:
        print(f"\nCONNECT FAILED: {exc}")
        print("Open TWS, log into paper (DUR857496), enable API on port 7497.")
        return 1

    accounts = list(ib.managedAccounts() or [])
    print(f"\n1) CONNECTED  accounts={accounts}")
    if "DUR857496" in accounts:
        print("   Paper account DUR857496 confirmed.")
    else:
        print("   NOTE: DUR857496 not in managedAccounts — check which account is logged in.")

    contract = Stock(SYMBOL, EXCHANGE, CURRENCY)
    try:
        ib.qualifyContracts(contract)
    except Exception as exc:
        print(f"Contract qualify failed: {exc}")

    # ── 2) LIVE (default market-data type — do NOT call reqMarketDataType) ──
    print("\n2) LIVE data test (no reqMarketDataType override)…")
    t_live = ib.reqMktData(contract, "", False, False)
    ib.sleep(3)
    _print_ticker_fields("LIVE SPY", t_live)
    live_ok = _snapshot_ok(t_live)
    try:
        ib.cancelMktData(contract)
    except Exception:
        pass

    # ── 3) DELAYED (type 3) ────────────────────────────────────────────────
    print("\n3) DELAYED data test (reqMarketDataType(3))…")
    ib.reqMarketDataType(3)
    t_del = ib.reqMktData(contract, "", False, False)
    ib.sleep(3)
    _print_ticker_fields("DELAYED SPY", t_del)
    delayed_ok = _snapshot_ok(t_del)
    try:
        ib.cancelMktData(contract)
    except Exception:
        pass

    # ── 4) REAL-TIME BARS (5-sec TRADES) ───────────────────────────────────
    print("\n4) REAL-TIME BARS test (5-sec TRADES, wait ~15s)…")
    bars = None
    try:
        bars = ib.reqRealTimeBars(contract, 5, "TRADES", False)
        ib.sleep(15)
        rt_n = len(bars) if bars is not None else 0
        print(f"   bars received: {rt_n}")
        if rt_n > 0:
            last_bar = bars[-1]
            # ib_insync uses open_ not open
            o = getattr(last_bar, "open_", getattr(last_bar, "open", None))
            print(
                f"   last bar OHLC: "
                f"O={o} H={last_bar.high} L={last_bar.low} C={last_bar.close} "
                f"time={last_bar.time}"
            )
        else:
            print("   last bar: (none)")
    except Exception as exc:
        print(f"   REALTIME_BARS ERROR: {exc}")
        rt_n = 0
    finally:
        if bars is not None:
            try:
                ib.cancelRealTimeBars(bars)
            except Exception:
                pass

    # ── 5) HISTORICAL (daily) ──────────────────────────────────────────────
    print("\n5) HISTORICAL test (5 daily bars)…")
    try:
        hist = ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr="5 D",
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1,
        )
        ib.sleep(2)
        if hist:
            hist_ok = True
            print(f"   received {len(hist)} daily bars:")
            for b in hist:
                o = getattr(b, "open_", getattr(b, "open", None))
                print(
                    f"     {b.date}  O={o} H={b.high} L={b.low} C={b.close} V={b.volume}"
                )
        else:
            print("   HISTORICAL: empty result")
            hist_ok = False
    except Exception as exc:
        print(f"   HISTORICAL ERROR: {exc}")
        hist_ok = False

    # ── Summary ────────────────────────────────────────────────────────────
    print("\n" + "=" * 64)
    print("SUMMARY")
    print("=" * 64)
    print(f"  LIVE          = {'works' if live_ok else 'nan'}")
    print(f"  DELAYED       = {'works' if delayed_ok else 'nan'}")
    print(f"  REALTIME_BARS = {rt_n} bars received")
    print(f"  HISTORICAL    = {'works' if hist_ok else 'fail'}")
    print("=" * 64)

    try:
        ib.disconnect()
        print("Disconnected cleanly.")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
