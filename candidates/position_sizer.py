"""
Q-Alpha position sizing calculator (Phase 3.2).

Computes entry/stop/target prices, share counts, and tranche splits
for incoming gap-day signals. Imported by scanner and execution layers.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from state_paths import CANDIDATES_DIR, state_path as get_state_path

POOL_STATE_FILE = get_state_path("pool_state.json")
POLYGON_BASE = "https://api.polygon.io"

DEFAULT_STARTING_POOL = 3000.0
MAX_OPEN_POSITIONS = 10
MAX_TRADES_PER_DAY = 3
MAX_DEPLOYMENT_PCT = 0.80
POSITION_PCT = 0.10
POSITION_PCT_HARD_MAX = 0.15
MIN_UNIT_PCT = 0.05
MAX_RISK_PCT = 0.05
REDUCED_RISK_PCT = 0.02
TRANCHE_PCT = 0.33
HALT_POOL = 2000.0
WARN_POOL = 2500.0
ATR_FALLBACK_PCT = 0.03
ATR_LOOKBACK_DAYS = 14
ATR_FETCH_CALENDAR_DAYS = 20
ATR_STOP_MULTIPLIER = 1.5
MAX_STOP_PCT = 0.08  # never more than 8% stop
MIN_STOP_PCT = 0.02  # never less than 2% stop

# Pool-scaled sizing: up to 10 concurrent full positions; shares always ÷4
# so a future 4-tier trailing stop can split evenly. Price ceiling is dynamic:
# max_affordable_price = (pool / 10) / 4  — at $3000 pool that is $75.
SHARE_LOT = 4
MIN_TRADE_SHARES = SHARE_LOT
SCAN_MIN_PRICE = 5.00  # scanner floor (unchanged); ceiling is dynamic


def per_trade_target(pool: float) -> float:
    """Dollar budget for one full position (= pool / max concurrent slots)."""
    return float(pool) / MAX_OPEN_POSITIONS


def max_affordable_price(pool: float) -> float:
    """
    DYNAMIC scanner price ceiling: 4 shares must fit in the per-trade pot.
    At pool=$3000 → $300/4 = $75. Recomputed from live pool each run.
    """
    return per_trade_target(pool) / SHARE_LOT


def compute_shares(pool: float, price: float) -> int:
    """
    Largest multiple of SHARE_LOT that fits in per_trade_target at `price`.

    Returns 0 when fewer than MIN_TRADE_SHARES (4) fit — caller must SKIP
    ("4 shares exceeds per-trade pot"). Guarantees shares % 4 == 0 when > 0
    so a future 4-tier stop can trust even splits.
    """
    if pool <= 0 or price <= 0:
        return 0
    target = per_trade_target(pool)
    max_by_target = math.floor(target / price)
    shares = (max_by_target // SHARE_LOT) * SHARE_LOT
    if shares < MIN_TRADE_SHARES:
        return 0
    return int(shares)


def load_pool_value(state_path: Path | None = None) -> float:
    """
    Current pool dollars from pool_state.json (the `pool` field — not peak).
    Used by scanners to recompute the dynamic price ceiling each run.
    """
    path = state_path or get_state_path("pool_state.json")
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return float(data.get("pool", DEFAULT_STARTING_POOL))
    return DEFAULT_STARTING_POOL


def now_et() -> datetime:
    """Current time in US/Eastern."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        from datetime import timezone
        return datetime.now(timezone(timedelta(hours=-4)))


def default_pool_state() -> dict:
    """Fresh pool state for a new $3,000 account."""
    today = now_et().strftime("%Y-%m-%d")
    return {
        "pool": DEFAULT_STARTING_POOL,
        "starting_pool": DEFAULT_STARTING_POOL,
        "deployed": 0.0,
        "peak_pool": DEFAULT_STARTING_POOL,
        "total_trades": 0,
        "winning_trades": 0,
        "open_positions": 0,
        "tranche3_only": 0,
        "eligible_for_reentry": [],
        "last_updated": today,
    }


