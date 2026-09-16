"""Unit tests for PAPER-ONLY Peak Hour B_arm4_lock shadow."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "candidates"))
sys.path.insert(0, str(ROOT / "strategy_lab"))

from tsd_scan_pipeline.php_early_trail_kill_paper import (  # noqa: E402
    B_ARM4_LOCK_ARM_MFE_PCT,
    B_ARM4_LOCK_MODE,
    FALLBACK_KILL_PCT,
    LOCK_WIDTH_FLOOR,
    LOCK_WIDTH_FRAC,
    b_arm4_lock_paper_kwargs,
    lock_trail_width,
    paper_process_bar,
    resolve_mae_trail_width,
    walk_paper_bars,
)
from tsd_scan_pipeline.php_arm_after_mfe_shadow import (  # noqa: E402
    extract_open_live_legs,
    replay_leg,
    resolve_b_arm4_lock_widths,
    shadow_enabled,
    tick_open_arm_shadows,
    write_daily_artifact,
)


def _live_book(*, high_so_far: float = 10.22) -> dict:
    """Minimal open Peak Hour book (live keep-profit fields untouched)."""
    return {
        "positions": [
            {
                "symbol": "RIPPER",
                "status": "OPEN",
                "opened_at": "2026-09-16T10:16:00-04:00",
                "legs": [
                    {
                        "time": "2026-09-16T10:16:00-04:00",
                        "price": 10.0,
                        "shares": 8,
                        "bar_hour": 10,
                        "order_id": 99,
                        "status": "OPEN",
                        "kill_pct": 0.05,
                        "trail": {
                            "entry_price": 10.0,
                            "kill_pct": 0.05,
                            "kill_price": 9.5,
                            "peak_high": high_so_far,
                            "php_keep_profit": True,
                            "tranches": [
                                {"id": "T1", "shares": 3, "closed": True},
                                {"id": "T2", "shares": 2, "closed": False},
                                {"id": "T3", "shares": 2, "closed": False},
                                {"id": "T4", "shares": 1, "closed": False},
                            ],
                        },
                    }
                ],
            }
        ]
    }


def test_b_arm4_lock_matches_study_definition():
    """Arm +4% of entry; lock width = 70% of MAE-p50 (floor 1% off high)."""
    assert B_ARM4_LOCK_MODE == "B_arm4_lock"
    assert B_ARM4_LOCK_ARM_MFE_PCT == 0.04
    assert LOCK_WIDTH_FRAC == 0.70
    assert LOCK_WIDTH_FLOOR == 0.010
    mae = 0.022
    trail = resolve_mae_trail_width(mae)
    lock = lock_trail_width(mae)
    assert trail == 0.022
    assert abs(lock - 0.022 * 0.70) < 1e-12
    kw = b_arm4_lock_paper_kwargs(mae, emergency_kill_pct=0.05)
    assert kw["trail_arm_mfe_pct"] == 0.04
    assert kw["trail_pct_off_high"] == lock
    assert kw["ratchet_kill_with_trail"] is True
    assert kw["mode"] == "B_arm4_lock"


def test_lock_width_floor_one_pct_off_high():
    lock = lock_trail_width(0.005)
    assert lock == LOCK_WIDTH_FLOOR


def test_arm_threshold_is_plus_four_pct_of_entry():
    """+3.9% of entry stays unarmed; +4.0% arms. Same-bar pullback does not exit."""
    kw = b_arm4_lock_paper_kwargs(0.025)
    state = walk_paper_bars(
        10.0,
        [{"high": 10.39, "low": 10.20, "close": 10.30, "when": "pre"}],
        shares=8,
        **{k: kw[k] for k in (
            "trail_pct_off_high", "trail_arm_mfe_pct",
            "kill_schedule", "ratchet_kill_with_trail", "mode",
        )},
    )
    assert state["trail_armed"] is False
    assert abs(state["mfe_peak_pct"] - 0.039) < 1e-9

    state, exits = paper_process_bar(
        state, high=10.40, low=10.15, close=10.28, when="arm",
    )
    assert state["trail_armed"] is True
    assert exits == []
    assert state["remaining"] == 8


def test_after_arm_trail_uses_lock_width():
    """Next bar: lock-width giveback from the high is a paper trail exit."""
    mae = 0.020
    kw = b_arm4_lock_paper_kwargs(mae)
    lock = lock_trail_width(mae)
    state = walk_paper_bars(
        10.0,
        [{"high": 10.50, "low": 10.40, "close": 10.45, "when": "arm"}],
        shares=8,
        **{k: kw[k] for k in (
            "trail_pct_off_high", "trail_arm_mfe_pct",
            "kill_schedule", "ratchet_kill_with_trail", "mode",
        )},
    )
    assert state["trail_armed"] is True
    state, exits = paper_process_bar(
        state, high=10.50, low=10.50 * (1.0 - lock) - 0.01, close=10.30, when="trail",
    )
    assert exits and exits[0]["reason"] == "trail"
    assert abs(exits[0]["exit_price"] - 10.50 * (1.0 - lock)) < 1e-6


def test_shadow_flag_default_on_explicit_off():
    assert shadow_enabled({}) is True
    assert shadow_enabled({"PHP_ARM_AFTER_MFE_SHADOW": "1"}) is True
    assert shadow_enabled({"PHP_ARM_AFTER_MFE_SHADOW": "0"}) is False
    assert shadow_enabled({"PHP_ARM_AFTER_MFE_SHADOW": "off"}) is False


def test_widths_from_profile_mae_p50():
    profile = {"mae": {"p50": 0.030, "p75": 0.045}}
    w = resolve_b_arm4_lock_widths("TEST", profile=profile)
    assert w["trail_arm_mfe_pct"] == 0.04
    assert abs(w["lock_trail_pct_off_high"] - 0.030 * 0.70) < 1e-9
    assert w["emergency_kill_pct"] == 0.045
    assert w["paper_kwargs"]["trail_arm_mfe_pct"] == 0.04


def test_extract_open_live_legs_skips_closed():
    book = _live_book()
    book["positions"].append({
        "symbol": "DEAD",
        "status": "CLOSED",
        "legs": [{"price": 5.0, "shares": 8, "status": "CLOSED"}],
    })
    recs = extract_open_live_legs(book)
    assert [r["symbol"] for r in recs] == ["RIPPER"]
    assert recs[0]["entry_price"] == 10.0
    assert recs[0]["shares"] == 8


def test_replay_leg_would_exit_while_live_still_open():
    rec = extract_open_live_legs(_live_book())[0]
    profile = {"mae": {"p50": 0.020, "p75": 0.05}}
    bars = [
        {"high": 10.50, "low": 10.40, "close": 10.45, "when": "arm"},
        {"high": 10.50, "low": 10.20, "close": 10.25, "when": "giveback"},
    ]
    with patch(
        "tsd_scan_pipeline.php_arm_after_mfe_shadow.bars_for_leg",
        return_value=(bars, "fixture"),
    ):
        snap = replay_leg(rec, profile=profile, fetch_1h=False)
    assert snap["paper_only"] is True
    assert snap["trail_arm_mfe_pct"] == 0.04
    assert snap["paper_closed"] is True
    assert snap["live_remaining"] == 5  # T1 already banked on live
    assert snap["would_have_exited"] is True
    assert "trail" in snap["paper_exit_reasons"]
    assert snap["paper_pnl_pct_of_entry"] is not None


def test_tick_never_touches_live_exits(tmp_path, monkeypatch):
    """Shadow tick must not call broker exits or live save_state."""
    from tsd_scan_pipeline import php_arm_after_mfe_shadow as sh
    from tsd_scan_pipeline import tsd_capacity, tsd_exit

    monkeypatch.setattr(sh, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(sh, "shadow_book_path", lambda: tmp_path / "shadow.json")
    monkeypatch.setenv("PHP_ARM_AFTER_MFE_SHADOW", "1")

    called = {"exit": 0, "save": 0}

    def _boom_exit(*_a, **_k):
        called["exit"] += 1
        raise AssertionError("place_tsd_exit must not run from shadow")

    def _boom_save(*_a, **_k):
        called["save"] += 1
        raise AssertionError("live save_state must not run from shadow")

    with (
        patch.object(tsd_exit, "place_tsd_exit", _boom_exit),
        patch.object(tsd_capacity, "save_state", _boom_save),
    ):
        snaps = tick_open_arm_shadows(
            lambda _s: {"high": 10.22, "low": 10.10, "last": 10.18, "close": 10.18},
            book=_live_book(),
            write_artifact=True,
        )
    assert called["exit"] == 0
    assert called["save"] == 0
    assert snaps
    assert snaps[0]["paper_armed"] is False  # 2.2% < 4%
    assert snaps[0]["live_t1_banked"] is True


def test_flag_off_is_noop(tmp_path, monkeypatch):
    from tsd_scan_pipeline import php_arm_after_mfe_shadow as sh

    monkeypatch.setattr(sh, "shadow_book_path", lambda: tmp_path / "shadow.json")
    monkeypatch.setenv("PHP_ARM_AFTER_MFE_SHADOW", "0")
    snaps = tick_open_arm_shadows(book=_live_book(), write_artifact=True)
    assert snaps == []
    assert not (tmp_path / "shadow.json").exists()


def test_write_daily_artifact_language_is_pct_of_entry(tmp_path, monkeypatch):
    from tsd_scan_pipeline import php_arm_after_mfe_shadow as sh

    monkeypatch.setattr(sh, "RESULTS_DIR", tmp_path)
    rec = extract_open_live_legs(_live_book())[0]
    profile = {"mae": {"p50": 0.025}}
    with patch(
        "tsd_scan_pipeline.php_arm_after_mfe_shadow.bars_for_leg",
        return_value=([
            {"high": 10.22, "low": 10.10, "close": 10.18, "when": "q"},
        ], "quote"),
    ):
        snap = replay_leg(rec, profile=profile)
    jp, mp = write_daily_artifact([snap], asof="20260916")
    doc = json.loads(jp.read_text(encoding="utf-8"))
    assert doc["paper_only"] is True
    assert doc["live_exits_changed"] is False
    assert doc["mode"] == "B_arm4_lock"
    assert doc["arm_mfe_pct_of_entry"] == 0.04
    md = mp.read_text(encoding="utf-8")
    assert "PAPER ONLY" in md
    assert "% of entry" in md
    assert "R-multiple" in md or "R-multiples" in md
    assert "B_arm4_lock" in md


def test_cli_writes_results_under_pipeline(tmp_path, monkeypatch):
    from tsd_scan_pipeline import php_arm_after_mfe_shadow as sh

    monkeypatch.setattr(sh, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(sh, "shadow_book_path", lambda: tmp_path / "shadow.json")
    book_path = tmp_path / "book.json"
    book_path.write_text(json.dumps(_live_book()), encoding="utf-8")
    rc = sh.main(["--write", "--book", str(book_path), "--asof", "20260916"])
    assert rc == 0
    assert (tmp_path / "php_arm_after_mfe_shadow_20260916.json").is_file()
    assert (tmp_path / "php_arm_after_mfe_shadow_20260916.md").is_file()


def test_trail_monitor_hook_is_after_save_and_paper_only():
    """Surgical hook: after live save_state, try/except, no exit-module import."""
    src = (
        ROOT / "candidates" / "tsd_scan_pipeline" / "tsd_trail_monitor.py"
    ).read_text(encoding="utf-8")
    save_at = src.index("save_state(state)")
    hook_at = src.index("tick_open_arm_shadows")
    assert hook_at > save_at
    assert "place_tsd_exit" not in src[hook_at:hook_at + 400]
    assert "Does NOT change live keep-profit" in src
    sched = (
        ROOT / "candidates" / "tsd_scan_pipeline" / "scheduler.py"
    ).read_text(encoding="utf-8")
    assert "maybe_run_after_scan" in sched
    assert sched.index("run_launch_pass") < sched.index("maybe_run_after_scan")


def test_keep_profit_untouched_by_shadow_module():
    """Live php_process_bar still banks T1 at +2% — shadow is a different engine."""
    from tsd_scan_pipeline.tsd_keep_profit import init_php_trail_state, php_process_bar as live_bar

    trail = init_php_trail_state(10.0, 20, kill_pct=0.05)
    trail, exits = live_bar(
        trail,
        high=10.25, low=10.05, close=10.20, when="t1",
        be_lock_after_t1=False,
        kill_tighten_after_t1=0.025,
    )
    assert any(e["tranche_id"] == "T1" and e["reason"] == "t1_bank" for e in exits)
    assert FALLBACK_KILL_PCT == 0.05
