"""Unit tests for Attention Pool + Case Review (post-signal veto)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline.tsd_attention import (  # noqa: E402
    annotate_momentum_context,
    build_attention_pool,
)
from tsd_scan_pipeline.tsd_case_review import (  # noqa: E402
    build_case_dossier,
    classify_room_class,
    review_case,
    select_enter_rows,
)


def _row(**kwargs):
    base = {
        "symbol": "TEST",
        "pass": True,
        "scan_score": 35.0,
        "continuation_score": 80.0,
        "combined_rank_score": 80.0,
        "bar_state": "yellow",
        "htf_score": 60.0,
        "htf_sma20_rising": True,
        "htf_range_20d_pct": 0.40,
        "dist_20d_high_pct": 0.12,
        "dist_20d_low_bounce": 0.05,
        "vol_ratio_20": 1.5,
        "st_msg_24h": 5.0,
        "st_ok": 1,
        "st_bull_ratio": 0.6,
        "news_velocity_24h": 1.0,
    }
    base.update(kwargs)
    return base


class TestAttentionPool(unittest.TestCase):
    def test_top_continuation_and_gainer_union(self):
        ranked = [
            _row(symbol="AAA", continuation_score=90),
            _row(symbol="BBB", continuation_score=85),
            _row(symbol="CCC", continuation_score=40, scan_score=60),
            _row(symbol="IREN", continuation_score=45, scan_score=58, dist_20d_high_pct=0.15),
        ]
        pool = build_attention_pool(
            ranked,
            gainers={"IREN", "ZZZ"},
            top_k=2,
            pool_max=10,
        )
        syms = {r["symbol"] for r in pool}
        self.assertIn("AAA", syms)
        self.assertIn("BBB", syms)
        self.assertIn("IREN", syms)  # gainer + soft-extension room
        iren = next(r for r in pool if r["symbol"] == "IREN")
        self.assertTrue(iren.get("on_gainers"))
        self.assertIn("polygon_gainer", iren.get("attention_reasons") or [])

    def test_hard_extended_excluded(self):
        ranked = [_row(symbol="HOT", scan_score=80, bar_state="extended", continuation_score=99)]
        pool = build_attention_pool(ranked, gainers={"HOT"}, top_k=6)
        self.assertEqual(pool, [])


class TestCaseReview(unittest.TestCase):
    def test_fgi_wreckage_rejects(self):
        """FGI-shaped: 338% range + deep dump under high + dead vol → REJECT."""
        fgi = _row(
            symbol="FGI",
            continuation_score=93.87,
            scan_score=29.0,
            htf_range_20d_pct=3.38,
            dist_20d_high_pct=1.68,
            dist_20d_low_bounce=0.0,
            vol_ratio_20=0.005,
            outlook="raised",
            deep_summary_line="quiet tape — no catalyst trail in lookback",
            news_velocity_24h=0.0,
            headline_count=0,
        )
        fgi = annotate_momentum_context(fgi, gainers=set(), buzz_threshold=99)
        self.assertEqual(classify_room_class(fgi), "WRECKAGE_ROOM")
        case = review_case(fgi, allow_llm=False, use_web_search=False)
        self.assertEqual(case["verdict"], "REJECT")
        self.assertIn("wreckage_room", case.get("risks") or [])

    def test_high_score_without_momentum_waits(self):
        quiet = _row(
            continuation_score=88.0,
            vol_ratio_20=0.05,
            st_msg_24h=1.0,
            htf_score=20.0,
            dist_20d_high_pct=0.08,
            htf_range_20d_pct=0.35,
        )
        quiet = annotate_momentum_context(quiet, gainers=set(), buzz_threshold=50)
        self.assertFalse(quiet.get("momentum_context"))
        case = review_case(quiet, allow_llm=False, use_web_search=False)
        self.assertEqual(case["verdict"], "WAIT")

    def test_momentum_early_swing_can_enter(self):
        good = _row(
            symbol="IREN",
            continuation_score=72.0,
            scan_score=40.0,
            htf_range_20d_pct=0.55,
            dist_20d_high_pct=0.14,
            vol_ratio_20=1.8,
            st_msg_24h=40.0,
            st_ok=1,
        )
        good = annotate_momentum_context(good, gainers={"IREN"}, buzz_threshold=12)
        self.assertTrue(good.get("momentum_context"))
        self.assertEqual(classify_room_class(good), "CONSTRUCTIVE_ROOM")
        case = review_case(good, allow_llm=False, use_web_search=False)
        self.assertEqual(case["verdict"], "ENTER")

    def test_select_enter_only(self):
        rows = [
            {**_row(symbol="A"), "case_verdict": "ENTER", "case_confidence": 0.9},
            {**_row(symbol="B"), "case_verdict": "WAIT", "case_confidence": 0.9},
            {**_row(symbol="C"), "case_verdict": "ENTER", "case_confidence": 0.7},
            {**_row(symbol="D"), "case_verdict": "REJECT", "case_confidence": 0.99},
        ]
        take = select_enter_rows(rows, max_n=2)
        self.assertEqual([r["symbol"] for r in take], ["A", "C"])

    def test_dossier_includes_room_class(self):
        d = build_case_dossier(_row(htf_range_20d_pct=3.0, dist_20d_high_pct=1.0))
        self.assertEqual(d["room_class"], "WRECKAGE_ROOM")


if __name__ == "__main__":
    unittest.main()
