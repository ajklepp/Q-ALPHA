"""Regression tests for complete and fresh Polygon 1H signal data."""
from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

import pandas as pd
import pytz

from tsd_scan_pipeline import tsd_1h_signal

ET = pytz.timezone("America/New_York")


def _polygon_bar(timestamp_ms: int, close: float) -> dict:
    """Build the minimum valid Polygon aggregate used by the parser."""
    return {
        "t": timestamp_ms,
        "o": close - 0.1,
        "h": close + 0.2,
        "l": close - 0.2,
        "c": close,
        "v": 1000,
    }


class TestPolygonHourlyPagination(unittest.TestCase):
    def test_follows_next_url_and_deduplicates_overlap(self):
        first_t = 1_788_000_000_000
        second_t = first_t + 3_600_000
        pages = [
            {
                "results": [_polygon_bar(first_t, 44.0)],
                "next_url": "https://api.polygon.io/next?cursor=abc",
            },
            {
                "results": [
                    _polygon_bar(first_t, 44.0),
                    _polygon_bar(second_t, 45.0),
                ],
            },
        ]

        with patch.object(
            tsd_1h_signal,
            "polygon_get",
            side_effect=pages,
        ) as polygon_get:
            bars = tsd_1h_signal._bars_1h_polygon(
                "IREN",
                api_key="test-key",
            )

        self.assertEqual(len(bars), 2)
        self.assertEqual(list(bars["close"]), [44.0, 45.0])
        self.assertEqual(polygon_get.call_count, 2)
        self.assertEqual(polygon_get.call_args_list[1].args[1], {})


class TestHourlyFreshness(unittest.TestCase):
    def test_stale_completed_bar_fails_loudly(self):
        index = pd.date_range(
            "2026-08-25T04:00:00-04:00",
            periods=80,
            freq="h",
        )
        bars = pd.DataFrame({
            "open": [44.0] * 80,
            "high": [44.2] * 80,
            "low": [43.8] * 80,
            "close": [44.1] * 80,
            "volume": [1000.0] * 80,
        }, index=index)
        now = ET.localize(datetime(2026, 9, 8, 7, 15))

        with patch.object(
            tsd_1h_signal,
            "_bars_1h_polygon",
            return_value=bars,
        ):
            passed, row = tsd_1h_signal.evaluate_1h_buy_signal(
                {"symbol": "IREN"},
                polygon_key="test-key",
                now=now,
            )

        self.assertFalse(passed)
        self.assertEqual(row["source"], "stale_1h_bars")
        self.assertEqual(row["reject_reason"], "stale_1h_bars")
        self.assertGreater(row["bar_age_minutes"], 90)


if __name__ == "__main__":
    unittest.main()
