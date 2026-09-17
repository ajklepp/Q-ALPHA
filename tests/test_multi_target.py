"""Unit tests for Peak Hour 3R multi-target ladder + shadow mirror."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline.tsd_multi_target import (  # noqa: E402
    LADDER_MT3,
    advance_multi_target_leg,
    alloc_shares,
    open_multi_target_leg,
)
from tsd_scan_pipeline import tsd_shadow_multi_target as sh  # noqa: E402


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


class _ShadowTmp:
    """Isolate shadow + live book files under a temp dir."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.shadow = self.dir / "tsd_shadow_mt3_book.json"
        self.live = self.dir / "tsd_book_state.json"
        self._patches = [
            patch.object(sh, "shadow_book_path", lambda: self.shadow),
            patch.object(sh, "live_book_path", lambda: self.live),
        ]

    def __enter__(self) -> "_ShadowTmp":
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc: object) -> None:
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()


def _ird_live_book() -> dict:
    """Closed Peak Hour IRD-style fill (2026-09-14) with MFE then kill."""
    return {
        "positions": [
            {
                "symbol": "IRD",
                "status": "CLOSED",
                "opened_at": "2026-09-14T10:15:00-04:00",
                "legs": [
                    {
                        "time": "2026-09-14T10:15:00-04:00",
                        "price": 10.0,
                        "shares": 20,
                        "order_id": 4242,
                        "status": "CLOSED",
                        "is_addon": False,
                        "bar_state": "green",
                        "trail": {
                            "entry_price": 10.0,
                            "peak_high": 10.30,
                            "last_close": 9.50,
                            "kill_price": 9.50,
                        },
                        "exits": [
                            {
                                "time": "2026-09-14T11:00:00-04:00",
                                "shares": 8,
                                "exit_price": 10.20,
                                "reason": "t1_bank",
                            },
                            {
                                "time": "2026-09-15T09:40:00-04:00",
                                "shares": 12,
                                "exit_price": 9.50,
                                "reason": "kill",
                            },
                        ],
                    }
                ],
            }
        ]
    }


def test_mirror_idempotent_on_order_id() -> None:
    with _ShadowTmp():
        a = sh.mirror_live_fill(
            symbol="IRD", fill_price=10.0, shares=20,
            opened_at="2026-09-14T10:15:00-04:00", order_id=4242,
        )
        b = sh.mirror_live_fill(
            symbol="IRD", fill_price=10.0, shares=20,
            opened_at="2026-09-14T10:15:00-04:00", order_id=4242,
        )
        assert a is not None and b is not None
        assert a["live_leg_key"] == b["live_leg_key"]
        book = sh.load_shadow_book()
        assert len(book["legs"]) + len(book["closed"]) == 1


def test_sync_backfills_closed_ird_from_live_book() -> None:
    """Empty shadow + historical Peak Hour fill → closed 3R leg with P&L."""
    with _ShadowTmp() as tmp:
        tmp.live.write_text(json.dumps(_ird_live_book()), encoding="utf-8")
        summary = sh.sync_shadow_from_live_book()
        assert summary["reason"] == "ok"
        assert summary["n_mirrored"] == 1
        assert summary["n_closed"] == 1
        assert summary["n_open"] == 0
        score = sh.scoreboard()
        assert score["n_closed"] == 1
        assert score["updated_at"]  # was None when the tab showed $0
        closed = score["closed_legs"][0]
        assert closed["symbol"] == "IRD"
        assert closed["status"] == "CLOSED"
        assert closed["pnl"] is not None
        reasons = {e["reason"] for e in closed.get("exits") or []}
        assert "target" in reasons
        assert "kill" in reasons
        # Second pass is idempotent — no duplicate legs.
        again = sh.sync_shadow_from_live_book()
        assert again["n_mirrored"] == 0
        assert again["n_closed"] == 1
        book = sh.load_shadow_book()
        assert len(book["closed"]) == 1


def test_sync_open_live_fill_stays_open() -> None:
    live = {
        "positions": [
            {
                "symbol": "ABC",
                "status": "OPEN",
                "legs": [
                    {
                        "time": "2026-09-16T10:00:00-04:00",
                        "price": 20.0,
                        "shares": 16,
                        "order_id": 7,
                        "status": "OPEN",
                        "trail": {
                            "entry_price": 20.0,
                            "peak_high": 20.10,
                            "last_close": 20.05,
                        },
                        "exits": [],
                    }
                ],
            }
        ]
    }
    with _ShadowTmp() as tmp:
        tmp.live.write_text(json.dumps(live), encoding="utf-8")
        summary = sh.sync_shadow_from_live_book()
        assert summary["n_open"] == 1
        assert summary["n_closed"] == 0
        score = sh.scoreboard(mark_by_symbol={"ABC": 20.05})
        assert score["n_open"] == 1
        assert score["open_mtm"] != 0 or score["open_legs"][0]["remaining"] == 16


def test_cloud_closed_row_reconstructs_when_no_local_book() -> None:
    with _ShadowTmp():
        summary = sh.sync_shadow_from_cloud_legs(
            [],
            [{
                "symbol": "IRD",
                "leg_opened_at": "2026-09-14T10:15:00-04:00",
                "entry_price": 10.0,
                "shares": 20,
                "exit_price": 9.50,
                "closed_at": "2026-09-15T09:40:00-04:00",
                "status": "CLOSED",
                "peak_high": 10.30,
            }],
        )
        assert summary["n_closed"] == 1
        score = sh.scoreboard()
        assert score["n_closed"] == 1
        assert score["closed_legs"][0]["symbol"] == "IRD"


def test_record_entry_mirrors_shadow_leg() -> None:
    from tsd_scan_pipeline.tsd_capacity import record_entry

    with _ShadowTmp() as tmp:
        # record_entry → mirror_live_fill uses patched shadow_book_path
        state: dict = {"positions": [], "entries_this_scan": 0}
        record_entry(
            state,
            "IRD",
            entry_price=10.0,
            shares=20,
            scan_score=40.0,
            is_addon=False,
            order_id=99,
        )
        assert tmp.shadow.is_file()
        book = json.loads(tmp.shadow.read_text(encoding="utf-8"))
        assert len(book["legs"]) == 1
        assert book["legs"][0]["symbol"] == "IRD"
        assert book["legs"][0]["order_id"] == 99
        # Live 4T book is unchanged aside from the new position.
        assert state["positions"][0]["legs"][0]["status"] == "OPEN"


def test_ensure_prefers_live_book_over_empty_cloud() -> None:
    with _ShadowTmp() as tmp:
        tmp.live.write_text(json.dumps(_ird_live_book()), encoding="utf-8")
        out = sh.ensure_shadow_synced(open_rows=[], closed_rows=[])
        assert out["source"] == "live_book"
        assert out["n_closed"] == 1


if __name__ == "__main__":
    test_ladder_constants()
    test_alloc_shares_lot()
    test_banks_first_two_then_kill()
    test_full_ladder_flat()
    test_mirror_idempotent_on_order_id()
    test_sync_backfills_closed_ird_from_live_book()
    test_sync_open_live_fill_stays_open()
    test_cloud_closed_row_reconstructs_when_no_local_book()
    test_record_entry_mirrors_shadow_leg()
    test_ensure_prefers_live_book_over_empty_cloud()
    print("OK test_multi_target")
