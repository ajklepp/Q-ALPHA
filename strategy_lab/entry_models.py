"""
strategy_lab/entry_models.py — shared entry models (backtest + future live).

Pure, deterministic functions. Given flag-day 1-min bars (premarket allowed),
each model returns either an ENTRY signal or None (pass / no entry).

Models share the SAME code path for lab backtests and a future live decision
engine. Reuses candidates/entry_study.py helpers (fetch_minute_bars, running_vwap,
ORB_MINUTES) — does not duplicate those calculations.

Does NOT modify agent files.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
LAB = Path(__file__).resolve().parent
CANDIDATES = ROOT / "candidates"
sys.path.insert(0, str(CANDIDATES))

from entry_study import (  # noqa: E402
    ORB_MINUTES,
    Bar,
    et_minutes,
    fetch_minute_bars,
    running_vwap,
)

ET = ZoneInfo("America/New_York")

RTH_OPEN_MIN = 9 * 60 + 30  # 09:30 ET
# Morning premarket window for sweep_reclaim reference (avoids overnight spikes).
PREMARKET_START_MIN = 8 * 60  # 08:00 ET
DEFAULT_SWEEP_WINDOW_MIN = 30
# Premarket-limit fill window: first 30 minutes of RTH (09:30–10:00 ET).
DEFAULT_LIMIT_WINDOW_MIN = 30
PREMARKET_JSON = LAB / "results" / "premarket.json"


@dataclass(frozen=True)
class EntrySignal:
    """Entry decision measured from the actual trigger bar (not always 09:30)."""

    entry_time: str
    entry_price: float
    model_name: str
    bar_index: int  # index into the bars list passed to the model

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Bar normalization (strategy_lab dicts OR entry_study Bar / Polygon raw)
# ---------------------------------------------------------------------------

def _parse_et(bar: dict | Bar) -> datetime | None:
    if isinstance(bar, Bar):
        return datetime.fromtimestamp(bar.t / 1000.0, tz=ET)
    # Lab bars use t_et; Polygon raw / other pipelines may use timestamp.
    t_et = bar.get("t_et") or bar.get("timestamp")
    if t_et:
        try:
            return datetime.fromisoformat(str(t_et).replace("Z", "+00:00"))
        except ValueError:
            pass
    ts = bar.get("t")
    if ts is not None:
        return datetime.fromtimestamp(int(ts) / 1000.0, tz=ET)
    return None


def _mod(bar: dict | Bar) -> int:
    """Minute-of-day ET (reuses entry_study.et_minutes for Bar / ms timestamps)."""
    if isinstance(bar, Bar):
        return int(et_minutes(bar.t))
    dt = _parse_et(bar)
    if dt is None:
        return -1
    return dt.hour * 60 + dt.minute


def _ohlcv(bar: dict | Bar) -> tuple[float, float, float, float, float]:
    if isinstance(bar, Bar):
        return bar.o, bar.h, bar.l, bar.c, bar.v
    return (
        float(bar["o"]),
        float(bar["h"]),
        float(bar["l"]),
        float(bar["c"]),
        float(bar.get("v") or 0),
    )


def _time_str(bar: dict | Bar) -> str:
    if isinstance(bar, Bar):
        return datetime.fromtimestamp(bar.t / 1000.0, tz=ET).isoformat()
    t_et = bar.get("t_et") or bar.get("timestamp")
    if t_et:
        return str(t_et)
    dt = _parse_et(bar)
    return dt.isoformat() if dt else ""


def _to_study_bars(bars: list[dict | Bar]) -> list[Bar]:
    """Convert lab/Polygon dicts into entry_study.Bar for VWAP helper reuse."""
    out: list[Bar] = []
    for b in bars:
        if isinstance(b, Bar):
            out.append(b)
            continue
        raw = {
            "t": int(b["t"]),
            "o": float(b["o"]),
            "h": float(b["h"]),
            "l": float(b["l"]),
            "c": float(b["c"]),
            "v": float(b.get("v") or 0),
        }
        out.append(Bar(raw))
    return out


def rth_start_index(bars: list[dict | Bar]) -> int | None:
    """Index of first bar at/after 09:30 ET."""
    for i, b in enumerate(bars):
        if _mod(b) >= RTH_OPEN_MIN:
            return i
    return None


def _signal(
    bars: list[dict | Bar],
    idx: int,
    model_name: str,
) -> EntrySignal:
    _o, _h, _l, c, _v = _ohlcv(bars[idx])
    return EntrySignal(
        entry_time=_time_str(bars[idx]),
        entry_price=float(c),
        model_name=model_name,
        bar_index=idx,
    )


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

def immediate(bars: list[dict | Bar]) -> EntrySignal | None:
    """
    Baseline: enter at the first 09:30 1-min close (naive open entry).

    Returns None if no RTH bar exists.
    """
    i0 = rth_start_index(bars)
    if i0 is None:
        return None
    return _signal(bars, i0, "immediate")


def orb_reclaim(
    bars: list[dict | Bar],
    *,
    orb_minutes: int = ORB_MINUTES,
) -> EntrySignal | None:
    """
    Wait for the opening range (first `orb_minutes` RTH bars) to form.
    Enter when price CLOSES back above the OR high. Skip if never reclaims.

    OR high/low use entry_study.ORB_MINUTES by default (imported, not duplicated).
    """
    i0 = rth_start_index(bars)
    if i0 is None:
        return None

    # Collect first N RTH minutes for the opening range.
    or_bars: list[int] = []
    for i in range(i0, len(bars)):
        m = _mod(bars[i])
        if m < RTH_OPEN_MIN:
            continue
        if m >= RTH_OPEN_MIN + orb_minutes:
            break
        or_bars.append(i)

    if len(or_bars) < orb_minutes:
        # Incomplete OR — cannot form a clean range.
        return None

    or_high = max(_ohlcv(bars[i])[1] for i in or_bars)
    last_or_idx = or_bars[-1]

    # After OR completes: first close above OR high = reclaim / breakout close.
    for i in range(last_or_idx + 1, len(bars)):
        if _mod(bars[i]) < RTH_OPEN_MIN:
            continue
        _o, _h, _l, c, _v = _ohlcv(bars[i])
        if c > or_high:
            return _signal(bars, i, "orb_reclaim")
    return None


def vwap_reclaim(bars: list[dict | Bar]) -> EntrySignal | None:
    """
    Enter on the first 1-min close back above session VWAP after the open.

    VWAP is computed via entry_study.running_vwap on RTH bars only (same helper
    the study harness uses). Requires a dip to/below VWAP, then a close above.
    """
    i0 = rth_start_index(bars)
    if i0 is None:
        return None

    rth = bars[i0:]
    study = _to_study_bars(rth)
    if not study:
        return None
    vwaps = running_vwap(study)

    dipped = False
    for j, b in enumerate(study):
        px = b.c
        vw = vwaps[j]
        if px <= vw:
            dipped = True
            continue
        if dipped and px > vw:
            return _signal(bars, i0 + j, "vwap_reclaim")
    return None


def sweep_reclaim(
    bars: list[dict | Bar],
    *,
    window_minutes: int = DEFAULT_SWEEP_WINDOW_MIN,
    premarket_start_min: int = PREMARKET_START_MIN,
) -> EntrySignal | None:
    """
    Failed-breakdown reversal (the USDE pattern).

    Reference low = min(morning-premarket low, opening 1-min low).
    If price SWEEPS below that low (makes a lower low) and then RECLAIMS it
    (closes back above the reference) within the first `window_minutes` of RTH,
    enter on that reclaim close. Otherwise None.

    Morning premarket defaults to 08:00–09:29 ET so an overnight spike does not
    pin the reference unrealistically low. Pass premarket_start_min=4*60 to use
    the full extended session.
    """
    i0 = rth_start_index(bars)
    if i0 is None:
        return None

    _o0, _h0, open_low, _c0, _v0 = _ohlcv(bars[i0])

    pm_lows: list[float] = []
    for i in range(0, i0):
        m = _mod(bars[i])
        if m < premarket_start_min:
            continue
        if m >= RTH_OPEN_MIN:
            break
        pm_lows.append(_ohlcv(bars[i])[2])

    if pm_lows:
        ref_low = min(min(pm_lows), open_low)
    else:
        ref_low = open_low

    window_end = RTH_OPEN_MIN + window_minutes
    swept = False

    # Sweep+reclaim can include the open bar itself (if it already undercut a
    # higher PM low), then subsequent bars within the window.
    for i in range(i0, len(bars)):
        m = _mod(bars[i])
        if m < RTH_OPEN_MIN:
            continue
        if m >= window_end:
            break
        _o, _h, lo, c, _v = _ohlcv(bars[i])
        if lo < ref_low - 1e-12:
            swept = True
        if swept and c > ref_low:
            return _signal(bars, i, "sweep_reclaim")
    return None


# ---------------------------------------------------------------------------
# Premarket limit models (anchors from results/premarket.json)
# ---------------------------------------------------------------------------

def load_premarket_row(ticker: str, flag_date: str) -> dict[str, Any] | None:
    """Load one setup's premarket anchors; None if file/key missing."""
    if not PREMARKET_JSON.exists():
        return None
    doc = json.loads(PREMARKET_JSON.read_text(encoding="utf-8"))
    key = f"{str(ticker).upper()}|{str(flag_date)[:10]}"
    row = (doc.get("premarket") or {}).get(key)
    return dict(row) if isinstance(row, dict) else None


