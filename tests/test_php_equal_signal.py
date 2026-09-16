"""Research-only equal-signal overlay — do not treat as live Peak Hour tests."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline.php_equal_signal import (  # noqa: E402
    MAX_NEW_ENTRIES_PER_SCAN,
    build_attention_pool_equal_signal,
    compute_continuation_score_equal_signal,
    counterfactual_hour,
    enrich_equal_signal_fields,
    is_equal_signal_list_candidate,
    is_hard_extended,
    live_soft_stage_leak_hard_block,
    live_vs_equal_pair,
    ohlc_bar_state,
    review_case_equal_signal,
    select_would_take,
)
from tsd_scan_pipeline.php_equal_signal_counterfactual import (  # noqa: E402
    run,
    run_mechanism_demo,
)
from tsd_scan_pipeline.tsd_attention import build_attention_pool  # noqa: E402
from tsd_scan_pipeline.tsd_case_review import review_case  # noqa: E402
from tsd_scan_pipeline.tsd_launch_score import (  # noqa: E402
    compute_continuation_score,
    enrich_launch_fields,
)


def _shared(**kwargs):
    base = {
        "buy_signal": True,
        "early_bull": False,
        "htf_score": 70.0,
        "htf_1h_bar_hour": 9,
        "htf_sma20_rising": True,
        "htf_range_20d_pct": 0.45,
        "dist_20d_high_pct": 0.12,
        "dist_20d_low_bounce": 0.08,
        "vol_ratio_20": 1.6,
        "open": 10.0,
        "high": 10.6,
        "low": 9.95,
        "close": 10.25,
        "rs_ok": 1,
        "rs_spy_5d": 0.04,
        "news_velocity_24h": 4.0,
        "st_ok": 1,
        "st_msg_24h": 8.0,
        "st_bull_ratio": 0.6,
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


class TestEqualSignalAdmission(unittest.TestCase):
    def test_soft_extension_leaks_to_live_hard_block(self):
        live = enrich_launch_fields(dict(SOFT_EXT))
        self.assertEqual(live["phase"], "EXTENSION")
        self.assertEqual(live["bar_state"], "extended")
        self.assertTrue(live_soft_stage_leak_hard_block(SOFT_EXT))
        self.assertFalse(live["bar_state"] != "extended")  # live would extension_hard

    def test_equal_signal_admits_soft_extension(self):
        self.assertTrue(is_equal_signal_list_candidate(SOFT_EXT, keep_hard_extension=True))
        self.assertFalse(is_hard_extended(SOFT_EXT, keep_hard=True))
        self.assertNotEqual(ohlc_bar_state(SOFT_EXT), "extended")

    def test_hard_extension_still_blocked(self):
        self.assertTrue(is_hard_extended(HARD_EXT, keep_hard=True))
        self.assertFalse(is_equal_signal_list_candidate(HARD_EXT, keep_hard_extension=True))
        self.assertTrue(is_equal_signal_list_candidate(HARD_EXT, keep_hard_extension=False))

    def test_live_ranker_unchanged_on_extension(self):
        """Sanity: we did not rewrite tsd_launch_score live path."""
        live_l = compute_continuation_score(LAUNCH)
        live_e = compute_continuation_score(SOFT_EXT)
        self.assertGreater(live_l, live_e + 20)


class TestEqualSignalRank(unittest.TestCase):
    def test_other_factors_equal_scores_match(self):
        s_l = compute_continuation_score_equal_signal(LAUNCH)
        s_e = compute_continuation_score_equal_signal(SOFT_EXT)
        self.assertAlmostEqual(s_l, s_e, delta=0.05)

    def test_live_gap_is_stage_not_tape(self):
        pair_l = live_vs_equal_pair(LAUNCH)
        pair_e = live_vs_equal_pair(SOFT_EXT)
        live_gap = pair_l["live_continuation"] - pair_e["live_continuation"]
        eq_gap = pair_l["equal_continuation"] - pair_e["equal_continuation"]
        self.assertGreater(live_gap, 25)
        self.assertLess(abs(eq_gap), 0.05)

    def test_room_still_ranks(self):
        tight = _shared(symbol="TIGHT", scan_score=68.0, trend_strength=0.75,
                        dist_20d_high_pct=-0.04)
        roomy = _shared(symbol="ROOMY", scan_score=68.0, trend_strength=0.75,
                        dist_20d_high_pct=0.18)
        self.assertGreater(
            compute_continuation_score_equal_signal(roomy),
            compute_continuation_score_equal_signal(tight),
        )


class TestEqualSignalAttentionAndCase(unittest.TestCase):
    def test_live_attention_drops_soft_extension_as_extended(self):
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

    def test_equal_attention_keeps_soft_extension(self):
        ranked = [
            enrich_equal_signal_fields(LAUNCH),
            enrich_equal_signal_fields(SOFT_EXT),
        ]
        for r in ranked:
            r["continuation_score"] = r["continuation_score_equal"]
        pool = build_attention_pool_equal_signal(ranked, popularity_ctx=POP)
        self.assertIn("EXTENDY", {r["symbol"] for r in pool})
        self.assertIn("LAUNCHY", {r["symbol"] for r in pool})

    def test_live_rules_do_not_enter_scan_68(self):
        row = enrich_equal_signal_fields(SOFT_EXT)
        from tsd_scan_pipeline.tsd_attention import annotate_momentum_context

        row = annotate_momentum_context(row, popularity_ctx=POP)
        case = review_case(row, allow_llm=False, use_web_search=False)
        self.assertNotEqual(case["verdict"], "ENTER")

    def test_equal_rules_enter_scan_68_with_room_and_popular(self):
        row = enrich_equal_signal_fields(SOFT_EXT)
        from tsd_scan_pipeline.tsd_attention import annotate_momentum_context

        row = annotate_momentum_context(row, popularity_ctx=POP)
        case = review_case_equal_signal(row)
        self.assertEqual(case["verdict"], "ENTER")
        self.assertIn("equal_signal", str(case.get("source") or ""))
        self.assertNotEqual(case.get("source"), "llm")

    def test_take_cap_is_live_two(self):
        self.assertEqual(MAX_NEW_ENTRIES_PER_SCAN, 2)
        names = ["AAA", "BBB", "CCC", "WIX", "COIN"]
        rows = []
        for i, sym in enumerate(names):
            r = enrich_equal_signal_fields(_shared(
                symbol=sym, scan_score=36.0 + i, trend_strength=0.2,
            ))
            from tsd_scan_pipeline.tsd_attention import annotate_momentum_context

            r = annotate_momentum_context(r, popularity_ctx={
                **POP,
                "popular_symbols": set(names),
                "recent_gainer_symbols": set(names),
                "live_gainers": set(names),
            })
            r["case_verdict"] = "ENTER"
            r["case_confidence"] = 0.9 - i * 0.01
            r["tradable_popular"] = True
            rows.append(r)
        take = select_would_take(rows, max_n=MAX_NEW_ENTRIES_PER_SCAN)
        self.assertEqual(len(take), 2)

    def test_counterfactual_hour_surfaces_okta_class(self):
        okta = _shared(
            symbol="OKTA", scan_score=68.0, trend_strength=0.75,
            dist_20d_high_pct=0.12,
        )
        tars = _shared(symbol="TARS", scan_score=36.0, trend_strength=0.2)
        cf = counterfactual_hour(
            [okta, tars],
            popularity_ctx={
                **POP,
                "popular_symbols": {"OKTA", "TARS"},
                "recent_gainer_symbols": {"OKTA", "TARS"},
                "live_gainers": {"OKTA"},
            },
            keep_hard_extension=True,
        )
        self.assertIn("OKTA", {r["symbol"] for r in cf["attention"]})
        self.assertTrue(cf["ranked"])
        self.assertNotIn("HARDX", cf["would_take_symbols"])
        # Live would leak-block OKTA; equal-signal ranks it.
        okta_row = next(r for r in cf["ranked"] if r["symbol"] == "OKTA")
        self.assertTrue(okta_row["live_soft_stage_leak_hard_block"])
        self.assertTrue(okta_row["equal_signal_list_ok"])


class TestMechanismAndReport(unittest.TestCase):
    def test_mechanism_demo_hard_off_adds_hardx(self):
        demo = run_mechanism_demo()
        self.assertIn("EXTENDY", demo["primary_attention"])
        self.assertNotIn("HARDX", demo["primary_ranked"])
        self.assertIn("HARDX", demo["sensitivity_hard_off_extra"] + demo["sensitivity_ranked"])
        self.assertIn("HARDX", demo["sensitivity_ranked"])

    def test_script_writes_markdown(self):
        payload = run(date_str="2026-09-14")
        self.assertTrue(payload["research_only"])
        self.assertTrue(payload["live_rules_unchanged"])
        self.assertEqual(payload["max_new_entries_per_scan"], 2)
        self.assertTrue(payload["hard_extension_primary"])
        md = ROOT / "candidates" / "tsd_scan_pipeline" / "results" / "EQUAL_SIGNAL_COUNTERFACTUAL_20260914.md"
        self.assertTrue(md.is_file())
        text = md.read_text(encoding="utf-8")
        self.assertIn("Hard-extension (primary): **ON**", text)
        self.assertIn("GAP (abort)", text)
        self.assertIn("TARS", text)
        self.assertIn("OKTA", text)
        self.assertIn("rules-only", text)
        self.assertIn("Do not merge this as a live enablement", text)
        self.assertIn("WIX", text)
        ill = payload["watchlist_illustrative"]
        self.assertIn("WIX", ill["would_take"])
        wix = ill["pairs"]["WIX"]
        self.assertFalse(wix["live_list_ok"])
        self.assertTrue(wix["live_soft_stage_leak_hard_block"])
        self.assertTrue(wix["equal_list_ok"])
        js = ROOT / "candidates" / "tsd_scan_pipeline" / "results" / "equal_signal_counterfactual_20260914.json"
        self.assertTrue(js.is_file())
        json.loads(js.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
