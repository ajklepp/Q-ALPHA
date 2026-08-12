"""
Q-Alpha paper trading layer (Phase 3.3).

Handles Telegram trade approval, simulated MOC fills via Polygon EOD,
and persistent trade state in paper_trades.json.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
import sys

from state_paths import CANDIDATES_DIR, state_path

if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

from position_sizer import (
    MAX_OPEN_POSITIONS,
    MAX_TRADES_PER_DAY,
    PoolManager,
    get_atr14,
    now_et,
)

POLYGON_BASE = "https://api.polygon.io"

FULL_SLOT_STATUSES = frozenset({"OPEN", "T1_HIT", "PENDING_MOC"})
T3_TRAIL_STATUSES = frozenset({"T3_TRAIL"})
OPEN_STATUSES = FULL_SLOT_STATUSES | T3_TRAIL_STATUSES

APPROVAL_PATTERN = re.compile(
    r"^(?P<action>YES|Y|NO|N|SKIP)\s+(?P<ticker>[A-Za-z]{1,5})$",
    re.IGNORECASE,
)


def load_dotenv() -> None:
    """Load repo .env for local runs."""
    env_path = CANDIDATES_DIR.parent / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv as _load
        _load(env_path)
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


def polygon_get(path: str, params: dict, api_key: str, timeout: float = 10.0) -> dict:
    """Polygon REST GET."""
    params = dict(params)
    params["apiKey"] = api_key
    url = f"{POLYGON_BASE}{path}"
    query = "&".join(f"{k}={v}" for k, v in params.items())
    full_url = f"{url}?{query}"

    try:
        import requests
        resp = requests.get(full_url, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except ImportError:
        import urllib.request
        with urllib.request.urlopen(full_url, timeout=timeout) as resp:
            return json.loads(resp.read().decode())


def fetch_eod_bar(ticker: str, api_key: str) -> dict | None:
    """
    Fetch latest completed daily bar for ticker.
    Uses /prev which returns the most recent trading session after close.
    """
    try:
        data = polygon_get(f"/v2/aggs/ticker/{ticker}/prev", {}, api_key)
        results = data.get("results") or []
        if not results:
            end = now_et().date()
            start = end - timedelta(days=7)
            data = polygon_get(
                f"/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}",
                {"adjusted": "true", "sort": "desc", "limit": 1},
                api_key,
            )
            results = data.get("results") or []
        if not results:
            return None
        bar = results[0]
        ts = bar.get("t") or 0
        bar_date = (
            datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
            if ts else now_et().strftime("%Y-%m-%d")
        )
        return {
            "open": float(bar["o"]),
            "high": float(bar["h"]),
            "low": float(bar["l"]),
            "close": float(bar["c"]),
            "volume": float(bar.get("v", 0)),
            "date": bar_date,
        }
    except Exception as exc:
        print(f"  fetch_eod_bar({ticker}) failed: {exc}")
        return None


@dataclass
class PaperTrade:
    """One paper trade from approval through exit."""

    ticker: str
    entry_date: str
    entry_price: float
    stop_price: float
    target_1r: float
    target_2r: float
    target_3r: float
    shares_total: int
    shares_t1: int
    shares_t2: int
    shares_t3: int
    status: str = "PENDING_MOC"
    tranche_1_exit: float | None = None
    tranche_2_exit: float | None = None
    tranche_3_exit: float | None = None
    stop_hit_date: str | None = None
    stop_hit_price: float | None = None
    days_held: int = 0
    pnl_dollars: float = 0.0
    pnl_pct: float = 0.0
    exit_reason: str = ""
    approved_by: str = ""
    order_plan: dict = field(default_factory=dict)
    atr_14: float = 0.0
    remaining_t1: int = 0
    remaining_t2: int = 0
    remaining_t3: int = 0
    position_value: float = 0.0
    skip_reason: str = ""
    ibkr_order_id: int | None = None
    ibkr_status: str = ""
    execution_mode: str = ""

    def __post_init__(self):
        if self.remaining_t1 == 0 and self.shares_t1:
            self.remaining_t1 = self.shares_t1
        if self.remaining_t2 == 0 and self.shares_t2:
            self.remaining_t2 = self.shares_t2
        if self.remaining_t3 == 0 and self.shares_t3:
            self.remaining_t3 = self.shares_t3

    @property
    def remaining_shares(self) -> int:
        return self.remaining_t1 + self.remaining_t2 + self.remaining_t3

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> PaperTrade:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})


def recalculate_brackets(entry_price: float, atr_14: float) -> dict[str, float]:
    """Rebuild stop and targets from actual MOC entry."""
    return {
        "entry_price": round(entry_price, 2),
        "stop_price": round(entry_price - atr_14, 2),
        "target_1r": round(entry_price + atr_14, 2),
        "target_2r": round(entry_price + 2 * atr_14, 2),
        "target_3r": round(entry_price + 3 * atr_14, 2),
    }


def calculate_trade_pnl(trade: PaperTrade) -> tuple[float, float]:
    """Return (pnl_dollars, pnl_pct) from realized exits vs entry."""
    pnl = 0.0
    cost_basis = trade.shares_total * trade.entry_price

    if trade.tranche_1_exit is not None:
        sold_t1 = trade.shares_t1 - trade.remaining_t1
        if sold_t1 > 0:
            pnl += sold_t1 * (trade.tranche_1_exit - trade.entry_price)
    if trade.tranche_2_exit is not None:
        sold_t2 = trade.shares_t2 - trade.remaining_t2
        if sold_t2 > 0:
            pnl += sold_t2 * (trade.tranche_2_exit - trade.entry_price)
    if trade.tranche_3_exit is not None:
        sold_t3 = trade.shares_t3 - trade.remaining_t3
        if sold_t3 > 0:
            pnl += sold_t3 * (trade.tranche_3_exit - trade.entry_price)

    if trade.exit_reason == "STOP" and trade.stop_hit_price is not None:
        pnl += trade.remaining_shares * (trade.stop_hit_price - trade.entry_price)
    elif trade.exit_reason == "TIME" and trade.tranche_3_exit is not None:
        pnl += trade.remaining_shares * (trade.tranche_3_exit - trade.entry_price)

    pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0.0
    return round(pnl, 2), round(pct, 2)


class PaperTradesStore:
    """Load/save paper_trades.json with rolling summary stats."""

    def __init__(self, path: Path | None = None):
        self._path_override = path

    @property
    def path(self) -> Path:
        """Resolve at access time so Modal volume path is used after env bootstrap."""
        return self._path_override or state_path("paper_trades.json")

    def load(self) -> dict:
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            data.setdefault("trades", [])
            data.setdefault("summary", {})
            return data
        return self._empty()

    def _empty(self) -> dict:
        return {
            "trades": [],
            "last_updated": now_et().strftime("%Y-%m-%d %H:%M:%S"),
            "summary": {
                "total_trades": 0,
                "open_trades": 0,
                "closed_trades": 0,
                "total_pnl": 0.0,
                "win_rate": 0.0,
            },
        }

    def save(self, data: dict) -> None:
        data["last_updated"] = now_et().strftime("%Y-%m-%d %H:%M:%S")
        data["summary"] = self._compute_summary(data["trades"])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @staticmethod
    def _compute_summary(trades: list[dict]) -> dict:
        closed = [
            t for t in trades
            if t.get("status") == "CLOSED" and t.get("approved_by") == "telegram_yes"
        ]
        open_trades = [t for t in trades if t.get("status") in OPEN_STATUSES]
        wins = sum(1 for t in closed if t.get("pnl_dollars", 0) > 0)
        total_pnl = sum(t.get("pnl_dollars", 0) for t in closed)
        win_rate = (wins / len(closed)) if closed else 0.0
        return {
            "total_trades": len([
                t for t in trades if t.get("approved_by") == "telegram_yes"
            ]),
            "open_trades": len(open_trades),
            "closed_trades": len(closed),
            "total_pnl": round(total_pnl, 2),
            "win_rate": round(win_rate, 4),
        }


class TelegramClient:
    """Send Telegram messages and fetch replies (single getUpdates call)."""

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = str(chat_id)
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

    def send(self, message: str) -> bool:
        import requests

        if not self.bot_token or not self.chat_id:
            print("  Telegram skipped (credentials missing)")
            print(f"   Token: {os.environ.get('TELEGRAM_BOT_TOKEN', 'NOT FOUND')[:20]}...")
            print(f"   Chat ID: {os.environ.get('TELEGRAM_CHAT_ID', 'NOT FOUND')}")
            return False
        try:
            resp = requests.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": message,
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            resp.raise_for_status()
            print("✅ Telegram sent successfully")
            return True
        except Exception as e:
            print(f"❌ Telegram failed: {e}")
            print(f"   Token: {os.environ.get('TELEGRAM_BOT_TOKEN', 'NOT FOUND')[:20]}...")
            print(f"   Chat ID: {os.environ.get('TELEGRAM_CHAT_ID', 'NOT FOUND')}")
            if hasattr(e, "response") and e.response is not None:
                print(f"   Response: {e.response.text[:200]}")
            return False

    def _load_offset(self) -> int:
        offset_file = state_path("telegram_offset.json")
        if offset_file.exists():
            return int(json.loads(offset_file.read_text()).get("offset", 0))
        return 0

    def _save_offset(self, offset: int) -> None:
        state_path("telegram_offset.json").write_text(
            json.dumps({"offset": offset}), encoding="utf-8")

    def fetch_replies_once(self, pending_candidates: list[dict]) -> dict[str, str]:
        """
        Single getUpdates call — parse YES/NO replies from today.
        Returns {ticker: 'APPROVED'|'SKIPPED'}.
        """
        import requests

        replies: dict[str, str] = {}
        if not pending_candidates:
            return replies

        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo("America/New_York")
        except Exception:
            from datetime import timezone
            tz = timezone(timedelta(hours=-4))

        today = now_et().date()
        today_midnight = datetime.combine(today, datetime.min.time()).replace(tzinfo=tz)
        today_midnight_unix = int(today_midnight.timestamp())

        try:
            resp = requests.get(
                f"{self.base_url}/getUpdates",
                params={"limit": 100, "timeout": 0},
                timeout=10,
            )
            resp.raise_for_status()
            updates = resp.json().get("result", [])
        except Exception as exc:
            print(f"  getUpdates failed: {exc}")
            return replies

        max_update_id = 0
        for update in updates:
            max_update_id = max(max_update_id, update.get("update_id", 0))
            msg = update.get("message") or update.get("edited_message") or {}
            if str(msg.get("chat", {}).get("id")) != self.chat_id:
                continue
            if msg.get("date", 0) < today_midnight_unix:
                continue

            text = (msg.get("text") or "").upper().strip()
            match = APPROVAL_PATTERN.match(text)
            if match:
                action = match.group("action").upper()
                ticker = match.group("ticker").upper()
                if action in ("YES", "Y"):
                    replies[ticker] = "APPROVED"
                else:
                    replies[ticker] = "SKIPPED"
                continue

            for candidate in pending_candidates:
                ticker = candidate["ticker"].upper()
                if ticker in replies:
                    continue
                if f"YES {ticker}" in text or text == f"Y {ticker}":
                    replies[ticker] = "APPROVED"
                elif (
                    f"NO {ticker}" in text
                    or text == f"N {ticker}"
                    or f"SKIP {ticker}" in text
                ):
                    replies[ticker] = "SKIPPED"

        if max_update_id:
            self._save_offset(max_update_id + 1)

        return replies


class PaperTrader:
    """Trade approval workflow and paper trade lifecycle."""

    def __init__(
        self,
        store: PaperTradesStore | None = None,
        pool: PoolManager | None = None,
    ):
        self.store = store or PaperTradesStore()
        self.pool = pool or PoolManager(state_path=state_path("pool_state.json"))
        self._data = self.store.load()

    @property
    def trades(self) -> list[dict]:
        return self._data["trades"]

    def reload(self) -> None:
        self._data = self.store.load()

    def save(self) -> None:
        self.store.save(self._data)

    def _load_trades(self) -> list:
        return self.trades

    def get_open_full_positions(self) -> list:
        """Returns list of tickers with full open positions
        (T1 or T2 not yet hit — occupying a slot).
        T3_TRAIL positions are excluded (slot released)."""
        trades = self._load_trades()
        return [
            t["ticker"] for t in trades
            if t.get("status") in ("OPEN", "T1_HIT")
        ]

    def get_trades_today_count(self) -> int:
        """Returns number of trades opened today."""
        from datetime import date
        today = date.today().isoformat()
        trades = self._load_trades()
        return sum(
            1 for t in trades
            if t.get("entry_date", "").startswith(today)
            and t.get("approved_by") == "telegram_yes"
        )

    def get_t3_trail_tickers(self) -> set[str]:
        return {
            t["ticker"].upper()
            for t in self.trades
            if t.get("status") in T3_TRAIL_STATUSES
        }

    def get_pending_moc_count(self) -> int:
        return sum(1 for t in self.trades if t.get("status") == "PENDING_MOC")

    def slots_in_use(self) -> int:
        return self.pool.open_positions + self.get_pending_moc_count()

    def log_skip(
        self,
        ticker: str,
        order_plan: dict,
        approved_by: str,
        skip_reason: str = "",
    ) -> None:
        """Record a skipped signal."""
        trade = PaperTrade(
            ticker=ticker,
            entry_date=now_et().strftime("%Y-%m-%d"),
            entry_price=order_plan.get("entry_price", 0),
            stop_price=order_plan.get("stop_price", 0),
            target_1r=order_plan.get("target_1r", 0),
            target_2r=order_plan.get("target_2r", 0),
            target_3r=order_plan.get("target_3r", 0),
            shares_total=order_plan.get("shares", 0),
            shares_t1=order_plan.get("tranche_1_shares", 0),
            shares_t2=order_plan.get("tranche_2_shares", 0),
            shares_t3=order_plan.get("tranche_3_shares", 0),
            status="SKIPPED",
            approved_by=approved_by,
            order_plan=order_plan,
            skip_reason=skip_reason,
        )
        self.trades.append(trade.to_dict())
        self.save()

    def create_pending_trade(
        self,
        candidate: dict,
        order_plan: dict,
        atr_14: float,
    ) -> PaperTrade:
        """Create approved trade pending MOC fill at EOD."""
        return PaperTrade(
            ticker=candidate["ticker"],
            entry_date=now_et().strftime("%Y-%m-%d"),
            entry_price=order_plan["entry_price"],
            stop_price=order_plan["stop_price"],
            target_1r=order_plan["target_1r"],
            target_2r=order_plan["target_2r"],
            target_3r=order_plan["target_3r"],
            shares_total=order_plan["shares"],
            shares_t1=order_plan["tranche_1_shares"],
            shares_t2=order_plan["tranche_2_shares"],
            shares_t3=order_plan["tranche_3_shares"],
            status="PENDING_MOC",
            approved_by="telegram_yes",
            order_plan=order_plan,
            atr_14=atr_14,
            position_value=order_plan["position_value"],
        )

    def fill_moc_entry(self, trade: PaperTrade, api_key: str) -> PaperTrade:
        """
        Fill pending MOC trade with Polygon EOD close.
        Recalculates brackets from actual entry; reserves pool capital.
        """
        bar = fetch_eod_bar(trade.ticker, api_key)
        if bar is None:
            raise RuntimeError(f"No EOD bar for {trade.ticker}")

        brackets = recalculate_brackets(bar["close"], trade.atr_14)
        trade.entry_price = brackets["entry_price"]
        trade.stop_price = brackets["stop_price"]
        trade.target_1r = brackets["target_1r"]
        trade.target_2r = brackets["target_2r"]
        trade.target_3r = brackets["target_3r"]
        trade.position_value = round(trade.shares_total * trade.entry_price, 2)
        trade.status = "OPEN"
        trade.days_held = max(trade.days_held, 1)
        return trade

    @staticmethod
    def format_approval_message(candidate: dict, pool: PoolManager) -> str:
        """Build per-ticker Telegram approval message."""
        plan = candidate["order_plan"]
        ticker = candidate["ticker"]
        gap = candidate["gap_estimate"]
        vol = candidate["pm_vol_ratio"]
        headline = candidate.get("news_headline") or "No news catalyst found"
        entry = plan["entry_price"]
        stop = plan["stop_price"]
        t2 = plan["target_2r"]
        risk_per = entry - stop
        reward_per = t2 - entry
        total_risk = plan["risk_dollars"]
        risk_pct = (total_risk / pool.pool * 100) if pool.pool > 0 else 0
        slots_free = MAX_OPEN_POSITIONS - pool.open_positions
        t1 = plan["tranche_1_shares"]
        t2s = plan["tranche_2_shares"]
        t3 = plan["tranche_3_shares"]

        lines = [
            f"🚨 Q-ALPHA TRADE SIGNAL — {ticker}",
            f"Entry ~${entry:.2f} | Stop ${stop:.2f} | Target ${t2:.2f}",
            f"Shares: {plan['shares']} | Risk: ${total_risk:.0f} | R/R: {plan['rr_ratio']:.1f}x",
            "",
            f"Reply: YES {ticker} or NO {ticker}",
            "⏱ Expires at market open (9:30 AM)",
        ]
        return "\n".join(lines)

    def queue_pending_approvals(
        self,
        candidates: list[dict],
        scan_date: str,
        bot_token: str,
        chat_id: str,
    ) -> int:
        """
        Save pending_approvals.json and send Telegram alerts (no waiting).
        Returns number of alerts sent.
        """
        valid = [c for c in candidates if c.get("order_plan", {}).get("valid")]
        if not valid:
            print("  No valid order plans to queue")
            return 0

        payload = {
            "date": scan_date,
            "expires": f"{scan_date}T09:30:00",
            "candidates": [
                {
                    "ticker": c["ticker"],
                    "order_plan": c["order_plan"],
                    "atr_14": c.get("atr_14"),
                    "gap_estimate": c.get("gap_estimate"),
                    "pm_vol_ratio": c.get("pm_vol_ratio"),
                    "news_headline": c.get("news_headline"),
                    "prev_close": c.get("prev_close"),
                    "premarket_price": c.get("premarket_price"),
                    "status": "PENDING",
                }
                for c in valid
            ],
        }
        pending_path = state_path("pending_approvals.json")
        pending_path.parent.mkdir(parents=True, exist_ok=True)
        pending_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"  Saved {len(valid)} pending approval(s) to {pending_path}")

        tg = TelegramClient(bot_token, chat_id)
        sent = 0
        for candidate in valid:
            msg = self.format_approval_message(candidate, self.pool)
            if tg.send(msg):
                sent += 1
            print(f"  Alert sent: {candidate['ticker']}")
        return sent

    def approve_trade(
        self,
        candidate: dict,
        api_key: str,
    ) -> PaperTrade | None:
        """Create funded PENDING_MOC trade after user approval."""
        plan = candidate["order_plan"]
        atr = candidate.get("atr_14") or get_atr14(
            candidate["ticker"],
            api_key,
            prev_close=candidate.get("prev_close"),
        )
        if not self.pool.can_open_trade() or self.slots_in_use() >= MAX_OPEN_POSITIONS:
            return None
        if not self.pool.can_open_today(self.get_trades_today_count()):
            return None

        trade = self.create_pending_trade(candidate, plan, atr)

        try:
            from candidates.ibkr_connector import IBKRConnector

            connector = IBKRConnector()
            if connector.connect(paper=True):
                order_result = connector.place_bracket_order(plan)
                trade.ibkr_order_id = order_result["parent_id"]
                trade.ibkr_status = order_result["status"]
                trade.execution_mode = "IBKR_PAPER"
                connector.disconnect()
                print(f"✅ IBKR bracket order placed: {order_result['parent_id']}")
            else:
                trade.execution_mode = "SIMULATION_FALLBACK"
                print("⚠️ TWS not available — falling back to simulation")
        except Exception as e:
            trade.execution_mode = "SIMULATION_FALLBACK"
            print(f"⚠️ IBKR error: {e} — falling back to simulation")

        self.pool.open_trade(trade.position_value)
        self.pool.save_state()
        self.trades.append(trade.to_dict())
        self.save()
        return trade

    def filter_candidates_for_trading(self, candidates: list[dict]) -> list[dict]:
        """Apply pool capacity, open-position, and daily trade limits."""
        if not self.pool.can_open_trade():
            print("  Pool at capacity — no new trades today")
            return []

        open_tickers = {t.upper() for t in self.get_open_full_positions()}
        filtered = [
            c for c in candidates
            if c["ticker"].upper() not in open_tickers
            and c.get("order_plan", {}).get("valid")
        ]

        trades_today = self.get_trades_today_count()
        if not self.pool.can_open_today(trades_today):
            print(f"  Daily trade limit reached ({trades_today}/{MAX_TRADES_PER_DAY})")
            return []

        available = min(
            MAX_TRADES_PER_DAY - trades_today,
            MAX_OPEN_POSITIONS - self.slots_in_use(),
        )
        return filtered[:max(0, available)]


def load_paper_trader(pool_state: dict | None = None) -> PaperTrader:
    """Factory with optional pool state override."""
    pool = PoolManager(state_path=state_path("pool_state.json"), initial_state=pool_state)
    return PaperTrader(pool=pool)


def run_approval_processor() -> None:
    """
    9:25 AM job: read pending_approvals.json, check Telegram once, log trades.
    No polling loops — completes in seconds.
    """
    from state_paths import is_trading_day

    if not is_trading_day():
        print(f"Market closed today ({now_et().date()}). Skipping.")
        return

    load_dotenv()
    api_key = os.environ.get("POLYGON_API_KEY", "")
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    pending_path = state_path("pending_approvals.json")
    if not pending_path.exists():
        print("No pending approvals")
        return

    data = json.loads(pending_path.read_text(encoding="utf-8"))
    pending = [c for c in data.get("candidates", []) if c.get("status") == "PENDING"]
    if not pending:
        print("No pending approvals")
        return

    print("=" * 55)
    print("  Q-ALPHA | Approval Processor")
    print("=" * 55)
    print(f"  Processing {len(pending)} pending approval(s)...")

    trader = load_paper_trader()
    tg = TelegramClient(bot_token, chat_id)
    replies = tg.fetch_replies_once(pending)
    print(f"  Telegram replies parsed: {replies}")

    for candidate in pending:
        ticker = candidate["ticker"].upper()
        plan = candidate["order_plan"]
        decision = replies.get(ticker)

        if decision == "APPROVED":
            trade = trader.approve_trade(candidate, api_key)
            if trade:
                candidate["status"] = "APPROVED"
                if trade.execution_mode == "IBKR_PAPER":
                    tg.send(
                        f"✅ {ticker} APPROVED — IBKR ORDER PLACED\n"
                        f"Order ID: {trade.ibkr_order_id}\n"
                        f"Entry: ${trade.entry_price:.2f} | Stop: ${trade.stop_price:.2f} | "
                        f"Target: ${trade.target_2r:.2f}"
                    )
                else:
                    tg.send(
                        f"✅ {ticker} APPROVED — SIMULATION MODE\n"
                        f"(TWS unavailable — tracked in paper_trades.json)"
                    )
                print(f"  {ticker}: APPROVED")
            else:
                trader.log_skip(ticker, plan, "telegram_yes", "pool at capacity")
                candidate["status"] = "SKIPPED"
                tg.send(f"⏭ {ticker} skipped (pool at capacity)")
                print(f"  {ticker}: skipped (pool at capacity)")
        elif decision == "SKIPPED":
            trader.log_skip(ticker, plan, "manual_skip", "manual skip")
            candidate["status"] = "SKIPPED"
            tg.send(f"⏭ {ticker} skipped")
            print(f"  {ticker}: skipped by user")
        else:
            trader.log_skip(ticker, plan, "no_response", "no reply before market open")
            candidate["status"] = "EXPIRED"
            tg.send(f"⏱ {ticker} auto-skipped (no reply)")
            print(f"  {ticker}: auto-skipped (no reply)")

    data["processed_at"] = now_et().strftime("%Y-%m-%d %H:%M:%S")
    pending_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    trader.pool.save_state()
    print("Approval processor complete")
