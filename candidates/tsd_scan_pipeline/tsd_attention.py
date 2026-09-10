"""
Peak Hour — Attention Pool builder.

1H continuation_score nominates deep-swing setups; gainers / buzz / soft-extension
promote additional names into case review. Score alone does not buy.
"""
from __future__ import annotations

import time
from typing import Any

import requests

from tsd_scan_pipeline.universe_tsd import POLYGON_BASE, load_polygon_key

# Attention pool sizing (AI cost cap)
ATTENTION_TOP_K = 6
ATTENTION_POOL_MAX = 12

# Soft-extension lane: may join pool when momentum context present (not auto-buy)
SOFT_EXT_SCAN_MIN = 55.0
SOFT_EXT_SCAN_MAX = 70.0
HARD_EXT_SCAN = 75.0

# Room / buzz thresholds
ROOM_TO_HIGH_MIN = 0.10  # 10% under 20d high
BUZZ_ACCEL_MIN_MSGS = 12.0  # absolute floor when no peer baseline
BUZZ_ACCEL_PEER_RATIO = 1.75  # vs median ST msgs among passers


def fetch_polygon_gainers(
    *,
    api_key: str | None = None,
    timeout: float = 20.0,
) -> set[str]:
    """
    Today's top US equity gainers (Polygon snapshot).

    Returns uppercase symbols. Empty set on failure (fail-open for pool build).
    """
    key = api_key or load_polygon_key()
    url = f"{POLYGON_BASE}/v2/snapshot/locale/us/markets/stocks/gainers"
    try:
        resp = requests.get(url, params={"apiKey": key}, timeout=timeout)
        time.sleep(0.12)
        if resp.status_code != 200:
            print(f"  gainers fetch HTTP {resp.status_code}")
            return set()
        tickers = resp.json().get("tickers") or []
        out: set[str] = set()
        for t in tickers:
            sym = str(t.get("ticker") or "").upper().strip()
            if sym:
                out.add(sym)
        return out
    except Exception as exc:
        print(f"  gainers fetch warn: {exc}")
        return set()


