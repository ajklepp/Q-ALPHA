"""Unit tests for UTS v2 Phase 2 quality_history_gate."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline.quality_history_gate import (
    ANALOG_WIN_RATE_MIN,
    apply_soft_tags,
    compute_analog_win_rate,
    evaluate_quality_history_gate,
    fetch_news_context,
)

ZIP_LAUNCH = {
    "symbol": "ZIP",
    "scan_score": 34.0,
    "trend_strength": 0.16,
    "buy_signal": True,
    "early_bull": False,
    "close": 5.25,
    "open": 5.30,
    "wt_gap": 5.1,
    "market_cap": 500_000_000,
    "tsd_profile": {
        "analog_count": 42,
        "analog_win_rate": 52.4,
        "analog_wins": 22,
        "analog_losses": 20,
        "kill_pct": 0.08,
    },
}

WEAK_PROFILE = {
    "symbol": "WEAK",
    "scan_score": 34.0,
    "buy_signal": True,
    "close": 5.25,
    "wt_gap": 5.0,
    "market_cap": 500_000_000,
    "tsd_profile": {
        "analog_count": 35,
        "analog_win_rate": 32.0,
        "analog_wins": 11,
        "analog_losses": 23,
    },
}

NO_ANALOGS = {
    "symbol": "NEWCO",
    "scan_score": 34.0,
    "buy_signal": True,
    "close": 6.50,
    "open": 6.60,
    "wt_gap": 5.0,
    "market_cap": 500_000_000,
}


class TestQualityHistoryGate(unittest.TestCase):
    @patch("tsd_scan_pipeline.quality_history_gate.passes_instrument_safety", return_value=True)
    def test_passes_with_zero_news_context(self, _safe):
        passed, gates, reasons = evaluate_quality_history_gate(ZIP_LAUNCH)
        self.assertTrue(passed)
        self.assertTrue(gates["analog_count"])
        self.assertTrue(gates["analog_win_rate"])
        self.assertEqual(reasons, [])

        tagged = apply_soft_tags(
            {**ZIP_LAUNCH, "launch_score": 72.5, "phase": "LAUNCH"},
            {
                "pre_catalyst": True,
                "catalyst_tier": 0,
                "sentiment_score": 0.0,
                "news_summary": "🔀 No Catalyst: No news found — possible technical move",
            },
        )
        self.assertIn("pre_catalyst", tagged["tags"])
        self.assertTrue(tagged["pre_catalyst"])
        self.assertEqual(tagged["catalyst_tier"], 0)

    @patch("tsd_scan_pipeline.quality_history_gate.passes_instrument_safety", return_value=True)
    def test_low_analog_win_rate_does_not_block(self, _safe):
        passed, gates, reasons = evaluate_quality_history_gate(WEAK_PROFILE)
        self.assertTrue(passed, msg=reasons)
        self.assertTrue(gates["analog_win_rate"])
        self.assertTrue(gates["analog_count"])
        self.assertFalse(any("analog_win_rate" in r for r in reasons))
        self.assertLess(
            compute_analog_win_rate(WEAK_PROFILE["tsd_profile"]),
            ANALOG_WIN_RATE_MIN,
        )

    @patch("tsd_scan_pipeline.quality_history_gate.passes_instrument_safety", return_value=True)
    def test_zero_analogs_passes_if_safety_price_ok(self, _safe):
        passed, gates, reasons = evaluate_quality_history_gate(NO_ANALOGS)
        self.assertTrue(passed, msg=reasons)
        self.assertTrue(gates["analog_count"])
        self.assertTrue(gates["price_floor"])
        self.assertEqual(reasons, [])

        missing_profile = {**NO_ANALOGS, "tsd_profile": {}}
        passed2, gates2, _ = evaluate_quality_history_gate(missing_profile)
        self.assertTrue(passed2)
        self.assertTrue(gates2["analog_count"])

    @patch("tsd_scan_pipeline.quality_history_gate.passes_instrument_safety", return_value=True)
    def test_negative_sentiment_does_not_block(self, _safe):
        passed, _, _ = evaluate_quality_history_gate(ZIP_LAUNCH)
        self.assertTrue(passed)
        tagged = apply_soft_tags(
            {**ZIP_LAUNCH, "launch_score": 70},
            {
                "pre_catalyst": False,
                "catalyst_tier": 1,
                "sentiment_score": -0.6,
                "news_summary": "📰 Downgrade: analyst cut to Sell on dilution fears",
            },
        )
        self.assertLess(tagged["sentiment_score"], 0)
        self.assertIn("catalyst_confirmed", tagged["tags"])
        self.assertGreater(tagged["launch_score_display"], tagged["launch_score"])

    @patch("catalyst_ai.summarize_catalyst", return_value="📈 Earnings Beat: revenue surged")
    @patch("tsd_scan_pipeline.quality_history_gate.fetch_headlines_48h", return_value=["Q2 beat"])
    def test_fetch_news_context_after_pass(self, _head, _sum):
        ctx = fetch_news_context("ZIP", polygon_key="test", summarize=True)
        self.assertFalse(ctx["pre_catalyst"])
        self.assertGreaterEqual(ctx["catalyst_tier"], 1)
        self.assertIn("news_summary", ctx)


if __name__ == "__main__":
    unittest.main()
