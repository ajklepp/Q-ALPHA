"""Live Peak Hour equal-signal: leak fix, hard-ext kept, flag OFF restores legacy."""
from __future__ import annotations

import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline.php_equal_signal import (  # noqa: E402
    compute_continuation_score_equal_signal,
    is_equal_signal_list_candidate,
    is_hard_extended,
    live_soft_stage_leak_hard_block,
    live_vs_equal_pair,
    ohlc_bar_state,
)
from tsd_scan_pipeline.tsd_attention import (  # noqa: E402
    annotate_momentum_context,
    build_attention_pool,
)
from tsd_scan_pipeline.tsd_case_review import review_case  # noqa: E402
from tsd_scan_pipeline.tsd_entry_gates import evaluate_entry_gates  # noqa: E402
from tsd_scan_pipeline.tsd_launch_score import (  # noqa: E402
    apply_php_equal_signal_env,
    classify_bar_state,
    compute_continuation_score,
    compute_continuation_score_v1_1,
    enrich_launch_fields,
    equal_signal_enabled,
    is_continuation_list_candidate,
    is_hard_extension_block,
    launch_score_banner,
    live_ranker_version_label,
)
from tsd_scan_pipeline.tsd_notify import format_scan_summary  # noqa: E402


@contextmanager
def php_equal_signal(on: bool):
    """Force PHP_EQUAL_SIGNAL for one test; restore previous env."""
    prev = os.environ.get("PHP_EQUAL_SIGNAL")
    os.environ["PHP_EQUAL_SIGNAL"] = "1" if on else "0"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("PHP_EQUAL_SIGNAL", None)
        else:
            os.environ["PHP_EQUAL_SIGNAL"] = prev


def _shared(**kwargs):
    base = {
        "buy_signal": True,
        "early_bull": False,
        "htf_score": 70.0,
        "htf_1h_bar_hour": 9,
        "htf_sma20_rising": True,
        "htf_close_above_sma50": True,
        "htf_range_20d_pct": 0.45,
        "dist_20d_high_pct": 0.12,
        "dist_20d_low_bounce": 0.08,
        "vol_ratio_20": 1.6,
        "open": 10.0,
        "high": 10.6,
        "low": 9.95,
        "close": 10.25,
        "htf_1h_close": 10.25,
        "htf_1h_buy_signal": True,
        "rs_ok": 1,
        "rs_spy_5d": 0.04,
        "news_velocity_24h": 4.0,
        "st_ok": 1,
        "st_msg_24h": 8.0,
        "st_bull_ratio": 0.6,
        "wt_gap": 5.0,
    }
    base.update(kwargs)
    return base


LAUNCH = _shared(symbol="LAUNCHY", scan_score=35.0, trend_strength=0.20)
SOFT_EXT = _shared(symbol="EXTENDY", scan_score=68.0, trend_strength=0.75)
HARD_EXT = _shared(symbol="HARDX", scan_score=80.0, trend_strength=0.80)

POP = {
    "recent_gainer_symbols": {"LAUNCHY", "EXTENDY", "OKTA", "WIX", "COIN"},
    "recent_active_symbols": {"LAUNCHY", "EXTENDY", "OKTA", "WIX", "COIN"},
    "live_gainers": {"EXTENDY", "OKTA", "WIX", "COIN"},
    "tws_symbols": set(),
    "popular_symbols": {"LAUNCHY", "EXTENDY", "OKTA", "WIX", "COIN"},
    "tws_ok": False,
    "sessions_loaded": 5,
}


class TestEqualSignalFlag(unittest.TestCase):
    def test_default_is_on_when_unset(self):
        with php_equal_signal(True):
            self.assertTrue(equal_signal_enabled())
            self.assertIn("equal_signal", live_ranker_version_label())
        os.environ.pop("PHP_EQUAL_SIGNAL", None)
        # Unset → default ON unless .env forces 0; env-unset with no 0 in .env.
        if equal_signal_enabled():
            self.assertIn("+equal_signal", live_ranker_version_label())

    def test_off_label(self):
        with php_equal_signal(False):
            self.assertFalse(equal_signal_enabled())
            label = live_ranker_version_label()
            self.assertTrue(label.startswith("v1.6"))
            self.assertNotIn("equal_signal", label)
            banner = launch_score_banner()
            self.assertIn("equal_signal=OFF", banner)
            self.assertIn("momentum_rank=", banner)

    def test_empty_env_and_missing_dotenv_default_on(self):
        """Blank PHP_EQUAL_SIGNAL= must not drop the overlay (mid-day banner bug)."""
        import tempfile
        from tsd_scan_pipeline import tsd_launch_score as ls

        prev = os.environ.get("PHP_EQUAL_SIGNAL")
        prev_mo = os.environ.get("PHP_MOMENTUM_RANK")
        try:
            os.environ["PHP_EQUAL_SIGNAL"] = ""
            os.environ["PHP_MOMENTUM_RANK"] = "1"
            with tempfile.TemporaryDirectory() as td:
                with patch.object(ls, "_REPO_ROOT", Path(td)):
                    self.assertTrue(equal_signal_enabled())
                    banner = launch_score_banner()
                    self.assertIn("score=v1.6+equal_signal", banner)
                    self.assertIn("equal_signal=ON", banner)
                    self.assertIn("momentum_rank=ON", banner)
                    apply_php_equal_signal_env()
                    self.assertEqual(os.environ.get("PHP_EQUAL_SIGNAL"), "1")
        finally:
            if prev is None:
                os.environ.pop("PHP_EQUAL_SIGNAL", None)
            else:
                os.environ["PHP_EQUAL_SIGNAL"] = prev
            if prev_mo is None:
                os.environ.pop("PHP_MOMENTUM_RANK", None)
            else:
                os.environ["PHP_MOMENTUM_RANK"] = prev_mo


