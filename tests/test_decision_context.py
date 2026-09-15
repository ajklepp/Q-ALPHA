"""Unit: decision-context overlays, live-path timeout, and soft-degrade."""
from __future__ import annotations

import sys
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "candidates"))

import pytz

from tsd_scan_pipeline.tsd_decision_context import (  # noqa: E402
    DEAD_TAPE_PENALTY,
    POLYGON_CTX_TIMEOUT_SEC,
    RS_1H_LEAD_STRONG_PTS,
    apply_decision_context_score_terms,
    attach_decision_context,
    fetch_options_call_share,
)
from tsd_scan_pipeline.tsd_launch_score import (  # noqa: E402
    CONTINUATION_SCORE_VERSION,
    compute_continuation_score,
)

ET = pytz.timezone("America/New_York")
NOW = ET.localize(datetime(2026, 9, 15, 7, 15))


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


def _session_bars(_symbol: str, _day) -> list[dict]:
    """Causal 1H bars so attach does not fetch per-name hour aggs."""
    return [{
        "et": NOW,
        "close_hour": 7,
        "o": 10.0,
        "h": 11.0,
        "l": 9.0,
        "c": 10.5,
        "v": 100_000.0,
    }]


def _rows(n: int = 3) -> list[dict]:
    out = []
    for i in range(n):
        out.append({
            "symbol": f"T{i}",
            "pass": True,
            "continuation_score": 80 - i,
            "htf_1h_bar_hour": 7,
            "htf_1h_close": 10.5,
            "float_shares": 40_000_000,
            "dollar_vol_20d": 50_000_000,
            "vol_ratio_20": 1.2,
        })
    return out


class TestScoreOverlays(unittest.TestCase):
    def test_version_is_v16(self) -> None:
        self.assertTrue(CONTINUATION_SCORE_VERSION.startswith("v1.6"))

    def test_rs_and_dead_tape_move_score(self) -> None:
        test_rs_and_dead_tape_move_score()

    def test_continuation_uses_decision_fields(self) -> None:
        test_continuation_uses_decision_fields()

    def test_early_session_outranks_late_identical_setup(self) -> None:
        test_early_session_outranks_late_identical_setup()