class PoolManager:
    """
    Tracks pool value, open slots, and deployed capital.
    Persists to candidates/pool_state.json between runs.
    """

    def __init__(self, state_path: Path | None = None,
                 initial_state: dict | None = None):
        self.state_path = state_path or get_state_path("pool_state.json")
        if initial_state is not None:
            merged = default_pool_state()
            merged.update(initial_state)
            self.state = merged
        else:
            self.state = self.load_state()

    @property
    def pool(self) -> float:
        return float(self.state["pool"])

    @property
    def deployed(self) -> float:
        return float(self.state["deployed"])

    @property
    def open_positions(self) -> int:
        return int(self.state["open_positions"])

    def load_state(self) -> dict:
        """Load pool state from JSON or create default."""
        if self.state_path.exists():
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            for key, value in default_pool_state().items():
                data.setdefault(key, value)
            return data
        state = default_pool_state()
        self.state = state
        self.save_state()
        return state

    def save_state(self) -> None:
        """Persist current pool state to JSON."""
        self.state["last_updated"] = now_et().strftime("%Y-%m-%d %H:%M:%S")
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, indent=2), encoding="utf-8")

    def min_position_size(self) -> float:
        """Half a unit — hard floor (5% of pool)."""
        return self.pool * MIN_UNIT_PCT

    def position_size(self) -> float:
        """10% of pool, capped at 15% hard max."""
        return min(self.pool * POSITION_PCT, self.pool * POSITION_PCT_HARD_MAX)

    def can_open_trade(self) -> bool:
        """True if slots, cash, and deployment limits allow a new trade."""
        if self.open_positions >= MAX_OPEN_POSITIONS:
            return False
        if self.pool < self.min_position_size():
            return False
        if self.deployed >= self.pool * MAX_DEPLOYMENT_PCT:
            return False
        return True

    def can_open_today(self, trades_today: int) -> bool:
        """True if fewer than 3 trades opened today."""
        return trades_today < MAX_TRADES_PER_DAY

    def halt_check(self) -> bool:
        """True if trading should halt (pool below $2,000)."""
        if self.pool < WARN_POOL:
            print(f"  WARNING: pool ${self.pool:,.2f} below ${WARN_POOL:,.0f}")
        if self.pool < HALT_POOL:
            print(f"  HALT: pool ${self.pool:,.2f} below ${HALT_POOL:,.0f}")
            return True
        return False

    def open_trade(self, size: float) -> None:
        """Reserve capital for a new position."""
        self.state["pool"] = round(self.pool - size, 2)
        self.state["deployed"] = round(self.deployed + size, 2)
        self.state["open_positions"] = self.open_positions + 1
        self.state["total_trades"] = int(self.state["total_trades"]) + 1
        total_value = self.pool + self.deployed
        self.state["peak_pool"] = max(float(self.state["peak_pool"]), total_value)
        self.save_state()

    def close_tranche(self, proceeds: float, is_final_tranche: bool) -> None:
        """Return proceeds to pool; release slot on final tranche exit."""
        self.state["pool"] = round(self.pool + proceeds, 2)
        self.state["deployed"] = round(max(0.0, self.deployed - proceeds), 2)
        if is_final_tranche:
            self.state["open_positions"] = max(0, self.open_positions - 1)
            self.state["tranche3_only"] = max(
                0, int(self.state["tranche3_only"]) - 1
            )
        self.save_state()

    def promote_to_tranche3(self) -> None:
        """Move position to tranche-3-only (releases full slot)."""
        self.state["open_positions"] = max(0, self.open_positions - 1)
        self.state["tranche3_only"] = int(self.state["tranche3_only"]) + 1
        self.save_state()

    def record_win(self) -> None:
        """Increment winning trade counter."""
        self.state["winning_trades"] = int(self.state["winning_trades"]) + 1
        self.save_state()


@dataclass
class SignalInput:
    """Incoming gap-day signal for position sizing."""

    ticker: str
    prev_close: float
    premarket_price: float
    atr_14: float
    gap_pct: float
    vix_regime: str
    spy_regime: str


@dataclass
class OrderPlan:
    """Sized order with bracket prices and tranche breakdown."""

    ticker: str = ""
    entry_price: float = 0.0
    stop_price: float = 0.0
    target_1r: float = 0.0
    target_2r: float = 0.0
    target_3r: float = 0.0
    position_value: float = 0.0
    shares: int = 0
    tranche_1_shares: int = 0
    tranche_2_shares: int = 0
    tranche_3_shares: int = 0
    risk_dollars: float = 0.0
    reward_dollars: float = 0.0
    rr_ratio: float = 0.0
    vix_adj: float = 1.0
    valid: bool = False
    skip_reason: str = ""


def compute_stop_distance(entry: float, atr_14: float) -> float:
    """
    ATR stop distance with min/max pct guards.
    Uses 1.5× ATR (Option D) capped at 8% and floored at 2% of entry.
    """
    stop_distance = min(ATR_STOP_MULTIPLIER * atr_14, entry * MAX_STOP_PCT)
    stop_distance = max(stop_distance, entry * MIN_STOP_PCT)
    return stop_distance