def _limit_fill_signal(
    bars: list[dict | Bar],
    limit: float,
    model_name: str,
    *,
    window_minutes: int = DEFAULT_LIMIT_WINDOW_MIN,
) -> EntrySignal | None:
    """
    Limit BUY at `limit`: fills on the first RTH bar whose low trades at/through
    the limit within [09:30, 09:30+window). Entry price = limit (not bar close).
    """
    if limit <= 0:
        return None
    i0 = rth_start_index(bars)
    if i0 is None:
        return None

    window_end = RTH_OPEN_MIN + window_minutes
    for i in range(i0, len(bars)):
        m = _mod(bars[i])
        if m < RTH_OPEN_MIN:
            continue
        if m >= window_end:
            break
        _o, _h, lo, _c, _v = _ohlcv(bars[i])
        if lo <= limit + 1e-12:
            return EntrySignal(
                entry_time=_time_str(bars[i]),
                entry_price=float(limit),
                model_name=model_name,
                bar_index=i,
            )
    return None


def premarket_median_limit(
    bars: list[dict | Bar],
    *,
    limit: float | None = None,
    ticker: str | None = None,
    flag_date: str | None = None,
    window_minutes: int = DEFAULT_LIMIT_WINDOW_MIN,
    premarket_row: dict[str, Any] | None = None,
) -> EntrySignal | None:
    """
    Place a limit BUY at premarket_median. Fills only if price trades down
    to/through that limit in the first 30 minutes after the open (09:30–10:00).
    No touch → cancel (None). Missing/thin premarket → None.
    """
    row = premarket_row
    if row is None and ticker and flag_date:
        row = load_premarket_row(ticker, flag_date)
    if limit is None:
        if not row or not row.get("premarket_available"):
            return None
        if row.get("premarket_median") is None:
            return None
        limit = float(row["premarket_median"])
    return _limit_fill_signal(
        bars, float(limit), "premarket_median_limit",
        window_minutes=window_minutes,
    )


