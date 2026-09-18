"""Unit tests for PAPER-ONLY Peak Hour arm-after-MFE trail study."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "candidates"))
sys.path.insert(0, str(ROOT / "strategy_lab"))

from tsd_scan_pipeline.php_early_trail_kill_paper import (  # noqa: E402
    FALLBACK_KILL_PCT,
    RIPPER_MFE_PCT,
    emergency_kill_schedule,
    init_paper_state,
    lock_trail_width,
    paper_process_bar,
    replay_paper_path,
    resolve_mae_trail_width,
    slice_trades,
)
from tsd_scan_pipeline.php_arm_after_mfe_trail_study import (  # noqa: E402
    FIXTURE_PATH,
    decide_recommendation,
    estimate_ticker_widths,
    main,
    replay_paper,
)


def test_arm_skips_trail_on_arm_bar():
    """Until MFE hits arm, only emergency kill; arm bar itself does not trail-exit."""
    entry = 10.0
    state = init_paper_state(
        entry, 8,
        trail_pct_off_high=0.02,
        trail_arm_mfe_pct=0.03,
        kill_schedule=emergency_kill_schedule(0.05),
        mode="B_arm3",
        ratchet_kill_with_trail=True,
    )
    # +2.2% — below arm. 2% giveback from high would trail if armed-from-entry.
    state, exits = paper_process_bar(
        state, high=10.22, low=9.95, close=10.10, when="pre",
    )
    assert state["trail_armed"] is False
    assert exits == []
    assert state["remaining"] == 8
    # +4% arms; same-bar pullback must not trail-exit.
    state, exits = paper_process_bar(
        state, high=10.40, low=10.10, close=10.30, when="arm",
    )
    assert state["trail_armed"] is True
    assert exits == []


def test_after_arm_trail_exits_pct_off_high():
    """After a later bar, 2% giveback from the high exits at run_high * 0.98."""
    entry = 10.0
    state = init_paper_state(
        entry, 8,
        trail_pct_off_high=0.02,
        trail_arm_mfe_pct=0.03,
        kill_schedule=emergency_kill_schedule(0.05),
        mode="B_arm3",
        ratchet_kill_with_trail=True,
    )
    state, _ = paper_process_bar(
        state, high=10.40, low=10.20, close=10.35, when="arm",
    )
    state, exits = paper_process_bar(
        state, high=10.40, low=10.10, close=10.15, when="trail",
    )
    assert exits and exits[0]["reason"] == "trail"
    assert abs(exits[0]["exit_price"] - 10.40 * 0.98) < 1e-6


def test_kill_ratchets_up_with_trail_never_down():
    """After arm, kill floor rises to the trail stop (price UP only)."""
    entry = 10.0
    state = init_paper_state(
        entry, 8,
        trail_pct_off_high=0.02,
        trail_arm_mfe_pct=0.03,
        kill_schedule=emergency_kill_schedule(0.05),
        mode="B_arm3",
        ratchet_kill_with_trail=True,
    )
    assert abs(state["kill_price"] - 9.50) < 1e-6
    state, _ = paper_process_bar(
        state, high=10.40, low=10.20, close=10.35, when="arm",
    )
    # Next bar: already armed, kill should lift to 10.40 * 0.98 = 10.192
    state, exits = paper_process_bar(
        state, high=10.40, low=10.25, close=10.30, when="hold",
    )
    assert exits == []
    assert state["kill_price"] + 1e-9 >= 10.40 * 0.98 - 1e-9
    prior = state["kill_price"]
    # Lower high must not loosen kill.
    state, _ = paper_process_bar(
        state, high=10.30, low=10.22, close=10.24, when="lower",
    )
    assert state["kill_price"] + 1e-12 >= prior


def test_lock_width_tighter_than_mae_p50():
    mae = 0.025
    trail = resolve_mae_trail_width(mae)
    lock = lock_trail_width(mae)
    assert trail == 0.025
    assert 0.010 <= lock < trail
    assert abs(lock - 0.025 * 0.70) < 1e-9


def test_ripper_slice_uses_4pct_of_entry():
    rows = [
        {"mfe_peak_pct": 0.039, "path": {"mfe_peak_pct": 0.039}, "pnl_pct_of_entry": 0.01},
        {"mfe_peak_pct": 0.041, "path": {"mfe_peak_pct": 0.041}, "pnl_pct_of_entry": 0.02},
    ]
    parts = slice_trades(rows)
    assert len(parts["rippers"]) == 1
    assert len(parts["grinders"]) == 1
    assert RIPPER_MFE_PCT == 0.04


def test_widths_from_prior_analogs_only():
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    analogs = [
        a for a in fixture["analogs"]["RIPPER"]
        if a["entry_date"] < "2026-09-08"
    ]
    w = estimate_ticker_widths(
        "RIPPER", analogs, profile=fixture["profiles"]["RIPPER"],
    )
    assert w["status"] in {"OK", "HEURISTIC"}
    assert w["n_analogs"] >= 3
    assert 0.010 <= float(w["trail_pct_off_high"]) <= 0.050
    assert float(w["lock_trail_pct_off_high"]) <= float(w["trail_pct_off_high"])


def test_replay_modes_on_fixture_rippers():
    """From-entry (C) must lose the RIPPER ride; arm-after-MFE must not."""
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    entries = []
    for e in fixture["entries"]:
        entries.append({
            "symbol": e["symbol"],
            "entry_price": e["entry_price"],
            "shares": e["shares"],
            "entry_date": e["entry_date"],
            "entry_hour": e["entry_hour"],
            "bars": e["bars"],
            "source": "fixture",
        })
    widths = {}
    for e in entries:
        analogs = [
            a for a in fixture["analogs"][e["symbol"]]
            if a["entry_date"] < e["entry_date"]
        ]
        widths[e["symbol"]] = estimate_ticker_widths(
            e["symbol"], analogs, profile=fixture["profiles"][e["symbol"]],
        )

    book_a = replay_paper(entries, "A", widths=widths)
    book_b3 = replay_paper(entries, "B_arm3", widths=widths)
    book_c = replay_paper(entries, "C", widths=widths)
    assert book_a["n"] == 6
    assert book_b3["n"] == 6
    assert book_c["n"] == 6

    rip_c = next(t for t in book_c["trades"] if t["symbol"] == "RIPPER")
    rip_b = next(t for t in book_b3["trades"] if t["symbol"] == "RIPPER")
    assert rip_c["path"]["mfe_peak_pct"] >= 0.04
    # From-entry is shaken out on the early chop (PR #16 ripper failure).
    assert rip_c["pnl_pct_of_entry"] < 0.02
    # Arm-after-MFE rides past +3% and keeps a runner-sized % of entry.
    assert rip_b["pnl_pct_of_entry"] > rip_c["pnl_pct_of_entry"]
    assert rip_b["pnl_pct_of_entry"] > 0.03

    gtl_b3 = next(t for t in book_b3["trades"] if t["symbol"] == "GTL")
    # Arm at +3% should trail before the −5% kill.
    assert gtl_b3["pnl_pct_of_entry"] > -0.05 - 0.0015


def test_recommendation_refuses_b_when_rippers_lose():
    """PR #16 gate: do not prefer B if rippers are worse than A."""
    rec = decide_recommendation(
        {
            "A": {
                "n": 4,
                "book_pnl_pct_of_entry_mean": 0.010,
                "green_then_lost_rate": 0.25,
                "slices": {
                    "rippers": {"n": 2, "mean_pnl_pct_of_entry": 0.0183},
                },
            },
            "B_arm3": {
                "n": 4,
                "label": "arm3",
                "book_pnl_pct_of_entry_mean": 0.012,
                "book_pnl_pct_of_entry_median": 0.011,
                "green_then_lost_rate": 0.25,
                "slices": {
                    "rippers": {
                        "n": 2,
                        "mean_pnl_pct_of_entry": -0.0108,
                        "mean_left_on_table_pct_of_entry": 0.08,
                    },
                },
            },
        },
        sample_label="unit",
    )
    assert rec["prefer_b_for_live_later"] is False
    assert rec["verdict"] == "DO_NOT_PREFER_B_FOR_LIVE"
    assert rec["best_b_on_rippers"]["mode"] == "B_arm3"
    assert rec["invite_human_review"] is True
    assert rec["live_patch_authorized"] is False


