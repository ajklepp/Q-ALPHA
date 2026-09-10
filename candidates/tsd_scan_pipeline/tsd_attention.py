"""
Peak Hour — Attention Pool builder.

1H continuation_score nominates deep-swing setups. Popularity / recent
leaderboard history + soft-extension promote into case review.
StockTwits is optional corroboration only — not the popularity source of truth.
"""
from __future__ import annotations

from typing import Any

from tsd_scan_pipeline.tsd_popularity import (
    annotate_popularity,
    build_popularity_context,
)

# Attention pool sizing (AI cost cap)
ATTENTION_TOP_K = 6
ATTENTION_POOL_MAX = 12

# Soft-extension lane: may join pool when momentum/popularity present (not auto-buy)
SOFT_EXT_SCAN_MIN = 55.0
SOFT_EXT_SCAN_MAX = 70.0
HARD_EXT_SCAN = 75.0

ROOM_TO_HIGH_MIN = 0.10  # 10% under 20d high
BUZZ_ACCEL_MIN_MSGS = 12.0
BUZZ_ACCEL_PEER_RATIO = 1.75


def fetch_polygon_gainers(*, api_key: str | None = None, timeout: float = 20.0) -> set[str]:
    """Backward-compatible wrapper — today's live gainers only."""
    from tsd_scan_pipeline.tsd_popularity import fetch_polygon_live_movers

    return fetch_polygon_live_movers(api_key=api_key).get("gainers") or set()


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
    """Peer-relative ST message floor (optional corroboration)."""
    vals = sorted(_st_msgs(r) for r in ranked if int(r.get("st_ok") or 0) == 1)
    if len(vals) < 3:
        return BUZZ_ACCEL_MIN_MSGS
    mid = vals[len(vals) // 2]
    return max(BUZZ_ACCEL_MIN_MSGS, mid * BUZZ_ACCEL_PEER_RATIO)


def annotate_momentum_context(
    row: dict[str, Any],
    *,
    popularity_ctx: dict[str, Any] | None = None,
    gainers: set[str] | None = None,
    buzz_threshold: float = BUZZ_ACCEL_MIN_MSGS,
) -> dict[str, Any]:
    """
    Attach popularity + momentum_context flags.

    Momentum context = HTF ok AND (tradable popularity OR buzz OR hot vol).
    Popularity prefers multi-day leaderboard / TWS scanners over first-print gainers.
    """
    out = dict(row)
    if popularity_ctx is not None:
        out = annotate_popularity(out, popularity_ctx)
    elif gainers is not None:
        sym = str(out.get("symbol") or "").upper()
        out["on_gainers"] = sym in gainers
        out["tradable_popular"] = out["on_gainers"]
        out["recent_leaderboard"] = False
        out["tws_popular"] = False

    st_ok = int(out.get("st_ok") or 0) == 1
    buzz = st_ok and _st_msgs(out) >= buzz_threshold
    try:
        vol_ratio = float(out.get("vol_ratio_20") or 0.0)
    except (TypeError, ValueError):
        vol_ratio = 0.0
    vol_hot = vol_ratio >= 1.25
    try:
        news_v = float(out.get("news_velocity_24h") or out.get("news_velocity_72h") or 0.0)
    except (TypeError, ValueError):
        news_v = 0.0
    news_hot = news_v >= 3.0
    htf_ok = (
        float(out.get("htf_score") or 0.0) > 0
        or out.get("htf_sma20_rising") is True
        or float(out.get("htf_sma20_slope_pct") or 0.0) > 0
    )
    popular = bool(out.get("tradable_popular"))
    momentum = bool(
        htf_ok
        and (
            popular
            or buzz
            or vol_hot
            or news_hot
            or (_room(out) >= ROOM_TO_HIGH_MIN and vol_ratio >= 0.8)
        )
    )
    out["buzz_accel"] = buzz
    out["momentum_context"] = momentum
    out["attention_reasons"] = list(out.get("attention_reasons") or [])
    return out


def _soft_extension_eligible(row: dict[str, Any]) -> bool:
    """scan 55–70 may join pool when room / popularity / buzz present."""
    scan = _scan(row)
    if scan < SOFT_EXT_SCAN_MIN or scan > SOFT_EXT_SCAN_MAX:
        return False
    if scan >= HARD_EXT_SCAN:
        return False
    if str(row.get("bar_state") or "") == "extended":
        return False
    return bool(
        _room(row) >= ROOM_TO_HIGH_MIN
        or row.get("tradable_popular")
        or row.get("on_gainers")
        or row.get("buzz_accel")
    )


def build_attention_pool(
    ranked: list[dict[str, Any]],
    *,
    polygon_key: str | None = None,
    gainers: set[str] | None = None,
    popularity_ctx: dict[str, Any] | None = None,
    top_k: int = ATTENTION_TOP_K,
    pool_max: int = ATTENTION_POOL_MAX,
    include_tws: bool = True,
) -> list[dict[str, Any]]:
    """
    Build case-review shortlist from ranked 1H passers.

    Union of:
      - top_k by continuation_score (deep swings)
      - tradable_popular (recent multi-day movers / TWS / live gainers)
      - optional StockTwits buzz
      - soft-extension lane with popularity/room hooks
    """
    if not ranked:
        return []

    ctx = popularity_ctx
    if ctx is None and gainers is None:
        ctx = build_popularity_context(
            api_key=polygon_key, include_tws=include_tws,
        )
    elif ctx is None and gainers is not None:
        ctx = {
            "recent_gainer_symbols": set(),
            "recent_active_symbols": set(),
            "live_gainers": set(gainers),
            "tws_symbols": set(),
            "popular_symbols": set(gainers),
            "tws_ok": False,
            "sessions_loaded": 0,
        }

    buzz_thr = buzz_accel_threshold(ranked)
    annotated = [
        annotate_momentum_context(
            r, popularity_ctx=ctx, buzz_threshold=buzz_thr,
        )
        for r in ranked
    ]
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

    for r in sorted(annotated, key=_cont, reverse=True)[: max(1, int(top_k))]:
        _put(r, "top_continuation")

    for r in annotated:
        if r.get("recent_leaderboard"):
            _put(r, "recent_leaderboard")
        if r.get("on_gainers"):
            _put(r, "polygon_gainer_today")
        if r.get("tws_popular"):
            _put(r, "tws_scanner")
        if r.get("buzz_accel"):
            _put(r, "buzz_accel")
        if _soft_extension_eligible(r):
            _put(r, "soft_extension")

    pool = list(by_sym.values())
    pool.sort(key=lambda r: (-_cont(r), _scan(r)))
    if len(pool) > pool_max:
        hot = [r for r in pool if r.get("momentum_context") or r.get("tradable_popular")]
        cold = [r for r in pool if r not in hot]
        pool = (hot + cold)[:pool_max]

    pop_n = len((ctx or {}).get("popular_symbols") or [])
    print(
        f"  Attention pool: {len(pool)}/{len(annotated)} passers "
        f"(popular_universe={pop_n} buzz_thr={buzz_thr:.1f})"
    )
    for r in pool:
        print(
            f"    {str(r.get('symbol')):<6} cont={_cont(r):.1f} "
            f"scan={_scan(r):.1f} mom={int(bool(r.get('momentum_context')))} "
            f"pop={int(bool(r.get('tradable_popular')))} "
            f"reasons={','.join(r.get('attention_reasons') or [])}"
        )
    return pool
