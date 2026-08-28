# SCHEDULE: 4:15 PM ET weekdays
# modal run candidates/position_monitor.py
"""
Q-Alpha position monitor (Phase 3.4-3.5).

Runs daily after market close. Fills pending MOC entries, checks bracket
exits against Polygon EOD prices, updates pool state and paper_trades.json.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

CANDIDATES_DIR = Path(__file__).resolve().parent
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

import modal

from state_paths import is_trading_day, state_path
from paper_trader import (
    PaperTrade,
    PaperTrader,
    TelegramClient,
    calculate_trade_pnl,
    fetch_eod_bar,
    load_dotenv,
)
from position_sizer import (
    DEFAULT_STARTING_POOL,
    MAX_OPEN_POSITIONS,
    PoolManager,
    get_atr14,
    now_et,
)

app = modal.App("q-alpha-position-monitor")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(["requests", "python-dotenv", "tzdata"])
    .add_local_file(
        local_path=str(CANDIDATES_DIR / "position_sizer.py"),
        remote_path="/root/candidates/position_sizer.py",
    )
    .add_local_file(
        local_path=str(CANDIDATES_DIR / "paper_trader.py"),
        remote_path="/root/candidates/paper_trader.py",
    )
    # paper_trader imports universe_filter at module scope, so this image would
    # fail to import without it.
    .add_local_file(
        local_path=str(CANDIDATES_DIR / "universe_filter.py"),
        remote_path="/root/candidates/universe_filter.py",
    )
)

polygon_secret = modal.Secret.from_name("polygon-api-key")
MAX_HOLD_DAYS = 5
MANAGED_TRADE_SOURCES = frozenset({"telegram_yes", "autonomous_agent"})
# Bracket / EOD only for real opens — never treat rejects as positions.
OPEN_STATUSES = frozenset({
    "OPEN", "T1_HIT", "T2_HIT", "T3_TRAIL", "PENDING_MOC",
})
TERMINAL_NON_OPEN = frozenset({
    "NEVER_FILLED", "REJECTED_INELIGIBLE", "REJECTED_NO_FILL",
    "SKIPPED", "CLOSED",
})


@dataclass
class MonitorEvent:
    """One EOD event for Telegram summary."""

    ticker: str
    event_type: str
    pnl: float = 0.0
    detail: str = ""


def recalculate_pool_from_trades(pool_mgr: PoolManager, all_trades: list[dict]) -> None:
    """
    Rebuild pool cash and deployed capital from the trade ledger.
    Fixes drift when incremental pool updates get out of sync.
    """
    starting_pool = float(pool_mgr.state.get("starting_pool", DEFAULT_STARTING_POOL))
    # NEVER_FILLED / REJECTED_* must not count as deployed capital.
    active_statuses = tuple(OPEN_STATUSES - {"PENDING_MOC"})

    realized_pnl = sum(
        t.get("pnl_dollars", 0) for t in all_trades
        if t.get("status") == "CLOSED"
    )
    deployed = sum(
        t.get("position_value", 0) for t in all_trades
        if str(t.get("status") or "").upper() in active_statuses
        and str(t.get("status") or "").upper() not in TERMINAL_NON_OPEN
    )
    pool = starting_pool + realized_pnl - deployed

    pool_mgr.state["pool"] = round(pool, 2)
    pool_mgr.state["deployed"] = round(deployed, 2)

    print(f"Pool recalculated: ${pool:.2f}")
    print(f"  Starting: ${starting_pool:,.2f}")
    print(f"  Realized P&L: ${realized_pnl:+.2f}")
    print(f"  Deployed: ${deployed:.2f}")


class PositionMonitor:
    """Daily EOD bracket monitor for open paper trades."""

    def __init__(
        self,
        api_key: str,
        pool: PoolManager | None = None,
        trader: PaperTrader | None = None,
        test_mode: bool = False,
    ):
        self.api_key = api_key
        self.pool = pool or PoolManager(state_path=state_path("pool_state.json"))
        self.trader = trader or PaperTrader(pool=self.pool)
        self.test_mode = test_mode
        self.events: list[MonitorEvent] = []
        self.today_pnl = 0.0

    def _update_trade_in_store(self, trade: PaperTrade) -> None:
        for i, t in enumerate(self.trader.trades):
            if (
                t.get("ticker") == trade.ticker
                and t.get("entry_date") == trade.entry_date
                and t.get("approved_by") == trade.approved_by
            ):
                self.trader.trades[i] = trade.to_dict()
                break

    def _record_event(self, trade: PaperTrade, event_type: str, pnl_delta: float,
                      detail: str = "") -> None:
        self.events.append(MonitorEvent(
            ticker=trade.ticker,
            event_type=event_type,
            pnl=pnl_delta,
            detail=detail,
        ))
        self.today_pnl += pnl_delta

    def fill_pending_moc_trades(self) -> None:
        """Fill PENDING_MOC trades with actual EOD close."""
        for raw in list(self.trader.trades):
            if raw.get("status") != "PENDING_MOC":
                continue
            trade = PaperTrade.from_dict(raw)
            try:
                filled = self.trader.fill_moc_entry(trade, self.api_key)
                self._update_trade_in_store(filled)
                print(f"  MOC fill: {filled.ticker} @ ${filled.entry_price:.2f}")
            except Exception as exc:
                print(f"  MOC fill failed {trade.ticker}: {exc}")

    def process_trade(self, raw: dict, bar: dict | None = None) -> PaperTrade:
        """Run full bracket check for one open trade."""
        trade = PaperTrade.from_dict(raw)
        if trade.status not in ("OPEN", "T1_HIT", "T3_TRAIL"):
            return trade

        if bar is None:
            bar = fetch_eod_bar(trade.ticker, self.api_key)
        if bar is None:
            print(f"  No EOD bar for {trade.ticker}")
            return trade

        today = now_et().strftime("%Y-%m-%d")
        today_high = bar["high"]
        today_low = bar["low"]
        today_close = bar["close"]
        trade.days_held += 1

        # Step 2: Stop check first (all remaining shares)
        effective_stop = trade.stop_price
        if trade.status == "T3_TRAIL" and trade.remaining_t3 > 0:
            atr = trade.atr_14 or get_atr14(
                trade.ticker, self.api_key, prev_close=trade.entry_price
            )
            effective_stop = max(trade.stop_price, today_close - atr)
            trade.stop_price = round(effective_stop, 2)

        if today_low <= effective_stop and trade.remaining_shares > 0:
            exit_px = effective_stop
            proceeds = trade.remaining_shares * exit_px
            pnl_delta = trade.remaining_shares * (exit_px - trade.entry_price)
            trade.stop_hit_date = today
            trade.stop_hit_price = exit_px
            if trade.remaining_t3 > 0 and trade.status == "T3_TRAIL":
                trade.tranche_3_exit = exit_px
                trade.exit_reason = "T3_TRAIL"
            else:
                trade.exit_reason = "STOP"
            trade.pnl_dollars, trade.pnl_pct = calculate_trade_pnl(trade)
            trade.remaining_t1 = 0
            trade.remaining_t2 = 0
            trade.remaining_t3 = 0
            trade.status = "CLOSED"
            self.pool.close_tranche(proceeds, is_final_tranche=True)
            self._record_event(
                trade,
                "T3_TRAIL" if trade.exit_reason == "T3_TRAIL" else "STOP",
                pnl_delta,
            )
            return trade

        # ── MFE tracking (all modes) ─────────────────────────────────────
        # Record how far the trade ran in our favor (in R) before exit, so
        # the 2R-vs-trailing-stop question can be answered from real data.
        risk_per_share = trade.entry_price - trade.stop_price
        if risk_per_share > 0:
            fav_r = (today_high - trade.entry_price) / risk_per_share
            if fav_r > trade.mfe_r:
                trade.mfe_r = round(fav_r, 3)
                trade.mfe_price = round(today_high, 2)

        # ── Step 3: SINGLE-BRACKET (2R) exit ─────────────────────────────
        # Books MUST equal broker. TWS holds one 100% stop + one 100% limit
        # at 2R, so the ledger exits 100% at target_2r here. No partial
        # scale-out, no T3 trail -> no books-vs-broker divergence, no
        # phantom STOP-HIT reports.
        if getattr(trade, "bracket_mode", "single_2r") == "single_2r":
            if trade.status == "OPEN" and today_high >= trade.target_2r \
                    and trade.remaining_shares > 0:
                exit_px = trade.target_2r
                proceeds = trade.remaining_shares * exit_px
                pnl_delta = trade.remaining_shares * (exit_px - trade.entry_price)
                trade.tranche_1_exit = exit_px
                trade.remaining_t1 = 0
                trade.remaining_t2 = 0
                trade.remaining_t3 = 0
                trade.status = "CLOSED"
                trade.exit_reason = "TARGET_2R"
                trade.pnl_dollars, trade.pnl_pct = calculate_trade_pnl(trade)
                self.pool.close_tranche(proceeds, is_final_tranche=True)
                if trade.pnl_dollars > 0:
                    self.pool.record_win()
                self._record_event(trade, "TARGET_2R", pnl_delta)
                return trade
            # single-bracket: skip the legacy tranche branches entirely
            if trade.days_held >= MAX_HOLD_DAYS and trade.status == "OPEN":
                exit_px = today_close
                proceeds = trade.remaining_shares * exit_px
                pnl_delta = trade.remaining_shares * (exit_px - trade.entry_price)
                trade.tranche_1_exit = exit_px
                trade.remaining_t1 = 0
                trade.remaining_t2 = 0
                trade.remaining_t3 = 0
                trade.status = "CLOSED"
                trade.exit_reason = "TIME"
                trade.pnl_dollars, trade.pnl_pct = calculate_trade_pnl(trade)
                self.pool.close_tranche(proceeds, is_final_tranche=True)
                if trade.pnl_dollars > 0:
                    self.pool.record_win()
                self._record_event(trade, "TIME", pnl_delta)
            return trade

        # Step 3a: T1 hit
        if trade.status == "OPEN" and trade.remaining_t1 > 0:
            if today_high >= trade.target_1r:
                exit_px = trade.target_1r
                proceeds = trade.remaining_t1 * exit_px
                pnl_delta = trade.remaining_t1 * (exit_px - trade.entry_price)
                trade.tranche_1_exit = exit_px
                trade.remaining_t1 = 0
                trade.status = "T1_HIT"
                self.pool.close_tranche(proceeds, is_final_tranche=False)
                self._record_event(trade, "T1", pnl_delta)

        # Step 3b: T2 hit
        if trade.status == "T1_HIT" and trade.remaining_t2 > 0:
            if today_high >= trade.target_2r:
                exit_px = trade.target_2r
                proceeds = trade.remaining_t2 * exit_px
                pnl_delta = trade.remaining_t2 * (exit_px - trade.entry_price)
                trade.tranche_2_exit = exit_px
                trade.remaining_t2 = 0
                trade.status = "T3_TRAIL"
                self.pool.close_tranche(proceeds, is_final_tranche=False)
                self.pool.promote_to_tranche3()
                self._record_event(trade, "T2", pnl_delta)

        # Step 3c: T3 trailing (ratchet only — exit handled above via stop)
        if trade.status == "T3_TRAIL" and trade.remaining_t3 > 0:
            return trade

        # Step 4: Time exit (5 days, OPEN only)
        if trade.days_held >= MAX_HOLD_DAYS and trade.status == "OPEN":
            exit_px = today_close
            proceeds = trade.remaining_shares * exit_px
            pnl_delta = trade.remaining_shares * (exit_px - trade.entry_price)
            trade.tranche_3_exit = exit_px
            trade.remaining_t1 = 0
            trade.remaining_t2 = 0
            trade.remaining_t3 = 0
            trade.status = "CLOSED"
            trade.exit_reason = "TIME"
            trade.pnl_dollars, trade.pnl_pct = calculate_trade_pnl(trade)
            self.pool.close_tranche(proceeds, is_final_tranche=True)
            if trade.pnl_dollars > 0:
                self.pool.record_win()
            self._record_event(trade, "TIME", pnl_delta)

        if trade.status == "CLOSED" and trade.pnl_dollars == 0:
            trade.pnl_dollars, trade.pnl_pct = calculate_trade_pnl(trade)

        return trade

    def update_reentry_eligible(self) -> None:
        """Add T3_TRAIL tickers to eligible_for_reentry in pool_state."""
        t3_tickers = self.trader.get_t3_trail_tickers()
        eligible = set(self.pool.state.get("eligible_for_reentry", []))
        eligible.update(t3_tickers)
        self.pool.state["eligible_for_reentry"] = sorted(eligible)
        if not self.test_mode:
            self.pool.save_state()

    def run(self) -> dict:
        """Process all trades and persist state."""
        self.events = []
        self.today_pnl = 0.0
        today = now_et().strftime("%Y-%m-%d")

        print(f"EOD Monitor — {today}")
        print(f"  State: {state_path('paper_trades.json')}")
        if not self.test_mode:
            self.fill_pending_moc_trades()

        open_trades = []
        for t in self.trader.trades:
            status = str(t.get("status") or "").upper()
            if status in TERMINAL_NON_OPEN:
                continue
            if (
                status in OPEN_STATUSES
                and t.get("approved_by") in MANAGED_TRADE_SOURCES
            ):
                open_trades.append(t)

        if not open_trades:
            print("  No open trades to monitor")

        for raw in open_trades:
            status = str(raw.get("status") or "").upper()
            if status == "PENDING_MOC":
                continue
            if status not in OPEN_STATUSES or status in TERMINAL_NON_OPEN:
                print(f"  Skip {raw.get('ticker')}: status={status}")
                continue
            updated = self.process_trade(raw)
            self._update_trade_in_store(updated)

        self.update_reentry_eligible()

        if not self.test_mode:
            recalculate_pool_from_trades(self.pool, self.trader.trades)
            self.trader.save()
            self.pool.save_state()

        summary = self.trader.store._compute_summary(self.trader.trades)
        return {
            "date": today,
            "events": [e.__dict__ for e in self.events],
            "today_pnl": round(self.today_pnl, 2),
            "summary": summary,
            "pool": self.pool.state,
        }


def format_eod_telegram(result: dict) -> str:
    """Build EOD Telegram summary message."""
    date = result["date"]
    events = result.get("events", [])
    pool = result.get("pool", {})
    summary = result.get("summary", {})

    if not events and summary.get("open_trades", 0) == 0:
        return "📊 Q-ALPHA EOD: No open positions. Waiting for signals."

    lines = [
        f"📊 Q-ALPHA EOD REPORT — {date}",
        "──────────────────────────────",
    ]

    icons = {
        "T1": "✅",
        "T2": "✅",
        "T3_TRAIL": "🏁",
        "STOP": "🛑",
        "TIME": "⏱",
    }
    labels = {
        "T1": "T1 HIT +1R",
        "T2": "T2 HIT +2R — slot released",
        "T3_TRAIL": "T3 TRAIL EXIT",
        "STOP": "STOP HIT",
        "TIME": "TIME EXIT day 5",
    }

    for ev in events:
        icon = icons.get(ev["event_type"], "•")
        label = labels.get(ev["event_type"], ev["event_type"])
        pnl_str = f"${ev['pnl']:+.2f}"
        lines.append(f"{icon} {ev['ticker']} {label}  ({pnl_str})")

    open_slots = summary.get("open_trades", 0)
    t3_count = pool.get("tranche3_only", 0)
    total_pnl = summary.get("total_pnl", 0)
    win_rate = summary.get("win_rate", 0)
    closed = summary.get("closed_trades", 0)
    wins = int(round(win_rate * closed)) if closed else 0
    losses = closed - wins

    lines.extend([
        "──────────────────────────────",
        f"Open positions:  {open_slots}/{MAX_OPEN_POSITIONS} slots",
        f"T3 trailing:     {t3_count} (free-running)",
        f"Today P&L:       ${result.get('today_pnl', 0):+.2f}",
        f"Total P&L:       ${total_pnl:+.2f}",
        f"Pool:            ${pool.get('pool', 0):,.2f}",
        "──────────────────────────────",
        f"Win rate:        {win_rate:.0%}  ({wins}W / {losses}L)",
    ])
    return "\n".join(lines)


def run_monitor_core(
    api_key: str,
    bot_token: str = "",
    chat_id: str = "",
    test_mode: bool = False,
) -> dict:
    """Execute monitor pipeline."""
    monitor = PositionMonitor(api_key, test_mode=test_mode)
    result = monitor.run()

    if bot_token and chat_id and not test_mode:
        tg = TelegramClient(bot_token, chat_id)
        msg = format_eod_telegram(result)
        tg.send(msg)

    return result


def run_monitor() -> dict | None:
    """
    Full EOD monitor pipeline: fill MOC, check brackets, Telegram report.
    Called by scheduler on Modal or manually via __main__.
    """
    if not is_trading_day():
        today = now_et().date()
        print(f"Market closed today ({today}). Skipping.")
        return None

    load_dotenv()
    api_key = os.environ.get("POLYGON_API_KEY", "")
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not api_key:
        print("ERROR: POLYGON_API_KEY not set")
        return None

    print("=" * 55)
    print("  Q-ALPHA | EOD Position Monitor")
    print("=" * 55)
    result = run_monitor_core(api_key, bot_token, chat_id)
    print(format_eod_telegram(result))

    if result:
        from supabase_sync import sync_to_supabase_safe
        from state_paths import state_path
        import json

        def _sync_eod(sync):
            from datetime import date

            trades_path = state_path("paper_trades.json")
            all_trades = []
            if trades_path.exists():
                data = json.loads(trades_path.read_text(encoding="utf-8"))
                all_trades = data.get("trades", [])

            volume_opens = [
                t for t in all_trades
                if str(t.get("status") or "").upper() in OPEN_STATUSES
                and t.get("approved_by") in MANAGED_TRADE_SOURCES
            ]

            for trade in all_trades:
                if trade.get("approved_by") not in MANAGED_TRADE_SOURCES:
                    continue
                # Pass through NEVER_FILLED / REJECTED_* as-is so Supabase
                # cannot stay OPEN after a local fill-truth correction.
                sync.upsert_trade(trade)

            pool_state = dict(result.get("pool", {}) or {})
            volume_open_n = len(volume_opens)
            pool_open_n = int(pool_state.get("open_positions") or 0)

            skip_pool_snapshot = False
            if volume_open_n == 0 or pool_open_n == 0:
                try:
                    today = date.today().isoformat()
                    supa = (
                        sync.client.table("trades")
                        .select("ticker,status,execution_mode")
                        .eq("entry_date", today)
                        .execute()
                    )
                    supa_opens = [
                        row for row in (supa.data or [])
                        if str(row.get("status") or "").upper() in OPEN_STATUSES
                        and str(row.get("execution_mode") or "") == "IBKR_PAPER"
                    ]
                    if supa_opens and volume_open_n == 0:
                        skip_pool_snapshot = True
                        tickers = ", ".join(
                            str(r.get("ticker") or "?") for r in supa_opens
                        )
                        print(
                            "  EOD stale-volume guard: skip pool_snapshot upsert "
                            f"(Modal volume 0 opens; Supabase OPEN: {tickers})"
                        )
                except Exception as exc:
                    print(f"  EOD stale-volume check warn: {exc}")

            if not skip_pool_snapshot:
                if volume_open_n > 0:
                    pool_state["open_positions"] = volume_open_n
                sync.upsert_pool_snapshot(pool_state)

            open_count = volume_open_n or result.get("summary", {}).get("open_trades", 0)
            sync.log_health(
                "eod_monitor",
                "OK",
                f"{open_count} open positions",
            )

        sync_to_supabase_safe(_sync_eod)

    return result


def run_test_mode(api_key: str) -> None:
    """
    Standalone test: fake SMCI trade vs real Polygon EOD bar.
    Does NOT modify paper_trades.json or pool_state.json.
    """
    print("=" * 55)
    print("  Q-ALPHA | Position Monitor TEST MODE")
    print("  (no files will be modified)")
    print("=" * 55)

    if not api_key:
        print("ERROR: POLYGON_API_KEY required for test")
        return

    fake_trade = PaperTrade(
        ticker="SMCI",
        entry_date=now_et().strftime("%Y-%m-%d"),
        entry_price=34.0,
        stop_price=33.0,
        target_1r=35.0,
        target_2r=36.0,
        target_3r=37.0,
        shares_total=6,
        shares_t1=2,
        shares_t2=2,
        shares_t3=2,
        status="OPEN",
        approved_by="telegram_yes",
        atr_14=1.0,
        days_held=1,
    )

    bar = fetch_eod_bar("SMCI", api_key)
    if bar is None:
        print("ERROR: Could not fetch SMCI EOD bar")
        return

    print(f"\nFake trade: {fake_trade.ticker} entry=${fake_trade.entry_price:.2f} "
          f"stop=${fake_trade.stop_price:.2f}")
    print(f"Real EOD bar: O={bar['open']:.2f} H={bar['high']:.2f} "
          f"L={bar['low']:.2f} C={bar['close']:.2f}")

    pool = PoolManager(state_path=CANDIDATES_DIR / "pool_state_test.json")
    monitor = PositionMonitor(api_key, pool=pool, test_mode=True)
    result_trade = monitor.process_trade(fake_trade.to_dict(), bar=bar)

    print(f"\nResult status:  {result_trade.status}")
    print(f"Exit reason:    {result_trade.exit_reason or 'none'}")
    print(f"Stop price:     ${result_trade.stop_price:.2f}")
    print(f"Days held:      {result_trade.days_held}")
    print(f"P&L:            ${result_trade.pnl_dollars:+.2f} "
          f"({result_trade.pnl_pct:+.2f}%)")

    if monitor.events:
        print("\nEvents:")
        for ev in monitor.events:
            print(f"  {ev.event_type}: {ev.ticker} P&L ${ev.pnl:+.2f}")
    else:
        print("\nNo bracket events triggered today.")

    print("\nTest complete — paper_trades.json unchanged.")


@app.function(
    image=image,
    timeout=600,
    memory=1024,
    secrets=[polygon_secret],
)
def run_position_monitor(bot_token: str = "", chat_id: str = ""):
    """Modal entrypoint for daily EOD monitor."""
    load_dotenv()
    api_key = os.environ.get("POLYGON_API_KEY")
    if not api_key:
        raise RuntimeError("POLYGON_API_KEY not set")
    return run_monitor_core(api_key, bot_token, chat_id)


@app.local_entrypoint()
def main():
    parser = argparse.ArgumentParser(description="Q-Alpha EOD position monitor")
    parser.add_argument("--test", action="store_true", help="Dry-run with fake trade")
    args, _ = parser.parse_known_args()

    load_dotenv()

    if args.test:
        run_test_mode(os.environ.get("POLYGON_API_KEY", ""))
        return

    run_monitor()


if __name__ == "__main__":
    load_dotenv()
    if "--test" in sys.argv:
        run_test_mode(os.environ.get("POLYGON_API_KEY", ""))
    else:
        run_monitor()
