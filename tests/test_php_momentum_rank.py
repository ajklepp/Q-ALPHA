"""Peak Hour momentum-rank: rippers over slow popular; flag OFF restores legacy."""
from __future__ import annotations

import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline.php_momentum_rank import (  # noqa: E402
    apply_momentum_rank,
    is_slow_popular,
    is_tape_hot,
    momentum_rank_delta,
    momentum_rank_enabled,
    momentum_rank_mode_label,
    take_eligible,
    take_sort_key,
)
from tsd_scan_pipeline.tsd_attention import (  # noqa: E402
    annotate_momentum_context,
    build_attention_pool,
)
from tsd_scan_pipeline.tsd_case_review import (  # noqa: E402
    review_case,
    select_enter_rows,
)
from tsd_scan_pipeline.tsd_launch_score import (  # noqa: E402
    compute_continuation_score,
    enrich_launch_fields,
    live_ranker_version_label,
)
from tsd_scan_pipeline.tsd_notify import format_scan_summary  # noqa: E402


@contextmanager
def php_flags(*, equal: bool = True, momentum: bool = True):
    """Force both live overlays for one test; restore previous env."""
    prev_eq = os.environ.get("PHP_EQUAL_SIGNAL")
    prev_mo = os.environ.get("PHP_MOMENTUM_RANK")
    os.environ["PHP_EQUAL_SIGNAL"] = "1" if equal else "0"
    os.environ["PHP_MOMENTUM_RANK"] = "1" if momentum else "0"
    try:
        yield
    finally:
        if prev_eq is None:
            os.environ.pop("PHP_EQUAL_SIGNAL", None)
        else:
            os.environ["PHP_EQUAL_SIGNAL"] = prev_eq
        if prev_mo is None:
            os.environ.pop("PHP_MOMENTUM_RANK", None)
        else:
            os.environ["PHP_MOMENTUM_RANK"] = prev_mo


def _base(**kwargs):
    row = {
        "buy_signal": True,
        "early_bull": False,
        "htf_score": 70.0,
        "htf_1h_bar_hour": 9,
        "htf_sma20_rising": True,
        "htf_close_above_sma50": True,
        "htf_range_20d_pct": 0.45,
        "dist_20d_high_pct": 0.12,
        "dist_20d_low_bounce": 0.08,
        "vol_ratio_20": 1.1,
        "open": 10.0,
        "high": 10.3,
        "low": 9.95,
        "close": 10.15,
        "htf_1h_close": 10.15,
        "htf_1h_buy_signal": True,
        "rs_ok": 1,
        "rs_spy_5d": 0.03,
        "news_velocity_24h": 3.0,
        "st_ok": 1,
        "st_msg_24h": 6.0,
        "st_bull_ratio": 0.55,
        "wt_gap": 5.0,
        "scan_score": 40.0,
        "trend_strength": 0.25,
        "ticker_prior_hit1r_rate": 0.45,
        "ticker_prior_mfe_p50": 0.03,
    }
    row.update(kwargs)
    return row


def _tars_like(**kwargs):
    """Slow popular: board member, quiet same-day tape, constructive room."""
    return _base(
        symbol="TARS",
        scan_score=38.0,
        tradable_popular=True,
        recent_leaderboard=True,
        on_gainers=False,
        vol_ratio_20=1.05,
        rs_spy_1h=0.004,
        rs_spy_1h_ok=1,
        session_ret=0.006,
        dist_20d_high_pct=0.134,
        dollar_vol_1h_vs_20d=0.04,
        ticker_prior_hit1r_rate=0.50,
        **kwargs,
    )


def _wix_like(**kwargs):
    """Ripper: live gainer, hot tape, room, same-day RS."""
    row = _base(
        symbol="WIX",
        scan_score=66.0,
        trend_strength=0.72,
        tradable_popular=True,
        recent_leaderboard=True,
        on_gainers=True,
        vol_ratio_20=2.4,
        rs_spy_1h=0.055,
        rs_spy_1h_ok=1,
        session_ret=0.08,
        dist_20d_high_pct=0.209,
        dollar_vol_1h_vs_20d=0.22,
        ticker_prior_hit1r_rate=0.20,
    )
    row.update(kwargs)
    return row


def _okta_like(**kwargs):
    """Ripper with tight prior-close room (case should WAIT)."""
    row = _wix_like(
        symbol="OKTA",
        dist_20d_high_pct=0.072,
        session_ret=0.09,
        rs_spy_1h=0.07,
    )
    row.update(kwargs)
    return row


def _coin_like(**kwargs):
    row = _wix_like(
        symbol="COIN",
        dist_20d_high_pct=0.105,
        session_ret=0.06,
        rs_spy_1h=0.045,
        vol_ratio_20=2.1,
    )
    row.update(kwargs)
    return row