class TestDecisionContextTimeout(unittest.TestCase):
    def tearDown(self) -> None:
        from tsd_scan_pipeline import tsd_decision_context as dc

        dc._CACHE.clear()

    def test_hard_timeout_returns_degraded_rows_quickly(self) -> None:
        """Wedged Polygon must not hold the launch tick (2026-09-15 hour-7)."""
        calls: list[float] = []

        def hung_get(url, params, api_key, timeout=60):
            calls.append(timeout)
            time.sleep(4.0)
            return {"results": []}

        t0 = time.time()
        with patch("tsd_scan_pipeline.tsd_decision_context.polygon_get", side_effect=hung_get), \
             patch(
                 "tsd_scan_pipeline.tsd_1h_signal.session_bars_from_cache",
                 side_effect=_session_bars,
             ):
            out = attach_decision_context(
                _rows(4),
                api_key="test-key",
                now=NOW,
                options_top_n=12,
                budget_sec=8.0,
                hard_timeout_sec=0.4,
            )
        elapsed = time.time() - t0
        self.assertLess(elapsed, 1.5)
        self.assertEqual(len(out), 4)
        self.assertTrue(all(int(r.get("decision_context_degraded") or 0) == 1 for r in out))
        self.assertTrue(
            all(r.get("decision_context_skip_reason") in ("timeout", "budget", "empty") for r in out)
        )
        self.assertTrue(calls)
        self.assertTrue(all(int(t) == POLYGON_CTX_TIMEOUT_SEC for t in calls))

    def test_budget_skips_remainder_without_raising(self) -> None:
        def slow_get(url, params, api_key, timeout=60):
            time.sleep(0.55)
            return {"results": []}

        t0 = time.time()
        with patch("tsd_scan_pipeline.tsd_decision_context.polygon_get", side_effect=slow_get), \
             patch(
                 "tsd_scan_pipeline.tsd_1h_signal.session_bars_from_cache",
                 side_effect=_session_bars,
             ):
            out = attach_decision_context(
                _rows(6),
                api_key="test-key",
                now=NOW,
                options_top_n=12,
                budget_sec=2.4,
                hard_timeout_sec=5.0,
            )
        elapsed = time.time() - t0
        self.assertLess(elapsed, 4.0)
        self.assertEqual(len(out), 6)
        n_deg = sum(1 for r in out if int(r.get("decision_context_degraded") or 0) == 1)
        self.assertGreaterEqual(n_deg, 1)
        self.assertTrue(all(r.get("symbol") for r in out))

    def test_options_uses_one_snapshot_http_not_study_aggs(self) -> None:
        """Live path must not call the weekly 40-contract agg helper."""
        urls: list[str] = []

        def fake_get(url, params, api_key, timeout=60):
            urls.append(str(url))
            self.assertEqual(int(timeout), POLYGON_CTX_TIMEOUT_SEC)
            if "snapshot/options" in str(url):
                return {
                    "results": [
                        {
                            "details": {
                                "contract_type": "call",
                                "strike_price": 10.5,
                                "expiration_date": "2026-10-16",
                            },
                            "day": {"volume": 800},
                        },
                        {
                            "details": {
                                "contract_type": "put",
                                "strike_price": 10.0,
                                "expiration_date": "2026-10-16",
                            },
                            "day": {"volume": 200},
                        },
                    ]
                }
            return {"results": []}

        def boom(*_a, **_k):
            raise AssertionError("live path must not call _fetch_options_day_volume")

        with patch("tsd_scan_pipeline.tsd_decision_context.polygon_get", side_effect=fake_get), \
             patch(
                 "tsd_scan_pipeline.tsd_1h_signal.session_bars_from_cache",
                 side_effect=_session_bars,
             ), \
             patch(
                 "tsd_scan_pipeline.tsd_options_study._fetch_options_day_volume",
                 side_effect=boom,
             ):
            out = attach_decision_context(
                _rows(3),
                api_key="test-key",
                now=NOW,
                options_top_n=12,
                budget_sec=10.0,
                hard_timeout_sec=10.0,
            )
        snap_urls = [u for u in urls if "snapshot/options" in u]
        agg_urls = [u for u in urls if "/range/1/day/" in u and "O:" in u]
        self.assertEqual(len(snap_urls), 3)
        self.assertEqual(agg_urls, [])
        shares = [r.get("options_call_share") for r in out]
        self.assertTrue(all(s == 0.8 for s in shares))
        self.assertTrue(all(int(r.get("decision_context_ok") or 0) == 1 for r in out))

    def test_fetch_options_call_share_caches_and_skips_study(self) -> None:
        def fake_get(url, params, api_key, timeout=60):
            return {
                "results": [
                    {
                        "details": {
                            "contract_type": "call",
                            "strike_price": 10.0,
                            "expiration_date": "2026-10-16",
                        },
                        "day": {"volume": 50},
                    },
                    {
                        "details": {
                            "contract_type": "put",
                            "strike_price": 10.0,
                            "expiration_date": "2026-10-16",
                        },
                        "day": {"volume": 50},
                    },
                ]
            }

        with patch("tsd_scan_pipeline.tsd_decision_context.polygon_get", side_effect=fake_get) as pg:
            a = fetch_options_call_share("AAA", api_key="k", as_of=NOW, spot=10.0)
            b = fetch_options_call_share("AAA", api_key="k", as_of=NOW, spot=10.0)
        self.assertEqual(a.get("call_share"), 0.5)
        self.assertEqual(b.get("call_share"), 0.5)
        self.assertEqual(pg.call_count, 1)

    def test_degraded_score_terms_are_no_ops(self) -> None:
        """Timeout overlay must not change take/trail rules — missing fields = 0 pts."""
        base = 50.0
        got = apply_decision_context_score_terms(
            base,
            {
                "decision_context_degraded": 1,
                "decision_context_ok": 0,
                "decision_context_skip_reason": "timeout",
            },
        )
        self.assertEqual(got, base)


class TestRankContinuesAfterDegrade(unittest.TestCase):
    def test_rank_1h_launches_continues_when_context_degraded(self) -> None:
        from tsd_scan_pipeline.tsd_1h_launch_scan import rank_1h_launches

        rows = [
            {
                "symbol": "AAA",
                "pass": True,
                "buy_signal": True,
                "scan_score": 40,
                "htf_1h_bar_hour": 7,
                "htf_1h_close": 10.5,
                "continuation_score": 70.0,
            }
        ]

        def degraded(rows_in, **_k):
            return [
                {
                    **r,
                    "decision_context_ok": 0,
                    "decision_context_degraded": 1,
                    "decision_context_skip_reason": "timeout",
                }
                for r in rows_in
            ]

        with patch(
            "tsd_scan_pipeline.tsd_deep_features.attach_deep_features",
            side_effect=lambda rows_in, **_k: list(rows_in),
        ), patch(
            "tsd_scan_pipeline.tsd_decision_context.attach_decision_context",
            side_effect=degraded,
        ):
            out = rank_1h_launches(
                rows, polygon_key="test-key", now=NOW, attach_social=False,
            )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["symbol"], "AAA")
        self.assertEqual(int(out[0].get("decision_context_degraded") or 0), 1)
        self.assertIsNotNone(out[0].get("continuation_score"))


if __name__ == "__main__":
    test_version_is_v16()
    test_rs_and_dead_tape_move_score()
    test_continuation_uses_decision_fields()
    test_early_session_outranks_late_identical_setup()
    unittest.main()
