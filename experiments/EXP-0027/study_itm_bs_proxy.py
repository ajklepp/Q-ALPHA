"""
EXP-0027 — ITM long-call Black–Scholes proxy (research only).

Label: model_proxy_pnl
This is NOT gospel, NOT broker fills, NOT live paper, NOT Track 100.

Premiums are European Black–Scholes values. They are not Polygon option
OHLC and they are not IBKR fills. IV is frozen from the most recent window
(IBKR option marks via the laptop bridge when that bridge is up; otherwise
realized vol of the last 10 stock sessions) and applied backward onto past
stock entries. That is a pricing sketch, not a causal forecast.

Rules follow the ITM long-call spirit already written for this folder:
5–12% ITM, target ~8%, 21–45 DTE, underlying ATR stop, +1R moves the next
session's stop to entry, +2R, half the model premium, 5 sessions. Long calls
only. One contract or skip when the debit is above $5,000.

Laptop (one command, from the repo root):

    cd C:\\Users\\ajkle\\Documents\\Q-ALPHA
    .\\experiments\\EXP-0027\\run_on_laptop.ps1

After the cash close, /v1/underlying/quote often has no last or mid. The
script then takes spot from stock hist last close, then from the study's
last daily close, and reads call marks from hist plus one option quote.

Stock bars: POLYGON_API_KEY, else a short Modal fetch using the
polygon-api-key secret (q-alpha-secrets is attached and not read), else
Yahoo chart daily OHLC labeled as a fallback. No yfinance import.
"""
from __future__ import annotations

import json
import math
import os
import random
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

APP_NAME = "q-alpha-exp027-itm-bs-proxy"
_HERE = Path(__file__).resolve()
EXP_DIR = _HERE.parent
OUT_MD = EXP_DIR / "results_bs_proxy.md"
OUT_JSON = EXP_DIR / "results_bs_proxy.json"

POLYGON = "https://api.polygon.io"
ET = ZoneInfo("America/New_York")
USER_AGENT = "q-alpha-exp0027-bs-proxy/1.0"

# Equity cost from EXP-0012. Applied to the 100-share stock baseline.
# The option headline is the raw BS mark. A 0.15% debit haircut is reported
# beside it and is not a bid/ask or a commission schedule.
COST_PER_TRADE = 0.0015

BOOK_USD = 5000.0  # one cash pool, one contract
CONTRACT_MULTIPLIER = 100
ITM_TARGET_PCT = 0.08  # strike ~= prior close * 0.92
ITM_MIN_PCT = 0.05
ITM_MAX_PCT = 0.12
DTE_MIN = 21
DTE_MAX = 45
DTE_TARGET = 32  # nearest Friday inside the DTE window
DTE_EXIT = 7
MAX_HOLD_SESSIONS = 5
ATR_WINDOW = 14
SMA_FAST = 20
SMA_REGIME = 50
STOP_ATR_MULT = 1.0
TARGET_R = 2.0
TRAIL_ARM_R = 1.0
PREMIUM_STOP_FRAC = 0.50
SLEEP_SEC = 0.12  # pause between Polygon ticker requests
SPLIT_GAP_UP = 1.8
SPLIT_GAP_DOWN = 0.55
MC_SIMS = 5000
SHARPE_MIN = 1.5
MAX_DD_FLOOR = -0.15
WF_MIN_PASS = 3
MIN_TRADES_WINDOW = 5
MIN_TRADES_MC = 5
YEAR_DAYS = 365.0  # ACT/365 for the model clock
RISK_FREE_RATE = 0.04  # continuous, assumed constant, not a Treasury print
MIN_IV = 0.05
MAX_IV = 3.0
IV_LOOKBACK_SESSIONS = 10  # recent window, frozen and applied backward
MIN_IV_PRINTS = 5
BRIDGE_URL = "http://127.0.0.1:8787"
BRIDGE_TIMEOUT_SEC = 25.0

# Assumed continuous dividend yields. Not declared dividends and not IBKR.
DIVIDEND_YIELD = {
    "SPY": 0.011,
    "NVDA": 0.0003,
    "TSLA": 0.0,
    "AVGO": 0.007,
}

# Cash priority when two names signal the same morning.
UNIVERSE = ["SPY", "NVDA", "TSLA", "AVGO"]

# Track 100 ops-stack window requested for this proxy. Warmup is for SMA50
# and ATR only; those bars are not entry dates.
FETCH_START = "2025-09-01"
FETCH_END = "2026-09-16"
ENTRY_START = "2026-01-16"
ENTRY_END = "2026-09-16"

WINDOWS = (
    ("W1", "2026-01-16", "2026-03-15"),
    ("W2", "2026-03-16", "2026-05-15"),
    ("W3", "2026-05-16", "2026-07-15"),
    ("W4", "2026-07-16", "2026-09-16"),
)

BANNER = "MODEL PROXY ONLY — NOT FILLS"
LABEL = "model_proxy_pnl"

LAPTOP_PS = (
    "cd C:\\Users\\ajkle\\Documents\\Q-ALPHA\n"
    ".\\experiments\\EXP-0027\\run_on_laptop.ps1"
)

try:
    import modal
except ImportError:
    modal = None  # type: ignore

if modal is not None:
    app = modal.App(APP_NAME)
    image = modal.Image.debian_slim(python_version="3.11")
    polygon_secret = modal.Secret.from_name("polygon-api-key")
    # Attached so this matches other Q-ALPHA studies. The function does not
    # read it and does not write Supabase or send orders.
    qa_secret = modal.Secret.from_name("q-alpha-secrets")

    @app.function(image=image, secrets=[polygon_secret, qa_secret], timeout=600)
    def fetch_stock_bars_modal(symbols: list[str], start: str, end: str) -> dict[str, Any]:
        """Pull unadjusted daily stock bars with the Modal Polygon secret.

        q-alpha-secrets is on the container and is not read. No orders.
        """
        key = os.environ.get("POLYGON_API_KEY") or ""
        return fetch_polygon_universe(key, symbols, start, end)
else:
    app = None


def rules_snapshot() -> dict[str, Any]:
    """Numeric rules coded in this file, echoed into the results."""
    return {
        "label": LABEL,
        "book_usd": BOOK_USD,
        "multiplier": CONTRACT_MULTIPLIER,
        "itm_min_pct": ITM_MIN_PCT,
        "itm_target_pct": ITM_TARGET_PCT,
        "itm_max_pct": ITM_MAX_PCT,
        "dte_min": DTE_MIN,
        "dte_max": DTE_MAX,
        "dte_target": DTE_TARGET,
        "dte_exit": DTE_EXIT,
        "max_hold_sessions": MAX_HOLD_SESSIONS,
        "atr_window": ATR_WINDOW,
        "stop_atr_mult": STOP_ATR_MULT,
        "target_r": TARGET_R,
        "trail_arm_r": TRAIL_ARM_R,
        "premium_stop_frac": PREMIUM_STOP_FRAC,
        "risk_free_continuous": RISK_FREE_RATE,
        "dividend_yield_assumed": DIVIDEND_YIELD,
        "year_days": YEAR_DAYS,
        "cost_per_trade_stock": COST_PER_TRADE,
        "option_headline": "gross_black_scholes_marks",
        "side": "BUY_TO_OPEN_CALL",
        "strike_grid": "nearest_1_dollar_inside_5_to_12pct_itm",
        "expiry_rule": "friday_closest_to_32_calendar_days",
        "iv_timing": "frozen_from_recent_window_applied_backward",
    }


