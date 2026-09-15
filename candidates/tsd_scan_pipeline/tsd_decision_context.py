"""
Peak Hour — decision-time context for continuation ranking (autopsy gaps).

Attaches causal, signal-time fields the taken-vs-missed study said we were
not looking at:
  - same-session RS vs SPY through the signal 1H bar
  - signal-bar dollar volume / liquidity vs 20d average
  - float shares (Polygon reference, best-effort)
  - lightweight options call-share (attention/take candidates only)

Never look ahead of the signal bar. Missing data → zeros / omit terms.

Live-path network is hard-bounded: short Polygon timeouts, a wall-clock
budget, and a thread join so a hung options/float call cannot hold the
scheduler tick lock (2026-09-15 hour-7 hang after Deep features).
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Any

import pytz

from tsd_scan_pipeline.universe_tsd import POLYGON_BASE, load_polygon_key, polygon_get

ET = pytz.timezone("America/New_York")
RATE_SLEEP = 0.12
_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_LOCK = threading.Lock()
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

# Live-path hard bounds. Do not inherit polygon_get's 60s default or the
# weekly study's 40-contract daily-agg loop (up to 12 × 41 HTTP × 60s).
POLYGON_CTX_TIMEOUT_SEC = 8
DECISION_CONTEXT_BUDGET_SEC = 25.0
DECISION_CONTEXT_HARD_TIMEOUT_SEC = 30.0
OPTIONS_PER_SYMBOL_BUDGET_SEC = 6.0
OPTIONS_SNAPSHOT_LIMIT = 50
OPTIONS_NEAR_DTE = 45
OPTIONS_STRIKE_PCT = 0.10
# Skip starting a new HTTP when less than this remains on the budget.
MIN_HTTP_BUDGET_SEC = 1.5


def _log(msg: str) -> None:
    """Scheduler-log line; always flush (redirected ticks are block-buffered)."""
    print(f"  {msg}", flush=True)


def _cache_get(key: str) -> Any | None:
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
    if not hit:
        return None
    ts, val = hit
    if time.time() - ts > CACHE_TTL_SEC:
        return None
    return val


def _cache_set(key: str, val: Any) -> None:
    with _CACHE_LOCK:
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


def _blank_context(
    row: dict[str, Any] | None = None,
    *,
    degraded: int = 0,
    reason: str | None = None,
) -> dict[str, Any]:
    """Zeros / omit-terms payload so rank can continue without options/RS."""
    src = row or {}
    out: dict[str, Any] = {
        "decision_context_ok": 0,
        "rs_spy_1h": None,
        "rs_spy_1h_ok": 0,
        "dollar_vol_1h": _finite(src.get("dollar_vol_1h")),
        "dollar_vol_1h_vs_20d": None,
        "float_shares": _finite(src.get("float_shares")),
        "micro_dead_tape": 0,
        "options_call_share": None,
        "options_score_lite": None,
        "decision_context_degraded": int(degraded),
    }
    if reason:
        out["decision_context_skip_reason"] = reason
    return out


def _cancelled(cancel: threading.Event | None) -> bool:
    return bool(cancel is not None and cancel.is_set())


def _budget_left(deadline_ts: float | None) -> float:
    if deadline_ts is None:
        return DECISION_CONTEXT_BUDGET_SEC
    return deadline_ts - time.time()


def _fetch_1h_bars_day(
    symbol: str,
    *,
    day,
    api_key: str,
    timeout_sec: int = POLYGON_CTX_TIMEOUT_SEC,
) -> list[dict[str, Any]]:
    """1H bars for one ET calendar day (04:00–20:00). Cached. Short HTTP timeout."""
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
            url,
            {"adjusted": "true", "sort": "asc", "limit": 50000},
            api_key,
            timeout=int(timeout_sec),
        )
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


def fetch_float_shares(
    symbol: str,
    *,
    api_key: str,
    timeout_sec: int = POLYGON_CTX_TIMEOUT_SEC,
) -> float | None:
    """Best-effort float / shares outstanding from Polygon ticker overview."""
    cache_key = f"float:{symbol.upper()}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return _finite(cached.get("float_shares")) if isinstance(cached, dict) else _finite(cached)

    url = f"{POLYGON_BASE}/v3/reference/tickers/{symbol.upper()}"
    out: float | None = None
    try:
        data = polygon_get(url, {}, api_key, timeout=int(timeout_sec))
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


def _contract_volume(item: dict[str, Any]) -> float:
    """Day volume from a Polygon options snapshot row (several payload shapes)."""
    day = item.get("day") or item.get("last_day") or {}
    if isinstance(day, dict):
        vol = _finite(day.get("volume") if day.get("volume") is not None else day.get("v"))
        if vol is not None and vol > 0:
            return vol
    vol = _finite(item.get("volume") if item.get("volume") is not None else item.get("v"))
    return vol if vol is not None and vol > 0 else 0.0


def _snapshot_results(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("results")
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    if isinstance(raw, dict):
        inner = raw.get("results")
        if isinstance(inner, list):
            return [r for r in inner if isinstance(r, dict)]
    return []


def _fetch_options_snapshot(
    symbol: str,
    *,
    api_key: str,
    as_of: datetime,
    spot: float,
    timeout_sec: int = POLYGON_CTX_TIMEOUT_SEC,
) -> dict[str, Any]:
    """
    One-page Polygon options snapshot → ATM-ish call/put volume share.

    Replaces the weekly study's per-contract daily-agg loop on the live path.
    Never follows next_url (one HTTP, hard-capped payload).
    """
    day = as_of.date()
    exp_cutoff = day + timedelta(days=OPTIONS_NEAR_DTE)
    url = f"{POLYGON_BASE}/v3/snapshot/options/{symbol.upper()}"
    params = {
        "limit": OPTIONS_SNAPSHOT_LIMIT,
        "expiration_date.gte": day.isoformat(),
        "expiration_date.lte": exp_cutoff.isoformat(),
    }
    data = polygon_get(url, params, api_key, timeout=int(timeout_sec))
    contracts = _snapshot_results(data)
    call_v = 0.0
    put_v = 0.0
    checked = 0
    for item in contracts:
        details = item.get("details") if isinstance(item.get("details"), dict) else item
        exp_raw = details.get("expiration_date") or item.get("expiration_date")
        if exp_raw:
            try:
                exp_d = datetime.strptime(str(exp_raw)[:10], "%Y-%m-%d").date()
            except ValueError:
                exp_d = None
            if exp_d is not None and (exp_d < day or exp_d > exp_cutoff):
                continue
        strike = _finite(details.get("strike_price") or item.get("strike_price"))
        if strike and spot > 0 and abs(strike - spot) / spot > OPTIONS_STRIKE_PCT:
            continue
        vol = _contract_volume(item)
        ctype = str(details.get("contract_type") or item.get("contract_type") or "").lower()
        if ctype == "call":
            call_v += vol
        elif ctype == "put":
            put_v += vol
        else:
            continue
        checked += 1
    out: dict[str, Any] = {
        "options_available": checked > 0 and (call_v + put_v) > 0,
        "call_volume": call_v,
        "put_volume": put_v,
        "contracts_checked": checked,
        "options_source": "snapshot",
    }
    return out


def fetch_options_call_share(
    symbol: str,
    *,
    api_key: str,
    as_of: datetime | None = None,
    spot: float | None = None,
    timeout_sec: int = POLYGON_CTX_TIMEOUT_SEC,
) -> dict[str, Any]:
    """
    Lightweight same-day options call/put share near ATM (best-effort).

    Live path: one snapshot HTTP with a short timeout. Does NOT call
    tsd_options_study._fetch_options_day_volume (40 contract aggs × 60s).
    """
    when = _as_et(as_of)
    day = when.date().isoformat()
    cache_key = f"opt:{symbol.upper()}:{day}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return dict(cached)

    px = float(spot or 0)
    if px <= 0:
        return {}

    ctx: dict[str, Any] = {}
    t0 = time.time()
    _log(f"decision_context options {symbol.upper()} snapshot start")
    try:
        ctx = _fetch_options_snapshot(
            symbol.upper(),
            api_key=api_key,
            as_of=when,
            spot=px,
            timeout_sec=timeout_sec,
        ) or {}
    except Exception as exc:
        _log(f"decision_context options {symbol.upper()} fail: {exc}")
        ctx = {}

    call_v = _finite(ctx.get("call_volume")) or 0.0
    put_v = _finite(ctx.get("put_volume")) or 0.0
    total = call_v + put_v
    if total > 0:
        ctx["call_share"] = round(call_v / total, 4)
    _cache_set(cache_key, ctx)
    _log(
        f"decision_context options {symbol.upper()} "
        f"call_share={ctx.get('call_share')} "
        f"contracts={ctx.get('contracts_checked')} "
        f"({time.time() - t0:.1f}s)"
    )
    return ctx


def decision_context_for_row(
    row: dict[str, Any],
    *,
    api_key: str | None = None,
    now: datetime | None = None,
    include_options: bool = False,
    spy_bars: list[dict[str, Any]] | None = None,
    deadline_ts: float | None = None,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    """
    Build decision-time feature dict for one launch row (no look-ahead).
    """
    if _cancelled(cancel):
        return _blank_context(row, degraded=1, reason="cancelled")

    key = api_key or load_polygon_key()
    when = _as_et(now)
    sym = str(row.get("symbol") or "").upper()
    out = _blank_context(row)
    if not sym or not key:
        return out

    try:
        hour = int(row.get("htf_1h_bar_hour") if row.get("htf_1h_bar_hour") is not None else when.hour)
    except (TypeError, ValueError):
        hour = when.hour

    day = when.date()
    from tsd_scan_pipeline.tsd_1h_signal import session_bars_from_cache

    stock_bars = session_bars_from_cache(sym, day)
    if not stock_bars:
        if _budget_left(deadline_ts) < MIN_HTTP_BUDGET_SEC or _cancelled(cancel):
            out["decision_context_degraded"] = 1
            out["decision_context_skip_reason"] = "budget"
            return out
        stock_bars = _fetch_1h_bars_day(sym, day=day, api_key=key)
    stock_ret, dv1h = _session_return_through_hour(stock_bars, signal_hour=hour)
    if dv1h is not None:
        out["dollar_vol_1h"] = round(dv1h, 2)

    spy = spy_bars if spy_bars is not None else []
    if not spy and _budget_left(deadline_ts) >= MIN_HTTP_BUDGET_SEC and not _cancelled(cancel):
        spy = _fetch_1h_bars_day("SPY", day=day, api_key=key)
    spy_ret, _ = _session_return_through_hour(spy, signal_hour=hour)
    if stock_ret is not None and spy_ret is not None:
        out["rs_spy_1h"] = round(stock_ret - spy_ret, 5)
        out["rs_spy_1h_ok"] = 1
        out["decision_context_ok"] = 1

    dv20 = _finite(row.get("dollar_vol_20d") or row.get("dollar_vol_20d_avg"))
    if out.get("dollar_vol_1h") and dv20 and dv20 > 0:
        out["dollar_vol_1h_vs_20d"] = round(float(out["dollar_vol_1h"]) / dv20, 5)

    if out.get("float_shares") is None:
        if _budget_left(deadline_ts) >= MIN_HTTP_BUDGET_SEC and not _cancelled(cancel):
            fl = fetch_float_shares(sym, api_key=key)
            if fl:
                out["float_shares"] = fl
        else:
            out["decision_context_degraded"] = 1
            out["decision_context_skip_reason"] = out.get("decision_context_skip_reason") or "budget"

    vr = _finite(row.get("vol_ratio_20") or row.get("vol_ratio")) or 1.0
    vs = out.get("dollar_vol_1h_vs_20d")
    if vr < DEAD_TAPE_VOL_RATIO and (vs is None or vs < DV1H_VS_AVG_DEAD):
        out["micro_dead_tape"] = 1

    if include_options:
        remain = _budget_left(deadline_ts)
        if remain < MIN_HTTP_BUDGET_SEC or _cancelled(cancel):
            out["decision_context_degraded"] = 1
            out["decision_context_skip_reason"] = out.get("decision_context_skip_reason") or "options_budget"
        else:
            spot = _finite(row.get("htf_1h_close") or row.get("close") or row.get("1h_close"))
            opt_deadline = time.time() + min(OPTIONS_PER_SYMBOL_BUDGET_SEC, max(0.0, remain))
            # Snapshot is a single HTTP; still skip if the join watchdog already fired.
            if time.time() < opt_deadline and not _cancelled(cancel):
                opt = fetch_options_call_share(sym, api_key=key, as_of=when, spot=spot)
                cs = _finite(opt.get("call_share"))
                if cs is not None:
                    out["options_call_share"] = cs
                    # 0–100 lite score from call share
                    out["options_score_lite"] = round(cs * 100.0, 2)
                    out["decision_context_ok"] = 1

    return out


def _attach_decision_context_bounded(
    rows: list[dict[str, Any]],
    *,
    api_key: str | None,
    now: datetime | None,
    options_top_n: int,
    deadline_ts: float,
    cancel: threading.Event | None,
    out_holder: dict[str, Any],
    out_lock: threading.Lock,
) -> None:
    """
    Fill out_holder['rows'] in order. Stops starting new work on budget/cancel.

    Writes a snapshot after each row so a hard-timeout join can use partial
    results without waiting for the current HTTP to finish.
    """
    key = api_key or load_polygon_key()
    when = _as_et(now)
    filled: list[dict[str, Any]] = []

    def _publish(reason: str | None = None) -> None:
        snapshot = list(filled)
        if reason:
            for row in rows[len(snapshot):]:
                snapshot.append({**row, **_blank_context(row, degraded=1, reason=reason)})
        with out_lock:
            out_holder["rows"] = snapshot
            if reason:
                out_holder["degraded"] = True
                out_holder["reason"] = reason

    if _cancelled(cancel) or _budget_left(deadline_ts) < MIN_HTTP_BUDGET_SEC:
        _publish("timeout" if _cancelled(cancel) else "budget")
        return

    _log(f"decision_context spy_1h fetch begin remain={_budget_left(deadline_ts):.1f}s")
    t_spy = time.time()
    spy_bars: list[dict[str, Any]] = []
    if key and _budget_left(deadline_ts) >= MIN_HTTP_BUDGET_SEC and not _cancelled(cancel):
        try:
            spy_bars = _fetch_1h_bars_day("SPY", day=when.date(), api_key=key)
        except Exception as exc:
            _log(f"decision_context spy_1h fail: {exc}")
            spy_bars = []
    _log(
        f"decision_context spy_1h bars={len(spy_bars)} "
        f"({time.time() - t_spy:.1f}s) remain={_budget_left(deadline_ts):.1f}s"
    )

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
    _log(
        f"decision_context options_top_n={options_top_n} "
        f"opt_syms={len(opt_syms)} n={len(rows)}"
    )

    for i, row in enumerate(rows):
        if _cancelled(cancel):
            _log(f"decision_context cancelled at row {i}/{len(rows)}")
            _publish("timeout")
            return
        if _budget_left(deadline_ts) < MIN_HTTP_BUDGET_SEC:
            _log(
                f"decision_context BUDGET skip remainder "
                f"filled={i}/{len(rows)} remain={_budget_left(deadline_ts):.1f}s"
            )
            _publish("budget")
            return
        sym = str(row.get("symbol") or "").upper()
        want_opt = sym in opt_syms
        _log(
            f"decision_context row {i + 1}/{len(rows)} {sym or '?'} "
            f"options={int(want_opt)} remain={_budget_left(deadline_ts):.1f}s"
        )
        try:
            ctx = decision_context_for_row(
                row,
                api_key=key,
                now=when,
                include_options=want_opt,
                spy_bars=spy_bars,
                deadline_ts=deadline_ts,
                cancel=cancel,
            )
        except Exception as exc:
            _log(f"decision_context row {sym or '?'} fail: {exc}")
            ctx = _blank_context(row, degraded=1, reason="row_error")
        filled.append({**row, **ctx})
        _publish(None)

    _publish(None)


def attach_decision_context(
    rows: list[dict[str, Any]],
    *,
    api_key: str | None = None,
    now: datetime | None = None,
    options_top_n: int = 12,
    budget_sec: float | None = None,
    hard_timeout_sec: float | None = None,
) -> list[dict[str, Any]]:
    """
    Mutate/return launch rows with decision-time context.

    Options overlay only on the top ``options_top_n`` by preliminary continuation
    score (Polygon cost control). Never blocks the launch: budget / hard-timeout
    return already-filled rows plus degraded blanks for the rest.
    """
    if not rows:
        return rows

    budget = DECISION_CONTEXT_BUDGET_SEC if budget_sec is None else float(budget_sec)
    hard = DECISION_CONTEXT_HARD_TIMEOUT_SEC if hard_timeout_sec is None else float(hard_timeout_sec)
    t0 = time.time()
    deadline_ts = t0 + max(0.05, budget)
    cancel = threading.Event()
    out_lock = threading.Lock()
    out_holder: dict[str, Any] = {"rows": None, "degraded": False, "reason": None}

    _log(
        f"STAGE decision_context: begin n={len(rows)} options_top_n={options_top_n} "
        f"budget={budget:.1f}s hard={hard:.1f}s"
    )

    def _run() -> None:
        try:
            _attach_decision_context_bounded(
                rows,
                api_key=api_key,
                now=now,
                options_top_n=options_top_n,
                deadline_ts=deadline_ts,
                cancel=cancel,
                out_holder=out_holder,
                out_lock=out_lock,
            )
        except Exception as exc:
            _log(f"decision_context worker fail: {exc}")
            with out_lock:
                if out_holder.get("rows") is None:
                    out_holder["rows"] = [
                        {**r, **_blank_context(r, degraded=1, reason="worker_error")}
                        for r in rows
                    ]
                    out_holder["degraded"] = True
                    out_holder["reason"] = "worker_error"

    worker = threading.Thread(
        target=_run, name="php-decision-context", daemon=True,
    )
    worker.start()
    worker.join(timeout=max(0.05, hard))
    timed_out = worker.is_alive()
    if timed_out:
        cancel.set()
        _log(
            f"decision_context HARD_TIMEOUT after {hard:.1f}s — "
            "degrading remainder, launch continues"
        )
        # Brief wait so a worker that just finished can publish; do not block
        # the tick on the in-flight Polygon call (capped at POLYGON_CTX_TIMEOUT_SEC).
        worker.join(timeout=0.2)

    with out_lock:
        filled = out_holder.get("rows")
        reason = out_holder.get("reason")
        degraded_flag = bool(out_holder.get("degraded")) or timed_out

    if filled is None:
        reason = "timeout" if timed_out else (reason or "empty")
        filled = [
            {**r, **_blank_context(r, degraded=1, reason=reason)}
            for r in rows
        ]
        degraded_flag = True
    elif len(filled) < len(rows):
        reason = reason or ("timeout" if timed_out else "budget")
        extra = [
            {**r, **_blank_context(r, degraded=1, reason=reason)}
            for r in rows[len(filled):]
        ]
        filled = list(filled) + extra
        degraded_flag = True
    elif timed_out:
        # Worker published a full list but join still timed out — keep data.
        degraded_flag = any(int(r.get("decision_context_degraded") or 0) == 1 for r in filled)

    n_ok = sum(1 for r in filled if int(r.get("decision_context_ok") or 0) == 1)
    n_deg = sum(1 for r in filled if int(r.get("decision_context_degraded") or 0) == 1)
    n_opt = sum(1 for r in filled if r.get("options_call_share") is not None)
    _log(
        f"STAGE decision_context: end ok={n_ok}/{len(filled)} "
        f"degraded={n_deg} options={n_opt} "
        f"reason={reason or ('ok' if not degraded_flag else 'partial')} "
        f"({time.time() - t0:.1f}s)"
    )
    return filled


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
