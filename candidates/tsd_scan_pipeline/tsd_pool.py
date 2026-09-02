"""
Q-ALPHA TSD pipeline — deployable pool accounting.

Separate from morning agent pool_state.json and Strategy Lab SIM book.

Accounting model:
  deploy_on_entry:  pool -= shares * entry_price; deployed += cost basis
  release_on_exit:  deployed -= shares * entry_price; pool += shares * exit_price
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from state_paths import state_path

TSD_POOL_FILE = "tsd_pool_state.json"
DEFAULT_STARTING_POOL = 3000.0


def _default_state() -> dict[str, Any]:
    return {
        "pool": DEFAULT_STARTING_POOL,
        "deployed": 0.0,
        "starting_pool": DEFAULT_STARTING_POOL,
    }


def load_pool(path: Path | None = None) -> dict[str, Any]:
    """Load pool state; initialize defaults if missing."""
    p = path or state_path(TSD_POOL_FILE)
    if not p.exists():
        state = _default_state()
        save_pool(state, p)
        return state
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc.setdefault("pool", DEFAULT_STARTING_POOL)
    doc.setdefault("deployed", 0.0)
    doc.setdefault("starting_pool", doc.get("pool", DEFAULT_STARTING_POOL))
    return doc


def save_pool(state: dict[str, Any], path: Path | None = None) -> None:
    p = path or state_path(TSD_POOL_FILE)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")


def available_pool(state: dict[str, Any] | None = None) -> float:
    """Cash available for new TSD entries."""
    doc = state or load_pool()
    return float(doc.get("pool") or 0.0)


def deploy_on_entry(
    shares: int,
    price: float,
    state: dict[str, Any] | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Deduct cost basis from pool and add to deployed on fill."""
    doc = state or load_pool(path)
    notional = float(shares) * float(price)
    doc["pool"] = max(0.0, float(doc.get("pool") or 0.0) - notional)
    doc["deployed"] = float(doc.get("deployed") or 0.0) + notional
    save_pool(doc, path)
    return doc


def _infer_entry_price(symbol: str | None) -> float | None:
    """Resolve entry price from open book leg when caller omits it."""
    try:
        from tsd_scan_pipeline.tsd_capacity import load_state

        book = load_state()
    except Exception:
        return None

    sym_filter = str(symbol).upper() if symbol else None
    for pos in book.get("positions") or []:
        if sym_filter and str(pos.get("symbol", "")).upper() != sym_filter:
            continue
        for leg in pos.get("legs") or []:
            if str(leg.get("status", "")).upper() != "OPEN":
                continue
            trail = leg.get("trail") or {}
            ep = trail.get("entry_price") or leg.get("price")
            if ep is not None and float(ep) > 0:
                return float(ep)
    return None


def release_on_exit(
    shares: int,
    exit_price: float,
    *,
    entry_price: float | None = None,
    symbol: str | None = None,
    state: dict[str, Any] | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """
    Return proceeds to pool and remove cost basis from deployed.

    deploy_on_entry adds shares*entry_price to deployed.
    release_on_exit subtracts shares*entry_price from deployed and adds
    shares*exit_price to pool (cash).
    """
    doc = state or load_pool(path)
    sh = int(shares)
    if sh <= 0:
        return doc

    exit_px = float(exit_price)
    entry_px = float(entry_price) if entry_price is not None else None
    if entry_px is None or entry_px <= 0:
        inferred = _infer_entry_price(symbol)
        if inferred is None or inferred <= 0:
            raise ValueError(
                "release_on_exit requires entry_price when book leg cannot be inferred"
            )
        entry_px = inferred

    cost_basis = sh * entry_px
    proceeds = sh * exit_px

    doc["deployed"] = max(0.0, float(doc.get("deployed") or 0.0) - cost_basis)
    doc["pool"] = float(doc.get("pool") or 0.0) + proceeds
    save_pool(doc, path)
    return doc


def pool_equity(state: dict[str, Any] | None = None) -> float:
    """Cash + deployed cost basis (excludes open MTM until marked)."""
    doc = state or load_pool()
    return float(doc.get("pool") or 0.0) + float(doc.get("deployed") or 0.0)