def premarket_vwap_limit(
    bars: list[dict | Bar],
    *,
    limit: float | None = None,
    ticker: str | None = None,
    flag_date: str | None = None,
    window_minutes: int = DEFAULT_LIMIT_WINDOW_MIN,
    premarket_row: dict[str, Any] | None = None,
) -> EntrySignal | None:
    """
    Identical to premarket_median_limit but limit = premarket_vwap.
    """
    row = premarket_row
    if row is None and ticker and flag_date:
        row = load_premarket_row(ticker, flag_date)
    if limit is None:
        if not row or not row.get("premarket_available"):
            return None
        if row.get("premarket_vwap") is None:
            return None
        limit = float(row["premarket_vwap"])
    return _limit_fill_signal(
        bars, float(limit), "premarket_vwap_limit",
        window_minutes=window_minutes,
    )


MODELS: dict[str, Callable[..., EntrySignal | None]] = {
    "immediate": immediate,
    "orb_reclaim": orb_reclaim,
    "vwap_reclaim": vwap_reclaim,
    "sweep_reclaim": sweep_reclaim,
    "premarket_median_limit": premarket_median_limit,
    "premarket_vwap_limit": premarket_vwap_limit,
}


def run_all_models(
    bars: list[dict | Bar],
    *,
    orb_minutes: int = ORB_MINUTES,
    sweep_window_minutes: int = DEFAULT_SWEEP_WINDOW_MIN,
    limit_window_minutes: int = DEFAULT_LIMIT_WINDOW_MIN,
    premarket_row: dict[str, Any] | None = None,
    ticker: str | None = None,
    flag_date: str | None = None,
) -> dict[str, EntrySignal | None]:
    """Run every model on the same bar set; return name -> signal|None."""
    row = premarket_row
    if row is None and ticker and flag_date:
        row = load_premarket_row(ticker, flag_date)

    return {
        "immediate": immediate(bars),
        "orb_reclaim": orb_reclaim(bars, orb_minutes=orb_minutes),
        "vwap_reclaim": vwap_reclaim(bars),
        "sweep_reclaim": sweep_reclaim(bars, window_minutes=sweep_window_minutes),
        "premarket_median_limit": premarket_median_limit(
            bars,
            premarket_row=row,
            window_minutes=limit_window_minutes,
        ),
        "premarket_vwap_limit": premarket_vwap_limit(
            bars,
            premarket_row=row,
            window_minutes=limit_window_minutes,
        ),
    }


