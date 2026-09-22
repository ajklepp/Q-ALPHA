"""
EXP-0026 — ITM long-call research study (Modal / Polygon).

Long calls only. Parallel to Track 100 equity. Does not place orders and does
not change Peak Hour or Track 100 paper defaults.

Laptop (repo root, secrets polygon-api-key and q-alpha-secrets):

    Set-Location C:\\Users\\ajkle\\Documents\\Q-ALPHA
    .\\venv\\Scripts\\python.exe -m modal run experiments/EXP-0026/study_itm_long_call_modal.py

If Polygon will not return option bars, the run writes NOT_RUN and leaves
P&L empty. It does not invent fills.
"""
from __future__ import annotations

import json
import math
import os
import random
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

APP_NAME = "q-alpha-exp026-itm-long-call"
_HERE = Path(__file__).resolve()
EXP_DIR = _HERE.parent
OUT_MD = EXP_DIR / "results.md"
OUT_JSON = EXP_DIR / "study_itm_long_call_metrics.json"

POLYGON = "https://api.polygon.io"
# Equity bps cost from EXP-0012. Not applied here: option fills are premium
# open/close from bars, and this study does not invent a spread or commission.
EQUITY_COST_PER_TRADE_NOT_USED = 0.0015

BOOK_USD = 5000.0  # research book, one cash pool
CONTRACT_MULTIPLIER = 100  # standard US equity option
ITM_TARGET_PCT = 0.08  # strike ~= spot * 0.92; depth stand-in, not a broker delta
ITM_MIN_PCT = 0.05
ITM_MAX_PCT = 0.12
DTE_MIN = 21  # calendar days
DTE_MAX = 45
DTE_EXIT = 7  # leave before the last week
MAX_HOLD_SESSIONS = 5  # same horizon idea as the 5-day equity study
ATR_WINDOW = 14
SMA_FAST = 20  # cross average; excludes the bar being tested
SMA_REGIME = 50  # SPY regime average; excludes the last close
STOP_ATR_MULT = 1.0  # initial underlying stop distance
TARGET_R = 2.0  # underlying +2R, then exit the call
TRAIL_ARM_R = 1.0  # after a session tags +1R, next session stop = entry
PREMIUM_STOP_FRAC = 0.50  # exit if option close <= half the entry premium
SLEEP_SEC = 0.12  # Polygon pause between ticker requests
PROBE_MIN_BARS = 3
SPLIT_GAP_UP = 1.8  # unadjusted close ratio treated as a split break
SPLIT_GAP_DOWN = 0.55
MC_SIMS = 5000
SHARPE_MIN = 1.5
MAX_DD_FLOOR = -0.15  # pass when drawdown is not worse than -15%
WF_MIN_PASS = 3
MIN_TRADES_WINDOW = 5
MIN_TRADES_MC = 5
STRIKE_QUERY_PAD = 0.01  # wider server filter; select_itm_call enforces the band
FETCH_START = "2022-06-01"  # warmup for SMA50 before the first entry window
FETCH_END = "2025-12-31"
ENTRY_START = "2023-01-01"
ENTRY_END = "2025-12-31"

# Liquid underlyings. Order is cash priority when two names signal the same morning.
UNIVERSE = [
    "SPY", "QQQ", "IWM", "NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "META", "AMD",
]

WINDOWS = (
    ("W1", "2023-01-01", "2023-12-31"),
    ("W2", "2024-01-01", "2024-06-30"),
    ("W3", "2024-07-01", "2024-12-31"),
    ("W4", "2025-01-01", "2025-12-31"),
)

LAPTOP_PS = (
    "Set-Location C:\\Users\\ajkle\\Documents\\Q-ALPHA\n"
    ".\\venv\\Scripts\\python.exe -m modal run experiments/EXP-0026/study_itm_long_call_modal.py"
)
BRIDGE_PS = (
    "Set-Location C:\\Users\\ajkle\\Documents\\Q-ALPHA\n"
    ".\\candidates\\start_options_bridge.ps1\n"
    ".\\venv\\Scripts\\python.exe experiments\\EXP-0026\\probe_options_bridge.py"
)

try:
    import modal
except ImportError:  # unit tests and cloud checkout have no modal package
    modal = None  # type: ignore


def rules_snapshot() -> dict[str, Any]:
    """Numeric rules actually coded, so a results file can echo them."""
    return {
        "book_usd": BOOK_USD,
        "multiplier": CONTRACT_MULTIPLIER,
        "itm_min_pct": ITM_MIN_PCT,
        "itm_target_pct": ITM_TARGET_PCT,
        "itm_max_pct": ITM_MAX_PCT,
        "dte_min": DTE_MIN,
        "dte_max": DTE_MAX,
        "dte_exit": DTE_EXIT,
        "max_hold_sessions": MAX_HOLD_SESSIONS,
        "atr_window": ATR_WINDOW,
        "stop_atr_mult": STOP_ATR_MULT,
        "target_r": TARGET_R,
        "trail_arm_r": TRAIL_ARM_R,
        "premium_stop_frac": PREMIUM_STOP_FRAC,
        "sharpe_min": SHARPE_MIN,
        "max_dd_floor": MAX_DD_FLOOR,
        "equity_cost_not_used": EQUITY_COST_PER_TRADE_NOT_USED,
        "side": "BUY_TO_OPEN_CALL",
        "delta": "not_fetched_percent_itm_is_the_depth_rule",
    }


