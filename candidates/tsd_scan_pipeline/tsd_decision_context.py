"""
Peak Hour — decision-time context for continuation ranking (autopsy gaps).

Attaches causal, signal-time fields the taken-vs-missed study said we were
not looking at:
  - same-session RS vs SPY through the signal 1H bar
  - signal-bar dollar volume / liquidity vs 20d average
  - float shares (Polygon reference, best-effort)
  - lightweight options call-share (attention/take candidates only)

Never look ahead of the signal bar. Missing data → zeros / omit terms.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

import pytz

from tsd_scan_pipeline.universe_tsd import POLYGON_BASE, load_polygon_key, polygon_get

ET = pytz.timezone("America/New_York")
RATE_SLEEP = 0.12
_CACHE: dict[str, tuple[float, Any]] = {}
CACHE_TTL_SEC = 900.0

# Soft score hooks (also mirrored in tsd_launch_score v1.5+)
RS_1H_LEAD = 0.015
RS_1H_LEAD_STRONG = 0.03
RS_1H_LAG = -0.015
RS_1H_LEAD_PTS = 6.0
RS_1H_LEAD_STRONG_PTS = 12.0
RS_1H_LAG_PENALTY = 12.0

DV1H_VS_AVG_STRONG = 0.15  # signal hour $vol >= 15% of 20d avg day
DV1H_VS_AVG_DEAD = 0.02
DV1H_STRONG_PTS = 8.0
DV1H_DEAD_PENALTY = 10.0

DEAD_TAPE_VOL_RATIO = 0.75
DEAD_TAPE_PENALTY = 14.0  # high-hist / weak tape demotion

OPTIONS_CALL_SHARE_BOOST = 0.58
OPTIONS_CALL_SHARE_PTS = 6.0
OPTIONS_PUT_HEAVY = 0.42
OPTIONS_PUT_PENALTY = 5.0

FLOAT_LOW = 20_000_000
FLOAT_HIGH = 150_000_000
FLOAT_SWEET_PTS = 4.0
FLOAT_BLOATED_PENALTY = 4.0


def _cache_get(key: str) -> Any | None:
    hit = _CACHE.get(key)
    if not hit:
        return None
    ts, val = hit
    if time.time() - ts > CACHE_TTL_SEC:
        return None
    return val


def _cache_set(key: str, val: Any) -> None:
    _CACHE[key] = (time.time(), val)


def _as_et(now: datetime | None) -> datetime:
    dt = now or datetime.now(ET)
    if dt.tzinfo is None:
        return ET.localize(dt)
    return dt.astimezone(ET)


def _finite(val: Any) -> float | None:
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _fetch_1h_bars_day(
    symbol: str,
    *,
    day,
    api_key: str,
) -> list[dict[str, Any]]:
    """1H bars for one ET calendar day (04:00–20:00). Cached."""
    cache_key = f"h1:{symbol.upper()}:{day.isoformat()}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return list(cached)

    start = ET.localize(datetime.combine(day, datetime.min.time().replace(hour=4)))
    end = ET.localize(datetime.combine(day, datetime.min.time().replace(hour=20)))
    url = (
        f"{POLYGON_BASE}/v2/aggs/ticker/{symbol.upper()}/range/1/hour/"
        f"{int(start.timestamp() * 1000)}/{int(end.timestamp() * 1000)}"
    )
    bars: list[dict[str, Any]] = []
    try:
        data = polygon_get(
            url, {"adjusted": "true", "sort": "asc", "limit": 50000}, api_key,
        )
        time.sleep(RATE_SLEEP)
        for b in data.get("results") or []:
            ts = int(b.get("t") or 0)
            if ts <= 0:
                continue
            dt = datetime.fromtimestamp(ts / 1000.0, tz=ET)
            # Polygon left-labeled: close hour = start+1
            close_hour = (dt.hour + 1) % 24
            bars.append({
                "et": dt,
                "close_hour": close_hour,
                "o": float(b.get("o") or 0),
                "h": float(b.get("h") or 0),
                "l": float(b.get("l") or 0),
                "c": float(b.get("c") or 0),
                "v": float(b.get("v") or 0),
            })
    except Exception:
        bars = []
    _cache_set(cache_key, bars)
    return list(bars)


def _session_return_through_hour(
    bars: list[dict[str, Any]],
    *,
    signal_hour: int,
) -> tuple[float | None, float | None]:
    """
    Return (session_ret, signal_bar_dollar_vol) using bars with close_hour <= signal_hour.
    Session base = first bar open of the day (causal at signal time).
    """
    through = [b for b in bars if int(b["close_hour"]) <= int(signal_hour)]
    if not through:
        return None, None
    first = through[0]
    last = through[-1]
    o0 = float(first["o"] or 0)
    c1 = float(last["c"] or 0)
    if o0 <= 0 or c1 <= 0:
        return None, None
    sig = next((b for b in through if int(b["close_hour"]) == int(signal_hour)), last)
    dv = float(sig["c"] or 0) * float(sig["v"] or 0)
    return (c1 / o0) - 1.0, dv if dv > 0 else None


def fetch_float_shares(symbol: str, *, api_key: str) -> float | None:
    """Best-effort float / shares outstanding from Polygon ticker overview."""
    cache_key = f"float:{symbol.upper()}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return _finite(cached.get("float_shares")) if isinstance(cached, dict) else _finite(cached)

    url = f"{POLYGON_BASE}/v3/reference/tickers/{symbol.upper()}"
    out: float | None = None
    try:
        data = polygon_get(url, {}, api_key)
        time.sleep(RATE_SLEEP)
        res = data.get("results") or {}
        for k in (
            "share_class_shares_outstanding",
            "weighted_shares_outstanding",
            "float",
        ):
            out = _finite(res.get(k))
            if out and out > 0:
                break
    except Exception:
        out = None
    _cache_set(cache_key, {"float_shares": out})
    return out


def fetch_options_call_share(
    symbol: str,
    *,
    api_key: str,
    as_of: datetime | None = None,
    spot: float | None = None,
) -> dict[str, Any]:
    """
    Lightweight same-day options call/put share near ATM (best-effort).

    Reuses the weekly study endpoint pattern; returns empty on tier/blocks.
    """
    when = _as_et(as_of)
    day = when.date().isoformat()
    cache_key = f"opt:{symbol.upper()}:{day}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return dict(cached)

    # Defer to options study helper when available (keeps one Polygon path)
    ctx: dict[str, Any] = {}
    try:
        from tsd_scan_pipeline.tsd_options_study import _fetch_options_day_volume

        px = float(spot or 0)
        if px <= 0:
            return {}
        ctx = _fetch_options_day_volume(api_key, symbol.upper(), when.date(), px) or {}
    except Exception:
        ctx = {}

    call_v = _finite(ctx.get("call_volume")) or 0.0
    put_v = _finite(ctx.get("put_volume")) or 0.0
    total = call_v + put_v
    if total > 0:
        ctx["call_share"] = round(call_v / total, 4)
    _cache_set(cache_key, ctx)
    return ctx


def decision_context_for_row(
    row: dict[str, Any],
    *,
    api_key: str | None = None,
    now: datetime | None = None,
    include_options: bool = False,
    spy_bars: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Build decision-time feature dict for one launch row (no look-ahead).
    """
    key = api_key or load_polygon_key()
    when = _as_et(now)
    sym = str(row.get("symbol") or "").upper()
    out: dict[str, Any] = {
        "decision_context_ok": 0,
        "rs_spy_1h": None,
        "rs_spy_1h_ok": 0,
        "dollar_vol_1h": _finite(row.get("dollar_vol_1h")),
        "dollar_vol_1h_vs_20d": None,
        "float_shares": _finite(row.get("float_shares")),
        "micro_dead_tape": 0,
        "options_call_share": None,
        "options_score_lite": None,
    }
    if not sym or not key:
        return out

    try:
        hour = int(row.get("htf_1h_bar_hour") if row.get("htf_1h_bar_hour") is not None else when.hour)
    except (TypeError, ValueError):
        hour = when.hour

    day = when.date()
    stock_bars = _fetch_1h_bars_day(sym, day=day, api_key=key)
    stock_ret, dv1h = _session_return_through_hour(stock_bars, signal_hour=hour)
    if dv1h is not None:
        out["dollar_vol_1h"] = round(dv1h, 2)

    spy = spy_bars if spy_bars is not None else _fetch_1h_bars_day("SPY", day=day, api_key=key)
    spy_ret, _ = _session_return_through_hour(spy, signal_hour=hour)
    if stock_ret is not None and spy_ret is not None:
        out["rs_spy_1h"] = round(stock_ret - spy_ret, 5)
        out["rs_spy_1h_ok"] = 1
        out["decision_context_ok"] = 1

    dv20 = _finite(row.get("dollar_vol_20d") or row.get("dollar_vol_20d_avg"))
    if out.get("dollar_vol_1h") and dv20 and dv20 > 0:
        out["dollar_vol_1h_vs_20d"] = round(float(out["dollar_vol_1h"]) / dv20, 5)

    if out.get("float_shares") is None:
        fl = fetch_float_shares(sym, api_key=key)
        if fl:
            out["float_shares"] = fl

    vr = _finite(row.get("vol_ratio_20") or row.get("vol_ratio")) or 1.0
    vs = out.get("dollar_vol_1h_vs_20d")
    if vr < DEAD_TAPE_VOL_RATIO and (vs is None or vs < DV1H_VS_AVG_DEAD):
        out["micro_dead_tape"] = 1

    if include_options:
        spot = _finite(row.get("htf_1h_close") or row.get("close") or row.get("1h_close"))
        opt = fetch_options_call_share(sym, api_key=key, as_of=when, spot=spot)
        cs = _finite(opt.get("call_share"))
        if cs is not None:
            out["options_call_share"] = cs
            # 0–100 lite score from call share
            out["options_score_lite"] = round(cs * 100.0, 2)
            out["decision_context_ok"] = 1

    return out