def norm_cdf(x: float) -> float:
    """Standard normal CDF via erf. No scipy dependency."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_call_price(spot: float, strike: float, t_years: float, iv: float, rate: float, div: float) -> float:
    """European call. At expiry this is intrinsic. Prices stay non-negative.

    Continuous rate and dividend yield. No early-exercise premium, so names
    that pay dividends (SPY, AVGO) are slightly cheap versus an American model.
    A five-session hold does not use that premium.
    """
    if spot <= 0 or strike <= 0:
        return float("nan")
    if t_years <= 0 or iv <= 0:
        return max(spot - strike, 0.0)
    vol_sqrt = iv * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (rate - div + 0.5 * iv * iv) * t_years) / vol_sqrt
    d2 = d1 - vol_sqrt
    return spot * math.exp(-div * t_years) * norm_cdf(d1) - strike * math.exp(-rate * t_years) * norm_cdf(d2)


def bs_call_vega(spot: float, strike: float, t_years: float, iv: float, rate: float, div: float) -> float:
    """dPrice/dIV. Used only to invert a mark. Not a trading greek."""
    if spot <= 0 or strike <= 0 or t_years <= 0 or iv <= 0:
        return 0.0
    vol_sqrt = iv * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (rate - div + 0.5 * iv * iv) * t_years) / vol_sqrt
    return spot * math.exp(-div * t_years) * norm_pdf(d1) * math.sqrt(t_years)


def implied_vol(price: float, spot: float, strike: float, t_years: float, rate: float, div: float) -> float | None:
    """Invert European IV. Newton, then bisection. None when the mark is unusable."""
    if price <= 0 or spot <= 0 or strike <= 0 or t_years <= 0:
        return None
    intrinsic = max(spot * math.exp(-div * t_years) - strike * math.exp(-rate * t_years), 0.0)
    if price < intrinsic - 0.05:
        return None
    iv = 0.35
    for _ in range(40):
        model = bs_call_price(spot, strike, t_years, iv, rate, div)
        vega = bs_call_vega(spot, strike, t_years, iv, rate, div)
        if not math.isfinite(model) or vega < 1e-8:
            break
        iv_next = iv - (model - price) / vega
        if not math.isfinite(iv_next):
            break
        iv_next = min(MAX_IV, max(MIN_IV, iv_next))
        if abs(iv_next - iv) < 1e-5:
            return iv_next
        iv = iv_next
    lo, hi = MIN_IV, MAX_IV
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        model = bs_call_price(spot, strike, t_years, mid, rate, div)
        if not math.isfinite(model):
            return None
        if model > price:
            hi = mid
        else:
            lo = mid
    out = 0.5 * (lo + hi)
    check = bs_call_price(spot, strike, t_years, out, rate, div)
    if not math.isfinite(check) or abs(check - price) > max(0.25, 0.05 * price):
        return None
    return out


def bull_regime(spy_closes: list[float]) -> bool:
    """SPY prior close at or above the prior 50-day average.

    The average excludes the last close. Too little history is not a bull day.
    """
    if len(spy_closes) < SMA_REGIME + 1:
        return False
    last = spy_closes[-1]
    avg = sum(spy_closes[-(SMA_REGIME + 1):-1]) / SMA_REGIME
    return last >= avg


def sma_cross_up(closes: list[float], window: int = SMA_FAST) -> bool:
    """Prior close crosses above its average. Each average excludes that close."""
    if len(closes) < window + 2:
        return False
    sma_prev = sum(closes[-(window + 2):-2]) / window
    sma_last = sum(closes[-(window + 1):-1]) / window
    return closes[-2] <= sma_prev and closes[-1] > sma_last


def atr14(bars: list[dict]) -> float | None:
    """Mean true range of the last 14 completed bars. Caller passes pre-entry bars only."""
    if len(bars) < ATR_WINDOW + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(bars)):
        high = bars[i]["h"]
        low = bars[i]["l"]
        prev_c = bars[i - 1]["c"]
        trs.append(max(high - low, abs(high - prev_c), abs(low - prev_c)))
    if len(trs) < ATR_WINDOW:
        return None
    window = trs[-ATR_WINDOW:]
    return sum(window) / ATR_WINDOW


def has_split_gap(bars: list[dict]) -> bool:
    """True when an unadjusted close jumps like a split. Those sessions are not traded."""
    for i in range(1, len(bars)):
        prev = bars[i - 1]["c"]
        cur = bars[i]["c"]
        if prev <= 0 or cur <= 0:
            continue
        ratio = cur / prev
        if ratio >= SPLIT_GAP_UP or ratio <= SPLIT_GAP_DOWN:
            return True
    return False


def choose_model_strike(spot: float) -> dict[str, float] | None:
    """Nearest $1 strike to 8% ITM that still sits inside 5–12%.

    This is a model grid, not a listed chain and not a Polygon contract.
    """
    if spot <= 0:
        return None
    target = spot * (1.0 - ITM_TARGET_PCT)
    center = int(round(target))
    best: tuple[tuple[float, float, float], float, float] | None = None
    for strike in range(max(1, center - 30), center + 31):
        itm = (spot - strike) / spot
        if itm < ITM_MIN_PCT or itm > ITM_MAX_PCT:
            continue
        key = (abs(itm - ITM_TARGET_PCT), abs(strike - target), float(strike))
        if best is None or key < best[0]:
            best = (key, float(strike), itm)
    if best is None:
        return None
    return {"strike": best[1], "itm_pct": best[2]}


def choose_model_expiry(entry: date) -> date | None:
    """Friday inside 21–45 calendar days, closest to 32 days."""
    best: date | None = None
    best_key: tuple[int, int] | None = None
    for delta in range(DTE_MIN, DTE_MAX + 1):
        cand = entry + timedelta(days=delta)
        if cand.weekday() != 4:
            continue
        key = (abs(delta - DTE_TARGET), delta)
        if best_key is None or key < best_key:
            best_key = key
            best = cand
    return best


def size_long_call(option_open: float) -> dict[str, Any]:
    """One contract, or skip when the debit is above the $5,000 book."""
    if option_open is None or not isinstance(option_open, (int, float)):
        return {"action": "skip", "reason": "bad_option_open", "contracts": 0, "debit_usd": None}
    if not math.isfinite(option_open) or option_open <= 0:
        return {"action": "skip", "reason": "bad_option_open", "contracts": 0, "debit_usd": None}
    debit = option_open * CONTRACT_MULTIPLIER
    if debit > BOOK_USD:
        return {
            "action": "skip",
            "reason": "premium_exceeds_book",
            "contracts": 0,
            "debit_usd": round(debit, 2),
        }
    return {
        "action": "buy",
        "reason": "ok",
        "contracts": 1,
        "debit_usd": round(debit, 2),
    }


def _years_left(expiry: date, session: date, at_close: bool) -> float:
    """ACT/365 time left. The close is one calendar day shorter than the open."""
    days = (expiry - session).days
    if at_close:
        days -= 1
    if days <= 0:
        return 0.0
    return days / YEAR_DAYS


def simulate_bs_call(
    entry_date: str,
    entry_und_open: float,
    atr: float,
    strike: float,
    expiry: str,
    iv: float,
    div: float,
    und_bars: list[dict],
) -> dict[str, Any]:
    """Walk one long call. Premiums are BS marks. Stock high/low drive the stop.

    Same-bar stop and +2R is dropped (no P&L). The trail moves only after the
    bar that tagged +1R. Exit premium is the model value at that session's close.
    """
    base = {
        "entry_date": entry_date,
        "status": "unpriced",
        "pnl_usd": None,
        "reason": "bad_inputs",
    }
    if entry_und_open <= 0 or atr <= 0 or strike <= 0 or iv <= 0:
        return base
    exp = date.fromisoformat(expiry)
    entry_d = date.fromisoformat(entry_date)
    t_open = _years_left(exp, entry_d, at_close=False)
    entry_opt = bs_call_price(entry_und_open, strike, t_open, iv, RISK_FREE_RATE, div)
    if not math.isfinite(entry_opt) or entry_opt <= 0:
        base["reason"] = "bad_model_open"
        return base
    sized = size_long_call(entry_opt)
    if sized["action"] != "buy":
        return {
            "entry_date": entry_date,
            "status": "skipped",
            "pnl_usd": None,
            "reason": sized["reason"],
            "debit_usd": sized["debit_usd"],
            "entry_opt_open": entry_opt,
        }
    risk = STOP_ATR_MULT * atr
    stop = entry_und_open - risk
    target = entry_und_open + TARGET_R * risk
    arm_level = entry_und_open + TRAIL_ARM_R * risk
    initial_stop = stop
    debit = entry_opt * CONTRACT_MULTIPLIER
    hold = [b for b in und_bars if b["date"] >= entry_date][:MAX_HOLD_SESSIONS]
    if not hold or hold[0]["date"] != entry_date:
        base["reason"] = "missing_underlying_bar"
        return base
    marks: dict[str, float] = {}
    for i, bar in enumerate(hold):
        session = date.fromisoformat(bar["date"])
        t_close = _years_left(exp, session, at_close=True)
        close_px = bs_call_price(float(bar["c"]), strike, t_close, iv, RISK_FREE_RATE, div)
        if not math.isfinite(close_px) or close_px < 0:
            return {
                "entry_date": entry_date,
                "status": "unpriced",
                "pnl_usd": None,
                "reason": "bad_model_close",
                "exit_date": bar["date"],
            }
        marks[bar["date"]] = close_px
        hit_stop = bar["l"] <= stop
        hit_target = bar["h"] >= target
        if hit_stop and hit_target:
            return {
                "entry_date": entry_date,
                "status": "ambiguous",
                "pnl_usd": None,
                "reason": "stop_and_target_same_bar",
                "exit_date": bar["date"],
                "initial_stop": initial_stop,
                "debit_usd": debit,
            }
        reason = None
        if hit_stop:
            reason = "underlying_stop"
        elif hit_target:
            reason = "target_2r"
        elif close_px <= entry_opt * PREMIUM_STOP_FRAC:
            reason = "premium_half"
        else:
            dte_left = (exp - session).days
            if dte_left <= DTE_EXIT:
                reason = "dte_floor"
            elif i == MAX_HOLD_SESSIONS - 1:
                reason = "time_5d"
        if reason is not None:
            pnl = (close_px - entry_opt) * CONTRACT_MULTIPLIER
            if pnl < -debit - 1e-6:
                return {
                    "entry_date": entry_date,
                    "status": "unpriced",
                    "pnl_usd": None,
                    "reason": "loss_exceeded_debit",
                    "exit_date": bar["date"],
                }
            return {
                "entry_date": entry_date,
                "exit_date": bar["date"],
                "status": "closed",
                "reason": reason,
                "pnl_usd": pnl,
                "pnl_after_equity_haircut_usd": pnl - COST_PER_TRADE * debit,
                "debit_usd": debit,
                "exit_premium_usd": close_px * CONTRACT_MULTIPLIER,
                "entry_opt_open": entry_opt,
                "exit_opt_close": close_px,
                "entry_und_open": entry_und_open,
                "exit_und_close": float(bar["c"]),
                "initial_stop": initial_stop,
                "marks": marks,
                "iv": iv,
                "fill_proxy": "black_scholes_open_to_close",
                "call_max_loss_usd": debit,
            }
        if bar["h"] >= arm_level:
            stop = max(stop, entry_und_open)
    return {
        "entry_date": entry_date,
        "status": "unpriced",
        "pnl_usd": None,
        "reason": "no_exit",
    }


def simulate_stock_100(entry_date: str, entry_open: float, atr: float, und_bars: list[dict]) -> dict[str, Any]:
    """100-share long with the same underlying stop, +1R, +2R, and 5-session cap.

    A stop inside the bar is filled at the stop. A gap through the stop is
    filled at the open. That gap is the loss the call is meant to cap.
    COST_PER_TRADE is charged once on entry notional.
    """
    base = {"entry_date": entry_date, "status": "unpriced", "pnl_usd": None, "reason": "bad_inputs"}
    if entry_open <= 0 or atr <= 0:
        return base
    risk = STOP_ATR_MULT * atr
    stop = entry_open - risk
    target = entry_open + TARGET_R * risk
    arm_level = entry_open + TRAIL_ARM_R * risk
    hold = [b for b in und_bars if b["date"] >= entry_date][:MAX_HOLD_SESSIONS]
    if not hold or hold[0]["date"] != entry_date:
        base["reason"] = "missing_underlying_bar"
        return base
    for i, bar in enumerate(hold):
        hit_stop = bar["l"] <= stop
        hit_target = bar["h"] >= target
        if hit_stop and hit_target:
            return {
                "entry_date": entry_date,
                "status": "ambiguous",
                "pnl_usd": None,
                "reason": "stop_and_target_same_bar",
                "exit_date": bar["date"],
            }
        exit_px = None
        reason = None
        if hit_stop:
            reason = "underlying_stop"
            exit_px = float(bar["o"]) if bar["o"] <= stop else stop
        elif hit_target:
            reason = "target_2r"
            exit_px = float(bar["o"]) if bar["o"] >= target else target
        elif i == MAX_HOLD_SESSIONS - 1:
            reason = "time_5d"
            exit_px = float(bar["c"])
        if reason is not None and exit_px is not None:
            gross = (exit_px - entry_open) * CONTRACT_MULTIPLIER
            cost = COST_PER_TRADE * entry_open * CONTRACT_MULTIPLIER
            return {
                "entry_date": entry_date,
                "exit_date": bar["date"],
                "status": "closed",
                "reason": reason,
                "exit_px": exit_px,
                "pnl_gross_usd": gross,
                "pnl_usd": gross - cost,
                "notional_usd": entry_open * CONTRACT_MULTIPLIER,
            }
        if bar["h"] >= arm_level:
            stop = max(stop, entry_open)
    base["reason"] = "no_exit"
    return base


def build_signals(bars_by_symbol: dict[str, list[dict]]) -> list[dict[str, Any]]:
    """Causal entries: SPY regime on, and the name just crossed above SMA20.

    Features use sessions before the entry date. Strike uses the prior close.
    The entry open is the underlying price the stop is measured from.
    """
    spy = bars_by_symbol.get("SPY") or []
    signals: list[dict[str, Any]] = []
    for symbol in UNIVERSE:
        und = bars_by_symbol.get(symbol) or []
        for i, bar in enumerate(und):
            entry_date = bar["date"]
            if entry_date < ENTRY_START or entry_date > ENTRY_END:
                continue
            prior = und[:i]
            if bar.get("o") is None or bar["o"] <= 0 or not prior:
                continue
            if has_split_gap(prior[-5:] + [bar]):
                continue
            spy_prior = [row["c"] for row in spy if row["date"] < entry_date]
            closes = [row["c"] for row in prior]
            if not bull_regime(spy_prior) or not sma_cross_up(closes):
                continue
            atr = atr14(prior)
            if atr is None or atr <= 0 or closes[-1] <= 0:
                continue
            picked = choose_model_strike(float(closes[-1]))
            expiry = choose_model_expiry(date.fromisoformat(entry_date))
            if picked is None or expiry is None:
                continue
            signals.append({
                "symbol": symbol,
                "entry_date": entry_date,
                "spot": float(closes[-1]),
                "entry_und_open": float(bar["o"]),
                "atr": float(atr),
                "strike": picked["strike"],
                "itm_pct_prior_close": picked["itm_pct"],
                "expiry": expiry.isoformat(),
                "dte": (expiry - date.fromisoformat(entry_date)).days,
            })
    signals.sort(key=lambda row: (row["entry_date"], UNIVERSE.index(row["symbol"])))
    return signals


def allocate_book(trades: list[dict], book: float = BOOK_USD) -> list[dict]:
    """Spend cash at the open. Exit proceeds are free on later sessions only."""
    ordered = sorted(trades, key=lambda row: (row.get("entry_date") or "", UNIVERSE.index(row.get("symbol") or "SPY")))
    cash = book
    open_pos: list[dict] = []
    out: list[dict] = []
    for trade in ordered:
        row = dict(trade)
        if row.get("status") != "closed":
            row["allocated"] = False
            row["pnl_usd"] = None
            out.append(row)
            continue
        still: list[dict] = []
        for pos in open_pos:
            if pos["exit_date"] < row["entry_date"]:
                cash += pos["exit_premium_usd"]
            else:
                still.append(pos)
        open_pos = still
        debit = float(row["debit_usd"])
        if cash + 1e-6 < debit:
            row["status"] = "skipped_cash"
            row["allocated"] = False
            row["pnl_usd"] = None
            out.append(row)
            continue
        cash -= debit
        row["allocated"] = True
        open_pos.append(row)
        out.append(row)
    return out


def equity_curve(trades: list[dict], calendar: list[str], book: float = BOOK_USD) -> list[float] | None:
    """Mark open calls at that session's model close. Missing marks abort the curve."""
    accepted = [row for row in trades if row.get("allocated") and row.get("status") == "closed"]
    if not calendar:
        return [book]
    # Point 0 is the untouched book, so day-1 losses count in the drawdown.
    by_entry: dict[str, list[dict]] = {}
    for row in accepted:
        by_entry.setdefault(row["entry_date"], []).append(row)
    cash = book
    open_pos: list[dict] = []
    curve: list[float] = [book]
    for day in calendar:
        still = []
        for pos in open_pos:
            if pos["exit_date"] < day:
                cash += pos["exit_premium_usd"]
            else:
                still.append(pos)
        open_pos = still
        for row in by_entry.get(day, []):
            cash -= float(row["debit_usd"])
            open_pos.append(row)
        mtm = 0.0
        for pos in open_pos:
            marks = pos.get("marks") or {}
            px = marks.get(day)
            if px is None:
                prior = [marks[k] for k in sorted(marks) if k <= day]
                if not prior:
                    return None
                px = prior[-1]
            mtm += float(px) * CONTRACT_MULTIPLIER
        curve.append(cash + mtm)
    return curve