def _redact(text: str, secret: str) -> str:
    """Drop the API key if a provider echoes the request URL."""
    raw = text or ""
    if secret:
        raw = raw.replace(secret, "[redacted]")
    return raw[:240]


def _not_authorized(body: dict) -> bool:
    """True when Polygon refuses the options (or stocks) entitlement."""
    if body.get("_status") in (401, 403):
        return True
    status = str(body.get("status") or "").upper()
    if status in {"NOT_AUTHORIZED", "NOT_ENTITLED", "FORBIDDEN"}:
        return True
    msg = str(body.get("message") or body.get("error") or "").lower()
    return "not authorized" in msg or "not entitled" in msg


def classify_probe(ref: dict, agg: dict | None) -> dict[str, Any]:
    """Decide whether historical option bars are actually available.

    A contract list without aggregates is not enough to price a call.
    """
    if _not_authorized(ref):
        return {"ok": False, "reason": "polygon_options_not_entitled", "stage": "contracts"}
    results = ref.get("results") or []
    ticker = str(results[0].get("ticker") or "") if results else ""
    if not ticker:
        return {"ok": False, "reason": "polygon_options_contracts_empty", "stage": "contracts"}
    if agg is None:
        return {
            "ok": False,
            "reason": "polygon_option_bars_missing",
            "stage": "aggs",
            "sample_ticker": ticker,
        }
    if _not_authorized(agg):
        return {
            "ok": False,
            "reason": "polygon_options_not_entitled",
            "stage": "aggs",
            "sample_ticker": ticker,
        }
    n_bars = len(agg.get("results") or [])
    if n_bars < PROBE_MIN_BARS:
        return {
            "ok": False,
            "reason": "polygon_option_bars_empty",
            "stage": "aggs",
            "sample_ticker": ticker,
            "n_bars": n_bars,
        }
    return {"ok": True, "sample_ticker": ticker, "n_bars": n_bars}


def parse_aggs(body: dict, allow_zero: bool = False) -> list[dict[str, Any]]:
    """Polygon aggregate rows to dated OHLC bars. Duplicate dates keep the last row."""
    by_date: dict[str, dict[str, Any]] = {}
    for raw in body.get("results") or []:
        try:
            d = datetime.fromtimestamp(int(raw["t"]) / 1000, tz=timezone.utc).date().isoformat()
            o = float(raw["o"])
            h = float(raw["h"])
            low = float(raw["l"])
            c = float(raw["c"])
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(x) for x in (o, h, low, c)):
            continue
        if min(o, h, low, c) < 0:
            continue
        if not allow_zero and min(o, h, low, c) <= 0:
            continue
        if h < low:
            continue
        by_date[d] = {"date": d, "o": o, "h": h, "l": low, "c": c}
    return [by_date[k] for k in sorted(by_date)]