# ---------------------------------------------------------------------------
# Self-test — USDE 2026-08-21 (immediate got killed; sweep should reclaim)
# ---------------------------------------------------------------------------

def _load_lab_bars(ticker: str, flag_date: str) -> list[dict]:
    path = LAB / "results" / "bars" / f"{ticker.upper()}_{flag_date}.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing lab bars: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("bars") or [])


def main() -> None:
    ticker, flag_date = "USDE", "2026-08-21"
    print("=" * 72)
    print(f"ENTRY MODELS self-test — {ticker} {flag_date}")
    print("Immediate entered the open flush and was killed; sweep_reclaim")
    print("should enter AFTER the flush on the reclaim close.")
    print("=" * 72)

    bars = _load_lab_bars(ticker, flag_date)
    print(f"Loaded {len(bars)} bars from strategy_lab/results/bars/")
    print(f"(fetch_minute_bars / running_vwap / ORB_MINUTES={ORB_MINUTES} "
          f"imported from entry_study)")
    print()

    i0 = rth_start_index(bars)
    if i0 is not None:
        _o, _h, lo, c, _v = _ohlcv(bars[i0])
        print(f"09:30 open bar: time={_time_str(bars[i0])}  "
              f"O={_o:.4f} H={_h:.4f} L={lo:.4f} C={c:.4f}")
    print()

    results = run_all_models(bars)
    print(f"{'model':<16} {'entered?':<10} {'time':<28} {'price':>10} {'idx':>6}")
    print("-" * 72)
    for name, sig in results.items():
        if sig is None:
            print(f"{name:<16} {'NO':<10} {'—':<28} {'—':>10} {'—':>6}")
        else:
            print(
                f"{name:<16} {'YES':<10} {sig.entry_time:<28} "
                f"{sig.entry_price:>10.4f} {sig.bar_index:>6}"
            )

    print()
    imm = results["immediate"]
    sw = results["sweep_reclaim"]
    if imm and sw:
        print(
            f"CHECK: immediate @ {imm.entry_time} ${imm.entry_price:.4f}  vs  "
            f"sweep_reclaim @ {sw.entry_time} ${sw.entry_price:.4f}"
        )
        if sw.entry_time > imm.entry_time:
            print(
                "OK — sweep_reclaim entered LATER (after the flush), "
                "avoiding the naive open kill."
            )
        else:
            print("WARN — sweep_reclaim did not enter after immediate.")
    elif imm and not sw:
        print("WARN — sweep_reclaim returned None (no sweep+reclaim in window).")
    print("=" * 72)


# Re-export study fetch so callers can pull bars with the same helper when
# lab cache is missing (live / ad-hoc dates).
__all__ = [
    "EntrySignal",
    "MODELS",
    "immediate",
    "orb_reclaim",
    "vwap_reclaim",
    "sweep_reclaim",
    "premarket_median_limit",
    "premarket_vwap_limit",
    "load_premarket_row",
    "run_all_models",
    "rth_start_index",
    "fetch_minute_bars",
    "running_vwap",
    "ORB_MINUTES",
    "DEFAULT_LIMIT_WINDOW_MIN",
]


if __name__ == "__main__":
    main()