def _scan(row: dict[str, Any]) -> float:
    try:
        return float(row.get("scan_score") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _room(row: dict[str, Any]) -> float:
    try:
        return float(row.get("dist_20d_high_pct") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _st_msgs(row: dict[str, Any]) -> float:
    try:
        return float(row.get("st_msg_24h") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _cont(row: dict[str, Any]) -> float:
    try:
        return float(
            row.get("continuation_score")
            or row.get("combined_rank_score")
            or 0.0
        )
    except (TypeError, ValueError):
        return 0.0


def buzz_accel_threshold(ranked: list[dict[str, Any]]) -> float:
    """Peer-relative ST message floor for buzz acceleration among passers."""
    vals = sorted(_st_msgs(r) for r in ranked if int(r.get("st_ok") or 0) == 1)
    if len(vals) < 3:
        return BUZZ_ACCEL_MIN_MSGS
    mid = vals[len(vals) // 2]
    return max(BUZZ_ACCEL_MIN_MSGS, mid * BUZZ_ACCEL_PEER_RATIO)


def annotate_momentum_context(
    row: dict[str, Any],
    *,
    gainers: set[str],
    buzz_threshold: float,
) -> dict[str, Any]:
    """
    Attach on_gainers / buzz_accel / momentum_context flags.

    Momentum context qualifies deep-swing scores: HTF rising + (gainers OR
    buzz OR elevated relative volume OR constructive room with vol).
    """
    out = dict(row)
    sym = str(out.get("symbol") or "").upper()
    on_gainers = sym in gainers
    st_ok = int(out.get("st_ok") or 0) == 1
    msgs = _st_msgs(out)
    buzz = st_ok and msgs >= buzz_threshold
    try:
        vol_ratio = float(out.get("vol_ratio_20") or 0.0)
    except (TypeError, ValueError):
        vol_ratio = 0.0
    vol_hot = vol_ratio >= 1.25
    htf_ok = (
        float(out.get("htf_score") or 0.0) > 0
        or out.get("htf_sma20_rising") is True
        or float(out.get("htf_sma20_slope_pct") or 0.0) > 0
    )
    room_ok = _room(out) >= ROOM_TO_HIGH_MIN
    momentum = bool(
        htf_ok
        and (on_gainers or buzz or vol_hot or (room_ok and vol_ratio >= 0.8))
    )
    out["on_gainers"] = on_gainers
    out["buzz_accel"] = buzz
    out["momentum_context"] = momentum
    out["attention_reasons"] = []
    return out


def _soft_extension_eligible(row: dict[str, Any]) -> bool:
    """scan 55–70 may join pool when room / gainers / buzz present."""
    scan = _scan(row)
    if scan < SOFT_EXT_SCAN_MIN or scan > SOFT_EXT_SCAN_MAX:
        return False
    if scan >= HARD_EXT_SCAN:
        return False
    if str(row.get("bar_state") or "") == "extended":
        return False
    return bool(
        _room(row) >= ROOM_TO_HIGH_MIN
        or row.get("on_gainers")
        or row.get("buzz_accel")
    )


def build_attention_pool(
    ranked: list[dict[str, Any]],
    *,
    polygon_key: str | None = None,
    gainers: set[str] | None = None,
    top_k: int = ATTENTION_TOP_K,
    pool_max: int = ATTENTION_POOL_MAX,
) -> list[dict[str, Any]]:
    """
    Build case-review shortlist from ranked 1H passers.

    Union of:
      - top_k by continuation_score
      - on Polygon gainers (with valid 1H pass)
      - buzz volume acceleration
      - soft-extension lane with momentum hooks
    """
    if not ranked:
        return []

    gset = gainers if gainers is not None else fetch_polygon_gainers(api_key=polygon_key)
    buzz_thr = buzz_accel_threshold(ranked)
    annotated = [
        annotate_momentum_context(r, gainers=gset, buzz_threshold=buzz_thr)
        for r in ranked
    ]
    # Drop hard-extended even if somehow present
    annotated = [
        r for r in annotated
        if _scan(r) < HARD_EXT_SCAN and str(r.get("bar_state") or "") != "extended"
    ]

    by_sym: dict[str, dict[str, Any]] = {}

    def _put(row: dict[str, Any], reason: str) -> None:
        sym = str(row.get("symbol") or "").upper()
        if not sym:
            return
        if sym not in by_sym:
            row = dict(row)
            row["attention_reasons"] = [reason]
            by_sym[sym] = row
        else:
            reasons = list(by_sym[sym].get("attention_reasons") or [])
            if reason not in reasons:
                reasons.append(reason)
            by_sym[sym]["attention_reasons"] = reasons

    # 1) Top continuation (deep-swing nomination)
    top = sorted(annotated, key=_cont, reverse=True)[: max(1, int(top_k))]
    for r in top:
        _put(r, "top_continuation")

    # 2) Gainers ∩ 1H passers
    for r in annotated:
        if r.get("on_gainers"):
            _put(r, "polygon_gainer")

    # 3) Buzz acceleration
    for r in annotated:
        if r.get("buzz_accel"):
            _put(r, "buzz_accel")

    # 4) Soft-extension attention lane
    for r in annotated:
        if _soft_extension_eligible(r):
            _put(r, "soft_extension")

    pool = list(by_sym.values())
    pool.sort(key=lambda r: (-_cont(r), _scan(r)))
    if len(pool) > pool_max:
        # Prefer momentum_context names when trimming
        hot = [r for r in pool if r.get("momentum_context")]
        cold = [r for r in pool if not r.get("momentum_context")]
        trimmed = (hot + cold)[:pool_max]
        pool = trimmed

    print(
        f"  Attention pool: {len(pool)}/{len(annotated)} passers "
        f"(gainers={len(gset)} buzz_thr={buzz_thr:.1f})"
    )
    for r in pool:
        print(
            f"    {str(r.get('symbol')):<6} cont={_cont(r):.1f} "
            f"scan={_scan(r):.1f} mom={int(bool(r.get('momentum_context')))} "
            f"reasons={','.join(r.get('attention_reasons') or [])}"
        )
    return pool