def sharpe_daily(equity: list[float]) -> float:
    """Annualized Sharpe of daily equity changes. Flat books are 0."""
    rets: list[float] = []
    for i in range(1, len(equity)):
        prev = equity[i - 1]
        if prev <= 0:
            continue
        rets.append(equity[i] / prev - 1.0)
    if len(rets) < 5:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    if var <= 0:
        return 0.0
    return mean / math.sqrt(var) * math.sqrt(252)


def max_drawdown(equity: list[float]) -> float:
    """Worst peak-to-trough fraction. 0 when the curve never falls."""
    if not equity:
        return 0.0
    peak = equity[0]
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst


def stock_pnl_curve(trades: list[dict], calendar: list[str]) -> list[float]:
    """Cumulative 100-share P&L marked at the close until the modeled exit.

    The curve starts at 0. Drawdown is in dollars from the peak of this P&L,
    and also as a fraction of a book that starts at $5,000 plus that P&L so
    the percent is comparable to the call book.
    """
    closed = [row for row in trades if row.get("status") == "closed" and row.get("pnl_usd") is not None]
    by_entry: dict[str, list[dict]] = {}
    for row in closed:
        by_entry.setdefault(row["entry_date"], []).append(row)
    open_pos: list[dict] = []
    realized = 0.0
    curve: list[float] = []
    for day in calendar:
        still = []
        for pos in open_pos:
            if pos["exit_date"] < day:
                realized += float(pos["pnl_usd"])
            else:
                still.append(pos)
        open_pos = still
        for row in by_entry.get(day, []):
            open_pos.append(row)
        unreal = 0.0
        for pos in open_pos:
            if pos["exit_date"] == day:
                unreal += float(pos["pnl_usd"])
            else:
                # Still open: no daily stock mark stored. Hold realized-to-date
                # only; the exit bar carries the full trade P&L.
                pass
        curve.append(realized + unreal)
    return curve


def buy_hold_return(bars: list[dict], start: str, end: str) -> float | None:
    """SPY close-to-close over the study window."""
    inside = [bar for bar in bars if start <= bar["date"] <= end and bar["c"] > 0]
    if len(inside) < 2:
        return None
    return inside[-1]["c"] / inside[0]["c"] - 1.0


def monte_carlo(pnls: list[float], n_sims: int = MC_SIMS, seed: int = 42) -> dict[str, Any]:
    """5,000-draw bootstrap of trade P&L. Informational on a model proxy."""
    if len(pnls) < MIN_TRADES_MC:
        return {"ran": False, "pass": False, "p_value": None, "reason": "fewer_than_5_trades", "n": len(pnls)}
    mean = sum(pnls) / len(pnls)
    var = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
    if var <= 0:
        return {"ran": False, "pass": False, "p_value": None, "reason": "zero_std", "n": len(pnls)}
    real = mean / math.sqrt(var) * math.sqrt(252)
    rng = random.Random(seed)
    below = 0
    for _ in range(n_sims):
        sample = [rng.choice(pnls) for _ in range(len(pnls))]
        s_mean = sum(sample) / len(sample)
        s_var = sum((p - s_mean) ** 2 for p in sample) / (len(sample) - 1)
        sim = s_mean / math.sqrt(s_var) * math.sqrt(252) if s_var > 0 else 0.0
        if sim < real:
            below += 1
    p_value = 1.0 - (below / n_sims)
    return {
        "ran": True,
        "pass": p_value < 0.05,
        "p_value": p_value,
        "real_trade_sharpe": real,
        "n": len(pnls),
        "warning": "fewer_than_100_trades" if len(pnls) < 100 else None,
    }


