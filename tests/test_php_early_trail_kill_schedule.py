"""Unit tests for PAPER-ONLY Peak Hour early-trail / kill-schedule study."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "candidates"))
sys.path.insert(0, str(ROOT / "strategy_lab"))

from tsd_scan_pipeline.php_early_trail_kill_paper import (  # noqa: E402
    FALLBACK_KILL_PCT,
    init_paper_state,
    kill_pct_for_mfe,
    measure_path_giveback,
    normalize_kill_schedule,
    paper_process_bar,
    path_from_facts,
    replay_paper_path,
)
from tsd_scan_pipeline.php_early_trail_kill_schedule_study import (  # noqa: E402
    FIXTURE_PATH,
    estimate_ticker_exit_schedule,
    main,
    replay_paper,
)


def test_kill_schedule_only_tightens():
    sched = normalize_kill_schedule([
        {"after_mfe_pct": 0.00, "kill_pct_below_entry": 0.05},
        {"after_mfe_pct": 0.03, "kill_pct_below_entry": 0.08},  # illegal loosen
        {"after_mfe_pct": 0.04, "kill_pct_below_entry": 0.015},
    ])
    assert sched[1]["kill_pct_below_entry"] == 0.05
    assert sched[2]["kill_pct_below_entry"] == 0.015
    assert kill_pct_for_mfe(sched, 0.035) == 0.05
    assert kill_pct_for_mfe(sched, 0.041) == 0.015


def test_paper_trail_exits_pct_off_high():
    """After arm, a 2% giveback from the high exits at run_high * 0.98."""
    entry = 10.0
    state = init_paper_state(
        entry, 8,
        trail_pct_off_high=0.02,
        trail_arm_mfe_pct=0.02,
        kill_schedule=[{"after_mfe_pct": 0.0, "kill_pct_below_entry": 0.05}],
        mode="C",
    )
    # Bar 1: +3% high — arms trail, skip trail-exit same bar.
    state, exits = paper_process_bar(
        state, high=10.30, low=10.10, close=10.25, when="arm",
    )
    assert state["trail_armed"] is True
    assert exits == []
    assert state["remaining"] == 8
    # Bar 2: high holds 10.30, low tags 2% off high = 10.094
    state, exits = paper_process_bar(
        state, high=10.30, low=10.05, close=10.08, when="trail",
    )
    assert exits and exits[0]["reason"] == "trail"
    assert abs(exits[0]["exit_price"] - 10.30 * 0.98) < 1e-6
    assert state["closed"] is True


def test_kill_ratchet_after_plus_3pct_mfe():
    """After +3% MFE, kill tightens from 5% to 2.5% below entry (price UP)."""
    entry = 10.0
    state = init_paper_state(
        entry, 8,
        trail_pct_off_high=0.025,
        trail_arm_mfe_pct=0.10,  # do not trail
        kill_schedule=[
            {"after_mfe_pct": 0.00, "kill_pct_below_entry": 0.05},
            {"after_mfe_pct": 0.03, "kill_pct_below_entry": 0.025},
        ],
        mode="C",
    )
    assert abs(state["kill_price"] - 9.50) < 1e-6
    state, exits = paper_process_bar(
        state, high=10.35, low=9.80, close=10.20, when="green",
    )
    # High printed +3.5% so kill ratchets to 9.75; low 9.80 does not hit it.
    assert abs(state["kill_price"] - 9.75) < 1e-6
    assert exits == []
    state, exits = paper_process_bar(
        state, high=10.20, low=9.74, close=9.90, when="fade",
    )
    assert exits and exits[0]["reason"] == "kill"
    assert abs(exits[0]["exit_price"] - 9.75) < 1e-6


def test_measure_giveback_flags_fade_after_3pct():
    bars = [
        {"high": 10.40, "low": 10.05, "close": 10.35},
        {"high": 10.20, "low": 9.60, "close": 9.70},
    ]
    gb = measure_path_giveback(10.0, bars)
    assert gb["touched_plus_3pct"] is True
    assert gb["faded_after_3pct"] is True
    assert gb["mfe_peak_pct"] >= 0.039
    assert gb["giveback_from_high_pct"] > 0.05


def test_path_from_facts_is_mfe_then_mae():
    bars = path_from_facts(10.0, mfe_pct=0.04, mae_pct=0.05, killed=True)
    assert bars[0]["high"] == 10.4
    assert bars[1]["low"] == 9.5
    gb = measure_path_giveback(10.0, bars)
    assert abs(gb["mfe_peak_pct"] - 0.04) < 1e-9
    assert abs(gb["mae_peak_pct"] - 0.05) < 1e-9


def test_estimate_schedule_on_fixture_analogs():
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    analogs = fixture["analogs"]["FADE"]
    sched = estimate_ticker_exit_schedule("FADE", analogs, profile=fixture["profiles"]["FADE"])
    assert sched["status"] in {"OK", "HEURISTIC"}
    assert sched["n_analogs"] >= 8
    assert 0.010 <= float(sched["early_trail_pct_off_high"]) <= 0.030
    assert 0.015 <= float(sched["trail_arm_mfe_pct_of_entry"]) <= 0.040
    kills = [s["kill_pct_below_entry"] for s in sched["kill_schedule"]]
    assert kills == sorted(kills, reverse=True)
    assert sched["drivers"]["fade_p75_off_high_after_3pct"] is not None


def test_replay_paper_modes_abcd_on_fixture():
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
    schedules = {}
    for e in entries:
        analogs = [
            a for a in fixture["analogs"][e["symbol"]]
            if a["entry_date"] < e["entry_date"]
        ]
        schedules[e["symbol"]] = estimate_ticker_exit_schedule(
            e["symbol"], analogs, profile=fixture["profiles"][e["symbol"]],
        )
    for mode in ("A", "B", "C", "D"):
        book = replay_paper(entries, mode, schedules=schedules, profiles=fixture["profiles"])
        assert book["n"] == 4
        assert book["n_no_bars"] == 0
        assert book["book_pnl"] is not None
        # FADE is green-then-lost on the raw path; paper C should not be worse
        # than dying at -5% of entry on that name (sanity, not a live claim).
        fade = next(t for t in book["trades"] if t["symbol"] == "FADE")
        assert fade["mfe_peak_pct"] >= 0.03
    book_c = replay_paper(entries, "C", schedules=schedules, profiles=fixture["profiles"])
    fade_c = next(t for t in book_c["trades"] if t["symbol"] == "FADE")
    # Early trail / ratchet should keep more than a full -5% kill.
    assert fade_c["pnl_pct_of_entry"] > -0.05 - 0.0015


def test_replay_paper_path_applies_cost():
    bars = [
        {"high": 10.30, "low": 10.10, "close": 10.25, "when": "1"},
        {"high": 10.30, "low": 10.05, "close": 10.08, "when": "2"},
    ]
    rec = replay_paper_path(
        10.0, bars, shares=8,
        trail_pct_off_high=0.02,
        trail_arm_mfe_pct=0.02,
        kill_schedule=[{"after_mfe_pct": 0.0, "kill_pct_below_entry": FALLBACK_KILL_PCT}],
    )
    assert rec["cost"] == 10.0 * 8 * 0.0015
    assert rec["pnl"] == rec["realized_gross"] - rec["cost"]


def test_dry_run_cli_writes_results():
    """CLI --dry-run produces JSON + MD under results/ (asof-stamped)."""
    rc = main(["--dry-run", "--asof", "2026-09-16"])
    assert rc == 0
    out_json = ROOT / "candidates" / "tsd_scan_pipeline" / "results" / (
        "php_early_trail_kill_schedule_20260916_dryrun.json"
    )
    out_md = ROOT / "candidates" / "tsd_scan_pipeline" / "results" / (
        "php_early_trail_kill_schedule_20260916_dryrun.md"
    )
    assert out_json.is_file()
    assert out_md.is_file()
    doc = json.loads(out_json.read_text(encoding="utf-8"))
    assert doc["paper_only"] is True
    assert doc["live_files_edited"] == []
    assert set(doc["modes_run"]) == {"A", "B", "C", "D"}
    assert doc["n_entries"] == 4
    assert "PAPER-ONLY" in out_md.read_text(encoding="utf-8")
