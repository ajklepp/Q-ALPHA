"""
Q-Alpha intraday position monitor (Modal — Polygon fallback marks ONLY).

Runs on Modal every 30 minutes during market hours (scheduler Cron
``*/30 13-20 * * 1-5`` ≈ 9:30–4:00 PM ET).

CRITICAL:
  Modal cannot reach local TWS (127.0.0.1:7497). This job does NOT close
  trades and must NEVER keep/reopen a name that local TWS sync already
  marked CLOSED or NEVER_FILLED.
  Live SoT for marks + filled-flat→CLOSED is local
  ``candidates/tws_intraday_sync.py`` (Task Scheduler, clientId 96).

Polygon marks here are secondary/fallback for still-OPEN ledger rows only.
IBKR_PAPER agent trades are skipped entirely — local tws_intraday_sync.py
(clientId 96) is the sole mark source for live paper positions.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

CANDIDATES_DIR = Path(__file__).resolve().parent
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

from state_paths import state_path

POLYGON_BASE = "https://api.polygon.io"
POLYGON_SLEEP = 0.12
OPEN_STATUSES = frozenset({
    "OPEN", "T1_HIT", "T2_HIT", "T3_TRAIL", "PENDING_MOC",
})
# Never mark / upsert live P&L for rejects or unfilled parents (fill-truth).
TERMINAL_NON_OPEN = frozenset({
    "NEVER_FILLED", "REJECTED_INELIGIBLE", "REJECTED_NO_FILL",
    "SKIPPED", "CLOSED",
})
MANAGED_TRADE_SOURCES = frozenset({"telegram_yes", "autonomous_agent"})


def load_dotenv_if_available() -> None:
    """Load repo .env for local runs."""
    env_path = CANDIDATES_DIR.parent / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
        return
    except ImportError:
        pass
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_current_price_polygon(ticker: str, api_key: str) -> float:
    """Get current price from Polygon snapshot."""
    try:
        url = (
            f"{POLYGON_BASE}/v2/snapshot/"
            f"locale/us/markets/stocks/tickers/{ticker}"
        )
        resp = requests.get(
            url, params={"apiKey": api_key}, timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        ticker_data = data.get("ticker", {})

        last = ticker_data.get("lastTrade", {}).get("p", 0)
        if last and last > 0:
            return float(last)

        day = ticker_data.get("day", {})
        close = day.get("c", 0)
        if close and close > 0:
            return float(close)

        prev = ticker_data.get("prevDay", {})
        prev_close = prev.get("c", 0)
        return float(prev_close) if prev_close else 0.0

    except Exception as exc:
        print(f"Price fetch failed for {ticker}: {exc}")
        return 0.0


def _supabase_terminal_status(sync, ticker: str, entry_date: str) -> str | None:
    """
    Return Supabase status when it is terminal (CLOSED / NEVER_FILLED / …).
    Used to block OPEN mark upserts and heal stale Modal volume rows.
    """
    try:
        result = (
            sync.client.table("trades")
            .select("status")
            .eq("ticker", ticker)
            .eq("entry_date", entry_date)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            return None
        existing = str(rows[0].get("status") or "").upper()
        if existing in TERMINAL_NON_OPEN:
            return existing
        return None
    except Exception as exc:
        print(f"  Supabase status check warn ({ticker}): {exc}")
        return None


def _supabase_status_is_terminal(sync, ticker: str, entry_date: str) -> bool:
    """True if Supabase already has CLOSED / NEVER_FILLED / REJECTED_*."""
    return _supabase_terminal_status(sync, ticker, entry_date) is not None


def run_intraday_monitor() -> None:
    """
    Fetch Polygon marks for open positions only; upsert unrealized P&L.

    Does not close positions. Skips CLOSED / NEVER_FILLED / REJECTED_* locally
    and will not overwrite those statuses in Supabase (no re-OPEN).
    """
    load_dotenv_if_available()

    try:
        from supabase_sync import SupabaseSync
    except ImportError:
        from candidates.supabase_sync import SupabaseSync

    api_key = os.environ.get("POLYGON_API_KEY", "")
    if not api_key:
        print("ERROR: POLYGON_API_KEY not set")
        return

    print(f"Intraday monitor (Polygon marks): {datetime.now().strftime('%H:%M:%S ET')}")

    trades_path = state_path("paper_trades.json")
    if not trades_path.exists():
        print(f"No paper_trades.json found at {trades_path}")
        return

    data = json.loads(trades_path.read_text(encoding="utf-8"))
    trades = data.get("trades", [])

    open_trades = []
    skipped_terminal = []
    for t in trades:
        status = str(t.get("status") or "").upper()
        if status in TERMINAL_NON_OPEN:
            if t.get("approved_by") in MANAGED_TRADE_SOURCES:
                skipped_terminal.append(f"{t.get('ticker')}:{status}")
            continue
        shares = int(t.get("shares_total") or t.get("shares") or 0)
        if shares <= 0:
            if t.get("approved_by") in MANAGED_TRADE_SOURCES:
                skipped_terminal.append(f"{t.get('ticker')}:shares=0")
            continue
        if (
            status in OPEN_STATUSES
            and t.get("approved_by") in MANAGED_TRADE_SOURCES
        ):
            if str(t.get("execution_mode") or "") == "IBKR_PAPER":
                skipped_terminal.append(f"{t.get('ticker')}:IBKR_PAPER(TWS SoT)")
                continue
            open_trades.append(t)

    if skipped_terminal:
        print(
            f"Skip marks for non-open: {', '.join(skipped_terminal)}"
        )

    if not open_trades:
        print("No open positions to monitor")
        return

    print(f"Monitoring {len(open_trades)} open positions...")

    sync = SupabaseSync()
    updates = []
    volume_healed = False

    for trade in open_trades:
        ticker = trade["ticker"]
        status = str(trade.get("status") or "").upper()
        entry_date = str(trade.get("entry_date") or "")
        # Belt-and-suspenders: never upsert OPEN marks for rejects.
        if status not in OPEN_STATUSES or status in TERMINAL_NON_OPEN:
            print(f"  Skip {ticker}: status={status} (not open)")
            continue
        # Do not resurrect Cloud CLOSED / NEVER_FILLED with stale Modal OPEN marks.
        cloud_status = (
            _supabase_terminal_status(sync, ticker, entry_date)
            if entry_date else None
        )
        if cloud_status:
            print(
                f"  Skip {ticker}: Supabase already {cloud_status} "
                f"(will not upsert OPEN marks / will not re-OPEN)"
            )
            # Heal volume/local ledger so the next cron does not retry.
            trade["status"] = cloud_status
            if int(trade.get("shares_total") or 0) > 0:
                trade["shares_total"] = 0
            volume_healed = True
            continue

        entry_price = float(trade.get("entry_price") or 0)
        shares = int(trade.get("shares_total") or trade.get("shares") or 0)
        stop_price = float(trade.get("stop_price") or 0)
        target_2r = float(trade.get("target_2r") or 0)

        if entry_price <= 0 or shares <= 0:
            continue

        current_price = get_current_price_polygon(ticker, api_key)
        time.sleep(POLYGON_SLEEP)

        if current_price <= 0:
            print(f"  {ticker}: price unavailable")
            continue

        pnl_per_share = current_price - entry_price
        pnl_dollars = pnl_per_share * shares
        pnl_pct = pnl_per_share / entry_price

        dist_to_stop = (
            (current_price - stop_price) / current_price
            if current_price > 0 else 0.0
        )
        dist_to_target = (
            (target_2r - current_price) / current_price
            if current_price > 0 else 0.0
        )

        risk_per_share = entry_price - stop_price
        r_multiple = (
            pnl_per_share / risk_per_share
            if risk_per_share > 0 else 0.0
        )

        if pnl_pct >= 0.05:
            status_icon = "🚀"
        elif pnl_pct >= 0.02:
            status_icon = "📈"
        elif pnl_pct >= 0:
            status_icon = "➡️"
        elif pnl_pct >= -0.02:
            status_icon = "⚠️"
        else:
            status_icon = "🔴"

        print(
            f"  {status_icon} {ticker:6s} ${current_price:.2f} "
            f"P&L: ${pnl_dollars:+.2f} ({pnl_pct:+.1%}) "
            f"R: {r_multiple:+.2f} Stop: {dist_to_stop:.1%} away"
        )

        trade_update = {
            **trade,
            "ticker": ticker,
            "entry_date": trade.get("entry_date"),
            "entry_price": entry_price,
            "current_price": round(current_price, 2),
            "pnl_dollars": round(pnl_dollars, 2),
            "pnl_pct": round(pnl_pct, 4),
            "r_multiple": round(r_multiple, 2),
            "dist_to_stop": round(dist_to_stop, 4),
            "dist_to_target": round(dist_to_target, 4),
            "stop_price": stop_price,
            "target_2r": target_2r,
            "shares_total": shares,
            # Preserve ledger status; never coerce NEVER_FILLED → OPEN.
            "status": status,
            "last_updated": datetime.now().isoformat(),
        }
        if trade_update["status"] not in OPEN_STATUSES:
            print(f"  Abort upsert {ticker}: status={trade_update['status']}")
            continue
        if trade_update["status"] in TERMINAL_NON_OPEN:
            print(f"  Abort upsert {ticker}: terminal status")
            continue
        # Race guard: re-check Cloud before upsert (TWS may have CLOSED since loop start).
        if entry_date and _supabase_status_is_terminal(sync, ticker, entry_date):
            print(f"  Abort upsert {ticker}: Supabase terminal on re-check")
            continue
        updates.append(trade_update)
        sync.upsert_trade_marks_only(trade_update)

    if volume_healed:
        try:
            trades_path.write_text(
                json.dumps(data, indent=2), encoding="utf-8",
            )
            print("  Healed volume/local paper_trades to match Supabase terminal statuses")
        except Exception as exc:
            print(f"  Volume heal warn: {exc}")

    if updates:
        total_pnl = sum(u["pnl_dollars"] for u in updates)
        print(f"\nTotal unrealized P&L: ${total_pnl:+.2f}")
        sync.log_health(
            "intraday_monitor",
            "OK",
            f"{len(open_trades)} positions | P&L: ${total_pnl:+.2f}",
        )

    print("Intraday monitor complete")


if __name__ == "__main__":
    run_intraday_monitor()
