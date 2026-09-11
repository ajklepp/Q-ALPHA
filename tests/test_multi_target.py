"""Unit tests for Peak Hour 3R multi-target ladder + shadow mirror."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline.tsd_multi_target import (  # noqa: E402
    LADDER_MT3,
    advance_multi_target_leg,
    alloc_shares,
    open_multi_target_leg,
)


def test_ladder_constants() -> None:
    assert LADDER_MT3["targets_r"] == (0.35, 0.50, 0.90)
    assert LADDER_MT3["weights"] == (0.50, 0.25, 0.25)
    assert abs(sum(LADDER_MT3["weights"]) - 1.0) < 1e-9


def test_alloc_shares_lot() -> None:
    assert sum(alloc_shares(20, (0.5, 0.25, 0.25))) == 20
    assert alloc_shares(20, (0.5, 0.25, 0.25))[0] >= 8


def test_banks_first_two_then_kill() -> None:
    leg = open_multi_target_leg(symbol="TEST", entry_price=100.0, shares=20)
    # Hit 2% high (covers 1.75% and 2.5%), low safe
    leg, exits, flat = advance_multi_target_leg(leg, high=102.6, low=99.0, when="t1")
    assert not flat
    assert len(exits) >= 1
    assert all(e["reason"] == "target" for e in exits)
    # Kill residual
    leg, exits2, flat2 = advance_multi_target_leg(leg, high=102.0, low=94.0, when="t2")
    assert flat2
    assert any(e["reason"] == "kill" for e in exits2)
    assert leg["status"] == "CLOSED"
    assert leg["pnl"] is not None


def test_full_ladder_flat() -> None:
    leg = open_multi_target_leg(symbol="RUN", entry_price=50.0, shares=16)
    # 4.5% clears all three targets
    leg, exits, flat = advance_multi_target_leg(leg, high=52.5, low=50.0, when="t")
    assert flat
    assert len(exits) == len(leg["slices"])
    assert all(e["reason"] == "target" for e in exits)


if __name__ == "__main__":
    test_ladder_constants()
    test_alloc_shares_lot()
    test_banks_first_two_then_kill()
    test_full_ladder_flat()
    print("OK test_multi_target")