def _window_for(entry_date: str) -> str | None:
    """Map an entry date to its walk-forward slice of the 2026 window."""
    for name, start, end in WINDOWS:
        if start <= entry_date <= end:
            return name
    return None


def _gate(name: str, ok: bool, value: Any, need: str) -> dict[str, Any]:
    """One informational pass/fail row. A pass is still only a model proxy."""
    return {"name": name, "pass": bool(ok), "value": value, "need": need, "label": "PASS" if ok else "FAIL"}


def realized_vol(bars: list[dict], n: int = IV_LOOKBACK_SESSIONS) -> dict[str, Any]:
    """Annualized sample stdev of the last n daily log returns.

    This is the fallback when the laptop bridge cannot price a recent ITM call.
    It is frozen and pushed backward. It is not an implied vol.
    """
    closes = [float(b["c"]) for b in bars if b.get("c")]
    if len(closes) < n + 1:
        return {"iv": None, "reason": "short_history", "n": len(closes)}
    window = closes[-(n + 1):]
    rets = []
    for i in range(1, len(window)):
        if window[i - 1] <= 0 or window[i] <= 0:
            continue
        rets.append(math.log(window[i] / window[i - 1]))
    if len(rets) < n:
        return {"iv": None, "reason": "short_returns", "n": len(rets)}
    sigma = statistics.stdev(rets) * math.sqrt(252)
    sigma = min(MAX_IV, max(MIN_IV, sigma))
    dates = [b["date"] for b in bars if b.get("c")][-n:]
    return {
        "iv": sigma,
        "reason": "realized_vol_fallback",
        "n": len(rets),
        "start": dates[0],
        "end": dates[-1],
    }


def _http_json(
    url: str,
    params: dict | None = None,
    timeout: float = 45.0,
    retries: int = 4,
) -> dict[str, Any]:
    """GET JSON with a short retry on rate limit and server errors."""
    query = urllib.parse.urlencode(params or {})
    full = url + (("?" + query) if query else "")
    last: Exception | None = None
    for i in range(max(1, retries)):
        req = urllib.request.Request(full, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            if isinstance(body, dict):
                body["_status"] = getattr(resp, "status", 200)
            return body
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or exc.code >= 500:
                time.sleep(0.7 * (i + 1))
                last = exc
                continue
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except Exception:
                payload = {"error": str(exc)}
            if isinstance(payload, dict):
                payload["_status"] = exc.code
                return payload
            return {"_status": exc.code, "error": str(exc)}
        except Exception as exc:
            last = exc
            time.sleep(0.3 * (i + 1))
    return {"_status": 0, "_error": str(last)[:180] if last else "request_failed"}


def _et_date(ms: int) -> str:
    """Session date in America/New_York for a Polygon or chart timestamp."""
    return datetime.fromtimestamp(ms / 1000.0, tz=ET).date().isoformat()


def parse_polygon_aggs(body: dict) -> list[dict[str, Any]]:
    """Unadjusted daily OHLC. Zero prices are dropped."""
    rows = []
    for bar in body.get("results") or []:
        try:
            o, h, low, c = float(bar["o"]), float(bar["h"]), float(bar["l"]), float(bar["c"])
            d = _et_date(int(bar["t"]))
        except (KeyError, TypeError, ValueError):
            continue
        if min(o, h, low, c) <= 0:
            continue
        rows.append({"date": d, "o": o, "h": h, "l": low, "c": c})
    rows.sort(key=lambda r: r["date"])
    return rows


def fetch_polygon_daily(key: str, symbol: str, start: str, end: str) -> dict[str, Any]:
    """One ticker of unadjusted daily bars. Retries live in _http_json."""
    url = f"{POLYGON}/v2/aggs/ticker/{symbol}/range/1/day/{start}/{end}"
    body = _http_json(url, {"adjusted": "false", "sort": "asc", "limit": 50000, "apiKey": key})
    status = int(body.get("_status") or 0)
    if status in (401, 403) or body.get("status") == "NOT_AUTHORIZED":
        return {"symbol": symbol, "bars": [], "ok": False, "error": f"polygon_not_authorized:{status}"}
    if body.get("_error"):
        return {"symbol": symbol, "bars": [], "ok": False, "error": body["_error"]}
    bars = parse_polygon_aggs(body)
    return {"symbol": symbol, "bars": bars, "ok": len(bars) >= SMA_REGIME + 5, "error": None if bars else "empty"}


def fetch_polygon_universe(key: str, symbols: list[str], start: str, end: str) -> dict[str, Any]:
    """Daily bars for the study universe. The API key is not returned."""
    if not key:
        return {"ok": False, "error": "missing_polygon_key", "bars": {}, "source": "polygon_unadjusted"}
    out: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}
    for i, symbol in enumerate(symbols):
        if i:
            time.sleep(SLEEP_SEC)
        got = fetch_polygon_daily(key, symbol, start, end)
        if not got.get("ok"):
            errors[symbol] = str(got.get("error"))
        out[symbol] = got.get("bars") or []
        print(f"polygon {symbol}: {len(out[symbol])} bars", flush=True)
    ok = all(len(out.get(sym) or []) >= SMA_REGIME + 5 for sym in symbols)
    return {
        "ok": ok,
        "error": None if ok else errors,
        "bars": out,
        "source": "polygon_unadjusted",
    }


def fetch_yahoo_chart_daily(symbol: str, start: str, end: str) -> dict[str, Any]:
    """Daily OHLC from the public chart endpoint. Not Polygon. Not yfinance.

    The quote OHLC is used (not dividend-adjusted close). Yahoo still
    split-adjusts history. Inside this 2026 window these four names have no
    split, so the prints match the prices that traded.
    """
    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(end).replace(tzinfo=timezone.utc) + timedelta(days=1)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    body = _http_json(
        url,
        {
            "period1": str(int(start_dt.timestamp())),
            "period2": str(int(end_dt.timestamp())),
            "interval": "1d",
            "events": "history",
        },
    )
    result = ((body.get("chart") or {}).get("result") or [None])[0]
    if not result:
        return {"symbol": symbol, "bars": [], "ok": False, "error": "yahoo_empty"}
    ts = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    rows = []
    for i, stamp in enumerate(ts):
        try:
            o = float(quote["open"][i])
            h = float(quote["high"][i])
            low = float(quote["low"][i])
            c = float(quote["close"][i])
            d = _et_date(int(stamp) * 1000)
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(x) and x > 0 for x in (o, h, low, c)):
            continue
        if d < start or d > end:
            continue
        rows.append({"date": d, "o": o, "h": h, "l": low, "c": c})
    rows.sort(key=lambda r: r["date"])
    # Collapse duplicate dates (rare on the chart feed).
    by_date = {row["date"]: row for row in rows}
    rows = [by_date[k] for k in sorted(by_date)]
    return {"symbol": symbol, "bars": rows, "ok": len(rows) >= SMA_REGIME + 5, "error": None if rows else "yahoo_empty"}


def fetch_yahoo_universe(symbols: list[str], start: str, end: str) -> dict[str, Any]:
    """Chart fallback used only when Polygon is unreachable from this process."""
    out: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}
    for i, symbol in enumerate(symbols):
        if i:
            time.sleep(SLEEP_SEC)
        got = fetch_yahoo_chart_daily(symbol, start, end)
        if not got.get("ok"):
            errors[symbol] = str(got.get("error"))
        out[symbol] = got.get("bars") or []
        print(f"yahoo-chart {symbol}: {len(out[symbol])} bars", flush=True)
    ok = all(len(out.get(sym) or []) >= SMA_REGIME + 5 for sym in symbols)
    return {
        "ok": ok,
        "error": None if ok else errors,
        "bars": out,
        "source": "yahoo_chart_daily_ohlc_not_polygon",
    }


def _bridge_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    """Read-only GET against the laptop options bridge. No order routes."""
    if "order" in path.lower():
        return {"ok": False, "error": {"code": "refused", "message": "study does not call order routes"}}
    return _http_json(
        BRIDGE_URL + path,
        {k: v for k, v in params.items() if v is not None},
        timeout=BRIDGE_TIMEOUT_SEC,
        retries=2,
    )


def _parse_yyyymmdd(raw: str) -> date | None:
    text = str(raw or "").replace("-", "")[:8]
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:
        return None


def _pick_bridge_call(chain: dict, spot: float, asof: date) -> dict[str, Any] | None:
    """~8% ITM call, 21–45 DTE, from the live chain. Puts are ignored."""
    best: tuple[tuple[float, float, float], dict[str, Any]] | None = None
    for exch in chain.get("exchanges") or []:
        for exp_raw in exch.get("expirations") or []:
            exp = _parse_yyyymmdd(str(exp_raw))
            if exp is None:
                continue
            dte = (exp - asof).days
            if dte < DTE_MIN or dte > DTE_MAX:
                continue
            for strike_raw in exch.get("strikes") or []:
                try:
                    strike = float(strike_raw)
                except (TypeError, ValueError):
                    continue
                if strike <= 0 or spot <= 0:
                    continue
                itm = (spot - strike) / spot
                if itm < ITM_MIN_PCT or itm > ITM_MAX_PCT:
                    continue
                key = (abs(itm - ITM_TARGET_PCT), abs(dte - DTE_TARGET), strike)
                row = {
                    "strike": strike,
                    "expiry": exp.strftime("%Y%m%d"),
                    "expiry_iso": exp.isoformat(),
                    "dte": dte,
                    "itm_pct": itm,
                    "exchange": exch.get("exchange") or "SMART",
                }
                if best is None or key < best[0]:
                    best = (key, row)
    return None if best is None else best[1]