def bull_regime(spy_closes: list[float]) -> bool:
    """SPY prior close at or above the prior 50-day average.

    The average excludes the last close (shift 1). Too little history is
    not a bull day — the trade is skipped.
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


def select_itm_call(
    contracts: list[dict],
    spot: float,
    entry: date,
) -> dict[str, Any] | None:
    """Pick the call whose percent-in-the-money is closest to the 8% target.

    Band is 5–12% ITM and 21–45 calendar days. Puts are ignored. This is a
    moneyness rule because historical Polygon aggregates do not include delta.
    """
    if spot <= 0:
        return None
    best: dict[str, Any] | None = None
    best_key: tuple[float, float, float] | None = None
    for contract in contracts:
        if str(contract.get("contract_type") or "").lower() != "call":
            continue
        exp_raw = str(contract.get("expiration_date") or "")[:10]
        try:
            exp = date.fromisoformat(exp_raw)
            strike = float(contract.get("strike_price"))
        except (TypeError, ValueError):
            continue
        dte = (exp - entry).days
        if dte < DTE_MIN or dte > DTE_MAX or strike <= 0:
            continue
        itm = (spot - strike) / spot
        if itm < ITM_MIN_PCT or itm > ITM_MAX_PCT:
            continue
        key = (abs(itm - ITM_TARGET_PCT), abs(strike - spot * (1.0 - ITM_TARGET_PCT)), strike)
        if best_key is None or key < best_key:
            best_key = key
            best = {
                "ticker": contract.get("ticker"),
                "strike": strike,
                "expiration": exp.isoformat(),
                "dte": dte,
                "itm_pct": itm,
            }
    return best


def size_long_call(option_open: float) -> dict[str, Any]:
    """One contract, or skip when the debit is above the $5,000 book.

    Standard contracts are 100 shares. This study does not invent a fraction
    of a contract when NVDA/SPY/TSLA premium does not fit.
    """
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
        "book_frac": debit / BOOK_USD,
    }


def _opt_px(bar: dict | None, field: str) -> float | None:
    """Option price from a bar. Zero is kept. Missing or negative is missing."""
    if not bar or field not in bar or bar[field] is None:
        return None
    try:
        val = float(bar[field])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(val) or val < 0:
        return None
    return val


def simulate_long_call(
    entry_date: str,
    entry_und_open: float,
    entry_opt_open: float,
    atr: float,
    expiry: str,
    und_bars: list[dict],
    opt_by_date: dict[str, dict],
) -> dict[str, Any]:
    """Walk the call with an underlying stop/target. Fills are option open and close only.

    Same-bar stop and +2R is ambiguous and is dropped (no P&L). A missing option
    bar is unpriced (no P&L). The trail moves only after the bar that tagged +1R.
    """
    base = {
        "entry_date": entry_date,
        "status": "unpriced",
        "pnl_usd": None,
        "reason": "bad_inputs",
    }
    if entry_und_open <= 0 or entry_opt_open <= 0 or atr <= 0:
        return base
    risk = STOP_ATR_MULT * atr
    stop = entry_und_open - risk
    target = entry_und_open + TARGET_R * risk
    arm_level = entry_und_open + TRAIL_ARM_R * risk
    initial_stop = stop
    debit = entry_opt_open * CONTRACT_MULTIPLIER
    hold = [b for b in und_bars if b["date"] >= entry_date][:MAX_HOLD_SESSIONS]
    if not hold or hold[0]["date"] != entry_date:
        base["reason"] = "missing_underlying_bar"
        return base
    marks: dict[str, float] = {}
    exp = date.fromisoformat(expiry)
    for i, bar in enumerate(hold):
        opt = opt_by_date.get(bar["date"])
        close_px = _opt_px(opt, "c")
        if close_px is None:
            return {
                "entry_date": entry_date,
                "status": "unpriced",
                "pnl_usd": None,
                "reason": "missing_option_bar",
                "exit_date": bar["date"],
            }
        if i == 0:
            open_px = _opt_px(opt, "o")
            if open_px is None:
                return {
                    "entry_date": entry_date,
                    "status": "unpriced",
                    "pnl_usd": None,
                    "reason": "missing_option_open",
                    "exit_date": bar["date"],
                }
            # Caller fill and bar open are the same print. Refuse a mismatch.
            if abs(open_px - entry_opt_open) > 1e-6:
                return {
                    "entry_date": entry_date,
                    "status": "unpriced",
                    "pnl_usd": None,
                    "reason": "option_open_mismatch",
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
            }
        reason = None
        if hit_stop:
            reason = "underlying_stop"
        elif hit_target:
            reason = "target_2r"
        elif close_px <= entry_opt_open * PREMIUM_STOP_FRAC:
            reason = "premium_half"
        else:
            dte_left = (exp - date.fromisoformat(bar["date"])).days
            if dte_left <= DTE_EXIT:
                reason = "dte_floor"
            elif i == MAX_HOLD_SESSIONS - 1:
                reason = "time_5d"
        if reason is not None:
            pnl = (close_px - entry_opt_open) * CONTRACT_MULTIPLIER
            shares = int(BOOK_USD // entry_und_open)
            return {
                "entry_date": entry_date,
                "exit_date": bar["date"],
                "status": "closed",
                "reason": reason,
                "pnl_usd": pnl,
                "debit_usd": debit,
                "exit_premium_usd": close_px * CONTRACT_MULTIPLIER,
                "entry_opt_open": entry_opt_open,
                "exit_opt_close": close_px,
                "entry_und_open": entry_und_open,
                "initial_stop": initial_stop,
                "marks": marks,
                "fill_proxy": "option_daily_open_to_daily_close",
                "call_max_loss_usd": debit,
                "controlled_shares": CONTRACT_MULTIPLIER,
                "stock_seat_notional_usd": entry_und_open * CONTRACT_MULTIPLIER,
                "stock_seat_stop_usd": (entry_und_open - initial_stop) * CONTRACT_MULTIPLIER,
                "stock_shares_book": shares,
                "stock_stop_distance_usd": (entry_und_open - initial_stop) * shares,
            }
        if bar["h"] >= arm_level:
            stop = max(stop, entry_und_open)
    return {
        "entry_date": entry_date,
        "status": "unpriced",
        "pnl_usd": None,
        "reason": "no_exit",
    }


def build_signals(bars_by_symbol: dict[str, list[dict]]) -> list[dict[str, Any]]:
    """Causal long entries: SPY regime on, and the name just crossed above SMA20.

    Features use sessions before the entry date. The entry session's open is
    the underlying reference for the stop. Strike selection uses the prior close.
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
            signals.append({
                "symbol": symbol,
                "entry_date": entry_date,
                "spot": closes[-1],
                "entry_und_open": float(bar["o"]),
                "atr": atr,
            })
    signals.sort(key=lambda row: (row["entry_date"], UNIVERSE.index(row["symbol"])))
    return signals