def attach_decision_context(
    rows: list[dict[str, Any]],
    *,
    api_key: str | None = None,
    now: datetime | None = None,
    options_top_n: int = 12,
) -> list[dict[str, Any]]:
    """
    Mutate/return launch rows with decision-time context.

    Options overlay only on the top ``options_top_n`` by preliminary continuation
    score (Polygon cost control).
    """
    if not rows:
        return rows
    key = api_key or load_polygon_key()
    when = _as_et(now)
    spy_bars = _fetch_1h_bars_day("SPY", day=when.date(), api_key=key) if key else []

    # Preliminary score for options budget (existing fields only)
    ranked = sorted(
        rows,
        key=lambda r: -(
            float(r.get("continuation_score") or r.get("combined_rank_score") or 0)
        ),
    )
    opt_syms = {
        str(r.get("symbol") or "").upper()
        for r in ranked[: max(0, int(options_top_n))]
    }

    out: list[dict[str, Any]] = []
    for row in rows:
        sym = str(row.get("symbol") or "").upper()
        ctx = decision_context_for_row(
            row,
            api_key=key,
            now=when,
            include_options=sym in opt_syms,
            spy_bars=spy_bars,
        )
        merged = {**row, **ctx}
        out.append(merged)
    return out


def apply_decision_context_score_terms(score: float, row: dict[str, Any]) -> float:
    """Soft decision-time overlays used by continuation ranker (v1.5+)."""
    s = float(score)

    if int(row.get("rs_spy_1h_ok") or 0) == 1:
        rs = _finite(row.get("rs_spy_1h"))
        if rs is not None:
            if rs >= RS_1H_LEAD_STRONG:
                s += RS_1H_LEAD_STRONG_PTS
            elif rs >= RS_1H_LEAD:
                s += RS_1H_LEAD_PTS
            elif rs <= RS_1H_LAG:
                s -= RS_1H_LAG_PENALTY

    vs = _finite(row.get("dollar_vol_1h_vs_20d"))
    if vs is not None:
        if vs >= DV1H_VS_AVG_STRONG:
            s += DV1H_STRONG_PTS
        elif vs < DV1H_VS_AVG_DEAD:
            s -= DV1H_DEAD_PENALTY

    if int(row.get("micro_dead_tape") or 0) == 1:
        # Extra demotion when history looks good but tape is dead (score inversion fix)
        prior = _finite(row.get("ticker_prior_hit1r_rate")) or 0.0
        if prior >= 0.35:
            s -= DEAD_TAPE_PENALTY
        else:
            s -= DEAD_TAPE_PENALTY * 0.5

    fl = _finite(row.get("float_shares"))
    if fl is not None:
        if FLOAT_LOW <= fl <= FLOAT_HIGH:
            s += FLOAT_SWEET_PTS
        elif fl > FLOAT_HIGH * 2:
            s -= FLOAT_BLOATED_PENALTY

    cs = _finite(row.get("options_call_share"))
    if cs is not None:
        if cs >= OPTIONS_CALL_SHARE_BOOST:
            s += OPTIONS_CALL_SHARE_PTS
        elif cs <= OPTIONS_PUT_HEAVY:
            s -= OPTIONS_PUT_PENALTY

    return s