def _hour_key(ts: str) -> str:
    """Align stock and option bars on YYYY-MM-DDTHH."""
    return str(ts)[:13]


def _positive(val: Any) -> float | None:
    """Finite price above zero. JSON null and NaN are missing."""
    try:
        num = float(val)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(num) or num <= 0:
        return None
    return num


def quote_mark(data: dict | None) -> float | None:
    """Best mark on a bridge quote: mid, last, close, then bid/ask.

    After the cash close, last and mid are often null. Close, or a two-sided
    quote, is still a usable mark. A one-sided bid or ask is kept only when
    nothing else printed.
    """
    if not isinstance(data, dict):
        return None
    for key in ("mid", "last", "close", "market_price"):
        px = _positive(data.get(key))
        if px is not None:
            return px
    bid = _positive(data.get("bid"))
    ask = _positive(data.get("ask"))
    if bid is not None and ask is not None:
        return (bid + ask) / 2.0
    return bid if bid is not None else ask


def last_hist_close(payload: dict | None) -> float | None:
    """Last positive close on a bridge hist envelope."""
    bars = ((payload or {}).get("data") or {}).get("bars") or []
    for bar in reversed(bars):
        if not isinstance(bar, dict):
            continue
        px = _positive(bar.get("close"))
        if px is not None:
            return px
    return None


def choose_spot(
    quote_data: dict | None,
    hist_closes: list[float | None],
    fallback_spot: float | None,
) -> tuple[float | None, str]:
    """Spot for the live ITM contract when the cash quote is empty.

    Order: underlying quote mark, then stock-hist last close, then the last
    close already loaded for the study. Phase 9A is not a spot source: that
    route needs und_px and returns put mids.
    """
    mark = quote_mark(quote_data)
    if mark is not None:
        return mark, "underlying_quote"
    for close in hist_closes:
        px = _positive(close)
        if px is not None:
            return px, "ibkr_stock_hist_last_close"
    fb = _positive(fallback_spot)
    if fb is not None:
        return fb, "study_daily_last_close"
    return None, "none"


def _bar_day(ts: str) -> str:
    """Session date from an ISO timestamp or a YYYYMMDD bar date."""
    text = str(ts or "")
    if len(text) >= 10 and text[4] == "-":
        return text[:10]
    if len(text) >= 8 and text[:8].isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text[:10]


def iv_samples_from_bars(
    opt_bars: list[dict],
    und_bars: list[dict],
    strike: float,
    expiry: date,
    div: float,
    hourly: bool,
) -> list[float]:
    """Invert IV on paired option and stock closes. Unusable pairs are dropped."""
    und: dict[str, float] = {}
    for bar in und_bars:
        if not isinstance(bar, dict):
            continue
        px = _positive(bar.get("close"))
        if px is None:
            continue
        ts = str(bar.get("ts") or "")
        key = _hour_key(ts) if hourly else _bar_day(ts)
        if key:
            und[key] = px
    samples: list[float] = []
    for bar in opt_bars:
        if not isinstance(bar, dict):
            continue
        opt_px = _positive(bar.get("close"))
        if opt_px is None:
            continue
        ts = str(bar.get("ts") or "")
        key = _hour_key(ts) if hourly else _bar_day(ts)
        und_px = und.get(key)
        if und_px is None:
            continue
        try:
            bar_day = date.fromisoformat(_bar_day(ts))
        except ValueError:
            continue
        t_years = (expiry - bar_day).days / YEAR_DAYS
        iv = implied_vol(opt_px, und_px, strike, t_years, RISK_FREE_RATE, div)
        if iv is not None:
            samples.append(iv)
    return samples


def _stock_hist(symbol: str, bar_size: str, what: str) -> dict[str, Any]:
    """Underlying history. secType=STK so a missing expiry is not read as an option."""
    return _bridge_get(
        "/v1/options/hist",
        {
            "symbol": symbol,
            "secType": "STK",
            "duration": "10 D",
            "bar_size": bar_size,
            "what": what,
            "use_rth": "true",
        },
    )


def _option_hist(symbol: str, picked: dict[str, Any], bar_size: str, what: str) -> dict[str, Any]:
    """Call history for the contract chosen from the chain."""
    return _bridge_get(
        "/v1/options/hist",
        {
            "symbol": symbol,
            "expiry": picked["expiry"],
            "strike": picked["strike"],
            "right": "C",
            "exchange": picked["exchange"],
            "duration": "10 D",
            "bar_size": bar_size,
            "what": what,
            "use_rth": "true",
        },
    )


def estimate_iv_from_bridge(
    symbol: str,
    asof: date | None = None,
    fallback_spot: float | None = None,
) -> dict[str, Any]:
    """Median IV of ~10 days of marks on one ~8% ITM, 21–45 DTE call.

    After the cash close, /v1/underlying/quote often has no last or mid.
    Spot then comes from the last stock-hist close, then from fallback_spot
    (the study's last daily close). Call marks prefer hourly mids, then daily
    mids, then daily trades. One live call quote is an extra sample. Phase 9A
    put-credit mids are not used as call IV.

    The bridge is read-only and loopback-only. A cloud VM fails this and the
    caller uses realized vol instead.
    """
    asof = asof or datetime.now(ET).date()
    health = _bridge_get("/v1/health", {})
    if not health.get("ok"):
        return {"symbol": symbol, "iv": None, "source": "bridge_down", "detail": "health_failed"}
    quote = _bridge_get("/v1/underlying/quote", {"symbol": symbol})
    quote_data = quote.get("data") if isinstance(quote.get("data"), dict) else {}
    hist_closes: list[float | None] = []
    if quote_mark(quote_data) is None:
        for bar_size, what in (("1 day", "TRADES"), ("1 day", "MIDPOINT"), ("1 hour", "TRADES")):
            hist = _stock_hist(symbol, bar_size, what)
            if hist.get("ok"):
                hist_closes.append(last_hist_close(hist))
            if _positive(hist_closes[-1] if hist_closes else None) is not None:
                break
    spot_f, spot_source = choose_spot(quote_data, hist_closes, fallback_spot)
    if spot_f is None:
        return {
            "symbol": symbol,
            "iv": None,
            "source": "bridge_down",
            "detail": "no_underlying_price",
            "spot_source": spot_source,
        }
    chain = _bridge_get("/v1/options/chain", {"symbol": symbol})
    if not chain.get("ok"):
        return {
            "symbol": symbol,
            "iv": None,
            "source": "bridge_down",
            "detail": "chain_failed",
            "spot": spot_f,
            "spot_source": spot_source,
        }
    picked = _pick_bridge_call(chain.get("data") or {}, spot_f, asof)
    if picked is None:
        return {
            "symbol": symbol,
            "iv": None,
            "source": "bridge_down",
            "detail": "no_itm_call_in_band",
            "spot": spot_f,
            "spot_source": spot_source,
        }
    exp = date.fromisoformat(picked["expiry_iso"])
    div = DIVIDEND_YIELD.get(symbol, 0.0)
    samples: list[float] = []
    hist_used = None
    for bar_size, opt_what, und_what, hourly in (
        ("1 hour", "MIDPOINT", "TRADES", True),
        ("1 hour", "MIDPOINT", "MIDPOINT", True),
        ("1 day", "MIDPOINT", "TRADES", False),
        ("1 day", "TRADES", "TRADES", False),
    ):
        opt_hist = _option_hist(symbol, picked, bar_size, opt_what)
        und_hist = _stock_hist(symbol, bar_size, und_what)
        if not opt_hist.get("ok") or not und_hist.get("ok"):
            continue
        batch = iv_samples_from_bars(
            (opt_hist.get("data") or {}).get("bars") or [],
            (und_hist.get("data") or {}).get("bars") or [],
            float(picked["strike"]),
            exp,
            div,
            hourly,
        )
        if len(batch) > len(samples):
            samples = batch
            hist_used = f"{bar_size} {opt_what}"
        if len(samples) >= MIN_IV_PRINTS:
            break
    opt_quote = _bridge_get(
        "/v1/options/quote",
        {
            "symbol": symbol,
            "expiry": picked["expiry"],
            "strike": picked["strike"],
            "right": "C",
            "exchange": picked["exchange"],
        },
    )
    quote_iv = None
    opt_mark = quote_mark(opt_quote.get("data") if isinstance(opt_quote.get("data"), dict) else None)
    if opt_mark is not None:
        t_years = max((exp - asof).days, 0) / YEAR_DAYS
        quote_iv = implied_vol(opt_mark, spot_f, float(picked["strike"]), t_years, RISK_FREE_RATE, div)
        if quote_iv is not None:
            samples.append(quote_iv)
    if len(samples) < MIN_IV_PRINTS:
        return {
            "symbol": symbol,
            "iv": None,
            "source": "bridge_thin",
            "detail": f"prints={len(samples)}",
            "contract": picked,
            "spot": spot_f,
            "spot_source": spot_source,
            "hist_used": hist_used,
            "quote_iv": quote_iv,
            "option_quote_mark": opt_mark,
        }
    med = statistics.median(samples)
    return {
        "symbol": symbol,
        "iv": med,
        "source": "ibkr_bridge_itm_call_mid_10d",
        "n_prints": len(samples),
        "iv_p25": statistics.quantiles(samples, n=4)[0] if len(samples) >= 4 else med,
        "iv_p75": statistics.quantiles(samples, n=4)[2] if len(samples) >= 4 else med,
        "contract": picked,
        "spot": spot_f,
        "spot_source": spot_source,
        "hist_used": hist_used,
        "quote_iv": quote_iv,
        "asof": asof.isoformat(),
    }