def allocate_book(trades: list[dict], book: float = BOOK_USD) -> list[dict]:
    """Spend cash at the open. Exit proceeds are free on later sessions only."""
    ordered = sorted(trades, key=lambda row: (row.get("entry_date") or "", row.get("symbol") or ""))
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
    """Mark open calls at that session's option close. Missing marks abort the curve."""
    accepted = [row for row in trades if row.get("allocated") and row.get("status") == "closed"]
    if not calendar:
        return [book]
    by_entry: dict[str, list[dict]] = {}
    for row in accepted:
        by_entry.setdefault(row["entry_date"], []).append(row)
    cash = book
    open_pos: list[dict] = []
    curve: list[float] = []
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
                # Valuation only: reuse the last option close on or before this
                # session when that name did not print. Not a new fill.
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


def buy_hold_return(bars: list[dict], start: str, end: str) -> float | None:
    """SPY close-to-close over the study window. None when the series is too short."""
    inside = [bar for bar in bars if start <= bar["date"] <= end and bar["c"] > 0]
    if len(inside) < 2:
        return None
    return inside[-1]["c"] / inside[0]["c"] - 1.0


def monte_carlo(pnls: list[float], n_sims: int = MC_SIMS, seed: int = 42) -> dict[str, Any]:
    """Bootstrap trade P&L the same way EXP-0012 does (5,000 draws, seed 42).

    p-value is the share of random Sharpes at least as large as the real one.
    Fewer than 5 trades does not pass.
    """
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
    """Map an entry date to its walk-forward window name."""
    for name, start, end in WINDOWS:
        if start <= entry_date <= end:
            return name
    return None


def _gate(name: str, ok: bool, value: Any, need: str) -> dict[str, Any]:
    """One pass/fail row. Failures stay labeled FAIL."""
    return {"name": name, "pass": bool(ok), "value": value, "need": need, "label": "PASS" if ok else "FAIL"}


