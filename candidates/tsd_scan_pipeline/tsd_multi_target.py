#!/usr/bin/env python3
"""
Peak Hour multi-target hard-bank ladder (paper shadow + research).

Shipped ladder for next-week paper bakeoff vs live 4T keep-profit:
  3-target 0.35R / 0.50R / 0.90R  →  1.75% / 2.5% / 4.5%
  weights 50 / 25 / 25
  residual kill 5% (1R)

Path-first: kill checked before targets on each bar/tick.
Does NOT place broker orders — used by shadow book + WF sims.
"""
from __future__ import annotations

from typing import Any

# 1R = 5% (matches EXP-0021 / live fallback kill)
KILL_PCT = 0.05
SHARE_LOT = 4

# Study winner (php_multi_target_study + bar WF) — practical 3-target
LADDER_MT3 = {
    "id": "mt3_035_050_090",
    "label": "3R multi-target (0.35/0.50/0.90R)",
    "targets_r": (0.35, 0.50, 0.90),
    "targets_pct": (0.0175, 0.025, 0.045),
    "weights": (0.50, 0.25, 0.25),
    "kill_pct": KILL_PCT,
}


def alloc_shares(n: int, weights: tuple[float, ...], *, lot: int = SHARE_LOT) -> list[int]:
    """Distribute shares by weight; prefer lot multiples, remainder on last slice."""
    if n <= 0:
        return [0] * len(weights)
    if n < lot:
        out = [0] * len(weights)
        out[0] = n
        return out
    lots = n // lot
    rem_shares = n - lots * lot
    raw = [w * lots for w in weights]
    base = [int(x) for x in raw]
    left = lots - sum(base)
    order = sorted(
        range(len(weights)),
        key=lambda i: (raw[i] - base[i], -i),
        reverse=True,
    )
    for i in order:
        if left <= 0:
            break
        base[i] += 1
        left -= 1
    out = [b * lot for b in base]
    out[-1] += rem_shares
    out[-1] += n - sum(out)
    return out