def load_stock_bars(fetch_remote: Callable[[], dict[str, Any]] | None = None) -> dict[str, Any]:
    """Polygon first. Chart OHLC only when the key and Modal both fail."""
    key = (os.environ.get("POLYGON_API_KEY") or "").strip()
    notes: list[str] = []
    if key:
        got = fetch_polygon_universe(key, UNIVERSE, FETCH_START, FETCH_END)
        got["key_source"] = "env"
        return got
    notes.append("POLYGON_API_KEY was not in the environment")
    if fetch_remote is not None:
        try:
            got = fetch_remote()
            if got.get("ok"):
                got["key_source"] = "modal_secret_polygon-api-key"
                got["notes"] = notes
                return got
            notes.append(f"modal remote returned {got.get('error')}")
        except Exception as exc:
            notes.append(f"modal remote failed: {type(exc).__name__}: {exc}"[:200])
    elif modal is not None and app is not None and not os.environ.get("MODAL_TASK_ID"):
        try:
            with app.run():
                got = fetch_stock_bars_modal.remote(list(UNIVERSE), FETCH_START, FETCH_END)
            if got.get("ok"):
                got["key_source"] = "modal_secret_polygon-api-key"
                got["notes"] = notes
                return got
            notes.append(f"modal app.run returned {got.get('error')}")
        except Exception as exc:
            notes.append(f"modal app.run failed: {type(exc).__name__}: {exc}"[:200])
    else:
        notes.append("modal package not installed; cloud VM cannot see laptop secrets")
    got = fetch_yahoo_universe(UNIVERSE, FETCH_START, FETCH_END)
    got["key_source"] = "none"
    got["notes"] = notes
    return got


def bridge_is_up() -> bool:
    """One health check. A refused loopback port is not retried per symbol."""
    health = _bridge_get("/v1/health", {})
    return bool(health.get("ok"))


def measure_iv(bars_by_symbol: dict[str, list[dict]]) -> dict[str, Any]:
    """Per-name IV. Bridge median when it has enough prints, else 10-day RV."""
    out: dict[str, Any] = {}
    bridge_up = bridge_is_up()
    print(f"options bridge {BRIDGE_URL}: {'up' if bridge_up else 'down'}", flush=True)
    for symbol in UNIVERSE:
        rv = realized_vol(bars_by_symbol.get(symbol) or [])
        bridged: dict[str, Any]
        if bridge_up:
            print(f"iv {symbol}: reading ~10D ITM call mids", flush=True)
            bars = bars_by_symbol.get(symbol) or []
            fallback = float(bars[-1]["c"]) if bars else None
            bridged = estimate_iv_from_bridge(symbol, fallback_spot=fallback)
        else:
            bridged = {"symbol": symbol, "iv": None, "source": "bridge_down", "detail": "health_failed"}
        if bridged.get("iv"):
            out[symbol] = {
                **bridged,
                "realized_vol_10d": rv.get("iv"),
                "rv_window": {"start": rv.get("start"), "end": rv.get("end")},
                "used": "ibkr_bridge",
            }
        else:
            out[symbol] = {
                "symbol": symbol,
                "iv": rv.get("iv"),
                "source": "realized_vol_fallback",
                "bridge_attempt": bridged,
                "rv_window": {"start": rv.get("start"), "end": rv.get("end"), "n": rv.get("n")},
                "used": "realized_vol_fallback",
            }
        used = out[symbol].get("iv")
        print(f"iv {symbol}: used={out[symbol]['used']} iv={None if used is None else round(used, 4)}", flush=True)
    return out


