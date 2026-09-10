"""Regression: verified mcap floor blocks microcaps like FGI."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline.quality_history_gate import evaluate_quality_history_gate
from tsd_scan_pipeline.tsd_htf_gates import HTF_RANGE_20D_MAX, evaluate_htf_daily_gates
from tsd_scan_pipeline.universe_tsd import MCAP_MIN, filter_by_verified_mcap


class TestVerifiedMcapFloor(unittest.TestCase):
    def test_filter_rejects_null_and_microcap(self):
        rows = [
            {"symbol": "FGI", "dollar_vol_20d_avg": 62_000_000},
            {"symbol": "OKCO", "dollar_vol_20d_avg": 20_000_000},
            {"symbol": "MISS", "dollar_vol_20d_avg": 10_000_000},
        ]

        def fake_mcap(sym: str, _key: str):
            return {
                "FGI": 14_581_096.0,
                "OKCO": 500_000_000.0,
                "MISS": None,
            }.get(sym)

        with patch(
            "tsd_scan_pipeline.universe_tsd.fetch_ticker_market_cap",
            side_effect=fake_mcap,
        ):
            kept = filter_by_verified_mcap(rows, api_key="test")

        self.assertEqual([r["symbol"] for r in kept], ["OKCO"])
        self.assertEqual(kept[0]["market_cap"], 500_000_000.0)

    @patch("tsd_scan_pipeline.quality_history_gate.passes_instrument_safety", return_value=True)
    def test_quality_gate_fails_closed_on_missing_mcap(self, _safe):
        passed, gates, reasons = evaluate_quality_history_gate({
            "symbol": "FGI",
            "scan_score": 34.0,
            "close": 7.7,
            "market_cap": None,
        })
        self.assertFalse(passed)
        self.assertFalse(gates["mcap_floor"])
        self.assertIn("mcap_unknown", reasons)

    @patch("tsd_scan_pipeline.quality_history_gate.passes_instrument_safety", return_value=True)
    def test_quality_gate_rejects_fgi_mcap(self, _safe):
        passed, gates, reasons = evaluate_quality_history_gate({
            "symbol": "FGI",
            "scan_score": 34.0,
            "close": 7.7,
            "market_cap": 14_581_096.0,
        })
        self.assertFalse(passed)
        self.assertFalse(gates["mcap_floor"])
        self.assertTrue(any(r.startswith("mcap<") for r in reasons))
        self.assertGreater(MCAP_MIN, 14_581_096.0)

    def test_htf_rejects_pump_dump_range(self):
        passed, gates, reasons, _ = evaluate_htf_daily_gates({
            "symbol": "FGI",
            "htf_range_20d_pct": 3.3802,
            "htf_close_above_sma50": True,
            "htf_sma20_rising": True,
            "close": 7.55,
        })
        self.assertFalse(passed)
        self.assertFalse(gates["range_20d"])
        self.assertIn(f"range_20d>{HTF_RANGE_20D_MAX:.0%}", reasons)


if __name__ == "__main__":
    unittest.main()