POP = {
    "recent_gainer_symbols": {"TARS", "WIX", "COIN", "OKTA", "HOOD"},
    "recent_active_symbols": {"TARS", "HOOD"},
    "live_gainers": {"WIX", "COIN", "OKTA"},
    "tws_symbols": {"WIX", "COIN", "OKTA", "TARS"},
    "popular_symbols": {"TARS", "WIX", "COIN", "OKTA", "HOOD"},
    "tws_ok": True,
    "sessions_loaded": 8,
}


class TestFlag(unittest.TestCase):
    def test_default_on_when_unset(self):
        os.environ.pop("PHP_MOMENTUM_RANK", None)
        # Unset → default ON unless .env forces 0.
        if momentum_rank_enabled():
            self.assertEqual(momentum_rank_mode_label(), "ON")
            self.assertIn("momentum_rank", live_ranker_version_label())

    def test_off_label(self):
        with php_flags(equal=True, momentum=False):
            self.assertFalse(momentum_rank_enabled())
            self.assertEqual(momentum_rank_mode_label(), "OFF")
            self.assertNotIn("momentum_rank", live_ranker_version_label())
            self.assertIn("equal_signal", live_ranker_version_label())


class TestTapeVsSlowPopular(unittest.TestCase):
    def test_tars_is_slow_popular_wix_is_tape_hot(self):
        self.assertFalse(is_tape_hot(_tars_like()))
        self.assertTrue(is_slow_popular(_tars_like()))
        self.assertTrue(is_tape_hot(_wix_like()))
        self.assertFalse(is_slow_popular(_wix_like()))

    def test_ripper_outranks_slow_popular(self):
        with php_flags(equal=True, momentum=True):
            tars = compute_continuation_score(_tars_like())
            wix = compute_continuation_score(_wix_like())
            coin = compute_continuation_score(_coin_like())
            self.assertGreater(wix, tars + 20)
            self.assertGreater(coin, tars + 15)
            # Overlay is what creates the gap — base equal-signal is close.
            with php_flags(equal=True, momentum=False):
                tars_eq = compute_continuation_score(_tars_like())
                wix_eq = compute_continuation_score(_wix_like())
            self.assertGreater(wix - tars, wix_eq - tars_eq)

    def test_flag_off_does_not_apply_delta(self):
        row = _wix_like()
        with php_flags(equal=True, momentum=False):
            base = compute_continuation_score(row)
        delta = momentum_rank_delta(row)
        self.assertGreater(delta, 10)
        self.assertAlmostEqual(apply_momentum_rank(base, row), base + delta, places=2)
        with php_flags(equal=True, momentum=True):
            live = compute_continuation_score(row)
        self.assertAlmostEqual(live, apply_momentum_rank(base, row), places=2)

    def test_ripper_boost_is_capped(self):
        monster = _wix_like(
            rs_spy_1h=0.20,
            session_ret=0.25,
            vol_ratio_20=8.0,
            dollar_vol_1h_vs_20d=0.80,
        )
        self.assertLessEqual(momentum_rank_delta(monster), 36.0)


