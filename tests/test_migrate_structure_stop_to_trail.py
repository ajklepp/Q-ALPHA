"""Tests for the structure_stop → trail/kill migration helper."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline.migrate_structure_stop_to_trail import (  # noqa: E402
    main as migrate_main,
    migrate_book,
    plan_leg_migration,
)
from tsd_scan_pipeline.tsd_structure import be_lock_price  # noqa: E402


def _atrc_leg():
    """ATRC-shaped open long: entry 53.29, kill 51.96, BE structure armed."""
    entry = 53.29
    kill_pct = 0.025
    kill = round(entry * (1.0 - kill_pct), 5)
    be = be_lock_price(entry)
    trail = {
        "entry_price": entry,
        "kill_price": kill,
        "kill_pct": kill_pct,
        "trail_pct": 0.025,
        "peak_high": 56.78,
        "tranches": [
            {
                "id": "T1",
                "shares": 1,
                "weight": 0.5,
                "trigger_pct": 0.02,
                "trigger_price": entry * 1.02,
                "trail_pct": 0.025,
                "trailing": False,
                "run_high": 0.0,
                "closed": False,
            },
            {
                "id": "T2",
                "shares": 1,
                "weight": 0.5,
                "trigger_pct": 0.035,
                "trigger_price": entry * 1.035,
                "trail_pct": 0.025,
                "trailing": False,
                "run_high": 0.0,
                "closed": False,
            },
        ],
    }
    leg = {
        "status": "OPEN",
        "price": entry,
        "shares": 2,
        "structure_stop": be,
        "structure_stop_reason": "be_lock_1r",
        "one_r_locked": True,
        "breakeven_locked": True,
        "kill_order_id": 777,
        "trail": trail,
    }
    return leg


def test_atrc_plan_clears_structure_keeps_and_raises_kill():
    leg = _atrc_leg()
    report = plan_leg_migration(
        leg,
        quote_high=56.78,
        quote_last=55.00,
    )
    assert report["ok"]
    after = report["after"]
    assert after.get("structure_stop") is None
    assert after.get("structure_stop_reason") is None
    assert (after.get("trail") or {}).get("structure_stop") is None
    assert after.get("kill_order_id") == 777
    assert report["kill_after"] >= report["kill_before"]
    assert report["kill_after"] > 0
    # Last=55 caps kill under last; BE lock ~53.13 should be reachable.
    assert report["kill_after"] >= be_lock_price(53.29) - 0.02
    assert report["kill_after"] < 55.00


def test_plan_never_removes_kill_even_without_mfe():
    leg = _atrc_leg()
    leg["trail"]["peak_high"] = 53.29
    report = plan_leg_migration(leg, quote_high=53.30, quote_last=53.28)
    assert report["ok"]
    assert report["kill_after"] >= report["kill_before"]
    assert report["kill_after"] > 0
    assert report["after"].get("structure_stop") is None


def test_plan_skips_leg_without_kill():
    leg = _atrc_leg()
    leg["trail"]["kill_price"] = None
    report = plan_leg_migration(leg)
    assert report["ok"] is False
    assert report["reason"] == "no_kill_price"


def test_migrate_book_symbol_filter_and_closed_skip():
    atrc = _atrc_leg()
    closed = _atrc_leg()
    closed["status"] = "CLOSED"
    nx = _atrc_leg()
    nx["price"] = 21.11
    state = {
        "positions": [
            {"symbol": "ATRC", "legs": [atrc, closed]},
            {"symbol": "NX", "legs": [nx]},
        ]
    }
    new_state, reports = migrate_book(state, symbol="ATRC", quote_high=56.78, quote_last=55.0)
    open_reports = [r for r in reports if r.get("ok")]
    assert len(open_reports) == 1
    assert open_reports[0]["symbol"] == "ATRC"
    atrc_after = new_state["positions"][0]["legs"][0]
    assert atrc_after.get("structure_stop") is None
    nx_after = new_state["positions"][1]["legs"][0]
    assert nx_after.get("structure_stop") is not None  # untouched
    # dry-run identity: original state object not mutated
    assert state["positions"][0]["legs"][0].get("structure_stop") is not None


def test_cli_dry_run_does_not_write(tmp_path):
    book = tmp_path / "tsd_book_state.json"
    state = {"positions": [{"symbol": "ATRC", "legs": [_atrc_leg()]}]}
    book.write_text(json.dumps(state), encoding="utf-8")
    rc = migrate_main(["--book", str(book), "--symbol", "ATRC", "--peak", "56.78", "--last", "55"])
    assert rc == 0
    reloaded = json.loads(book.read_text(encoding="utf-8"))
    assert reloaded["positions"][0]["legs"][0]["structure_stop"] is not None


def test_cli_apply_writes_cleared_structure(tmp_path):
    book = tmp_path / "tsd_book_state.json"
    state = {"positions": [{"symbol": "ATRC", "legs": [_atrc_leg()]}]}
    book.write_text(json.dumps(state), encoding="utf-8")
    rc = migrate_main([
        "--book", str(book), "--symbol", "ATRC",
        "--peak", "56.78", "--last", "55", "--apply",
    ])
    assert rc == 0
    reloaded = json.loads(book.read_text(encoding="utf-8"))
    leg = reloaded["positions"][0]["legs"][0]
    assert leg.get("structure_stop") is None
    assert float((leg.get("trail") or {}).get("kill_price") or 0) > 0
    assert leg.get("kill_order_id") == 777
