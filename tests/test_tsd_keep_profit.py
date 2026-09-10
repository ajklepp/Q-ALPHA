"""Unit tests for Peak Hour keep-profit trail."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "candidates"))
sys.path.insert(0, str(ROOT / "strategy_lab"))

from tsd_scan_pipeline.tsd_keep_profit import (  # noqa: E402
    init_php_trail_state,
    php_process_bar,
)
from tsd_scan_pipeline.tsd_trail import remaining_shares  # noqa: E402


def test_t1_banks_at_2pct_then_kill_tightens():
    entry = 10.0
    trail = init_php_trail_state(entry, 20, kill_pct=0.05)
    assert abs(trail["kill_price"] - 9.5) < 1e-6
    # Bar hits +2% high — T1 should bank at 10.20
    trail, exits = php_process_bar(
        trail,
        high=10.25,
        low=10.05,
        close=10.20,
        when="t1",
        be_lock_after_t1=False,
        kill_tighten_after_t1=0.025,
    )
    assert any(e["tranche_id"] == "T1" and e["reason"] == "t1_bank" for e in exits)
    assert abs(float(trail["kill_price"]) - entry * 0.975) < 1e-6
    assert remaining_shares(trail) == 20 - next(
        e["shares"] for e in exits if e["tranche_id"] == "T1"
    )


def test_t1_bank_without_be_preserves_runner_room():
    entry = 10.0
    trail = init_php_trail_state(entry, 20, kill_pct=0.05)
    trail, _ = php_process_bar(
        trail, high=10.25, low=10.05, close=10.20, when="t1",
        be_lock_after_t1=False, kill_tighten_after_t1=0.025,
    )
    # Dip to 9.80 should NOT kill (BE would); 2.5% kill is 9.75
    trail, exits = php_process_bar(
        trail, high=10.10, low=9.80, close=9.90, when="dip",
        be_lock_after_t1=False, kill_tighten_after_t1=0.025,
    )
    assert not any(e["reason"].startswith("kill") for e in exits)
    assert remaining_shares(trail) > 0
