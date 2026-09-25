"""Viewers for the PRO MIX, SEYKOTA, and LUCA'S STRATEGY paper files. No strategy engine."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard_luca_strategy import (  # noqa: E402
    LUCA_ORIGIN_PLACEHOLDER,
    SOURCE_NOTE as LUCA_SOURCE,
    load_luca_strategy_book,
)
from dashboard_pro_mix import (  # noqa: E402
    ALIAS,
    PROMIX_ORIGIN_URL,
    SOURCE_NOTE as PRO_MIX_SOURCE,
    load_pro_mix_book,
)
from dashboard_seykota import SEYKOTA_ORIGIN_URL, load_seykota_book  # noqa: E402
from dashboard_strategy_paper import (  # noqa: E402
    equity_points,
    fmt_heat,
    normalize_strategy_book,
    universe_rows,
)


class TestPaperBookViewer(unittest.TestCase):
    def test_repo_scaffolds_are_empty_paper_books(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PRO_MIX_PAPER_BOOK": "",
                "SEYKOTA_PAPER_BOOK": "",
                "LUCA_STRATEGY_PAPER_BOOK": "",
            },
            clear=False,
        ):
            pro = load_pro_mix_book()
            sey = load_seykota_book()
            luca = load_luca_strategy_book()
        self.assertTrue(pro["ok"])
        self.assertEqual(pro["mode"], "PAPER")
        self.assertEqual(pro["alias"], ALIAS)
        self.assertEqual(pro["open"], [])
        self.assertEqual(pro["closed"], [])
        self.assertEqual(pro["universe"], [])
        self.assertEqual(pro["totals"]["n_open"], 0)
        self.assertTrue(str(pro["path"]).endswith("results/pro_mix/paper_book.json"))
        self.assertTrue(sey["ok"])
        self.assertEqual(sey["strategy"], "seykota")
        self.assertTrue(str(sey["path"]).endswith("results/seykota/paper_book.json"))
        self.assertIn("seykota-lab", str(sey.get("source_project")))
        self.assertTrue(luca["ok"])
        self.assertEqual(luca["mode"], "PAPER")
        self.assertEqual(luca["strategy"], "luca_strategy")
        self.assertEqual(luca["alias"], "LUCA'S STRATEGY")
        self.assertEqual(luca["open"], [])
        self.assertEqual(luca["closed"], [])
        self.assertTrue(str(luca["path"]).endswith("results/luca_strategy/paper_book.json"))
        self.assertNotIn("PROMIX", str(luca.get("source_project")))
        self.assertIn("not published", str(luca.get("source_project")))

    def test_env_file_wins_over_scaffold(self) -> None:
        payload = {
            "strategy": "pro_mix",
            "alias": "LUCA'S STRATEGY",
            "updated_et": "2026-09-25T16:00:00-04:00",
            "universe": [{"symbol": "ABC", "heat": 1.4, "rank": 1}],
            "open": [{
                "symbol": "ABC",
                "entry": 10.0,
                "qty": 50,
                "stop": 9.5,
                "risk_usd": 50.0,
                "setup": "pullback",
            }],
            "closed": [{
                "symbol": "ABC",
                "entry": 10.0,
                "exit": 11.0,
                "pnl_usd": 50.0,
                "reason": "target",
            }],
            "totals": {"equity_usd": 5000},
            "equity_curve": [
                {"date": "2026-09-24", "equity_usd": 5000},
                {"date": "2026-09-25", "equity_usd": 5050},
                {"equity_usd": 1},
                "skip",
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paper_book.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with patch.dict(os.environ, {"PRO_MIX_PAPER_BOOK": str(path)}, clear=False):
                book = load_pro_mix_book()
        self.assertTrue(book["ok"])
        self.assertEqual(book["path"], str(path))
        self.assertEqual(book["open"][0]["setup"], "pullback")
        self.assertEqual(book["universe"][0]["symbol"], "ABC")
        self.assertAlmostEqual(book["totals"]["heat_pct"], 0.01)
        self.assertEqual(len(equity_points(book)), 2)

    def test_unreadable_env_file_does_not_fall_through(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paper_book.json"
            path.write_text("{", encoding="utf-8")
            with patch.dict(os.environ, {"SEYKOTA_PAPER_BOOK": str(path)}, clear=False):
                book = load_seykota_book()
        self.assertFalse(book["ok"])
        self.assertTrue(str(book["reason"]).startswith("unreadable:"))
        self.assertEqual(book["open"], [])

    def test_luca_env_file_wins_over_scaffold(self) -> None:
        payload = {
            "strategy": "luca_strategy",
            "alias": "LUCA'S STRATEGY",
            "mode": "PAPER",
            "updated_et": "2026-09-25T16:00:00-04:00",
            "open": [{
                "symbol": "XYZ",
                "entry": 20.0,
                "qty": 10,
                "stop": 19.0,
                "risk_usd": 10.0,
            }],
            "closed": [],
            "totals": {"equity_usd": 5000, "heat_pct": 0.0},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paper_book.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with patch.dict(os.environ, {"LUCA_STRATEGY_PAPER_BOOK": str(path)}, clear=False):
                book = load_luca_strategy_book()
        self.assertTrue(book["ok"])
        self.assertEqual(book["strategy"], "luca_strategy")
        self.assertEqual(book["alias"], "LUCA'S STRATEGY")
        self.assertEqual(book["mode"], "PAPER")
        self.assertEqual(book["path"], str(path))
        self.assertEqual(book["open"][0]["symbol"], "XYZ")
        self.assertEqual(book["totals"]["n_open"], 1)

    def test_missing_env_path_uses_repo_scaffold(self) -> None:
        missing = "/tmp/qalpha-missing-pro-mix-paper-book.json"
        with patch.dict(os.environ, {"PRO_MIX_PAPER_BOOK": missing}, clear=False):
            book = load_pro_mix_book()
        self.assertTrue(book["ok"])
        self.assertTrue(str(book["path"]).endswith("results/pro_mix/paper_book.json"))

    def test_string_universe_symbols_and_heat_format(self) -> None:
        rows = universe_rows({"shortlist": ["ABC", ""]})
        self.assertEqual(rows, [{"symbol": "ABC"}])
        self.assertEqual(fmt_heat(0.2), "20.0%")
        self.assertEqual(fmt_heat(20), "20.0%")
        book = normalize_strategy_book(
            {"open": [{"symbol": "ABC", "risk_usd": "nope"}]},
            path="x",
            strategy="pro_mix",
            alias="LUCA'S STRATEGY",
        )
        self.assertEqual(book["totals"]["n_open"], 1)
        self.assertIsNone(book["totals"]["heat_pct"])

    def test_dashboard_tabs_are_viewers_only(self) -> None:
        text = (ROOT / "dashboard.py").read_text(encoding="utf-8")
        match = re.search(r"st\.tabs\(\[(.*?)\]\)", text, re.S)
        self.assertIsNotNone(match)
        labels = re.findall(r'"([^"]+)"', match.group(1))
        self.assertEqual(
            labels,
            [
                "Live Status",
                "Track 100",
                "PRO MIX",
                "SEYKOTA",
                "LUCA'S STRATEGY",
                "3R Paper",
                "Trade Log",
                "Performance",
                "System Health",
                "Daily Reviews",
                "Weekly Review",
                "Glossary",
            ],
        )
        self.assertNotIn("Camillo", labels)
        self.assertFalse((ROOT / "dashboard_camillo.py").exists())
        for name in (
            "dashboard_pro_mix.py",
            "dashboard_seykota.py",
            "dashboard_luca_strategy.py",
            "dashboard_strategy_paper.py",
        ):
            src = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("ib_insync", src)
            self.assertNotIn("MarketOrder", src)
        self.assertIn(SEYKOTA_ORIGIN_URL, (ROOT / "dashboard_seykota.py").read_text(encoding="utf-8"))
        self.assertIn("LUCA'S STRATEGY", PRO_MIX_SOURCE)
        self.assertIn(PROMIX_ORIGIN_URL, PRO_MIX_SOURCE)
        luca_src = (ROOT / "dashboard_luca_strategy.py").read_text(encoding="utf-8")
        self.assertIn(LUCA_ORIGIN_PLACEHOLDER, luca_src)
        self.assertIn(LUCA_ORIGIN_PLACEHOLDER, LUCA_SOURCE)
        self.assertNotIn(PROMIX_ORIGIN_URL, luca_src)
        self.assertNotIn("aaron-klepp-alderson/PROMIX", luca_src)
        self.assertNotIn(SEYKOTA_ORIGIN_URL, luca_src)


class TestStrategyTabRender(unittest.TestCase):
    def test_empty_scaffolds_render(self) -> None:
        from streamlit.testing.v1 import AppTest

        pro = _run_tab(AppTest, "dashboard_pro_mix", "render_pro_mix_tab")
        sey = _run_tab(AppTest, "dashboard_seykota", "render_seykota_tab")
        luca = _run_tab(AppTest, "dashboard_luca_strategy", "render_luca_strategy_tab")
        self.assertFalse(list(pro.exception))
        self.assertFalse(list(sey.exception))
        self.assertFalse(list(luca.exception))

        pro_text = _visible_text(pro)
        self.assertIn("PAPER", pro_text)
        self.assertIn("LUCA'S STRATEGY", pro_text)
        self.assertIn("Heat shortlist pending", pro_text)
        self.assertIn("Cursor Origin", pro_text)
        self.assertIn(PROMIX_ORIGIN_URL, pro_text)
        self.assertIn("Live Peak Hour", pro_text)

        sey_text = _visible_text(sey)
        self.assertIn("PAPER", sey_text)
        self.assertIn("seykota-lab", sey_text)
        self.assertIn(SEYKOTA_ORIGIN_URL, sey_text)
        self.assertIn("Live Peak Hour", sey_text)

        luca_text = _visible_text(luca)
        self.assertIn("PAPER", luca_text)
        self.assertIn("LUCA'S STRATEGY", luca_text)
        self.assertIn("Paper book is empty", luca_text)
        self.assertIn(LUCA_ORIGIN_PLACEHOLDER, luca_text)
        self.assertIn("Live Peak Hour", luca_text)
        self.assertNotIn(PROMIX_ORIGIN_URL, luca_text)
        self.assertIn("does not send TWS orders", luca_text)


def _run_tab(app_test: object, module: str, fn: str) -> object:
    """Execute one viewer tab with this repo on sys.path."""
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        f"from {module} import {fn}\n"
        f"{fn}()\n"
    )
    return app_test.from_string(script).run(timeout=30)


def _visible_text(at: object) -> str:
    """Join markdown, captions, and info boxes from a Streamlit AppTest."""
    chunks: list[str] = []
    for attr in ("markdown", "caption", "info", "warning", "error", "subheader"):
        for el in getattr(at, attr, []) or []:
            chunks.append(str(getattr(el, "value", el)))
    return "\n".join(chunks)


if __name__ == "__main__":
    unittest.main()
