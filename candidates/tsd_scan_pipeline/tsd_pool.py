"""
Q-ALPHA TSD pipeline — deployable pool accounting.

Separate from morning agent pool_state.json and Strategy Lab SIM book.
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


def deploy_on_entry(shares: int, price: float, state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Deduct notional from pool and add to deployed on fill."""
    doc = state or load_pool()
    notional = float(shares) * float(price)
    doc["pool"] = max(0.0, float(doc.get("pool") or 0.0) - notional)
    doc["deployed"] = float(doc.get("deployed") or 0.0) + notional
    save_pool(doc)
    return doc


def release_on_exit(shares: int, price: float, state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return notional to pool on exit (Phase 4 hook)."""
    doc = state or load_pool()
    notional = float(shares) * float(price)
    doc["deployed"] = max(0.0, float(doc.get("deployed") or 0.0) - notional)
    doc["pool"] = float(doc.get("pool") or 0.0) + notional
    save_pool(doc)
    return doc
