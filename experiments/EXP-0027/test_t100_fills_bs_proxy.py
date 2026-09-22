"""Tape parsing and one priced fill. No network."""
from __future__ import annotations

import unittest
from datetime import date

import study_itm_bs_proxy as bs
from study_t100_fills_bs_proxy import (
    BANNER,
    LEVERAGE_2X_TO_UNDERLYING,
    map_underlying,
    normalize_trade,
    price_one_fill,
)


def _bar(day: str, px: float) -> dict:
    return {"date": day, "o": px, "h": px + 1, "l": px - 1, "c": px}


class TapeTests(unittest.TestCase):
    def test_nvdl_maps_to_nvda(self) -> None:
        mapped = map_underlying("NVDL", None)
        self.assertEqual(mapped["underlying"], "NVDA")
        self.assertEqual(LEVERAGE_2X_TO_UNDERLYING["TSLL"], "TSLA")

    def test_tape_underlying_wins(self) -> None:
        mapped = map_underlying("MSFU", "MSFT")
        self.assertEqual(mapped["underlying"], "MSFT")
        self.assertEqual(mapped["map_source"], "tape_underlying")

    def test_normalize_keeps_tape_pnl(self) -> None:
        row = normalize_trade({
            "symbol": "NVDA",
            "entry": 100,
            "exit": 110,
            "pnl_usd": 50,
            "opened_et": "2026-01-16T09:30:00-05:00",
            "closed_et": "2026-01-20T16:00:00-05:00",
            "reason": "target",
            "notional": 500,
        })
        self.assertIsNotNone(row)
        self.assertEqual(row["entry_date"], "2026-01-16")
        self.assertEqual(row["exit_date"], "2026-01-20")
        self.assertEqual(row["pnl_usd"], 50)
        self.assertEqual(row["reason"], "target")

    def test_priced_call_uses_tape_exit_not_a_new_signal(self) -> None:
        trade = normalize_trade({
            "symbol": "NVDA",
            "entry": 100,
            "exit": 110,
            "pnl_usd": 40,
            "opened_et": "2026-02-02",
            "closed_et": "2026-02-06",
            "reason": "target",
            "notional": 5000,
        })
        bars = [
            _bar("2026-01-30", 100),
            _bar("2026-02-02", 100),
            _bar("2026-02-06", 110),
        ]
        priced = price_one_fill(trade, bars, 0.30)
        self.assertEqual(priced["status"], "closed")
        self.assertEqual(priced["option_reason"], "track100_exit_same_session")
        self.assertEqual(priced["pnl_usd"], 40)
        self.assertIsNotNone(priced["option_pnl_usd"])
        self.assertGreaterEqual(priced["option_pnl_usd"], -priced["debit_usd"] - 1e-6)
        expiry = date.fromisoformat(priced["expiry"])
        self.assertGreaterEqual((expiry - date(2026, 2, 2)).days, 21)
        self.assertLessEqual((expiry - date(2026, 2, 2)).days, 45)

    def test_seat_rejects_a_debit_above_the_fill(self) -> None:
        trade = normalize_trade({
            "symbol": "NVDA",
            "entry": 200,
            "exit": 210,
            "pnl_usd": 10,
            "opened_et": "2026-02-02",
            "closed_et": "2026-02-06",
            "notional": 100,
        })
        bars = [_bar("2026-01-30", 200), _bar("2026-02-02", 200), _bar("2026-02-06", 210)]
        priced = price_one_fill(trade, bars, 0.30)
        self.assertEqual(priced["reason_skip"], "premium_exceeds_seat")
        self.assertIsNone(priced["option_pnl_usd"])

    def test_banner(self) -> None:
        self.assertIn("NOT FILLS", BANNER)
        self.assertGreater(bs.bs_call_price(100, 92, 30 / 365, 0.3, 0.04, 0.0), 8)


if __name__ == "__main__":
    unittest.main()
