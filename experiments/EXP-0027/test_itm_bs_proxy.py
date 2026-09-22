"""Rule checks for the EXP-0027 BS proxy. No network."""
from __future__ import annotations

import unittest
from datetime import date, timedelta

from study_itm_bs_proxy import (
    BANNER,
    BOOK_USD,
    LABEL,
    atr14,
    bs_call_price,
    bull_regime,
    choose_model_expiry,
    choose_model_strike,
    implied_vol,
    simulate_bs_call,
    simulate_stock_100,
    sma_cross_up,
)


def _session(day: str, o: float, h: float, l: float, c: float) -> dict:
    return {"date": day, "o": o, "h": h, "l": l, "c": c}


class BlackScholesTests(unittest.TestCase):
    def test_atm_one_year_zero_rates(self) -> None:
        # S=K=100, T=1, r=q=0, sigma=0.2 → 100 * (2*N(0.1) - 1).
        price = bs_call_price(100.0, 100.0, 1.0, 0.2, 0.0, 0.0)
        self.assertAlmostEqual(price, 7.965567, places=4)

    def test_iv_roundtrip(self) -> None:
        true_iv = 0.35
        price = bs_call_price(180.0, 166.0, 32 / 365.0, true_iv, 0.04, 0.0)
        solved = implied_vol(price, 180.0, 166.0, 32 / 365.0, 0.04, 0.0)
        self.assertIsNotNone(solved)
        self.assertAlmostEqual(solved, true_iv, places=3)

    def test_deep_itm_is_at_least_intrinsic_when_rates_zero(self) -> None:
        price = bs_call_price(100.0, 92.0, 30 / 365.0, 0.25, 0.0, 0.0)
        self.assertGreaterEqual(price, 8.0 - 1e-9)


class RuleTests(unittest.TestCase):
    def test_strike_is_inside_band_near_8pct(self) -> None:
        picked = choose_model_strike(184.0)
        self.assertIsNotNone(picked)
        self.assertGreaterEqual(picked["itm_pct"], 0.05)
        self.assertLessEqual(picked["itm_pct"], 0.12)
        self.assertLess(abs(picked["itm_pct"] - 0.08), 0.01)

    def test_expiry_is_a_friday_inside_the_window(self) -> None:
        exp = choose_model_expiry(date(2026, 1, 20))
        self.assertIsNotNone(exp)
        self.assertEqual(exp.weekday(), 4)
        dte = (exp - date(2026, 1, 20)).days
        self.assertGreaterEqual(dte, 21)
        self.assertLessEqual(dte, 45)

    def test_sma_cross_uses_only_the_closes_it_is_given(self) -> None:
        flat = [10.0] * 22
        self.assertFalse(sma_cross_up(flat))
        # Prior close still inside the average; last close steps above it.
        self.assertTrue(sma_cross_up([10.0] * 20 + [9.5, 11.0]))
        self.assertFalse(bull_regime([1.0] * 10))

    def test_atr_ignores_a_bar_the_caller_does_not_pass(self) -> None:
        bars = []
        day = date(2026, 1, 1)
        for i in range(16):
            px = 100.0 + i
            bars.append(_session((day + timedelta(days=i)).isoformat(), px, px + 1, px - 1, px))
        atr = atr14(bars)
        self.assertIsNotNone(atr)
        self.assertLess(atr, 5.0)

    def test_crash_loss_cannot_exceed_debit(self) -> None:
        bars = [
            _session("2026-02-02", 100, 101, 99, 100),
            _session("2026-02-03", 100, 100.5, 70, 72),
        ]
        result = simulate_bs_call(
            "2026-02-02",
            100.0,
            2.0,
            92.0,
            "2026-03-06",
            0.30,
            0.0,
            bars,
        )
        # First bar does not tag the stop (low 99 > 98). Second bar does.
        self.assertEqual(result["status"], "closed")
        self.assertEqual(result["reason"], "underlying_stop")
        self.assertGreaterEqual(result["pnl_usd"], -result["debit_usd"] - 1e-6)
        self.assertLess(result["pnl_usd"], 0.0)

    def test_same_bar_stop_and_target_has_no_pnl(self) -> None:
        bars = [_session("2026-02-02", 100, 110, 90, 100)]
        result = simulate_bs_call(
            "2026-02-02",
            100.0,
            2.0,
            92.0,
            "2026-03-06",
            0.30,
            0.0,
            bars,
        )
        self.assertEqual(result["status"], "ambiguous")
        self.assertIsNone(result["pnl_usd"])

    def test_expensive_underlying_skips_the_book(self) -> None:
        bars = [_session("2026-02-02", 754, 760, 750, 755)]
        result = simulate_bs_call(
            "2026-02-02",
            754.0,
            8.0,
            694.0,
            "2026-03-06",
            0.15,
            0.011,
            bars,
        )
        self.assertEqual(result["reason"], "premium_exceeds_book")
        self.assertIsNone(result["pnl_usd"])
        self.assertGreater(result["debit_usd"], BOOK_USD)

    def test_stock_gap_fills_at_the_open(self) -> None:
        bars = [
            _session("2026-02-02", 100, 101, 99, 100),
            _session("2026-02-03", 90, 91, 88, 89),
        ]
        result = simulate_stock_100("2026-02-02", 100.0, 2.0, bars)
        self.assertEqual(result["status"], "closed")
        self.assertEqual(result["reason"], "underlying_stop")
        self.assertAlmostEqual(result["exit_px"], 90.0)
        # Gap loss is larger than 1R ($200) before the cost haircut.
        self.assertLess(result["pnl_gross_usd"], -200.0)

    def test_banner_constant(self) -> None:
        self.assertIn("NOT FILLS", BANNER)
        self.assertEqual(LABEL, "model_proxy_pnl")


if __name__ == "__main__":
    unittest.main()