def test_replay_paper_path_applies_cost():
    bars = [
        {"high": 10.40, "low": 10.20, "close": 10.35, "when": "1"},
        {"high": 10.40, "low": 10.10, "close": 10.15, "when": "2"},
    ]
    rec = replay_paper_path(
        10.0, bars, shares=8,
        trail_pct_off_high=0.02,
        trail_arm_mfe_pct=0.03,
        kill_schedule=[{"after_mfe_pct": 0.0, "kill_pct_below_entry": FALLBACK_KILL_PCT}],
        ratchet_kill_with_trail=True,
    )
    assert rec["cost"] == 10.0 * 8 * 0.0015
    assert rec["pnl"] == rec["realized_gross"] - rec["cost"]
    assert rec["left_on_table_pct_of_entry"] == rec["mfe_peak_pct"] - rec["pnl_pct_of_entry"]


def test_dry_run_cli_writes_results():
    """CLI --dry-run produces dated JSON + MD + novelty note."""
    rc = main(["--dry-run", "--asof", "2026-09-16"])
    assert rc == 0
    out_json = ROOT / "candidates" / "tsd_scan_pipeline" / "results" / (
        "php_arm_after_mfe_trail_20260916_dryrun.json"
    )
    out_md = ROOT / "candidates" / "tsd_scan_pipeline" / "results" / (
        "php_arm_after_mfe_trail_20260916_dryrun.md"
    )
    novelty = ROOT / "candidates" / "tsd_scan_pipeline" / "results" / (
        "novelty_php_arm_after_mfe_20260916_dryrun.md"
    )
    assert out_json.is_file()
    assert out_md.is_file()
    assert novelty.is_file()
    doc = json.loads(out_json.read_text(encoding="utf-8"))
    assert doc["paper_only"] is True
    assert doc["live_files_edited"] == []
    assert set(doc["modes_run"]) == {
        "A", "B_arm3", "B_arm4", "B_arm3_lock", "B_arm4_lock", "C",
    }
    assert doc["n_entries"] == 6
    rec = doc["recommendation"]
    assert rec["invite_human_review"] is True
    assert rec["live_patch_authorized"] is False
    # Designed fixture is mechanism-only — never a live-preference claim.
    assert rec["prefer_b_for_live_later"] is False
    assert rec["verdict"] == "MECHANISM_ONLY"
    assert rec["best_b_on_rippers"]["mode"] in {
        "B_arm3", "B_arm4", "B_arm3_lock", "B_arm4_lock",
    }
    md = out_md.read_text(encoding="utf-8")
    assert "PAPER-ONLY" in md
    assert "% of entry" in md
    assert "Rippers" in md
