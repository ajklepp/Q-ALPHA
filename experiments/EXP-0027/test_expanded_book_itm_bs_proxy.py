"""Expanded-book tape parsing and one priced fill. No network. No invented P&L."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from study_expanded_book_itm_bs_proxy import (
    BANNER,
    EXPANDED_BOOK,
    _row_book,
    choose_tape,
    comparison_line,
    find_tape_files,
    load_tape_file,
    parse_tape_payload,
    price_expanded_trades,
    summarize_expanded,
)


def _bar(day: str, px: float) -> dict:
    return {"date": day, "o": px, "h": px + 1, "l": px - 1, "c": px}


def _expanded_row(**overrides) -> dict:
    row = {
        "book": "expanded_half_equity_2",
        "symbol": "AMD",
        "entry": 80,
        "exit": 70,
        "pnl_usd": -25.0,
        "entry_date": "2026-03-02",
        "exit_date": "2026-03-06",
        "notional": 1250,
        "reason": "stop",
        "filter": "paper_filter_winloss_v1",
        "exit_model": "C_ratchet_struct",
    }
    row.update(overrides)
    return row


class ExpandedBookTests(unittest.TestCase):
    def test_banner(self) -> None:
        self.assertIn("NOT FILLS", BANNER)

    def test_filters_to_expanded_book(self) -> None:
        payload = {
            "trades": [
                _expanded_row(),
                {
                    "book": "baseline_half_equity_2",
                    "symbol": "NVDA",
                    "entry": 100,
                    "exit": 110,
                    "pnl_usd": 400,
                    "entry_date": "2026-02-02",
                    "exit_date": "2026-02-06",
                    "notional": 1250,
                },
            ]
        }
        parsed = parse_tape_payload(payload, "expanded")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["book"], EXPANDED_BOOK)
        self.assertEqual(len(parsed["trades"]), 1)
        self.assertEqual(parsed["trades"][0]["traded"], "AMD")
        self.assertEqual(parsed["trades"][0]["pnl_usd"], -25.0)
        self.assertEqual(parsed["dropped"]["wrong_book"], 1)

    def test_modal_csv_comparison_is_the_seat_book(self) -> None:
        """Modal rows store the seat book in comparison and the exit in book."""
        row = {
            "comparison": "expanded_half_equity_2",
            "book": "C_ratchet_struct",
            "traded": "AMD",
            "entry": 80,
            "exit": 70,
            "pnl_usd": -25.0,
            "entry_date": "2026-03-02",
            "exit_date": "2026-03-06",
        }
        self.assertEqual(_row_book(row, None), EXPANDED_BOOK)
        self.assertIsNone(_row_book({"book": "C_ratchet_struct", "traded": "AMD"}, None))
        parsed = parse_tape_payload(
            [
                row,
                {
                    "comparison": "baseline_half_equity_2",
                    "book": "C_ratchet_struct",
                    "traded": "NVDA",
                    "entry": 100,
                    "exit": 110,
                    "pnl_usd": 400,
                    "entry_date": "2026-02-02",
                    "exit_date": "2026-02-06",
                },
            ],
            "expanded",
        )
        self.assertTrue(parsed["ok"])
        self.assertEqual(len(parsed["trades"]), 1)
        self.assertEqual(parsed["trades"][0]["book"], EXPANDED_BOOK)
        self.assertEqual(parsed["trades"][0]["traded"], "AMD")
        self.assertEqual(parsed["trades"][0]["pnl_usd"], -25.0)
        self.assertNotIn("option_pnl_usd", parsed["trades"][0])
        self.assertEqual(parsed["dropped"]["wrong_book"], 1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "universe_expand_movers_trades.csv"
            path.write_text(
                "comparison,book,traded,entry,exit,pnl_usd,entry_date,exit_date\n"
                "expanded_half_equity_2,C_ratchet_struct,AMD,80,70,-25,2026-03-02,2026-03-06\n",
                encoding="utf-8",
            )
            loaded = load_tape_file(path, "expanded")
        self.assertTrue(loaded["ok"])
        self.assertEqual(loaded["trades"][0]["book"], EXPANDED_BOOK)
        self.assertEqual(loaded["trades"][0]["pnl_usd"], -25.0)
        self.assertNotIn("option_pnl_usd", loaded["trades"][0])

    def test_book_key_stamps_rows_that_omit_the_field(self) -> None:
        payload = {
            "expanded_half_equity_2": {
                "trades": [
                    {
                        "symbol": "SMCI",
                        "entry": 40,
                        "exit": 44,
                        "pnl_usd": 12,
                        "opened_et": "2026-04-01",
                        "closed_et": "2026-04-03",
                        "notional": 1250,
                    }
                ]
            },
            "baseline_half_equity_2": {
                "trades": [
                    {
                        "symbol": "NVDA",
                        "entry": 100,
                        "exit": 110,
                        "pnl_usd": 50,
                        "entry_date": "2026-02-02",
                        "exit_date": "2026-02-06",
                    }
                ]
            },
        }
        parsed = parse_tape_payload(payload, "expanded")
        self.assertEqual([row["traded"] for row in parsed["trades"]], ["SMCI"])
        self.assertEqual(parsed["trades"][0]["book"], EXPANDED_BOOK)
        self.assertEqual(parsed["trades"][0]["pnl_usd"], 12)

    def test_empty_closed_list_does_not_hide_book_trades(self) -> None:
        payload = {
            "closed": [],
            "expanded_half_equity_2": {
                "trades": [
                    {
                        "symbol": "ARM",
                        "entry": 90,
                        "exit": 95,
                        "pnl_usd": 15,
                        "entry_date": "2026-05-01",
                        "exit_date": "2026-05-05",
                        "notional": 1250,
                    }
                ]
            },
        }
        parsed = parse_tape_payload(payload, "expanded")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["trades"][0]["traded"], "ARM")
        self.assertEqual(parsed["trades"][0]["pnl_usd"], 15)

    def test_summary_only_does_not_invent_fills_or_dollars(self) -> None:
        payload = {
            "window": ["2026-01-16", "2026-09-16"],
            "books": {
                "baseline_half_equity_2": {"n_closed": 17, "win_rate": 0.765, "pnl_usd": 1661.0},
                "expanded_half_equity_2": {"n_closed": 30, "win_rate": 0.333, "pnl_usd": -734.39},
            },
        }
        parsed = parse_tape_payload(payload, "expanded")
        self.assertFalse(parsed["ok"])
        self.assertEqual(parsed["reason"], "summary_only_no_trade_list")
        self.assertEqual(parsed["trades"], [])
        result = {
            "status": "NOT_RUN",
            "book": EXPANDED_BOOK,
            "n_closed": 0,
            "stock_pnl_usd": None,
            "option_pnl_usd": None,
            "stock_pnl_pct_on_5k": None,
            "stock_win_rate": None,
            "option_pnl_pct_on_5k": None,
            "option_win_rate": None,
        }
        line = comparison_line(result)
        self.assertIn("NOT_RUN", line)
        self.assertIn("n/a", line)
        self.assertNotIn("734", line)
        self.assertNotIn("1661", line)
        self.assertIsNone(result["option_pnl_usd"])
        self.assertIsNone(result["stock_pnl_usd"])

    def test_option_path_runs_and_missing_bars_do_not_invent_pnl(self) -> None:
        parsed = parse_tape_payload(
            [_expanded_row(pnl_usd=-25.0, entry=100, exit=110, notional=5000)],
            "expanded",
        )
        trade = parsed["trades"][0]
        bars = [
            _bar("2026-02-27", 100),
            _bar("2026-03-02", 100),
            _bar("2026-03-06", 110),
        ]
        priced = price_expanded_trades(parsed["trades"], {"AMD": bars}, {"AMD": {"iv": 0.30}})
        self.assertEqual(priced[0]["status"], "closed")
        self.assertEqual(priced[0]["pnl_usd"], -25.0)
        self.assertIsNotNone(priced[0]["option_pnl_usd"])
        self.assertNotEqual(priced[0]["option_pnl_usd"], priced[0]["pnl_usd"])

        # NVDL is not the option underlying. With no NVDA bars the model has
        # no spot, and it must not fill in a price or a dollar P&L.
        levered = parse_tape_payload(
            [_expanded_row(symbol="NVDL", pnl_usd=-40.0, entry=50, exit=40, notional=1250)],
            "expanded",
        )
        missing = price_expanded_trades(levered["trades"], {}, {"NVDA": {"iv": 0.30}})
        self.assertEqual(missing[0]["underlying"], "NVDA")
        self.assertIsNone(missing[0]["option_pnl_usd"])
        self.assertNotEqual(missing[0]["status"], "closed")
        self.assertEqual(missing[0]["pnl_usd"], -40.0)
        summary = summarize_expanded(missing)
        self.assertEqual(summary["stock_pnl_usd"], -40.0)
        self.assertIsNone(summary["option_pnl_usd"])
        self.assertEqual(summary["n_calls"], 0)
        self.assertNotEqual(summary["option_pnl_usd"], 0)

    def test_prefers_trade_file_over_ops_stack_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ops_stack_5k_trades.json").write_text(
                json.dumps([{"symbol": "NVDA", "entry": 1, "exit": 2, "pnl_usd": 1661, "entry_date": "2026-02-02", "exit_date": "2026-02-06"}]),
                encoding="utf-8",
            )
            (root / "universe_expand_movers_trades.json").write_text(
                json.dumps({"expanded_half_equity_2": {"n_closed": 30, "pnl_usd": -734.39, "win_rate": 0.333}}),
                encoding="utf-8",
            )
            (root / "universe_expand_movers_trades.csv").write_text(
                "book,symbol,entry,exit,pnl_usd,entry_date,exit_date,notional\n"
                "expanded_half_equity_2,AMD,80,70,-25,2026-03-02,2026-03-06,1250\n"
                "baseline_half_equity_2,NVDA,100,110,400,2026-02-02,2026-02-06,1250\n",
                encoding="utf-8",
            )
            files = find_tape_files([root])
            names = [path.name for path in files]
            self.assertNotIn("ops_stack_5k_trades.json", names)
            chosen = choose_tape(files, "expanded")
            self.assertTrue(chosen["ok"])
            self.assertTrue(str(chosen["path"]).endswith("universe_expand_movers_trades.csv"))
            self.assertEqual(len(chosen["trades"]), 1)
            self.assertEqual(chosen["trades"][0]["pnl_usd"], -25.0)
            self.assertNotIn("option_pnl_usd", chosen["trades"][0])


if __name__ == "__main__":
    unittest.main()
