"""EXP-0026 walk-forward engine: signal definition, $5k/10-seat book, no invented P&L."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytz

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))
sys.path.insert(0, str(ROOT / "experiments" / "EXP-0026"))

from tsd_scan_pipeline.php_equal_signal import is_equal_signal_list_candidate  # noqa: E402

from php_t100_wf_engine import (  # noqa: E402
    COST_PER_TRADE,
    IS_CUT_DEFAULT,
    MAX_SEATS,
    SEAT_NOTIONAL,
    STARTING_CASH,
    PhpSignal,
    build_results_payload,
    choose_window,
    scan_equal_signals,
    simulate_book,
)
from track100_adapter import (  # noqa: E402
    ExitFill,
    FilterDecision,
    Track100Missing,
    apply_paper_filter_winloss_v1,
    track100_available,
)

ET = pytz.timezone("America/New_York")


def _sig(
    symbol: str,
    day: str,
    hour: int,
    fill_px: float,
    *,
    scan: float = 30.0,
) -> PhpSignal:
    """Deterministic signal at 1H start = hour-1 on `day`."""
    start = hour - 1
    sig_ts = ET.localize(datetime.fromisoformat(f"{day}T{start:02d}:00:00"))
    fill_ts = sig_ts + pd.Timedelta(hours=1)
    return PhpSignal(
        symbol=symbol,
        signal_ts=sig_ts.isoformat(),
        signal_date=day,
        close_hour=hour,
        open=fill_px * 0.99,
        high=fill_px * 1.01,
        low=fill_px * 0.98,
        close=fill_px,
        buy_signal=True,
        early_bull=False,
        scan_score=scan,
        wt1=-20.0,
        wt2=-18.0,
        trend_strength=0.2,
        vol_ratio=1.2,
        structure_level=fill_px * 0.97,
        fill_ts=fill_ts.isoformat(),
        fill_px=fill_px,
    )


def _bars_from_path(start: str, hour: int, ohlc: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    ts0 = ET.localize(datetime.fromisoformat(f"{start}T{hour:02d}:00:00"))
    rows = []
    for i, (o, h, l, c) in enumerate(ohlc):
        rows.append({
            "time": ts0 + pd.Timedelta(hours=i),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": 1000.0,
        })
    return pd.DataFrame(rows).set_index("time")


def _pass_filter(row: dict) -> FilterDecision:
    return FilterDecision(passed=True, reason="pass")


def _skip_low_scan(row: dict) -> FilterDecision:
    if float(row.get("scan_score") or 0) < 25:
        return FilterDecision(passed=False, reason="scan_below_is_median")
    return FilterDecision(passed=True, reason="pass")


def _hard_target_exit(bars, entry, structure_level, signal_row=None):
    """Test-only 5% stop / 15% target — not Track 100 C_ratchet."""
    stop = entry * 0.95
    target = entry * 1.15
    for b in bars:
        if float(b["low"]) <= stop:
            return ExitFill(exit_px=stop, reason="stop", bar_ts=b["ts"])
        if float(b["high"]) >= target:
            return ExitFill(exit_px=target, reason="target", bar_ts=b["ts"])
    last = bars[-1]
    return ExitFill(exit_px=float(last["close"]), reason="time", bar_ts=last["ts"])


class EqualSignalDefinitionTests(unittest.TestCase):
    def test_equal_signal_needs_trigger(self):
        self.assertFalse(
            is_equal_signal_list_candidate({"buy_signal": False, "early_bull": False, "scan_score": 30})
        )
        self.assertTrue(
            is_equal_signal_list_candidate({"buy_signal": True, "early_bull": False, "scan_score": 30})
        )
        self.assertTrue(
            is_equal_signal_list_candidate({"buy_signal": False, "early_bull": True, "scan_score": 40})
        )

    def test_hard_extension_scan_75_blocked(self):
        self.assertFalse(
            is_equal_signal_list_candidate({"buy_signal": True, "early_bull": False, "scan_score": 75})
        )
        self.assertTrue(
            is_equal_signal_list_candidate({"buy_signal": True, "early_bull": False, "scan_score": 74.9})
        )


class ScanFillTests(unittest.TestCase):
    def test_scan_uses_next_open_and_hour_gate(self):
        idx = pd.date_range(
            ET.localize(datetime(2026, 6, 11, 0, 0)),
            periods=120,
            freq="h",
            tz=ET,
        )
        df = pd.DataFrame({
            "open": 10.0,
            "high": 10.2,
            "low": 9.8,
            "close": 10.05,
            "volume": 1000.0,
        }, index=idx)

        def fake_enrich(frame):
            out = frame.copy()
            out["buy_signal"] = False
            out["early_bull"] = False
            out["scan_score"] = 30.0
            out["wt1"] = -10.0
            out["wt2"] = -12.0
            out["trend_strength"] = 0.1
            out["vol_ratio"] = 1.0
            # 06:00 start → close hour 7 (allowed). Next open is the fill.
            hit = ET.localize(datetime(2026, 6, 15, 6, 0))
            out.loc[out.index == hit, "buy_signal"] = True
            # 03:00 start → close hour 4 (NOT allowed).
            bad = ET.localize(datetime(2026, 6, 15, 3, 0))
            out.loc[out.index == bad, "buy_signal"] = True
            return out

        with patch("php_t100_wf_engine.enrich_tsd", fake_enrich):
            sigs = scan_equal_signals(
                "AAA",
                df,
                window_start=date(2026, 6, 15),
                window_end=date(2026, 6, 15),
            )
        self.assertEqual(len(sigs), 1)
        self.assertEqual(sigs[0].close_hour, 7)
        fill_bar = df.loc[ET.localize(datetime(2026, 6, 15, 7, 0))]
        self.assertAlmostEqual(sigs[0].fill_px, float(fill_bar["open"]), places=6)

    def test_hard_extended_scan_not_emitted(self):
        idx = pd.date_range(
            ET.localize(datetime(2026, 6, 11, 0, 0)),
            periods=120,
            freq="h",
            tz=ET,
        )
        df = pd.DataFrame({
            "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.05, "volume": 1.0,
        }, index=idx)

        def fake_enrich(frame):
            out = frame.copy()
            out["buy_signal"] = False
            out["early_bull"] = False
            out["scan_score"] = 80.0
            out["wt1"] = 0.0
            out["wt2"] = 0.0
            out["trend_strength"] = 1.0
            out["vol_ratio"] = 2.0
            hit = ET.localize(datetime(2026, 6, 15, 10, 0))
            out.loc[out.index == hit, "buy_signal"] = True
            return out

        with patch("php_t100_wf_engine.enrich_tsd", fake_enrich):
            sigs = scan_equal_signals(
                "BBB", df,
                window_start=date(2026, 6, 15),
                window_end=date(2026, 6, 15),
            )
        self.assertEqual(sigs, [])


class BookOccupancyTests(unittest.TestCase):
    def test_ten_seats_and_dollar_cost(self):
        day = "2026-06-15"
        sigs = [_sig(f"S{i:02d}", day, 10, 10.0) for i in range(12)]
        # Path: fill bar then one more that neither stops nor targets — time exit.
        bars = {
            f"S{i:02d}": _bars_from_path(day, 10, [
                (10.0, 10.1, 9.9, 10.05),
                (10.05, 10.1, 9.95, 10.0),
            ])
            for i in range(12)
        }
        book = simulate_book(
            sigs,
            bars,
            apply_filter=True,
            filter_fn=_pass_filter,
            exit_fn=_hard_target_exit,
            is_cut=date(2026, 7, 20),
            window_end=date(2026, 6, 16),
        )
        self.assertEqual(book.n_signals, 12)
        self.assertEqual(book.n_fills, MAX_SEATS)
        self.assertEqual(book.extra["n_skip_seats_full"], 2)
        # $500/seat, 0.15% RT on notional
        self.assertAlmostEqual(book.trades[0].notional, SEAT_NOTIONAL, places=4)
        self.assertAlmostEqual(book.trades[0].cost, SEAT_NOTIONAL * COST_PER_TRADE, places=6)
        # Dollar equity, not equal-weight % sum
        self.assertEqual(book.starting_cash, STARTING_CASH)
        expected_cost = MAX_SEATS * SEAT_NOTIONAL * COST_PER_TRADE
        # time exit at 10.0 = entry → PnL = -cost
        self.assertAlmostEqual(book.total_pnl, -expected_cost, places=2)

    def test_filter_skip_counts(self):
        day = "2026-06-15"
        sigs = [
            _sig("GOOD", day, 10, 10.0, scan=40),
            _sig("BAD", day, 11, 10.0, scan=10),
        ]
        bars = {
            "GOOD": _bars_from_path(day, 10, [(10, 10.1, 9.9, 10), (10, 10.1, 9.9, 10)]),
            "BAD": _bars_from_path(day, 11, [(10, 10.1, 9.9, 10), (10, 10.1, 9.9, 10)]),
        }
        book = simulate_book(
            sigs, bars, apply_filter=True,
            filter_fn=_skip_low_scan,
            exit_fn=_hard_target_exit,
            window_end=date(2026, 6, 16),
        )
        self.assertEqual(book.n_filter_skip, 1)
        self.assertEqual(book.filter_skip_reasons.get("scan_below_is_median"), 1)
        self.assertEqual(book.n_fills, 1)

    def test_is_oos_split_on_signal_date(self):
        sigs = [
            _sig("OLD", "2026-07-20", 10, 10.0),
            _sig("NEW", "2026-07-21", 10, 10.0),
        ]
        bars = {
            "OLD": _bars_from_path("2026-07-20", 10, [(10, 10.1, 9.9, 10), (10, 10, 10, 10)]),
            "NEW": _bars_from_path("2026-07-21", 10, [(10, 10.1, 9.9, 10), (10, 10, 10, 10)]),
        }
        book = simulate_book(
            sigs, bars, apply_filter=False,
            exit_fn=_hard_target_exit,
            is_cut=IS_CUT_DEFAULT,
            window_end=date(2026, 7, 22),
        )
        splits = {t.symbol: t.split for t in book.trades}
        self.assertEqual(splits["OLD"], "IS")
        self.assertEqual(splits["NEW"], "OOS")


class AdapterAndResultsTests(unittest.TestCase):
    def test_missing_track100_raises(self):
        avail = track100_available()
        if avail.get("ready"):
            self.skipTest("Track 100 modules present on this machine")
        with self.assertRaises(Track100Missing):
            apply_paper_filter_winloss_v1({"scan_score": 30, "buy_signal": True})

    def test_blocked_payload_has_no_invented_pnl(self):
        window = choose_window(date(2026, 6, 8), date(2026, 9, 4))
        payload = build_results_payload(
            variants=None,
            window=window,
            is_cut=IS_CUT_DEFAULT,
            status="blocked",
            blocked_reason="polygon_api_key_missing",
        )
        self.assertFalse(payload["invented_pnl"])
        self.assertIsNone(payload["primary"])
        self.assertEqual(payload["status"], "blocked")
        self.assertTrue(window["snapped"])

    def test_window_snap_notes_shorter_history(self):
        w = choose_window(date(2026, 6, 8), date(2026, 9, 4))
        self.assertEqual(w["start"], "2026-06-08")
        self.assertEqual(w["end"], "2026-09-04")
        self.assertTrue(w["snapped"])


if __name__ == "__main__":
    os.environ.setdefault("PHP_EQUAL_SIGNAL", "1")
    unittest.main()