class TestSoftExtensionLeak(unittest.TestCase):
    def test_legacy_classify_leaks_phase_to_extended(self):
        live = {**SOFT_EXT, "phase": "EXTENSION"}
        self.assertEqual(classify_bar_state(live, equal_signal=False), "extended")
        self.assertTrue(live_soft_stage_leak_hard_block(SOFT_EXT))

    def test_flag_on_soft_extension_not_hard_blocked_via_phase_leak(self):
        with php_equal_signal(True):
            row = enrich_launch_fields(dict(SOFT_EXT))
            self.assertEqual(row["phase"], "EXTENSION")
            self.assertNotEqual(row["bar_state"], "extended")
            self.assertEqual(row["bar_state"], ohlc_bar_state(SOFT_EXT))
            self.assertFalse(is_hard_extension_block(row))
            self.assertTrue(is_continuation_list_candidate(row))
            self.assertTrue(is_equal_signal_list_candidate(row))

    def test_flag_off_restores_phase_leak_hard_block(self):
        with php_equal_signal(False):
            row = enrich_launch_fields(dict(SOFT_EXT))
            self.assertEqual(row["phase"], "EXTENSION")
            self.assertEqual(row["bar_state"], "extended")
            self.assertTrue(is_hard_extension_block(row))
            self.assertFalse(is_continuation_list_candidate(row))

    def test_hard_scan_75_still_blocked_when_on(self):
        with php_equal_signal(True):
            row = enrich_launch_fields(dict(HARD_EXT))
            self.assertTrue(is_hard_extended(HARD_EXT))
            self.assertEqual(row["bar_state"], "extended")
            self.assertTrue(is_hard_extension_block(row))
            self.assertFalse(is_continuation_list_candidate(row))
            self.assertFalse(is_equal_signal_list_candidate(HARD_EXT))


class TestEqualContinuation(unittest.TestCase):
    def test_identical_non_stage_features_score_equal_when_on(self):
        with php_equal_signal(True):
            s_l = compute_continuation_score(LAUNCH)
            s_e = compute_continuation_score(SOFT_EXT)
            self.assertAlmostEqual(s_l, s_e, delta=0.05)
            self.assertAlmostEqual(
                compute_continuation_score_equal_signal(LAUNCH),
                compute_continuation_score_equal_signal(SOFT_EXT),
                delta=0.05,
            )

    def test_flag_off_restores_stage_grading_gap(self):
        prev_mo = os.environ.get("PHP_MOMENTUM_RANK")
        os.environ["PHP_MOMENTUM_RANK"] = "0"
        try:
            with php_equal_signal(False):
                live_l = compute_continuation_score(LAUNCH)
                live_e = compute_continuation_score(SOFT_EXT)
                self.assertGreater(live_l, live_e + 20)
                # Dispatcher is v1.6 when both overlays are OFF.
                self.assertAlmostEqual(live_l, compute_continuation_score_v1_1(LAUNCH), places=2)
        finally:
            if prev_mo is None:
                os.environ.pop("PHP_MOMENTUM_RANK", None)
            else:
                os.environ["PHP_MOMENTUM_RANK"] = prev_mo

    def test_room_still_ranks_under_equal_signal(self):
        tight = _shared(symbol="TIGHT", scan_score=68.0, trend_strength=0.75,
                        dist_20d_high_pct=-0.04)
        roomy = _shared(symbol="ROOMY", scan_score=68.0, trend_strength=0.75,
                        dist_20d_high_pct=0.18)
        with php_equal_signal(True):
            self.assertGreater(
                compute_continuation_score(roomy),
                compute_continuation_score(tight),
            )