class TestTakeSelection(unittest.TestCase):
    def test_confidence_first_picks_tars_when_off(self):
        """Legacy: high-confidence slow popular beats a lower-confidence ripper."""
        rows = [
            {
                **enrich_launch_fields(_tars_like()),
                "case_verdict": "ENTER",
                "case_confidence": 0.92,
                "tradable_popular": True,
                "recent_leaderboard": True,
                "on_gainers": False,
            },
            {
                **enrich_launch_fields(_wix_like()),
                "case_verdict": "ENTER",
                "case_confidence": 0.70,
                "tradable_popular": True,
                "on_gainers": True,
            },
        ]
        with php_flags(equal=True, momentum=False):
            take = select_enter_rows(rows, max_n=1)
        self.assertEqual([r["symbol"] for r in take], ["TARS"])

    def test_momentum_rank_picks_wix_over_tars(self):
        with php_flags(equal=True, momentum=True):
            rows = [
                {
                    **enrich_launch_fields(_tars_like()),
                    "case_verdict": "ENTER",
                    "case_confidence": 0.92,
                    "tradable_popular": True,
                    "recent_leaderboard": True,
                    "on_gainers": False,
                },
                {
                    **enrich_launch_fields(_wix_like()),
                    "case_verdict": "ENTER",
                    "case_confidence": 0.70,
                    "tradable_popular": True,
                    "on_gainers": True,
                },
            ]
            take = select_enter_rows(rows, max_n=1)
        self.assertEqual([r["symbol"] for r in take], ["WIX"])

    def test_first_day_ripper_can_take_without_board(self):
        """Hot tape + momentum_context ENTER is eligible even if not popular."""
        with php_flags(equal=True, momentum=True):
            row = enrich_launch_fields(
                _wix_like(
                    symbol="NEWB",
                    tradable_popular=False,
                    recent_leaderboard=False,
                    on_gainers=False,
                    momentum_context=True,
                )
            )
            row["tradable_popular"] = False
            row["recent_leaderboard"] = False
            row["on_gainers"] = False
            row["momentum_context"] = True
            row["case_verdict"] = "ENTER"
            row["case_confidence"] = 0.68
            self.assertTrue(is_tape_hot(row))
            self.assertTrue(take_eligible(row))
            take = select_enter_rows([row], max_n=1)
            self.assertEqual([r["symbol"] for r in take], ["NEWB"])

    def test_flag_off_still_requires_popularity(self):
        row = {
            **enrich_launch_fields(_wix_like(symbol="NEWB")),
            "tradable_popular": False,
            "recent_leaderboard": False,
            "on_gainers": False,
            "momentum_context": True,
            "case_verdict": "ENTER",
            "case_confidence": 0.80,
        }
        with php_flags(equal=True, momentum=False):
            take = select_enter_rows([row], max_n=1)
        self.assertEqual(take, [])

    def test_case_wait_cannot_take(self):
        with php_flags(equal=True, momentum=True):
            okta = enrich_launch_fields(_okta_like())
            okta = annotate_momentum_context(okta, popularity_ctx=POP)
            case = review_case(okta, allow_llm=False, use_web_search=False)
            okta["case_verdict"] = case["verdict"]
            okta["case_confidence"] = case["confidence"]
            self.assertEqual(case["verdict"], "WAIT")
            take = select_enter_rows([okta], max_n=2)
            self.assertEqual(take, [])

    def test_hard_extended_still_out_of_attention(self):
        with php_flags(equal=True, momentum=True):
            hard = enrich_launch_fields(
                _wix_like(symbol="HARDX", scan_score=80.0, trend_strength=0.85)
            )
            pool = build_attention_pool(
                [hard], popularity_ctx=POP, top_k=6, include_tws=False,
            )
            self.assertEqual(pool, [])

    def test_sort_key_prefers_score_over_confidence(self):
        low_conf_rip = {
            "combined_rank_score": 110.0,
            "continuation_score": 110.0,
            "case_confidence": 0.60,
        }
        high_conf_slow = {
            "combined_rank_score": 70.0,
            "continuation_score": 70.0,
            "case_confidence": 0.95,
        }
        self.assertLess(take_sort_key(low_conf_rip), take_sort_key(high_conf_slow))


class TestSep14WatchlistOrder(unittest.TestCase):
    def test_equal_plus_momentum_would_take_wix_coin_not_tars(self):
        """Illustrative 2026-09-14 set: rippers consume the 2-slot cap."""
        with php_flags(equal=True, momentum=True):
            names = [_tars_like(), _wix_like(), _coin_like(), _okta_like()]
            rows = []
            for raw in names:
                row = annotate_momentum_context(
                    enrich_launch_fields(raw), popularity_ctx=POP,
                )
                case = review_case(row, allow_llm=False, use_web_search=False)
                row["case_verdict"] = case["verdict"]
                row["case_confidence"] = case["confidence"]
                rows.append(row)
            take = select_enter_rows(rows, max_n=2)
            taken = [r["symbol"] for r in take]
            self.assertIn("WIX", taken)
            self.assertIn("COIN", taken)
            self.assertNotIn("TARS", taken)
            self.assertNotIn("OKTA", taken)  # tight room → WAIT

    def test_equal_only_still_lets_tars_compete(self):
        """Without the overlay, TARS can still win a slot on confidence/score."""
        with php_flags(equal=True, momentum=False):
            tars = annotate_momentum_context(
                enrich_launch_fields(_tars_like()), popularity_ctx=POP,
            )
            wix = annotate_momentum_context(
                enrich_launch_fields(_wix_like()), popularity_ctx=POP,
            )
            for row in (tars, wix):
                case = review_case(row, allow_llm=False, use_web_search=False)
                row["case_verdict"] = case["verdict"]
                row["case_confidence"] = case["confidence"]
            tars["case_confidence"] = 0.90
            wix["case_confidence"] = 0.70
            take = select_enter_rows([tars, wix], max_n=1)
            self.assertEqual([r["symbol"] for r in take], ["TARS"])


class TestTelegram(unittest.TestCase):
    def test_scan_summary_shows_momentum_rank(self):
        with php_flags(equal=True, momentum=True):
            msg = format_scan_summary(
                hour=10, htf_pass=10, launches_n=1, take_n=0, entered_n=None,
            )
            self.assertIn("momentum_rank=ON", msg)
            self.assertIn("equal_signal=ON", msg)
            self.assertIn("momentum_rank", msg.split("score=")[-1])
        with php_flags(equal=True, momentum=False):
            msg = format_scan_summary(
                hour=10, htf_pass=10, launches_n=1, take_n=0, entered_n=None,
            )
            self.assertIn("momentum_rank=OFF", msg)
            self.assertNotIn("+momentum_rank", msg)


if __name__ == "__main__":
    unittest.main()