def run_proxy(bars_by_symbol: dict[str, list[dict]], iv_by_symbol: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """Signals, BS walk, $5k book, and the 100-share stock baseline."""
    signals = build_signals(bars_by_symbol)
    raw_trades: list[dict] = []
    stock_all: list[dict] = []
    for sig in signals:
        symbol = sig["symbol"]
        und = bars_by_symbol[symbol]
        iv = (iv_by_symbol.get(symbol) or {}).get("iv")
        div = DIVIDEND_YIELD.get(symbol, 0.0)
        stock = simulate_stock_100(sig["entry_date"], sig["entry_und_open"], sig["atr"], und)
        stock["symbol"] = symbol
        stock_all.append(stock)
        if not iv:
            call = {
                "symbol": symbol,
                "entry_date": sig["entry_date"],
                "status": "skipped",
                "reason": "missing_iv",
                "pnl_usd": None,
            }
        else:
            call = simulate_bs_call(
                sig["entry_date"],
                sig["entry_und_open"],
                sig["atr"],
                sig["strike"],
                sig["expiry"],
                float(iv),
                div,
                und,
            )
        call["symbol"] = symbol
        call["strike"] = sig["strike"]
        call["expiry"] = sig["expiry"]
        call["dte"] = sig["dte"]
        call["itm_pct_prior_close"] = sig["itm_pct_prior_close"]
        call["spot_prior_close"] = sig["spot"]
        call["atr"] = sig["atr"]
        call["iv_used"] = iv
        call["iv_source"] = (iv_by_symbol.get(symbol) or {}).get("used")
        call["stock_100"] = {k: v for k, v in stock.items() if k != "marks"}
        raw_trades.append(call)
    allocated = allocate_book(raw_trades)
    # Refuse a book whose model loss exceeds the debit. BS prices cannot do
    # this; the check stays so a later edit cannot publish an unbounded loss.
    if any(row.get("reason") == "loss_exceeded_debit" for row in allocated):
        meta = dict(meta)
        meta["aborted"] = "loss_exceeded_debit"
        return {"status": "ABORTED", "meta": meta, "trades": []}
    return score(allocated, stock_all, bars_by_symbol, iv_by_symbol, meta, len(signals))


def _stock_summary(rows: list[dict]) -> dict[str, Any]:
    """Dollar P&L and win rate for a list of 100-share simulations."""
    closed = [row for row in rows if row.get("status") == "closed" and row.get("pnl_usd") is not None]
    pnl = sum(float(row["pnl_usd"]) for row in closed)
    gross = sum(float(row.get("pnl_gross_usd") if row.get("pnl_gross_usd") is not None else row["pnl_usd"]) for row in closed)
    wins = sum(1 for row in closed if float(row["pnl_usd"]) > 0)
    return {
        "n": len(closed),
        "pnl_usd": pnl,
        "pnl_gross_usd": gross,
        "win_rate": (wins / len(closed)) if closed else None,
        "ambiguous": sum(1 for row in rows if row.get("status") == "ambiguous"),
    }


def score(
    allocated: list[dict],
    stock_all: list[dict],
    bars_by_symbol: dict[str, list[dict]],
    iv_by_symbol: dict[str, Any],
    meta: dict[str, Any],
    n_signals: int,
) -> dict[str, Any]:
    """Headline numbers for the proxy book. Gates are informational."""
    closed = [row for row in allocated if row.get("allocated") and row.get("status") == "closed"]
    spy = bars_by_symbol.get("SPY") or []
    calendar = [bar["date"] for bar in spy if ENTRY_START <= bar["date"] <= ENTRY_END]
    for row in closed:
        for day in row.get("marks") or {}:
            if day not in calendar:
                calendar.append(day)
    calendar = sorted(set(calendar))
    curve = equity_curve(allocated, calendar) if closed else [BOOK_USD]
    if curve is None:
        sharpe = None
        dd = None
        total_ret = None
        end_equity = None
    else:
        sharpe = sharpe_daily(curve)
        dd = max_drawdown(curve)
        total_ret = (curve[-1] / curve[0] - 1.0) if curve[0] else None
        end_equity = curve[-1]
    pnl = sum(float(row["pnl_usd"]) for row in closed)
    haircut = sum(float(row.get("pnl_after_equity_haircut_usd") or row["pnl_usd"]) for row in closed)
    wins = sum(1 for row in closed if float(row["pnl_usd"]) > 0)
    spy_ret = buy_hold_return(spy, ENTRY_START, ENTRY_END)
    by_window: dict[str, list[dict]] = {name: [] for name, _, _ in WINDOWS}
    for row in closed:
        name = _window_for(row["entry_date"])
        if name:
            by_window[name].append(row)
    wf = []
    for name, start, end in WINDOWS:
        rows = by_window[name]
        cal = [day for day in calendar if start <= day <= end]
        for row in rows:
            for day in row.get("marks") or {}:
                if day not in cal:
                    cal.append(day)
        cal = sorted(set(cal))
        w_curve = equity_curve(rows, cal) if rows else [BOOK_USD]
        if w_curve is None:
            wf.append({"window": name, "pass": False, "label": "FAIL", "reason": "missing_marks", "n": len(rows)})
            continue
        w_sharpe = sharpe_daily(w_curve)
        w_dd = max_drawdown(w_curve)
        w_ret = (w_curve[-1] / w_curve[0] - 1.0) if w_curve and w_curve[0] else 0.0
        ok = len(rows) >= MIN_TRADES_WINDOW and w_sharpe >= SHARPE_MIN and w_ret > 0 and w_dd >= MAX_DD_FLOOR
        wf.append({
            "window": name,
            "start": start,
            "end": end,
            "pass": ok,
            "label": "PASS" if ok else "FAIL",
            "n": len(rows),
            "pnl_usd": sum(float(r["pnl_usd"]) for r in rows),
            "sharpe": w_sharpe,
            "return": w_ret,
            "max_drawdown": w_dd,
        })
    wf_pass_n = sum(1 for row in wf if row["pass"])
    mc = monte_carlo([float(row["pnl_usd"]) for row in closed])
    gates = [
        _gate("sharpe", sharpe is not None and sharpe >= SHARPE_MIN, sharpe, f">= {SHARPE_MIN}"),
        _gate("max_drawdown", dd is not None and dd >= MAX_DD_FLOOR, dd, f">= {MAX_DD_FLOOR}"),
        _gate("positive_return", total_ret is not None and total_ret > 0, total_ret, "> 0"),
        _gate(
            "beats_spy_buy_hold",
            total_ret is not None and spy_ret is not None and total_ret > spy_ret,
            {"strategy": total_ret, "spy": spy_ret},
            "strategy return > SPY close-to-close",
        ),
        _gate("walk_forward", wf_pass_n >= WF_MIN_PASS, f"{wf_pass_n}/4", f">= {WF_MIN_PASS}/4"),
        _gate("monte_carlo", bool(mc.get("pass")), mc.get("p_value"), "p < 0.05"),
    ]
    allocated_keys = {(row["symbol"], row["entry_date"]) for row in closed}
    stock_on_taken = [
        row["stock_100"]
        for row in closed
        if isinstance(row.get("stock_100"), dict)
    ]
    skip_counts: dict[str, int] = {}
    skip_by_symbol: dict[str, dict[str, int]] = {symbol: {} for symbol in UNIVERSE}
    for row in allocated:
        if row.get("allocated"):
            continue
        if row.get("status") == "skipped_cash":
            reason = "skipped_cash"
        elif row.get("status") == "skipped":
            reason = str(row.get("reason") or "skipped")
        else:
            reason = str(row.get("status") or row.get("reason") or "other")
        skip_counts[reason] = skip_counts.get(reason, 0) + 1
        symbol = str(row.get("symbol") or "?")
        bucket = skip_by_symbol.setdefault(symbol, {})
        bucket[reason] = bucket.get(reason, 0) + 1
    reasons: dict[str, int] = {}
    for row in closed:
        reasons[row["reason"]] = reasons.get(row["reason"], 0) + 1
    by_symbol: dict[str, dict[str, Any]] = {}
    for symbol in UNIVERSE:
        rows = [row for row in closed if row["symbol"] == symbol]
        by_symbol[symbol] = {
            "n": len(rows),
            "pnl_usd": sum(float(row["pnl_usd"]) for row in rows),
            "win_rate": (sum(1 for row in rows if row["pnl_usd"] > 0) / len(rows)) if rows else None,
            "iv": (iv_by_symbol.get(symbol) or {}).get("iv"),
            "iv_source": (iv_by_symbol.get(symbol) or {}).get("used"),
        }
    stock_curve = stock_pnl_curve(stock_all, calendar)
    stock_peak = 0.0
    stock_dd_usd = 0.0
    for value in stock_curve:
        stock_peak = max(stock_peak, value)
        stock_dd_usd = min(stock_dd_usd, value - stock_peak)
    return {
        "status": "OK",
        "label": LABEL,
        "banner": BANNER,
        "not_gospel": True,
        "not_broker_fills": True,
        "not_live_paper": True,
        "recommendation": "MODEL_PROXY_ONLY",
        "gates_informational": "FAIL" if not all(g["pass"] for g in gates) else "PASS",
        "generated": datetime.now(timezone.utc).isoformat(),
        "runtime_sec": meta.get("runtime_sec"),
        "window": {"entry_start": ENTRY_START, "entry_end": ENTRY_END, "fetch_start": FETCH_START, "fetch_end": FETCH_END},
        "window_note": (
            "Entries align with the Track 100 ops stack window 2026-01-16 → 2026-09-16. "
            "Bars from 2025-09-01 exist only so SMA50 and ATR are known before the first entry. "
            "This is not the 2023–2025 walk-forward."
        ),
        "universe": UNIVERSE,
        "stock_bar_source": meta.get("source"),
        "stock_bar_notes": meta.get("notes") or [],
        "assumptions": {
            "model": "european_black_scholes_continuous_dividend",
            "r": RISK_FREE_RATE,
            "q": DIVIDEND_YIELD,
            "iv_timing": "frozen_from_recent_window_applied_backward",
            "strike_grid": "1 dollar, not a historical listed chain",
            "expiry": "Friday closest to 32 calendar days, inside 21–45",
            "option_exit": "model value at the session close, not a tick and not a fill",
            "no_polygon_option_ohlc": True,
            "early_exercise": "ignored",
            "after_hours_spot": (
                "If the underlying quote last/mid is null, spot is the last "
                "stock-hist close, else the study's last daily close. Call marks "
                "are bridge hist, plus one option-quote mark when it prints."
            ),
        },
        "iv": iv_by_symbol,
        "pnl_usd": pnl,
        "pnl_pct_on_5k": pnl / BOOK_USD,
        "pnl_after_equity_haircut_usd": haircut,
        "end_equity": end_equity,
        "win_rate": (wins / len(closed)) if closed else None,
        "n_signals": n_signals,
        "n_allocated": len(closed),
        "n_allocated_keys": len(allocated_keys),
        "max_drawdown": dd,
        "sharpe": sharpe,
        "total_return": total_ret,
        "spy_buy_hold": spy_ret,
        "exit_reasons": reasons,
        "skip_counts": skip_counts,
        "skip_by_symbol": skip_by_symbol,
        "by_symbol": by_symbol,
        "stock_100_all_signals": _stock_summary(stock_all),
        "stock_100_all_signals_max_dd_usd": stock_dd_usd,
        "stock_100_same_allocated_entries": _stock_summary(stock_on_taken),
        "walk_forward": wf,
        "walk_forward_pass": f"{wf_pass_n}/4",
        "monte_carlo": mc,
        "gates": gates,
        "rules": rules_snapshot(),
        "trades": [{k: v for k, v in row.items() if k != "marks"} | {"mark_dates": sorted((row.get("marks") or {}))} for row in closed],
        "laptop_powershell": LAPTOP_PS,
    }


def _fmt_pct(value: Any) -> str:
    if value is None or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def _fmt_usd(value: Any) -> str:
    if value is None or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return "n/a"
    return f"${float(value):,.2f}"


def _fmt_num(value: Any) -> str:
    if value is None or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return "n/a"
    return f"{float(value):.3f}"


def render_results(result: dict) -> str:
    """Markdown report. The first lines are the banner and the proxy dollars."""
    pnl = result.get("pnl_usd")
    pct = result.get("pnl_pct_on_5k")
    stock = result.get("stock_100_all_signals") or {}
    stock_same = result.get("stock_100_same_allocated_entries") or {}
    lines = [
        f"# {BANNER}",
        "",
        f"**{LABEL}**. NOT gospel. NOT broker fills. NOT live paper. NOT Track 100.",
        "",
        f"Proxy P&L **{_fmt_usd(pnl)}** ({_fmt_pct(pct)} on $5,000).",
        f"Win rate {_fmt_pct(result.get('win_rate'))}. Trades {result.get('n_allocated')}. "
        f"Max DD {_fmt_pct(result.get('max_drawdown'))}.",
        f"Informational research gates: **{result.get('gates_informational')}** "
        f"(Sharpe, drawdown, walk-forward, Monte Carlo). A pass would still be a model proxy.",
        f"Same-signal 100-share stock baseline (all technical signals, net of {COST_PER_TRADE:.2%} once): "
        f"**{_fmt_usd(stock.get('pnl_usd'))}** on {stock.get('n')} trades, "
        f"win rate {_fmt_pct(stock.get('win_rate'))}, "
        f"max DD {_fmt_usd(result.get('stock_100_all_signals_max_dd_usd'))}.",
        f"100-share stock on the entries the call book actually took: "
        f"**{_fmt_usd(stock_same.get('pnl_usd'))}** on {stock_same.get('n')} trades.",
        "",
        "These dollars are Black–Scholes marks with a frozen volatility. "
        "They are not fills. Do not trade from this file.",
        "",
        "## What was measured",
        "",
        f"- Window: {ENTRY_START} → {ENTRY_END} (Track 100 ops stack). Fetch warmup from {FETCH_START}.",
        f"- Names: {', '.join(UNIVERSE)}.",
        f"- Stock bars: `{result.get('stock_bar_source')}`.",
        f"- Signals: {result.get('n_signals')}. Allocated calls: {result.get('n_allocated')}.",
        f"- Option headline is gross BS P&L. After a {COST_PER_TRADE:.2%} debit haircut "
        f"(not a spread): {_fmt_usd(result.get('pnl_after_equity_haircut_usd'))}.",
        f"- Runtime: {result.get('runtime_sec')} seconds.",
        "",
        "## IV used",
        "",
    ]
    for symbol in UNIVERSE:
        row = (result.get("iv") or {}).get(symbol) or {}
        window = row.get("rv_window") or {}
        contract = row.get("contract") or {}
        extra = ""
        if contract:
            extra = (
                f" Contract {contract.get('expiry_iso')} {contract.get('strike')}C "
                f"ITM {_fmt_pct(contract.get('itm_pct'))}, prints {row.get('n_prints')}."
            )
        spot_src = row.get("spot_source") or (row.get("bridge_attempt") or {}).get("spot_source")
        if spot_src:
            extra += f" Spot `{spot_src}`."
        lines.append(
            f"- **{symbol}**: IV {_fmt_num(row.get('iv'))} via `{row.get('used')}` "
            f"(source `{row.get('source')}`). "
            f"10-day RV window {window.get('start')} → {window.get('end')}.{extra}"
        )
    lines += [
        "",
        "IV from the recent window is frozen and applied to every past entry. "
        "That is not the volatility the market was charging on the entry date.",
        "",
        "## Assumptions",
        "",
        f"- European Black–Scholes, continuous r = {RISK_FREE_RATE}, q = {DIVIDEND_YIELD}.",
        "- r and q are constants, not Treasury or dividend prints.",
        "- No early exercise. SPY and AVGO calls are slightly cheap versus American models.",
        "- Strike is the nearest $1 inside 5–12% ITM of the prior close (target 8%). Not a listed chain.",
        "- Expiry is the Friday closest to 32 calendar days, inside 21–45. Holiday Fridays are still used.",
        "- Entry premium uses the stock open and DTE/365. Later premiums use that session's stock close and one day less.",
        "- Underlying stop, +1R breakeven on the next session, +2R, half the model premium, 5 sessions, or 7 DTE.",
        "- Same-bar stop and target is dropped and has no P&L.",
        "- One contract. Debit above $5,000 is a skip (`premium_exceeds_book`), not a fraction.",
        "- Long calls only. No puts, no short calls.",
        "- No Polygon option OHLC was requested or invented.",
        "- After hours, a null underlying last/mid is ignored. Spot is the last stock-hist close, then this study's last daily close.",
        "- Call IV uses ~10 days of bridge hist (hourly mids, then daily). One live call quote mid/last/close is an extra sample.",
        "- Phase 9A put-credit mids are not call IV.",
        "",
        "## Stock bars",
        "",
    ]
    notes = result.get("stock_bar_notes") or []
    if notes:
        for note in notes:
            lines.append(f"- {note}")
    else:
        lines.append("- Polygon key was available to this process.")
    lines += [
        "",
        result.get("window_note") or "",
        "",
        "## By name",
        "",
        "| Name | IV | IV source | Call trades | Call P&L | Call win rate |",
        "|---|---:|---|---:|---:|---:|",
    ]
    for symbol, row in (result.get("by_symbol") or {}).items():
        lines.append(
            f"| {symbol} | {_fmt_num(row.get('iv'))} | {row.get('iv_source')} | {row.get('n')} | "
            f"{_fmt_usd(row.get('pnl_usd'))} | {_fmt_pct(row.get('win_rate'))} |"
        )
    lines += [
        "",
        "## Exit reasons (allocated calls)",
        "",
    ]
    reasons = result.get("exit_reasons") or {}
    if not reasons:
        lines.append("- No allocated call closed.")
    for reason, n in sorted(reasons.items()):
        lines.append(f"- {reason}: {n}")
    lines += ["", "## Skips", ""]
    skips = result.get("skip_counts") or {}
    if not skips:
        lines.append("- None.")
    for reason, n in sorted(skips.items()):
        lines.append(f"- {reason}: {n}")
    by_skip = result.get("skip_by_symbol") or {}
    for symbol, bucket in by_skip.items():
        if not bucket:
            continue
        detail = ", ".join(f"{reason} {n}" for reason, n in sorted(bucket.items()))
        lines.append(f"- {symbol}: {detail}")
    lines += [
        "",
        "## Informational gates",
        "",
        "These use the EXP-0013 thresholds on an 8-month proxy. A FAIL here is a research note. "
        "A PASS would still not be a fill.",
        "",
    ]
    for gate in result.get("gates") or []:
        lines.append(f"- **{gate['label']}** {gate['name']}: {gate.get('value')} (need {gate.get('need')})")
    lines += [
        "",
        f"Walk-forward {result.get('walk_forward_pass')}.",
        "",
    ]
    for row in result.get("walk_forward") or []:
        lines.append(
            f"- **{row.get('label')}** {row.get('window')} {row.get('start')} → {row.get('end')}: "
            f"n={row.get('n')} pnl={_fmt_usd(row.get('pnl_usd'))} "
            f"sharpe={_fmt_num(row.get('sharpe'))} dd={_fmt_pct(row.get('max_drawdown'))}"
        )
    mc = result.get("monte_carlo") or {}
    lines += [
        "",
        f"Monte Carlo (5,000, seed 42): p={mc.get('p_value')} ran={mc.get('ran')} "
        f"{'FAIL' if not mc.get('pass') else 'PASS'} {mc.get('reason') or ''}".rstrip(),
        "",
        "## Trades",
        "",
        "| Entry | Exit | Name | Strike | DTE | Reason | Call P&L | Debit | Stock 100 P&L |",
        "|---|---|---|---:|---:|---|---:|---:|---:|",
    ]
    for row in result.get("trades") or []:
        stock_row = row.get("stock_100") or {}
        lines.append(
            f"| {row.get('entry_date')} | {row.get('exit_date')} | {row.get('symbol')} | "
            f"{row.get('strike')} | {row.get('dte')} | {row.get('reason')} | "
            f"{_fmt_usd(row.get('pnl_usd'))} | {_fmt_usd(row.get('debit_usd'))} | "
            f"{_fmt_usd(stock_row.get('pnl_usd'))} |"
        )
    if not result.get("trades"):
        lines.append("| — | — | — | — | — | no allocated trades | — | — | — |")
    lines += [
        "",
        "## Laptop rerun",
        "",
        "The cloud VM has no IBKR. This file is whatever that VM could measure. "
        "On the laptop the same script prefers Polygon and the bridge, and overwrites this report.",
        "",
        "```powershell",
        LAPTOP_PS,
        "```",
        "",
        "One command after the pull: `.\\experiments\\EXP-0027\\run_on_laptop.ps1`",
        "",
    ]
    return "\n".join(lines)


def write_outputs(result: dict) -> None:
    """Write the markdown and JSON reports next to this script."""
    EXP_DIR.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(render_results(result), encoding="utf-8")
    OUT_JSON.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"wrote {OUT_MD}", flush=True)
    print(f"wrote {OUT_JSON}", flush=True)