def open_multi_target_leg(
    *,
    symbol: str,
    entry_price: float,
    shares: int,
    ladder: dict[str, Any] | None = None,
    opened_at: str | None = None,
    live_leg_key: str | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a software multi-target leg (no broker)."""
    L = ladder or LADDER_MT3
    px = float(entry_price)
    sh = int(shares)
    weights = tuple(float(w) for w in L["weights"])
    targets = tuple(float(t) for t in L["targets_pct"])
    kill_pct = float(L.get("kill_pct") or KILL_PCT)
    slices = []
    for i, (slice_sh, t_pct) in enumerate(zip(alloc_shares(sh, weights), targets)):
        if slice_sh <= 0:
            continue
        slices.append({
            "id": f"S{i + 1}",
            "shares": slice_sh,
            "target_pct": t_pct,
            "target_r": round(t_pct / kill_pct, 4),
            "target_price": round(px * (1.0 + t_pct), 4),
            "closed": False,
            "exit_price": None,
            "exit_reason": None,
        })
    doc = {
        "symbol": str(symbol).upper(),
        "live_leg_key": live_leg_key,
        "opened_at": opened_at,
        "entry_price": round(px, 4),
        "shares": sh,
        "remaining": sh,
        "kill_pct": kill_pct,
        "kill_price": round(px * (1.0 - kill_pct), 4),
        "ladder_id": L.get("id"),
        "ladder_label": L.get("label"),
        "targets_r": list(L.get("targets_r") or []),
        "weights": list(weights),
        "slices": slices,
        "realized_gross": 0.0,
        "exits": [],
        "status": "OPEN",
        "pnl": None,
        "closed_at": None,
        "exit_mode": "multi_target_3r",
        "meta": dict(meta or {}),
    }
    return doc


def advance_multi_target_leg(
    leg: dict[str, Any],
    *,
    high: float,
    low: float,
    when: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    """
    Advance one leg on an OHLC tick.

    Returns (updated_leg, new_exits, fully_flat).
    """
    if str(leg.get("status") or "").upper() == "CLOSED":
        return leg, [], True

    entry = float(leg["entry_price"])
    kill_px = float(leg["kill_price"])
    new_exits: list[dict[str, Any]] = []

    if float(low) <= kill_px:
        for sl in leg.get("slices") or []:
            if sl.get("closed"):
                continue
            sh = int(sl["shares"])
            sl["closed"] = True
            sl["exit_price"] = kill_px
            sl["exit_reason"] = "kill"
            leg["realized_gross"] = float(leg.get("realized_gross") or 0) + (kill_px - entry) * sh
            ex = {
                "id": sl["id"],
                "shares": sh,
                "exit_price": kill_px,
                "reason": "kill",
                "when": when,
            }
            leg.setdefault("exits", []).append(ex)
            new_exits.append(ex)
        leg["remaining"] = 0
        leg["status"] = "CLOSED"
        leg["closed_at"] = when
        cost = entry * int(leg["shares"]) * 0.0015
        leg["pnl"] = round(float(leg["realized_gross"]) - cost, 2)
        return leg, new_exits, True

    for sl in leg.get("slices") or []:
        if sl.get("closed"):
            continue
        tgt = float(sl["target_price"])
        if float(high) >= tgt:
            sh = int(sl["shares"])
            sl["closed"] = True
            sl["exit_price"] = tgt
            sl["exit_reason"] = "target"
            leg["realized_gross"] = float(leg.get("realized_gross") or 0) + (tgt - entry) * sh
            leg["remaining"] = int(leg.get("remaining") or 0) - sh
            ex = {
                "id": sl["id"],
                "shares": sh,
                "exit_price": tgt,
                "reason": "target",
                "when": when,
            }
            leg.setdefault("exits", []).append(ex)
            new_exits.append(ex)

    rem = int(leg.get("remaining") or 0)
    flat = rem <= 0 or all(s.get("closed") for s in (leg.get("slices") or []))
    if flat:
        leg["remaining"] = 0
        leg["status"] = "CLOSED"
        leg["closed_at"] = when
        cost = entry * int(leg["shares"]) * 0.0015
        leg["pnl"] = round(float(leg["realized_gross"]) - cost, 2)
    return leg, new_exits, flat


def mark_flat_leg(leg: dict[str, Any], *, mark: float, when: str, reason: str = "mark") -> dict[str, Any]:
    """Force-close residual shares at mark (EOD / live twin closed)."""
    if str(leg.get("status") or "").upper() == "CLOSED":
        return leg
    entry = float(leg["entry_price"])
    px = float(mark)
    for sl in leg.get("slices") or []:
        if sl.get("closed"):
            continue
        sh = int(sl["shares"])
        sl["closed"] = True
        sl["exit_price"] = px
        sl["exit_reason"] = reason
        leg["realized_gross"] = float(leg.get("realized_gross") or 0) + (px - entry) * sh
        leg.setdefault("exits", []).append({
            "id": sl["id"], "shares": sh, "exit_price": px,
            "reason": reason, "when": when,
        })
    leg["remaining"] = 0
    leg["status"] = "CLOSED"
    leg["closed_at"] = when
    cost = entry * int(leg["shares"]) * 0.0015
    leg["pnl"] = round(float(leg["realized_gross"]) - cost, 2)
    return leg


def slice_caption(leg: dict[str, Any]) -> str:
    """Short dashboard string for open slice state."""
    parts = []
    for sl in leg.get("slices") or []:
        if sl.get("closed"):
            parts.append(f"{sl['id']}✓@{sl.get('exit_reason')}")
        else:
            parts.append(
                f"{sl['id']}→{float(sl.get('target_pct') or 0)*100:.2f}% "
                f"({sl.get('shares')}sh)"
            )
    rem = int(leg.get("remaining") or 0)
    return f"rem={rem} " + " | ".join(parts)
