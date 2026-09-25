"""Rule tests for EXP-0026. No network, no fills invented."""
from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import study_itm_long_call_modal as m  # noqa: E402


def _bar(day: str, close: float, open_: float | None = None, high: float | None = None, low: float | None = None) -> dict:
    o = close if open_ is None else open_
    h = close if high is None else high
    low_ = close if low is None else low
    return {"date": day, "o": o, "h": h, "l": low_, "c": close}


def _days(n: int, start: str = "2023-01-02") -> list[str]:
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


class RulesTest(unittest.TestCase):
    def test_short_history_is_not_bull(self) -> None:
        self.assertFalse(m.bull_regime([100.0] * 10))

    def test_bull_regime_uses_average_excluding_last_close(self) -> None:
        closes = [100.0] * 51
        closes[-1] = 100.0
        self.assertTrue(m.bull_regime(closes))
        closes[-1] = 90.0
        self.assertFalse(m.bull_regime(closes))

    def test_cross_and_flat_do_not_both_signal(self) -> None:
        flat = [10.0] * 24
        self.assertFalse(m.sma_cross_up(flat))
        crossed = [10.0] * 23 + [11.0]
        self.assertTrue(m.sma_cross_up(crossed))

    def test_selects_call_near_8pct_itm(self) -> None:
        entry = date(2024, 6, 3)
        contracts = [
            {"contract_type": "call", "strike_price": 92, "expiration_date": "2024-07-05", "ticker": "TARGET"},
            {"contract_type": "call", "strike_price": 95, "expiration_date": "2024-07-05", "ticker": "SHALLOW"},
            {"contract_type": "call", "strike_price": 80, "expiration_date": "2024-07-05", "ticker": "TOO_DEEP"},
            {"contract_type": "call", "strike_price": 92, "expiration_date": "2024-06-10", "ticker": "TOO_SOON"},
            {"contract_type": "put", "strike_price": 92, "expiration_date": "2024-07-05", "ticker": "PUT"},
        ]
        chosen = m.select_itm_call(contracts, 100.0, entry)
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual(chosen["ticker"], "TARGET")
        self.assertGreaterEqual(chosen["itm_pct"], 0.05)
        self.assertLessEqual(chosen["itm_pct"], 0.12)

    def test_size_skips_when_one_contract_exceeds_book(self) -> None:
        fit = m.size_long_call(50.0)
        self.assertEqual(fit["action"], "buy")
        self.assertEqual(fit["contracts"], 1)
        self.assertAlmostEqual(fit["debit_usd"], 5000.0)
        skip = m.size_long_call(50.01)
        self.assertEqual(skip["reason"], "premium_exceeds_book")
        self.assertEqual(skip["contracts"], 0)
        self.assertIsNone(m.size_long_call(0.0)["debit_usd"])

    def test_stop_and_target_same_bar_has_no_pnl(self) -> None:
        und = [_bar("2024-01-02", 100, open_=100, high=105, low=97)]
        opt = {"2024-01-02": _bar("2024-01-02", 3.0, open_=2.0)}
        out = m.simulate_long_call("2024-01-02", 100, 2.0, 2.0, "2024-02-16", und, opt)
        self.assertEqual(out["status"], "ambiguous")
        self.assertIsNone(out["pnl_usd"])

    def test_missing_option_bar_has_no_pnl(self) -> None:
        und = [_bar("2024-01-02", 100, open_=100, high=101, low=99)]
        out = m.simulate_long_call("2024-01-02", 100, 2.0, 2.0, "2024-02-16", und, {})
        self.assertEqual(out["status"], "unpriced")
        self.assertIsNone(out["pnl_usd"])

    def test_trail_arms_for_the_next_session_only(self) -> None:
        und = [
            _bar("2024-01-02", 102, open_=100, high=103, low=99),
            _bar("2024-01-03", 100, open_=101, high=101, low=99.5),
        ]
        opt = {
            "2024-01-02": _bar("2024-01-02", 1.9, open_=2.0),
            "2024-01-03": _bar("2024-01-03", 1.5, open_=1.8),
        }
        out = m.simulate_long_call("2024-01-02", 100, 2.0, 2.0, "2024-02-16", und, opt)
        self.assertEqual(out["status"], "closed")
        self.assertEqual(out["exit_date"], "2024-01-03")
        self.assertEqual(out["reason"], "underlying_stop")

    def test_gap_through_stop_cannot_lose_more_than_premium(self) -> None:
        und = [_bar("2024-01-02", 80, open_=100, high=100, low=50)]
        opt = {"2024-01-02": _bar("2024-01-02", 0.01, open_=2.0)}
        out = m.simulate_long_call("2024-01-02", 100, 2.0, 2.0, "2024-02-16", und, opt)
        self.assertEqual(out["reason"], "underlying_stop")
        self.assertGreaterEqual(out["pnl_usd"], -out["debit_usd"] - 1e-6)
        self.assertLess(out["call_max_loss_usd"], out["stock_seat_notional_usd"])
        self.assertAlmostEqual(out["pnl_usd"], -199.0)

    def test_time_exit_on_fifth_session(self) -> None:
        days = _days(5, "2024-01-02")
        und = [_bar(day, 100.2, open_=100, high=101, low=99.5) for day in days]
        opt = {day: _bar(day, 1.8, open_=2.0) for day in days}
        out = m.simulate_long_call(days[0], 100, 2.0, 2.0, "2024-03-01", und, opt)
        self.assertEqual(out["reason"], "time_5d")
        self.assertEqual(out["exit_date"], days[4])

    def test_signal_uses_prior_bars_only(self) -> None:
        days = _days(61, "2023-01-02")
        spy = [_bar(day, 100, high=101, low=99) for day in days]
        qqq = [_bar(day, 10, high=11, low=9) for day in days]
        qqq[-2] = _bar(days[-2], 11, high=12, low=10)
        qqq[-1] = _bar(days[-1], 11.2, open_=11.2, high=12, low=10.5)
        signals = m.build_signals({"SPY": spy, "QQQ": qqq})
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["symbol"], "QQQ")
        self.assertEqual(signals[0]["entry_date"], days[-1])
        self.assertAlmostEqual(signals[0]["spot"], 11.0)

    def test_bear_regime_blocks_entry(self) -> None:
        days = _days(61, "2023-01-02")
        spy = [_bar(day, 100, high=101, low=99) for day in days]
        spy[-2] = _bar(days[-2], 50, high=51, low=49)
        qqq = [_bar(day, 10, high=11, low=9) for day in days]
        qqq[-2] = _bar(days[-2], 11, high=12, low=10)
        qqq[-1] = _bar(days[-1], 11.2, open_=11.2, high=12, low=10.5)
        self.assertEqual(m.build_signals({"SPY": spy, "QQQ": qqq}), [])

    def test_cash_from_a_close_is_not_reused_the_same_morning(self) -> None:
        def trade(symbol: str, entry: str, exit_: str) -> dict:
            return {
                "symbol": symbol,
                "entry_date": entry,
                "exit_date": exit_,
                "status": "closed",
                "debit_usd": 3000.0,
                "exit_premium_usd": 3000.0,
                "pnl_usd": 0.0,
                "marks": {entry: 30.0, exit_: 30.0},
            }

        rows = m.allocate_book([
            trade("SPY", "2024-01-02", "2024-01-03"),
            trade("QQQ", "2024-01-03", "2024-01-04"),
            trade("IWM", "2024-01-04", "2024-01-05"),
        ])
        by_symbol = {row["symbol"]: row for row in rows}
        self.assertTrue(by_symbol["SPY"]["allocated"])
        self.assertEqual(by_symbol["QQQ"]["status"], "skipped_cash")
        self.assertIsNone(by_symbol["QQQ"]["pnl_usd"])
        self.assertTrue(by_symbol["IWM"]["allocated"])

    def test_equity_carries_last_close_without_a_new_fill(self) -> None:
        trade = {
            "symbol": "SPY",
            "entry_date": "2024-01-02",
            "exit_date": "2024-01-04",
            "status": "closed",
            "allocated": True,
            "debit_usd": 200.0,
            "exit_premium_usd": 250.0,
            "pnl_usd": 50.0,
            "marks": {"2024-01-02": 2.0, "2024-01-04": 2.5},
        }
        curve = m.equity_curve([trade], ["2024-01-02", "2024-01-03", "2024-01-04"])
        self.assertEqual(curve, [5000.0, 5000.0, 5050.0])

    def test_empty_run_is_fail_not_a_made_up_sharpe_pass(self) -> None:
        spy = [
            _bar("2023-01-03", 100),
            _bar("2025-12-30", 110),
        ]
        result = m.summarize([], {}, spy, ["2023-01-03", "2025-12-30"], 1.2, {"ok": True})
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["recommendation"], "FAIL")
        self.assertEqual(result["pnl_usd"], 0)
        names = {gate["name"]: gate["label"] for gate in result["gates"]}
        self.assertEqual(names["sharpe"], "FAIL")
        self.assertEqual(names["lightgbm_precision"], "N/A")
        text = m.render_results(result)
        self.assertIn("**sharpe**: **FAIL**", text)

    def test_not_run_render_has_no_pnl(self) -> None:
        result = m.not_run("not_executed_in_cloud", "No bars requested.", 0)
        text = m.render_results(result)
        self.assertIn("**NOT_RUN**", text)
        self.assertIn("no P&L", text)
        self.assertNotIn("P&L USD", text)
        self.assertIsNone(result["sharpe"])
        self.assertIsNone(result["pnl_usd"])
        self.assertIsNone(result["monte_carlo_p_value"])

    def test_probe_classifier(self) -> None:
        refused = m.classify_probe({"_status": 403, "status": "NOT_AUTHORIZED"}, None)
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["reason"], "polygon_options_not_entitled")
        empty = m.classify_probe({"results": [{"ticker": "O:SPY"}]}, {"results": [{}, {}]})
        self.assertEqual(empty["reason"], "polygon_option_bars_empty")
        ok = m.classify_probe({"results": [{"ticker": "O:SPY"}]}, {"results": [{}, {}, {}]})
        self.assertTrue(ok["ok"])

    def test_redact_strips_key(self) -> None:
        self.assertNotIn("SECRET", m._redact("url SECRET tail", "SECRET"))

    def test_split_gap(self) -> None:
        bars = [_bar("2024-01-02", 100), _bar("2024-01-03", 20)]
        self.assertTrue(m.has_split_gap(bars))
        self.assertFalse(m.has_split_gap([_bar("2024-01-02", 100), _bar("2024-01-03", 110)]))

    def test_readme_and_powershell_mention_bridge_and_book(self) -> None:
        root = Path(__file__).resolve().parent
        readme = (root / "README.md").read_text(encoding="utf-8")
        ps1 = (root / "run_on_laptop.ps1").read_text(encoding="utf-8")
        self.assertIn("127.0.0.1:8787", readme)
        self.assertIn("$5,000", readme)
        self.assertIn("polygon-api-key", readme)
        self.assertIn("q-alpha-secrets", readme)
        self.assertIn("modal run experiments/EXP-0026/study_itm_long_call_modal.py", ps1)


if __name__ == "__main__":
    unittest.main()