def not_run(reason: str, detail: str, runtime_sec: float, meta: dict | None = None) -> dict[str, Any]:
    """Empty P&L. Used when stock bars cannot be loaded. No invented prices."""
    return {
        "status": "NOT_RUN",
        "label": LABEL,
        "banner": BANNER,
        "not_gospel": True,
        "not_broker_fills": True,
        "not_live_paper": True,
        "recommendation": "MODEL_PROXY_ONLY",
        "reason": reason,
        "detail": detail,
        "pnl_usd": None,
        "pnl_pct_on_5k": None,
        "win_rate": None,
        "n_allocated": 0,
        "max_drawdown": None,
        "stock_100_all_signals": {"n": 0, "pnl_usd": None, "win_rate": None},
        "stock_100_same_allocated_entries": {"n": 0, "pnl_usd": None},
        "stock_100_all_signals_max_dd_usd": None,
        "runtime_sec": round(runtime_sec, 1),
        "stock_bar_source": None,
        "stock_bar_notes": (meta or {}).get("notes") or [detail],
        "iv": {},
        "by_symbol": {},
        "gates": [],
        "gates_informational": "NOT_RUN",
        "walk_forward": [],
        "walk_forward_pass": "n/a",
        "monte_carlo": {},
        "trades": [],
        "exit_reasons": {},
        "skip_counts": {},
        "n_signals": 0,
        "pnl_after_equity_haircut_usd": None,
        "window_note": "No bars, so the window was not walked.",
        "laptop_powershell": LAPTOP_PS,
    }


def main(fetch_remote: Callable[[], dict[str, Any]] | None = None) -> dict[str, Any]:
    """Load bars, measure IV, walk the proxy, write the report."""
    started = time.time()
    print("EXP-0027 ITM BS proxy — model marks only, no orders", flush=True)
    loaded = load_stock_bars(fetch_remote)
    if not loaded.get("ok"):
        result = not_run(
            "stock_bars_unavailable",
            str(loaded.get("error")),
            time.time() - started,
            loaded,
        )
        result["stock_bar_source"] = loaded.get("source")
        write_outputs(result)
        return result
    bars = loaded["bars"]
    iv = measure_iv(bars)
    meta = {
        "source": loaded.get("source"),
        "notes": loaded.get("notes") or [],
        "runtime_sec": None,
    }
    result = run_proxy(bars, iv, meta)
    result["runtime_sec"] = round(time.time() - started, 1)
    result["stock_bar_source"] = loaded.get("source")
    result["stock_bar_notes"] = loaded.get("notes") or []
    write_outputs(result)
    print(
        f"{BANNER} proxy {_fmt_usd(result.get('pnl_usd'))} "
        f"({_fmt_pct(result.get('pnl_pct_on_5k'))} on $5k) "
        f"trades={result.get('n_allocated')}",
        flush=True,
    )
    return result


if modal is not None and app is not None:
    @app.local_entrypoint()
    def run() -> None:
        """Laptop Modal entry: Polygon secret for bars, local bridge for IV."""

        def _remote() -> dict[str, Any]:
            return fetch_stock_bars_modal.remote(list(UNIVERSE), FETCH_START, FETCH_END)

        main(fetch_remote=_remote)


if __name__ == "__main__":
    main()
