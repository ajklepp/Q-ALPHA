"""
Causal Peak Hour signal × Track 100 $5k/10-seat walk-forward engine.

Signal source of truth: Q-ALPHA Peak Hour equal-signal / 1H LAUNCH-eligible
prints (every valid signal — not Cap-taken live fills).

Stack under test:
  1) Peak Hour signal → next 1H open fill
  2) Optional paper_filter_winloss_v1 (frozen IS medians)
  3) Exit: C_ratchet_struct only
  4) Book: $5,000, 10 seats, ~$500/seat, 0.15% RT cost, dollar equity

No IBKR. Study only. No invented filter/exit math.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import pytz

EXP_DIR = Path(__file__).resolve().parent
REPO = EXP_DIR.parents[1]
CANDIDATES = REPO / "candidates"
if str(CANDIDATES) not in sys.path:
    sys.path.insert(0, str(CANDIDATES))
if str(EXP_DIR) not in sys.path:
    sys.path.insert(0, str(EXP_DIR))

from tsd_scan_pipeline.php_equal_signal import (  # noqa: E402
    is_equal_signal_list_candidate,
)
from tsd_scan_pipeline.tsd_htf_gates import (  # noqa: E402
    HTF_BARS_NEEDED,
    compute_htf_metrics,
)
from tsd_scan_pipeline.tsd_kill import (  # noqa: E402
    STRUCTURE_LOOKBACK_BARS,
    structure_area_low,
    structure_too_wide,
)
from tsd_scan_pipeline.tsd_signals import enrich_tsd  # noqa: E402
from tsd_scan_pipeline.universe_tsd import (  # noqa: E402
    MIN_DOLLAR_VOL_20D,
    MIN_PRICE,
)

from track100_adapter import (  # noqa: E402
    EXIT_BOOK,
    FILTER_VERSION,
    ExitFill,
    FilterDecision,
    apply_paper_filter_winloss_v1,
    filter_skip_reason_counts,
    simulate_c_ratchet_struct,
    track100_available,
)

ET = pytz.timezone("America/New_York")

# Copied from tsd_1h_signal — Peak Hour 1H LAUNCH window (do not drift).
ALLOWED_HOURS = {5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15}
HTF_1H_BARS_MIN = 80
BAR_SOURCE = "polygon_1h_aggs_start_labeled"

# Track 100 $5k paper stack (ops_stack_5k / walkforward_5k / paper_book).
STARTING_CASH = 5000.0
MAX_SEATS = 10
SEAT_NOTIONAL = 500.0
COST_PER_TRADE = 0.0015  # 0.15% RT proxy, same as EXP-0012

# Window: align Track 100 IS cut; ~8 months ending ~2026-09-16.
WINDOW_END = date(2026, 9, 16)
WINDOW_START_TARGET = date(2026, 1, 20)  # 8 calendar months before 2026-09-16
IS_CUT_DEFAULT = date(2026, 7, 20)

NYSE_HOLIDAYS = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
    "2026-05-25", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25",
}

SIGNAL_DEFINITION = (
    "Peak Hour equal-signal / LAUNCH-eligible 1H print: last completed 1H bar "
    "with (buy_signal OR early_bull) from tsd_signals.enrich_tsd, "
    "is_equal_signal_list_candidate (not hard-extended: scan_score < 75), "
    "close hour ET in ALLOWED_HOURS {5–15}, ≥80 prior 1H bars, "
    "not structure_too_wide (structure risk > 3.5%), and causal HTF-pass "
    "membership for that session (dailies strictly before signal date: "
    "20d range 25–150%, close>SMA50, SMA20 rising, price≥$5, "
    "20d dollar volume ≥$5M). Equal-signal ON. Case review, 2/scan cap, "
    "popularity, momentum-rank slot pick, and Cap-taken fills are NOT applied."
)

FILL_RULE = (
    "Fill at the next 1H bar OPEN after the signal bar close "
    "(tsd_1h_signal bars are start-labeled; next row open is the first causal "
    "1H-grid executable print after the :15 scan). Live Peak Hour additionally "
    "micro-confirms on 1m tape and rests a pullback Limit near signal close; "
    "this study does not replay 1m micro-confirm (TIMEOUT/ABORT not modeled)."
)


def bar_close_hour_et(ts: datetime | pd.Timestamp) -> int:
    """Polygon 1H timestamp is bar start; close hour is start+1h (right label)."""
    if ts.tzinfo is None:
        ts = ET.localize(ts) if isinstance(ts, datetime) else ts.tz_localize(ET)
    else:
        ts = ts.tz_convert(ET) if hasattr(ts, "tz_convert") else ts.astimezone(ET)
    return (int(ts.hour) + 1) % 24


def trading_days(start: date, end: date) -> list[str]:
    """Weekdays in [start, end] excluding NYSE full closures."""
    out: list[str] = []
    d = start
    while d <= end:
        ds = d.isoformat()
        if d.weekday() < 5 and ds not in NYSE_HOLIDAYS:
            out.append(ds)
        d += timedelta(days=1)
    return out


def _as_et_index(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    if idx.tz is None:
        return idx.tz_localize(ET)
    return idx.tz_convert(ET)


@dataclass
class PhpSignal:
    """One Peak Hour equal-signal print (pre-filter, pre-seat)."""

    symbol: str
    signal_ts: str
    signal_date: str
    close_hour: int
    open: float
    high: float
    low: float
    close: float
    buy_signal: bool
    early_bull: bool
    scan_score: float
    wt1: float | None
    wt2: float | None
    trend_strength: float | None
    vol_ratio: float | None
    structure_level: float | None
    fill_ts: str
    fill_px: float
    htf_score: float | None = None


@dataclass
class ClosedTrade:
    symbol: str
    signal_ts: str
    signal_date: str
    opened_ts: str
    closed_ts: str
    entry: float
    exit: float
    qty: float
    notional: float
    cost: float
    pnl_dollar: float
    pnl_pct: float
    reason: str
    filtered: bool
    split: str  # IS | OOS (signal date vs cut)


@dataclass
class BookResult:
    starting_cash: float
    final_equity: float
    total_pnl: float
    is_end_equity: float
    oos_equity: float
    oos_pnl: float
    win_pct: float
    max_dd: float
    n_signals: int
    n_filter_pass: int
    n_filter_skip: int
    n_fills: int
    n_closed: int
    n_open_end: int
    filter_skip_reasons: dict[str, int]
    occupancy_avg: float
    trades: list[ClosedTrade] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


def is_htf_pass_from_dailies(
    dailies: pd.DataFrame,
    *,
    asof: date,
) -> tuple[bool, dict[str, Any]]:
    """
    Causal HTF + liquidity gate using dailies with date < asof (no same-day peek).
    """
    if dailies is None or dailies.empty:
        return False, {"reason": "no_dailies"}
    idx = _as_et_index(dailies.index)
    work = dailies.copy()
    work.index = idx
    prior = work[work.index.date < asof]
    if len(prior) < HTF_BARS_NEEDED:
        return False, {"reason": "htf_insufficient_bars", "n": int(len(prior))}

    closes = [float(x) for x in prior["close"].tolist()]
    highs = [float(x) for x in prior["high"].tolist()]
    lows = [float(x) for x in prior["low"].tolist()]
    metrics = compute_htf_metrics(closes, highs, lows)
    if metrics.get("insufficient_bars"):
        return False, {"reason": "htf_insufficient_bars"}
    if not metrics.get("range_ok"):
        return False, {"reason": "htf_range", **metrics}
    if not metrics.get("close_above_sma50"):
        return False, {"reason": "htf_sma50", **metrics}
    if not metrics.get("sma20_rising"):
        return False, {"reason": "htf_sma20", **metrics}
    if not metrics.get("price_ok"):
        return False, {"reason": "htf_price", **metrics}

    last20 = prior.tail(20)
    dv = float((last20["close"] * last20["volume"]).mean()) if "volume" in last20 else 0.0
    if dv < MIN_DOLLAR_VOL_20D:
        return False, {"reason": "dollar_vol", "dollar_vol_20d": dv}
    last_px = float(prior.iloc[-1]["close"])
    if last_px < MIN_PRICE:
        return False, {"reason": "price", "close": last_px}
    return True, {"htf": metrics, "dollar_vol_20d": dv}


def scan_equal_signals(
    symbol: str,
    bars_1h: pd.DataFrame,
    *,
    window_start: date,
    window_end: date,
    htf_ok_dates: set[str] | None = None,
) -> list[PhpSignal]:
    """
    Walk completed 1H bars and emit every Peak Hour equal-signal print.

    Fill = next bar open. Requires a subsequent bar (no peeking past available data).
    """
    if bars_1h is None or len(bars_1h) < HTF_1H_BARS_MIN + 1:
        return []
    df = bars_1h.copy()
    df.index = _as_et_index(df.index)
    df = df.sort_index()
    en = enrich_tsd(df)
    out: list[PhpSignal] = []
    n = len(en)
    for i in range(HTF_1H_BARS_MIN - 1, n - 1):
        ts = pd.Timestamp(en.index[i])
        sig_date = ts.date()
        if sig_date < window_start or sig_date > window_end:
            continue
        close_hour = bar_close_hour_et(ts)
        if close_hour not in ALLOWED_HOURS:
            continue
        if htf_ok_dates is not None and sig_date.isoformat() not in htf_ok_dates:
            continue
        row = en.iloc[i]
        buy = bool(row.get("buy_signal", False))
        early = bool(row.get("early_bull", False))
        try:
            scan = float(row.get("scan_score") or 0.0)
        except (TypeError, ValueError):
            scan = 0.0
        feat = {
            "buy_signal": buy,
            "early_bull": early,
            "scan_score": scan,
        }
        if not is_equal_signal_list_candidate(feat, keep_hard_extension=True):
            continue
        o = float(row["open"])
        h = float(row["high"])
        l = float(row["low"])
        c = float(row["close"])
        if min(o, h, l, c) <= 0:
            continue
        lows = [float(x) for x in en["low"].iloc[max(0, i - STRUCTURE_LOOKBACK_BARS + 1): i + 1]]
        area = structure_area_low(lows)
        if structure_too_wide(c, area):
            continue
        nxt = en.iloc[i + 1]
        fill_px = float(nxt["open"])
        if fill_px <= 0:
            continue
        fill_ts = pd.Timestamp(en.index[i + 1])
        out.append(
            PhpSignal(
                symbol=symbol.upper(),
                signal_ts=ts.isoformat(),
                signal_date=sig_date.isoformat(),
                close_hour=close_hour,
                open=o,
                high=h,
                low=l,
                close=c,
                buy_signal=buy,
                early_bull=early,
                scan_score=round(scan, 4),
                wt1=_f(row.get("wt1")),
                wt2=_f(row.get("wt2")),
                trend_strength=_f(row.get("trend_strength")),
                vol_ratio=_f(row.get("vol_ratio")),
                structure_level=area,
                fill_ts=fill_ts.isoformat(),
                fill_px=round(fill_px, 6),
            )
        )
    return out


def _f(val: Any) -> float | None:
    try:
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def signal_to_filter_row(sig: PhpSignal) -> dict[str, Any]:
    """Signal-time feature dict for paper_filter (no future fields)."""
    return {
        "symbol": sig.symbol,
        "signal_ts": sig.signal_ts,
        "signal_date": sig.signal_date,
        "hour": sig.close_hour,
        "open": sig.open,
        "high": sig.high,
        "low": sig.low,
        "close": sig.close,
        "buy_signal": sig.buy_signal,
        "early_bull": sig.early_bull,
        "scan_score": sig.scan_score,
        "wt1": sig.wt1,
        "wt2": sig.wt2,
        "trend_strength": sig.trend_strength,
        "vol_ratio": sig.vol_ratio,
        "vol_ratio_20": sig.vol_ratio,
        "structure_level": sig.structure_level,
        "htf_score": sig.htf_score,
    }


def _ohlc_after_fill(
    bars_1h: pd.DataFrame,
    fill_ts: str,
) -> list[dict[str, Any]]:
    """1H bars from the fill bar inclusive (path for C_ratchet)."""
    df = bars_1h.copy()
    df.index = _as_et_index(df.index)
    start = pd.Timestamp(fill_ts)
    if start.tzinfo is None:
        start = ET.localize(start.to_pydatetime())
    else:
        start = start.tz_convert(ET)
    path = df[df.index >= start]
    out: list[dict[str, Any]] = []
    for ts, row in path.iterrows():
        out.append({
            "ts": pd.Timestamp(ts).isoformat(),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]) if "volume" in row else 0.0,
        })
    return out


def _last_px_asof(bars_1h: pd.DataFrame | None, asof: datetime, fallback: float) -> float:
    if bars_1h is None or bars_1h.empty:
        return fallback
    df = bars_1h.copy()
    df.index = _as_et_index(df.index)
    prior = df[df.index <= asof]
    if prior.empty:
        return fallback
    return float(prior.iloc[-1]["close"])


def simulate_book(
    signals: list[PhpSignal],
    bars_by_sym: dict[str, pd.DataFrame],
    *,
    apply_filter: bool,
    starting_cash: float = STARTING_CASH,
    max_seats: int = MAX_SEATS,
    seat_notional: float = SEAT_NOTIONAL,
    is_cut: date = IS_CUT_DEFAULT,
    window_end: date = WINDOW_END,
    filter_fn: Callable[[dict[str, Any]], FilterDecision] | None = None,
    exit_fn: Callable[..., ExitFill | None] | None = None,
) -> BookResult:
    """
    Occupancy-aware $5k / 10-seat dollar book.

    FIFO by (fill_ts, symbol). One open seat per symbol. Filter skips do not
    occupy a seat. Exits are C_ratchet_struct only (injected or Track 100).
    """
    filt = filter_fn or apply_paper_filter_winloss_v1
    xfn = exit_fn or (
        lambda bars, entry, structure_level, signal_row: simulate_c_ratchet_struct(
            bars,
            entry=entry,
            structure_level=structure_level,
            signal_row=signal_row,
        )
    )

    ordered = sorted(signals, key=lambda s: (s.fill_ts, s.symbol, s.signal_ts))
    cash = float(starting_cash)
    open_pos: dict[str, dict[str, Any]] = {}
    closed: list[ClosedTrade] = []
    filter_decisions: list[FilterDecision] = []
    n_pass = 0
    n_skip = 0
    n_fills = 0
    n_skip_seats_full = 0
    n_skip_already_open = 0
    n_skip_cash = 0
    seat_samples: list[int] = []
    equity_curve: list[float] = []
    is_end_equity = float(starting_cash)
    is_end_seen = False

    # Pre-compute ratchet exits so the clock can close seats when due.
    pending_exit: dict[str, ExitFill | None] = {}

    def _split(sig_date: str) -> str:
        d = datetime.strptime(sig_date[:10], "%Y-%m-%d").date()
        return "IS" if d <= is_cut else "OOS"

    def _mark(asof: datetime) -> float:
        mtm = 0.0
        unpaid = 0.0
        for pos in open_pos.values():
            px = _last_px_asof(
                bars_by_sym.get(pos["symbol"]), asof, float(pos["entry"]),
            )
            mtm += float(pos["qty"]) * px
            unpaid += float(pos["cost"])
        return cash + mtm - unpaid

    def _close_pos(sym: str, fill: ExitFill, *, asof_ts: str) -> None:
        nonlocal cash
        pos = open_pos.pop(sym)
        entry = float(pos["entry"])
        qty = float(pos["qty"])
        cost = float(pos["cost"])
        pnl = (float(fill.exit_px) - entry) * qty - cost
        cash += qty * float(fill.exit_px) - cost  # RT cost leaves the dollar book
        closed.append(
            ClosedTrade(
                symbol=sym,
                signal_ts=pos["signal_ts"],
                signal_date=pos["signal_date"],
                opened_ts=pos["opened_ts"],
                closed_ts=fill.bar_ts or asof_ts,
                entry=entry,
                exit=float(fill.exit_px),
                qty=qty,
                notional=float(pos["notional"]),
                cost=cost,
                pnl_dollar=round(pnl, 4),
                pnl_pct=round((float(fill.exit_px) / entry - 1.0), 6) if entry else 0.0,
                reason=fill.reason,
                filtered=bool(pos.get("filtered")),
                split=_split(pos["signal_date"]),
            )
        )

    for sig in ordered:
        fill_dt = pd.Timestamp(sig.fill_ts)
        if fill_dt.tzinfo is None:
            fill_dt = ET.localize(fill_dt.to_pydatetime())
        else:
            fill_dt = fill_dt.tz_convert(ET)

        # Expire opens whose ratchet already fired at/before this fill clock.
        for sym in list(open_pos):
            ex = pending_exit.get(sym)
            if ex is None or not ex.bar_ts:
                continue
            ex_ts = pd.Timestamp(ex.bar_ts)
            if ex_ts.tzinfo is None:
                ex_ts = ET.localize(ex_ts.to_pydatetime())
            if ex_ts <= fill_dt:
                _close_pos(sym, ex, asof_ts=ex.bar_ts)
                pending_exit.pop(sym, None)

        if not is_end_seen and fill_dt.date() > is_cut:
            is_end_equity = _mark(ET.localize(datetime(is_cut.year, is_cut.month, is_cut.day, 16, 0)))
            is_end_seen = True

        row = signal_to_filter_row(sig)
        if apply_filter:
            dec = filt(row)
            filter_decisions.append(dec)
            if not dec.passed:
                n_skip += 1
                continue
            n_pass += 1
        else:
            n_pass += 1

        if sig.symbol in open_pos:
            n_skip_already_open += 1
            continue
        if len(open_pos) >= max_seats:
            n_skip_seats_full += 1
            continue
        if cash < seat_notional * 0.5:
            n_skip_cash += 1
            continue
        qty = seat_notional / sig.fill_px
        if qty <= 0:
            continue
        notional = qty * sig.fill_px
        cost = notional * COST_PER_TRADE
        if cash < notional:
            n_skip_cash += 1
            continue
        cash -= notional
        path = _ohlc_after_fill(bars_by_sym.get(sig.symbol, pd.DataFrame()), sig.fill_ts)
        try:
            ex = xfn(
                path,
                entry=sig.fill_px,
                structure_level=sig.structure_level,
                signal_row=row,
            )
        except TypeError:
            ex = xfn(path, sig.fill_px, sig.structure_level, row)
        open_pos[sig.symbol] = {
            "symbol": sig.symbol,
            "signal_ts": sig.signal_ts,
            "signal_date": sig.signal_date,
            "opened_ts": sig.fill_ts,
            "entry": sig.fill_px,
            "qty": qty,
            "notional": notional,
            "cost": cost,
            "filtered": apply_filter,
        }
        pending_exit[sig.symbol] = ex
        n_fills += 1
        seat_samples.append(len(open_pos))
        equity_curve.append(_mark(fill_dt))

        # Same-bar / immediate exit
        if ex is not None and ex.bar_ts:
            ex_ts = pd.Timestamp(ex.bar_ts)
            if ex_ts.tzinfo is None:
                ex_ts = ET.localize(ex_ts.to_pydatetime())
            if ex_ts <= fill_dt:
                _close_pos(sig.symbol, ex, asof_ts=ex.bar_ts)
                pending_exit.pop(sig.symbol, None)

    # End-of-window: fire remaining ratchet results or mark.
    end_dt = ET.localize(datetime(window_end.year, window_end.month, window_end.day, 16, 0))
    for sym in list(open_pos):
        ex = pending_exit.get(sym)
        if ex is not None:
            _close_pos(sym, ex, asof_ts=ex.bar_ts or end_dt.isoformat())
            pending_exit.pop(sym, None)
            continue
        pos = open_pos[sym]
        mark_px = _last_px_asof(bars_by_sym.get(sym), end_dt, float(pos["entry"]))
        _close_pos(
            sym,
            ExitFill(exit_px=mark_px, reason="window_mark", bar_ts=end_dt.isoformat()),
            asof_ts=end_dt.isoformat(),
        )

    if not is_end_seen:
        is_end_equity = _mark(
            ET.localize(datetime(is_cut.year, is_cut.month, is_cut.day, 16, 0))
        )

    final = cash
    equity_curve.append(final)
    peak = starting_cash
    max_dd = 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = min(max_dd, (eq - peak) / peak)

    wins = sum(1 for t in closed if t.pnl_dollar > 0)
    n_closed = len(closed)
    win_pct = (wins / n_closed) if n_closed else 0.0
    oos_eq = final
    return BookResult(
        starting_cash=starting_cash,
        final_equity=round(final, 4),
        total_pnl=round(final - starting_cash, 4),
        is_end_equity=round(is_end_equity, 4),
        oos_equity=round(oos_eq, 4),
        oos_pnl=round(oos_eq - is_end_equity, 4),
        win_pct=round(win_pct, 6),
        max_dd=round(max_dd, 6),
        n_signals=len(signals),
        n_filter_pass=n_pass,
        n_filter_skip=n_skip,
        n_fills=n_fills,
        n_closed=n_closed,
        n_open_end=0,
        filter_skip_reasons=filter_skip_reason_counts(filter_decisions),
        occupancy_avg=round(
            (sum(seat_samples) / len(seat_samples)) if seat_samples else 0.0, 4
        ),
        trades=closed,
        extra={
            "max_seats": max_seats,
            "seat_notional": seat_notional,
            "cost_per_trade": COST_PER_TRADE,
            "apply_filter": apply_filter,
            "filter_version": FILTER_VERSION if apply_filter else None,
            "exit_book": EXIT_BOOK,
            "n_skip_seats_full": n_skip_seats_full,
            "n_skip_already_open": n_skip_already_open,
            "n_skip_cash": n_skip_cash,
        },
    )


def book_to_dict(book: BookResult, *, include_trades: bool = True) -> dict[str, Any]:
    d = asdict(book)
    if not include_trades:
        d["trades"] = f"{len(book.trades)} trades omitted"
    return d


def summarize_plain(book: BookResult, *, label: str) -> str:
    """Aaron-facing dollar summary (no jargon dump)."""
    return "\n".join([
        f"### {label}",
        f"- Starting cash: **${book.starting_cash:,.2f}**",
        f"- Final $: **${book.final_equity:,.2f}**",
        f"- Total P&L: **${book.total_pnl:,.2f}**",
        f"- IS-end $: **${book.is_end_equity:,.2f}**",
        f"- OOS $ (book at end): **${book.oos_equity:,.2f}**",
        f"- OOS P&L (end − IS-end): **${book.oos_pnl:,.2f}**",
        f"- Win%: **{book.win_pct * 100:.1f}%** ({book.n_closed} closed)",
        f"- Max DD (equity): **{book.max_dd * 100:.1f}%**",
        f"- n signals: **{book.n_signals}**",
        f"- filter pass / skip: **{book.n_filter_pass} / {book.n_filter_skip}**",
        f"- skip reasons: `{book.filter_skip_reasons}`",
        f"- n fills: **{book.n_fills}**",
        f"- occupancy skips (full / already-open / cash): "
        f"**{book.extra.get('n_skip_seats_full', 0)} / "
        f"{book.extra.get('n_skip_already_open', 0)} / "
        f"{book.extra.get('n_skip_cash', 0)}**",
        f"- avg occupancy (at fills): **{book.occupancy_avg:.2f} / {MAX_SEATS}**",
    ])


def run_variants(
    signals: list[PhpSignal],
    bars_by_sym: dict[str, pd.DataFrame],
    *,
    is_cut: date,
    window_end: date,
    include_unfiltered: bool = True,
    filter_fn: Callable[[dict[str, Any]], FilterDecision] | None = None,
    exit_fn: Callable[..., ExitFill | None] | None = None,
) -> dict[str, BookResult]:
    """Primary = filter ON. Side row (a) = same exits, filter OFF."""
    out: dict[str, BookResult] = {}
    out["php_t100_filter_c"] = simulate_book(
        signals,
        bars_by_sym,
        apply_filter=True,
        is_cut=is_cut,
        window_end=window_end,
        filter_fn=filter_fn,
        exit_fn=exit_fn,
    )
    if include_unfiltered:
        out["php_c_nofilter"] = simulate_book(
            signals,
            bars_by_sym,
            apply_filter=False,
            is_cut=is_cut,
            window_end=window_end,
            filter_fn=filter_fn,
            exit_fn=exit_fn,
        )
    return out


def choose_window(
    data_start: date | None,
    data_end: date | None,
    *,
    target_start: date = WINDOW_START_TARGET,
    target_end: date = WINDOW_END,
) -> dict[str, Any]:
    """Longest honest causal window inside target; record if snapped."""
    start = target_start
    end = target_end
    snapped = False
    notes: list[str] = []
    if data_start is not None and data_start > start:
        start = data_start
        snapped = True
        notes.append(f"start snapped to first available bar date {data_start}")
    if data_end is not None and data_end < end:
        end = data_end
        snapped = True
        notes.append(f"end snapped to last available bar date {data_end}")
    if start > end:
        raise RuntimeError(f"empty window after snap: {start} > {end}")
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "target_start": target_start.isoformat(),
        "target_end": target_end.isoformat(),
        "snapped": snapped,
        "notes": notes,
        "months": round((end - start).days / 30.4, 1),
    }


def build_results_payload(
    *,
    variants: dict[str, BookResult] | None,
    window: dict[str, Any],
    is_cut: date,
    status: str,
    blocked_reason: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """JSON blob for results/*.json — P&L only when status=ok."""
    payload: dict[str, Any] = {
        "study": "EXP-0026 Peak Hour signals × Track100 filter+C $5k/10",
        "status": status,
        "blocked_reason": blocked_reason,
        "signal_definition": SIGNAL_DEFINITION,
        "fill_rule": FILL_RULE,
        "filter": FILTER_VERSION,
        "exit": EXIT_BOOK,
        "book": {
            "starting_cash": STARTING_CASH,
            "max_seats": MAX_SEATS,
            "seat_notional": SEAT_NOTIONAL,
            "cost_per_trade": COST_PER_TRADE,
            "rank": "dollar_equity",
        },
        "window": window,
        "is_cut": is_cut.isoformat(),
        "track100": track100_available(),
        "invented_pnl": False,
    }
    if extra:
        payload["extra"] = extra
    if variants and status == "ok":
        payload["variants"] = {
            k: book_to_dict(v, include_trades=False) for k, v in variants.items()
        }
        payload["primary"] = book_to_dict(variants["php_t100_filter_c"], include_trades=False)
    else:
        payload["primary"] = None
        payload["variants"] = None
    return payload


def render_results_md(payload: dict[str, Any], variants: dict[str, BookResult] | None = None) -> str:
    """Plain English for Aaron."""
    lines = [
        "# EXP-0026 — Peak Hour signals × Track 100 filter + C_ratchet ($5k / 10 seats)",
        "",
        f"**Status:** `{payload.get('status')}`",
        "",
        "## Question",
        "",
        "If we take **every** Peak Hour valid signal (not only seats Cap took live) "
        "and apply the Track 100 paper stack (`paper_filter_winloss_v1` + "
        "`C_ratchet_struct`) on a **$5,000 / 10-seat** book, what is the P&L?",
        "",
        "## Signal definition (from code)",
        "",
        payload.get("signal_definition", SIGNAL_DEFINITION),
        "",
        "## Fill rule",
        "",
        payload.get("fill_rule", FILL_RULE),
        "",
        "## Walk-forward",
        "",
        f"- Target window: {payload.get('window', {}).get('target_start')} → "
        f"{payload.get('window', {}).get('target_end')} (~8 months)",
        f"- Used window: **{payload.get('window', {}).get('start')} → "
        f"{payload.get('window', {}).get('end')}** "
        f"({payload.get('window', {}).get('months')} months"
        f"{', snapped to available bars' if payload.get('window', {}).get('snapped') else ''})",
        f"- IS signal dates ≤ **{payload.get('is_cut')}**; OOS after",
        "- Filter thresholds frozen on IS only (Track 100 `paper_filter.py` — not recut on OOS)",
        "- Rank / report: **dollar equity** (occupancy matters)",
        "",
        "## Stack",
        "",
        "1. Peak Hour equal-signal print → next 1H open",
        f"2. Gate: `{FILTER_VERSION}` (skip counts reported)",
        f"3. Exit: `{EXIT_BOOK}` only",
        "4. Book: $5k, 10 concurrent seats, $500/seat, 0.15% RT",
        "",
    ]
    if payload.get("status") != "ok":
        lines += [
            "## P&L",
            "",
            "**No invented P&L.** This VM could not finish a live Polygon walk-forward.",
            f"Reason: `{payload.get('blocked_reason')}`",
            "",
            "Run on the laptop (Track 100 sibling + Modal Polygon secret):",
            "",
            "```powershell",
            "cd C:\\Users\\ajkle\\Documents\\Q-ALPHA",
            "py -3 experiments\\EXP-0026\\vendor_from_track100.py",
            ".\\venv\\Scripts\\python.exe -m modal run experiments/EXP-0026/study_php_t100_wf_5k_modal.py",
            "```",
            "",
            "Artifacts overwrite `experiments/EXP-0026/results.md` and `results.json`.",
            "",
        ]
    elif variants:
        primary = variants["php_t100_filter_c"]
        lines += [
            "## P&L (dollar book — primary)",
            "",
            summarize_plain(primary, label="Peak Hour signals + paper_filter_winloss_v1 + C_ratchet_struct"),
            "",
        ]
        if "php_c_nofilter" in variants:
            lines += [
                "## Side row (a) — same signals + C_ratchet, **no** Track 100 filter",
                "",
                summarize_plain(variants["php_c_nofilter"], label="Unfiltered"),
                "",
            ]
        lines += [
            "## Side row (b) — Peak Hour live-style exits",
            "",
            "Skipped (not cheap / not the question). Live keep-profit stays on the "
            "Peak Hour book; this study does not replay it.",
            "",
        ]
    notes = payload.get("window", {}).get("notes") or []
    if notes:
        lines += ["## Window notes", ""] + [f"- {n}" for n in notes] + [""]
    extra = payload.get("extra") or {}
    if extra.get("runtime_sec") is not None:
        lines += [f"**Runtime:** {extra['runtime_sec']:.1f}s", ""]
    lines += [
        "## Honesty",
        "",
        "- No look-ahead on features, HTF membership, or fills.",
        "- Filter medians were **not** recut on OOS.",
        "- Live Peak Hour gates were **not** changed. No IBKR orders.",
        "",
    ]
    return "\n".join(lines) + "\n"


def write_results(payload: dict[str, Any], variants: dict[str, BookResult] | None = None) -> None:
    out_json = EXP_DIR / "results.json"
    out_md = EXP_DIR / "results.md"
    out_json.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    out_md.write_text(render_results_md(payload, variants), encoding="utf-8")