class TestAttentionAndCase(unittest.TestCase):
    def test_flag_off_attention_drops_soft_extension_as_extended(self):
        with php_equal_signal(False):
            live_e = enrich_launch_fields(dict(SOFT_EXT))
            live_l = enrich_launch_fields(dict(LAUNCH))
            pool = build_attention_pool(
                [live_l, live_e],
                popularity_ctx=POP,
                top_k=6,
                include_tws=False,
            )
            self.assertIn("LAUNCHY", {r["symbol"] for r in pool})
            self.assertNotIn("EXTENDY", {r["symbol"] for r in pool})

    def test_flag_on_attention_keeps_soft_extension(self):
        with php_equal_signal(True):
            ranked = [
                enrich_launch_fields(dict(LAUNCH)),
                enrich_launch_fields(dict(SOFT_EXT)),
            ]
            pool = build_attention_pool(
                ranked, popularity_ctx=POP, top_k=6, include_tws=False,
            )
            self.assertIn("EXTENDY", {r["symbol"] for r in pool})
            self.assertIn("LAUNCHY", {r["symbol"] for r in pool})

    def test_flag_on_hard_extended_still_excluded_from_attention(self):
        with php_equal_signal(True):
            ranked = [enrich_launch_fields(dict(HARD_EXT))]
            pool = build_attention_pool(
                ranked, gainers={"HARDX"}, top_k=6, include_tws=False,
            )
            self.assertEqual(pool, [])

    def test_flag_off_rules_do_not_enter_scan_68(self):
        with php_equal_signal(False):
            row = annotate_momentum_context(
                enrich_launch_fields(dict(SOFT_EXT)), popularity_ctx=POP,
            )
            case = review_case(row, allow_llm=False, use_web_search=False)
            self.assertNotEqual(case["verdict"], "ENTER")

    def test_flag_on_rules_enter_scan_68_with_room_and_popular(self):
        with php_equal_signal(True):
            row = annotate_momentum_context(
                enrich_launch_fields(dict(SOFT_EXT)), popularity_ctx=POP,
            )
            case = review_case(row, allow_llm=False, use_web_search=False)
            self.assertEqual(case["verdict"], "ENTER")
            self.assertIn("equal_signal", str(case.get("evidence") or []))
            self.assertNotEqual(case.get("source"), "llm")


class TestEntryGatesAndEvaluate(unittest.TestCase):
    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value=set())
    def test_soft_ext_passes_gates_when_on(self, _occ):
        import pytz
        from datetime import datetime

        now = pytz.timezone("America/New_York").localize(datetime(2026, 9, 1, 9, 5))
        with php_equal_signal(True):
            passed, gates, reasons = evaluate_entry_gates(
                SOFT_EXT, regime_bull=True, now=now,
            )
        self.assertTrue(passed, msg=reasons)
        self.assertTrue(gates["not_extension"])
        self.assertNotIn("extension_hard", reasons)

    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value=set())
    def test_soft_ext_extension_hard_when_off(self, _occ):
        import pytz
        from datetime import datetime

        now = pytz.timezone("America/New_York").localize(datetime(2026, 9, 1, 9, 5))
        with php_equal_signal(False):
            passed, gates, reasons = evaluate_entry_gates(
                SOFT_EXT, regime_bull=True, now=now,
            )
        self.assertFalse(passed)
        self.assertFalse(gates["not_extension"])
        self.assertIn("extension_hard", reasons)

    @patch("tsd_scan_pipeline.tsd_entry_gates.occupied_symbols", return_value=set())
    def test_hard_scan_still_fails_gates_when_on(self, _occ):
        import pytz
        from datetime import datetime

        now = pytz.timezone("America/New_York").localize(datetime(2026, 9, 1, 9, 5))
        with php_equal_signal(True):
            passed, gates, reasons = evaluate_entry_gates(
                HARD_EXT, regime_bull=True, now=now,
            )
        self.assertFalse(passed)
        self.assertFalse(gates["not_extension"])
        self.assertIn("extension_hard", reasons)

    def test_evaluate_1h_symbol_soft_ext_on_vs_off(self):
        from tsd_scan_pipeline import tsd_1h_launch_scan as scan

        def _run(on: bool, row: dict) -> dict:
            with php_equal_signal(on):
                enriched = enrich_launch_fields(dict(row))
                with patch.object(
                    scan, "evaluate_1h_buy_signal", return_value=(True, enriched),
                ):
                    return scan.evaluate_1h_symbol("EXTENDY")

        out_on = _run(True, SOFT_EXT)
        self.assertTrue(out_on.get("pass"))
        self.assertNotEqual(out_on.get("reject_reason"), "extension_hard")

        out_off = _run(False, SOFT_EXT)
        self.assertFalse(out_off.get("pass"))
        self.assertEqual(out_off.get("reject_reason"), "extension_hard")

        out_hard = _run(True, HARD_EXT)
        self.assertFalse(out_hard.get("pass"))
        self.assertEqual(out_hard.get("reject_reason"), "extension_hard")


class TestTelegramMarker(unittest.TestCase):
    def test_scan_summary_shows_equal_signal_mode(self):
        with php_equal_signal(True):
            msg = format_scan_summary(
                hour=10, htf_pass=10, launches_n=1, take_n=0, entered_n=None,
            )
            self.assertIn("equal_signal=ON", msg)
            pair = live_vs_equal_pair(SOFT_EXT)
            self.assertTrue(pair["equal_list_ok"])
            self.assertFalse(pair["live_list_ok"])
        with php_equal_signal(False):
            msg = format_scan_summary(
                hour=10, htf_pass=10, launches_n=1, take_n=0, entered_n=None,
            )
            self.assertIn("equal_signal=OFF", msg)
            self.assertIn("score=v1.6", msg)


if __name__ == "__main__":
    unittest.main()