class PositionSizer:
    """Convert a signal + pool state into an executable order plan."""

    def calculate(self, signal: SignalInput, pool: PoolManager) -> OrderPlan:
        """Size a trade with ATR bracket stops; shares always divisible by 4."""
        vix_adj = 0.5 if signal.vix_regime == "ELEVATED" else 1.0
        entry_est = signal.premarket_price

        stop_distance = compute_stop_distance(entry_est, signal.atr_14)
        stop = entry_est - stop_distance
        t1 = entry_est + (1.0 * signal.atr_14)
        t2 = entry_est + (2.0 * signal.atr_14)
        t3 = entry_est + (3.0 * signal.atr_14)

        if stop <= 0 or signal.atr_14 <= 0:
            return OrderPlan(
                ticker=signal.ticker,
                valid=False,
                skip_reason="Invalid ATR or stop price",
            )

        # VIX still scales the effective pool for sizing; shares always ÷4.
        sized_pool = pool.pool * vix_adj
        shares = compute_shares(sized_pool, entry_est)
        if shares < MIN_TRADE_SHARES:
            return OrderPlan(
                ticker=signal.ticker,
                valid=False,
                skip_reason=f"SKIP {signal.ticker}: 4 shares exceeds per-trade pot",
            )

        risk_per_share = stop_distance
        total_risk = shares * risk_per_share

        if total_risk > pool.pool * MAX_RISK_PCT:
            reduced = math.floor((pool.pool * REDUCED_RISK_PCT) / risk_per_share)
            shares = (reduced // SHARE_LOT) * SHARE_LOT
            if shares < MIN_TRADE_SHARES:
                return OrderPlan(
                    ticker=signal.ticker,
                    valid=False,
                    skip_reason="ATR too wide for account size",
                )
            total_risk = shares * risk_per_share

        # Single-bracket 2R for now: 100% in T1; T2/T3 zero (agent path).
        # Legacy tranche split kept only when TRANCHE_PCT path is desired —
        # here we still expose 3 fields but keep shares % 4 == 0 overall.
        t1_shares = int(shares * TRANCHE_PCT)
        t2_shares = int(shares * TRANCHE_PCT)
        t3_shares = shares - t1_shares - t2_shares

        reward_dollars = shares * (t2 - entry_est)
        rr_ratio = (t2 - entry_est) / risk_per_share if risk_per_share > 0 else 0.0

        return OrderPlan(
            ticker=signal.ticker,
            entry_price=round(entry_est, 2),
            stop_price=round(stop, 2),
            target_1r=round(t1, 2),
            target_2r=round(t2, 2),
            target_3r=round(t3, 2),
            position_value=round(shares * entry_est, 2),
            shares=shares,
            tranche_1_shares=t1_shares,
            tranche_2_shares=t2_shares,
            tranche_3_shares=t3_shares,
            risk_dollars=round(total_risk, 2),
            reward_dollars=round(reward_dollars, 2),
            rr_ratio=round(rr_ratio, 2),
            vix_adj=vix_adj,
            valid=True,
            skip_reason="",
        )


def get_atr14(
    ticker: str,
    api_key: str,
    prev_close: float | None = None,
    timeout: float = 5.0,
) -> float:
    """
    Fetch daily bars from Polygon and compute 14-day average true range.
    Excludes today's bar so gap-day volatility does not inflate ATR.
    Falls back to 3% of prev_close on error.
    """
    fallback = (prev_close or 10.0) * ATR_FALLBACK_PCT
    try:
        import requests

        end = now_et().date()
        start = end - timedelta(days=ATR_FETCH_CALENDAR_DAYS)
        url = f"{POLYGON_BASE}/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}"
        resp = requests.get(
            url,
            params={
                "adjusted": "true",
                "sort": "asc",
                "limit": 50000,
                "apiKey": api_key,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        bars = resp.json().get("results", [])
        if len(bars) < ATR_LOOKBACK_DAYS + 2:
            return fallback

        # Exclude today's gap day — use only prior sessions for ATR
        bars = bars[:-1]

        true_ranges = []
        for i in range(1, len(bars)):
            high = float(bars[i]["h"])
            low = float(bars[i]["l"])
            prev_c = float(bars[i - 1]["c"])
            tr = max(high - low, abs(high - prev_c), abs(low - prev_c))
            true_ranges.append(tr)

        if len(true_ranges) < ATR_LOOKBACK_DAYS:
            return fallback

        atr14 = sum(true_ranges[-ATR_LOOKBACK_DAYS:]) / ATR_LOOKBACK_DAYS
        return round(atr14, 4)
    except Exception:
        return round(fallback, 4)


def order_plan_to_dict(plan: OrderPlan) -> dict:
    """Serialize OrderPlan for JSON output."""
    return asdict(plan)


def _load_dotenv():
    """Load POLYGON_API_KEY from repo .env if present."""
    env_path = CANDIDATES_DIR.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _print_order_plan(plan: OrderPlan, pool_before: float, pool_after: float,
                      open_slots: int, trades_today: int) -> None:
    """Pretty-print order plan for standalone test."""
    entry = plan.entry_price
    stop_delta = entry - plan.stop_price
    t1_delta = plan.target_1r - entry
    t2_delta = plan.target_2r - entry
    t3_delta = plan.target_3r - entry
    pool = pool_before
    risk_pct = (plan.risk_dollars / pool * 100) if pool > 0 else 0.0
    reward_pct = (plan.reward_dollars / pool * 100) if pool > 0 else 0.0
    vix_label = "ELEVATED" if plan.vix_adj == 0.5 else "NORMAL"

    print("=" * 34)
    print(f"ORDER PLAN — {plan.ticker}")
    print("=" * 34)
    print(f"Entry (est):    ${entry:.2f}")
    print(f"Stop:           ${plan.stop_price:.2f}  (-${stop_delta:.2f})")
    print(f"Target 1R:      ${plan.target_1r:.2f}  (+${t1_delta:.2f})")
    print(f"Target 2R:      ${plan.target_2r:.2f}  (+${t2_delta:.2f})")
    print(f"Target 3R:      ${plan.target_3r:.2f}  (+${t3_delta:.2f})")
    print("-" * 34)
    print(f"Shares:         {plan.shares}")
    print(f"Tranche 1:      {plan.tranche_1_shares} shares → sell at ${plan.target_1r:.2f}")
    print(f"Tranche 2:      {plan.tranche_2_shares} shares → sell at ${plan.target_2r:.2f}")
    print(f"Tranche 3:      {plan.tranche_3_shares} shares → trail from ${plan.target_3r:.2f}")
    print("-" * 34)
    print(f"Position value: ${plan.position_value:.2f}")
    print(f"Risk:           ${plan.risk_dollars:.2f}  ({risk_pct:.2f}% of pool)")
    print(f"Reward (2R):    ${plan.reward_dollars:.2f}  ({reward_pct:.2f}% of pool)")
    print(f"R/R Ratio:      {plan.rr_ratio:.1f}x")
    print(f"VIX adjustment: {plan.vix_adj:.1f}x ({vix_label})")
    print("=" * 34)
    print(f"Pool: ${pool_before:,.2f} → ${pool_after:,.2f} after deployment")
    print(f"Open slots: {open_slots}/{MAX_OPEN_POSITIONS} used")
    print(f"New trades today: {trades_today}/{MAX_TRADES_PER_DAY}")


if __name__ == "__main__":
    _load_dotenv()

    test_prev_close = 31.60
    test_premarket = 33.99
    test_gap = 0.076
    test_atr = 1.02

    api_key = os.environ.get("POLYGON_API_KEY", "")
    if api_key:
        test_atr = get_atr14("SMCI", api_key, prev_close=test_prev_close)
        print(f"Fetched ATR14 for SMCI: ${test_atr:.4f}")
    else:
        print(f"No POLYGON_API_KEY — using fallback ATR: ${test_atr:.4f}")

    signal = SignalInput(
        ticker="SMCI",
        prev_close=test_prev_close,
        premarket_price=test_premarket,
        atr_14=test_atr,
        gap_pct=test_gap,
        vix_regime="NORMAL",
        spy_regime="BULL",
    )

    test_state_path = CANDIDATES_DIR / "pool_state_test.json"
    pool_mgr = PoolManager(state_path=test_state_path)
    sizer = PositionSizer()
    pool_before = pool_mgr.pool

    plan = sizer.calculate(signal, pool_mgr)
    if not plan.valid:
        print(f"INVALID PLAN: {plan.skip_reason}")
    else:
        pool_after = pool_before - plan.position_value
        open_slots = pool_mgr.open_positions + 1
        trades_today = 1
        _print_order_plan(plan, pool_before, pool_after, open_slots, trades_today)

        print("\nPool state BEFORE:")
        print(json.dumps(pool_mgr.state, indent=2))
        pool_mgr.open_trade(plan.position_value)
        print("\nPool state AFTER open_trade():")
        print(json.dumps(pool_mgr.state, indent=2))
