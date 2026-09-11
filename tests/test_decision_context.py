"""Unit: decision-context soft score overlays (no Polygon)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline.tsd_decision_context import (  # noqa: E402
    DEAD_TAPE_PENALTY,
    RS_1H_LEAD_STRONG_PTS,
    apply_decision_context_score_terms,
)
from tsd_scan_pipeline.tsd_launch_score import (  # noqa: E402
    CONTINUATION_SCORE_VERSION,
    compute_continuation_score,
)


def test_version_is_v16() -> None:
    assert CONTINUATION_SCORE_VERSION.startswith("v1.6")


def test_rs_and_dead_tape_move_score() -> None:
    base = 50.0
    lead = apply_decision_context_score_terms(
        base,
        {"rs_spy_1h_ok": 1, "rs_spy_1h": 0.04},
    )
    assert lead == base + RS_1H_LEAD_STRONG_PTS

    dead = apply_decision_context_score_terms(
        base,
        {
            "micro_dead_tape": 1,
            "ticker_prior_hit1r_rate": 0.5,
        },
    )
    assert dead == base - DEAD_TAPE_PENALTY


def test_continuation_uses_decision_fields() -> None:
    weak = {
        "buy_signal": True,
        "scan_score": 40,
        "launch_score": 50,
        "htf_score": 40,
        "htf_1h_bar_hour": 7,
        "bar_state": "yellow",
        "dist_20d_high_pct": 0.1,
        "vol_ratio_20": 0.4,
        "ticker_prior_hit1r_rate": 0.6,
        "ticker_prior_mfe_p50": 0.04,
        "rs_spy_1h_ok": 1,
        "rs_spy_1h": -0.03,
        "micro_dead_tape": 1,
        "dollar_vol_1h_vs_20d": 0.005,
    }
    strong = {
        **weak,
        "rs_spy_1h": 0.04,
        "micro_dead_tape": 0,
        "dollar_vol_1h_vs_20d": 0.2,
        "vol_ratio_20": 2.0,
        "options_call_share": 0.7,
        "float_shares": 40_000_000,
    }
    assert compute_continuation_score(strong) > compute_continuation_score(weak)


def test_early_session_outranks_late_identical_setup() -> None:
    """v1.6: same setup at hour 7 should beat hour 13 (same-day path ablation)."""
    base = {
        "buy_signal": True,
        "scan_score": 40,
        "launch_score": 55,
        "htf_score": 50,
        "bar_state": "yellow",
        "dist_20d_high_pct": 0.08,
        "vol_ratio_20": 1.5,
        "ticker_prior_hit1r_rate": 0.3,
        "ticker_prior_mfe_p50": 0.03,
    }
    early = compute_continuation_score({**base, "htf_1h_bar_hour": 7})
    late = compute_continuation_score({**base, "htf_1h_bar_hour": 13})
    assert early > late


if __name__ == "__main__":
    test_version_is_v16()
    test_rs_and_dead_tape_move_score()
    test_continuation_uses_decision_fields()
    test_early_session_outranks_late_identical_setup()
    print("OK test_decision_context")