def summarize(
    allocated: list[dict],
    skip_counts: dict[str, int],
    spy_bars: list[dict],
    calendar: list[str],
    runtime_sec: float,
    probe: dict,
) -> dict[str, Any]:
    """Score closed, cash-allocated trades. Empty P&L is a measured FAIL, not a guess."""
    closed = [row for row in allocated if row.get("allocated") and row.get("status") == "closed"]
    calendar = sorted(set(calendar) | {day for row in closed for day in (row.get("marks") or {})})
    curve = equity_curve(allocated, calendar)
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
    spy_ret = buy_hold_return(spy_bars, ENTRY_START, ENTRY_END)
    pnl = sum(float(row["pnl_usd"]) for row in closed)
    by_window: dict[str, list[dict]] = {name: [] for name, _, _ in WINDOWS}
    for row in closed:
        name = _window_for(row["entry_date"])
        if name:
            by_window[name].append(row)
    wf = []
    for name, start, end in WINDOWS:
        rows = by_window[name]
        cal = [day for day in calendar if start <= day <= end]
        # Hold marks can sit a few sessions past the window edge.
        for row in rows:
            for day in (row.get("marks") or {}):
                if day not in cal:
                    cal.append(day)
        cal = sorted(set(cal))
        w_curve = equity_curve(rows, cal) if rows else [BOOK_USD for _ in cal] or [BOOK_USD]
        if w_curve is None:
            wf.append({"window": name, "pass": False, "label": "FAIL", "reason": "missing_marks", "n": len(rows)})
            continue
        w_sharpe = sharpe_daily(w_curve)
        w_dd = max_drawdown(w_curve)
        w_ret = (w_curve[-1] / w_curve[0] - 1.0) if w_curve and w_curve[0] else 0.0
        ok = len(rows) >= MIN_TRADES_WINDOW and w_sharpe >= SHARPE_MIN and w_ret > 0 and w_dd >= MAX_DD_FLOOR
        wf.append({
            "window": name,
            "pass": ok,
            "label": "PASS" if ok else "FAIL",
            "n": len(rows),
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
        _gate("walk_forward", wf_pass_n >= WF_MIN_PASS, f"{wf_pass_n}/{len(WINDOWS)}", f">= {WF_MIN_PASS}/4"),
        _gate("monte_carlo", bool(mc.get("pass")), mc.get("p_value"), "p < 0.05"),
        {
            "name": "lightgbm_precision",
            "pass": False,
            "value": None,
            "need": "not applicable — this study has no classifier",
            "label": "N/A",
        },
    ]
    # Precision is recorded and is not part of the pass set.
    required = [g for g in gates if g["name"] != "lightgbm_precision"]
    passed = all(g["pass"] for g in required)
    return {
        "status": "OK",
        "recommendation": "PASS" if passed else "FAIL",
        "reason": "gates_passed" if passed else "gates_failed",
        "detail": "Closed trades use option daily open and option daily close only.",
        "generated": datetime.now(timezone.utc).isoformat(),
        "runtime_sec": round(runtime_sec, 1),
        "pnl_usd": pnl,
        "end_equity": end_equity,
        "sharpe": sharpe,
        "max_drawdown": dd,
        "total_return": total_ret,
        "spy_buy_hold": spy_ret,
        "n_allocated": len(closed),
        "walk_forward": wf,
        "walk_forward_pass": f"{wf_pass_n}/{len(WINDOWS)}",
        "monte_carlo": mc,
        "monte_carlo_p_value": mc.get("p_value"),
        "gates": gates,
        "skip_counts": dict(skip_counts),
        "probe": probe,
        "rules": rules_snapshot(),
        "trades": closed,
        "laptop_powershell": LAPTOP_PS,
    }


def not_run(reason: str, detail: str, runtime_sec: float, probe: dict | None = None) -> dict[str, Any]:
    """Result object with every performance field empty."""
    return {
        "status": "NOT_RUN",
        "recommendation": "NOT_RUN",
        "reason": reason,
        "detail": detail,
        "generated": datetime.now(timezone.utc).isoformat(),
        "runtime_sec": round(runtime_sec, 1),
        "pnl_usd": None,
        "end_equity": None,
        "sharpe": None,
        "max_drawdown": None,
        "total_return": None,
        "spy_buy_hold": None,
        "n_allocated": None,
        "walk_forward": None,
        "walk_forward_pass": None,
        "monte_carlo": None,
        "monte_carlo_p_value": None,
        "gates": None,
        "skip_counts": None,
        "probe": probe,
        "rules": rules_snapshot(),
        "trades": None,
        "laptop_powershell": LAPTOP_PS,
    }


def _fmt_gate(gate: dict) -> str:
    """One markdown bullet for a gate, with the label in front of the value."""
    label = gate["label"]
    value = gate["value"]
    if value is None:
        shown = "not computed"
    elif isinstance(value, float):
        shown = f"{value:.4f}"
    else:
        shown = str(value)
    return f"- **{gate['name']}**: **{label}** — {shown} (need {gate['need']})"


def render_results(result: dict) -> str:
    """Markdown report. NOT_RUN never prints a performance number."""
    lines = [
        "# EXP-0026 — ITM long-call study",
        "",
        "Research only. Long calls. Separate from Track 100 equity. No orders.",
        "",
        f"**Status:** {result.get('status')}",
        f"**Generated:** {result.get('generated')}",
        f"**Runtime:** {result.get('runtime_sec')}s",
        f"**Recommendation:** **{result.get('recommendation')}**",
        "",
        f"**Reason:** `{result.get('reason')}`",
        "",
        str(result.get("detail") or ""),
        "",
    ]
    if result.get("recommendation") == "NOT_RUN":
        lines += [
            "Option performance was not computed. This file has no P&L, Sharpe, drawdown, walk-forward score, or Monte Carlo p-value.",
            "",
            "## Rules (what a laptop run will apply)",
            "",
            "See `README.md`. Constants:",
            "",
            "```json",
            json.dumps(result.get("rules"), indent=2),
            "```",
            "",
            "## Laptop Modal",
            "",
            "```powershell",
            LAPTOP_PS,
            "```",
            "",
            "Optional read-only TWS bridge probe (does not build P&L):",
            "",
            "```powershell",
            BRIDGE_PS,
            "```",
            "",
        ]
        if result.get("probe"):
            lines += ["## Probe", "", "```json", json.dumps(result["probe"], indent=2), "```", ""]
        return "\n".join(lines)

    lines += ["## Gates", ""]
    for gate in result.get("gates") or []:
        lines.append(_fmt_gate(gate))
    lines += [
        "",
        "LightGBM precision is **not applicable** (no classifier in this study) and is not counted as a pass.",
        "",
        f"**Allocated trades:** {result.get('n_allocated')}",
        f"**P&L USD (gross premium):** {result.get('pnl_usd')}",
        f"**Walk-forward:** {result.get('walk_forward_pass')}",
        "",
        "### Walk-forward windows",
        "",
    ]
    for row in result.get("walk_forward") or []:
        lines.append(
            f"- **{row.get('window')}**: **{row.get('label')}** — n={row.get('n')} "
            f"sharpe={row.get('sharpe')} return={row.get('return')} max_dd={row.get('max_drawdown')}"
        )
    lines += ["", "### Skip counts", "", "```json", json.dumps(result.get("skip_counts"), indent=2), "```", ""]
    lines += ["### Trades (allocated)", ""]
    trades = result.get("trades") or []
    if not trades:
        lines.append("No allocated closed trades.")
    else:
        lines += [
            "| Symbol | Entry | Exit | Reason | Debit | P&L | Call max loss | 100-share seat |",
            "|---|---|---|---|---:|---:|---:|---:|",
        ]
        for row in trades[:40]:
            lines.append(
                f"| {row.get('symbol')} | {row.get('entry_date')} | {row.get('exit_date')} | {row.get('reason')} | "
                f"{float(row.get('debit_usd') or 0):.2f} | {float(row.get('pnl_usd') or 0):.2f} | "
                f"{float(row.get('call_max_loss_usd') or 0):.2f} | {float(row.get('stock_seat_notional_usd') or 0):.2f} |"
            )
        if len(trades) > 40:
            lines.append("")
            lines.append(f"Table shows 40 of {len(trades)}. Full list is in `study_itm_long_call_metrics.json`.")
    lines += [
        "",
        "Fills are the option daily open and the option daily close (`option_daily_open_to_daily_close`). "
        "That is a bar proxy for the underlying stop touch, not a tick fill and not a broker report.",
        "",
        f"Equity `COST_PER_TRADE` {EQUITY_COST_PER_TRADE_NOT_USED} is not subtracted.",
        "",
    ]
    return "\n".join(lines)


def write_outputs(result: dict, exp_dir: Path | None = None) -> None:
    """Write results.md and the metrics JSON. Trades on a NOT_RUN stay null."""
    folder = exp_dir or EXP_DIR
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "results.md").write_text(render_results(result), encoding="utf-8")
    (folder / "study_itm_long_call_metrics.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _get(url: str, params: dict, retries: int = 4) -> dict:
    """GET JSON with retry. 401/403 return immediately. The key is never printed."""
    import requests

    last: Exception | None = None
    for i in range(retries):
        try:
            response = requests.get(url, params=params, timeout=45)
            try:
                body = response.json()
            except Exception:
                body = {"_error": "non_json"}
            if not isinstance(body, dict):
                body = {"_error": "non_object", "raw_type": type(body).__name__}
            body["_status"] = response.status_code
            if response.status_code == 429:
                time.sleep(0.7 * (i + 1))
                continue
            if response.status_code in (401, 403):
                return body
            if response.status_code == 404:
                body["results"] = body.get("results") or []
                return body
            if response.status_code >= 400:
                if i < retries - 1:
                    time.sleep(0.3 * (i + 1))
                    continue
                return body
            return body
        except Exception as exc:
            last = exc
            time.sleep(0.3 * (i + 1))
    return {"_error": str(last)[:160] if last else "request_failed"}


def _probe_extra(body: dict, key: str, stage: str) -> dict[str, Any]:
    """Short Polygon error fields with the API key removed."""
    return {
        "stage": stage,
        "http_status": body.get("_status"),
        "polygon_status": body.get("status"),
        "message": _redact(str(body.get("message") or body.get("error") or body.get("_error") or ""), key),
    }


def probe_polygon_options(key: str) -> dict[str, Any]:
    """One historical SPY call plus a month of daily bars. Counts bars; does not score them."""
    params = {
        "underlying_ticker": "SPY",
        "contract_type": "call",
        "expiration_date.gte": "2024-06-01",
        "expiration_date.lte": "2024-06-30",
        "expired": "true",
        "limit": 5,
        "sort": "expiration_date",
        "order": "asc",
        "apiKey": key,
    }
    ref = _get(f"{POLYGON}/v3/reference/options/contracts", params)
    time.sleep(SLEEP_SEC)
    if ref.get("_status") == 400:
        retry = dict(params)
        retry.pop("expired", None)
        ref = _get(f"{POLYGON}/v3/reference/options/contracts", retry)
        time.sleep(SLEEP_SEC)
    if _not_authorized(ref) or not (ref.get("results") or []):
        decision = classify_probe(ref, None)
        decision["detail"] = _probe_extra(ref, key, str(decision.get("stage") or "contracts"))
        return decision
    ticker = str(ref["results"][0]["ticker"])
    agg = _get(
        f"{POLYGON}/v2/aggs/ticker/{quote(ticker, safe='')}/range/1/day/2024-06-03/2024-06-28",
        {"adjusted": "false", "sort": "asc", "limit": 50000, "apiKey": key},
    )
    time.sleep(SLEEP_SEC)
    decision = classify_probe(ref, agg)
    decision["detail"] = _probe_extra(agg, key, "aggs")
    return decision


def fetch_stock_daily(key: str, symbol: str) -> dict[str, Any]:
    """Unadjusted daily OHLC so strikes line up with the prices that traded."""
    body = _get(
        f"{POLYGON}/v2/aggs/ticker/{symbol}/range/1/day/{FETCH_START}/{FETCH_END}",
        {"adjusted": "false", "sort": "asc", "limit": 50000, "apiKey": key},
    )
    time.sleep(SLEEP_SEC)
    if _not_authorized(body):
        return {"symbol": symbol, "bars": [], "error": "not_authorized", "detail": _probe_extra(body, key, "stocks")}
    return {"symbol": symbol, "bars": parse_aggs(body), "error": None}


def fetch_contracts(key: str, symbol: str, as_of: str, spot: float) -> dict[str, Any]:
    """Calls in the ITM band and DTE window as of the entry date."""
    as_of_d = date.fromisoformat(as_of)
    params: dict[str, Any] = {
        "underlying_ticker": symbol,
        "contract_type": "call",
        "expiration_date.gte": (as_of_d + timedelta(days=DTE_MIN)).isoformat(),
        "expiration_date.lte": (as_of_d + timedelta(days=DTE_MAX)).isoformat(),
        "strike_price.gte": round(spot * (1.0 - ITM_MAX_PCT - STRIKE_QUERY_PAD), 2),
        "strike_price.lte": round(spot * (1.0 - ITM_MIN_PCT + STRIKE_QUERY_PAD), 2),
        "as_of": as_of,
        "expired": "true",
        "limit": 250,
        "sort": "strike_price",
        "order": "asc",
        "apiKey": key,
    }
    url = f"{POLYGON}/v3/reference/options/contracts"
    body = _get(url, params)
    time.sleep(SLEEP_SEC)
    used_fallback = False
    if body.get("_status") == 400:
        used_fallback = True
        params.pop("as_of", None)
        body = _get(url, params)
        time.sleep(SLEEP_SEC)
    if _not_authorized(body):
        return {"error": "not_authorized", "contracts": [], "detail": _probe_extra(body, key, "contracts")}
    contracts = list(body.get("results") or [])
    pages = 1
    while body.get("next_url") and pages < 4:
        body = _get(str(body["next_url"]), {"apiKey": key})
        time.sleep(SLEEP_SEC)
        if _not_authorized(body):
            return {"error": "not_authorized", "contracts": [], "detail": _probe_extra(body, key, "contracts")}
        contracts.extend(body.get("results") or [])
        pages += 1
    return {"error": None, "contracts": contracts, "as_of_fallback": used_fallback}


def fetch_option_bars(key: str, ticker: str, start: str, end: str) -> dict[str, Any]:
    """Daily option OHLC. adjusted=false. Empty is a missing bar, not a zero fill."""
    body = _get(
        f"{POLYGON}/v2/aggs/ticker/{quote(ticker, safe='')}/range/1/day/{start}/{end}",
        {"adjusted": "false", "sort": "asc", "limit": 50000, "apiKey": key},
    )
    time.sleep(SLEEP_SEC)
    if _not_authorized(body):
        return {"error": "not_authorized", "bars": [], "detail": _probe_extra(body, key, "option_aggs")}
    return {"error": None, "bars": parse_aggs(body, allow_zero=True)}


def run_study_body() -> dict[str, Any]:
    """Probe option bars, then price the rule. Any entitlement miss returns NOT_RUN."""
    t0 = time.time()
    key = os.environ.get("POLYGON_API_KEY") or ""
    if not key:
        return not_run(
            "missing_POLYGON_API_KEY",
            "Modal secret polygon-api-key did not provide POLYGON_API_KEY. No option request was sent.",
            time.time() - t0,
        )
    probe = probe_polygon_options(key)
    if not probe.get("ok"):
        return not_run(
            str(probe.get("reason") or "polygon_options_unavailable"),
            "Polygon did not return usable historical option bars. P&L was not computed. "
            "TWS bridge 127.0.0.1:8787 is laptop-only and its default history is about 10 days, "
            "so it is not a backfill for this 2023–2025 study.",
            time.time() - t0,
            probe=probe,
        )
    bars_by_symbol: dict[str, list[dict]] = {}
    for i, symbol in enumerate(UNIVERSE, start=1):
        fetched = fetch_stock_daily(key, symbol)
        bars_by_symbol[symbol] = fetched["bars"]
        print(f"underlying {i}/{len(UNIVERSE)} {symbol} bars={len(fetched['bars'])}", flush=True)
        if symbol == "SPY" and (fetched["error"] or len(fetched["bars"]) < SMA_REGIME + 1):
            return not_run(
                "spy_daily_bars_unavailable",
                "SPY daily bars were not usable, so the regime filter could not run. No option P&L.",
                time.time() - t0,
                probe=probe,
            )
    signals = build_signals(bars_by_symbol)
    print(f"signals {len(signals)}", flush=True)
    simulated: list[dict] = []
    skips: Counter[str] = Counter()
    as_of_fallback = 0
    for n, signal in enumerate(signals, start=1):
        if n % 25 == 0 or n == 1:
            print(f"option contracts {n}/{len(signals)} {signal['symbol']} {signal['entry_date']}", flush=True)
        und = bars_by_symbol[signal["symbol"]]
        hold = [bar for bar in und if bar["date"] >= signal["entry_date"]][:MAX_HOLD_SESSIONS]
        if has_split_gap(hold):
            skips["split_in_hold"] += 1
            continue
        pack = fetch_contracts(key, signal["symbol"], signal["entry_date"], signal["spot"])
        if pack["error"] == "not_authorized":
            return not_run(
                "polygon_options_not_entitled_mid_run",
                "Option contract request was refused after the probe. Partial trades were discarded. No P&L.",
                time.time() - t0,
                probe=probe,
            )
        if pack.get("as_of_fallback"):
            as_of_fallback += 1
        chosen = select_itm_call(
            pack["contracts"],
            signal["spot"],
            date.fromisoformat(signal["entry_date"]),
        )
        if not chosen or not chosen.get("ticker"):
            skips["no_contract_in_band"] += 1
            continue
        end = hold[-1]["date"] if hold else signal["entry_date"]
        opt = fetch_option_bars(key, str(chosen["ticker"]), signal["entry_date"], end)
        if opt["error"] == "not_authorized":
            return not_run(
                "polygon_options_not_entitled_mid_run",
                "Option aggregate request was refused after the probe. Partial trades were discarded. No P&L.",
                time.time() - t0,
                probe=probe,
            )
        opt_by = {bar["date"]: bar for bar in opt["bars"]}
        entry_bar = opt_by.get(signal["entry_date"])
        entry_open = _opt_px(entry_bar, "o")
        if entry_open is None:
            skips["missing_option_open"] += 1
            continue
        if entry_open >= signal["entry_und_open"]:
            skips["premium_above_underlying"] += 1
            continue
        sizing = size_long_call(entry_open)
        if sizing["action"] != "buy":
            skips[str(sizing["reason"])] += 1
            continue
        sim = simulate_long_call(
            signal["entry_date"],
            signal["entry_und_open"],
            entry_open,
            signal["atr"],
            str(chosen["expiration"]),
            hold,
            opt_by,
        )
        sim["symbol"] = signal["symbol"]
        sim["strike"] = chosen["strike"]
        sim["itm_pct"] = chosen["itm_pct"]
        sim["dte"] = chosen["dte"]
        sim["option_ticker"] = chosen["ticker"]
        if sim["status"] == "closed":
            # Loss cannot exceed premium when the exit print is non-negative.
            if float(sim["pnl_usd"]) < -float(sim["debit_usd"]) - 1e-6:
                return not_run(
                    "loss_bound_violated",
                    "A computed call loss exceeded the debit. Results were discarded instead of published.",
                    time.time() - t0,
                    probe=probe,
                )
            simulated.append(sim)
        else:
            skips[str(sim["reason"])] += 1
    allocated = allocate_book(simulated)
    for row in allocated:
        if row.get("status") == "skipped_cash":
            skips["skipped_cash"] += 1
    calendar = [bar["date"] for bar in bars_by_symbol["SPY"] if ENTRY_START <= bar["date"] <= ENTRY_END]
    probe = dict(probe)
    probe["as_of_fallback_queries"] = as_of_fallback
    probe["signals"] = len(signals)
    return summarize(allocated, dict(skips), bars_by_symbol["SPY"], calendar, time.time() - t0, probe)


if modal is not None:
    app = modal.App(APP_NAME)
    image = modal.Image.debian_slim(python_version="3.12").pip_install(["requests"])
    polygon_secret = modal.Secret.from_name("polygon-api-key")
    qalpha_secrets = modal.Secret.from_name("q-alpha-secrets")

    @app.function(image=image, secrets=[polygon_secret, qalpha_secrets], timeout=3600)
    def run_study() -> dict[str, Any]:
        """Modal entry. Reads POLYGON_API_KEY only. q-alpha-secrets is attached, not used for orders."""
        return run_study_body()

    @app.local_entrypoint()
    def main() -> None:
        """Run on the laptop and write results next to this file."""
        print("EXP-0026 ITM long-call — probing Polygon option bars, then the rule")
        result = run_study.remote()
        write_outputs(result, EXP_DIR)
        print(json.dumps({
            "recommendation": result.get("recommendation"),
            "reason": result.get("reason"),
            "pnl_usd": result.get("pnl_usd"),
            "runtime_sec": result.get("runtime_sec"),
            "wrote": str(OUT_MD),
        }, indent=2))
