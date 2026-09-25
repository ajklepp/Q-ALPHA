"""
Dashboard viewer — PRO MIX paper book (LUCA'S STRATEGY).

The strategy engine lives in its own Cursor Origin repo. This module only
reads JSON under ``results/pro_mix/`` and draws it. Camillo heat, pullback
entries, and the Seykota risk card are described here; they are not computed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from dashboard_strategy_paper import (
    PAPER_MAX_HEAT_PCT,
    PAPER_RISK_PER_TRADE_PCT,
    PAPER_STARTING_EQUITY_USD,
    display_path,
    load_strategy_paper_book,
    render_paper_strategy_tab,
)

UNIVERSE_ROW_LIMIT = 100  # names drawn before a "showing N of M" note
STRATEGY = "pro_mix"
ALIAS = "LUCA'S STRATEGY"
ENV_VAR = "PRO_MIX_PAPER_BOOK"
REPO_RELATIVE = Path("results") / "pro_mix" / "paper_book.json"

# Separate Cursor Origin project. Q-ALPHA does not clone it and does not run it.
SOURCE_NOTE = (
    "Source of truth: the **PRO MIX / LUCA'S STRATEGY** Cursor Origin repo "
    "(separate project, scaffolded on `main` — not this repository). "
    "Q-ALPHA does not run the heat filter, entries, or risk engine, and it does not clone that repo. "
    "Publish paper state to `results/pro_mix/paper_book.json`, or set `PRO_MIX_PAPER_BOOK`."
)

RULE = (
    "PRO MIX (LUCA'S STRATEGY) is a long-only paper book owned by its Cursor Origin repo. "
    "That book combines a Camillo-style heat/universe filter, pullback or break-retest entries "
    "(breakout chases stay off the book), and the Seykota risk card: "
    f"${PAPER_STARTING_EQUITY_USD:,.0f} starting equity, "
    f"{PAPER_RISK_PER_TRADE_PCT:.0%} of equity at risk on each new long, "
    f"and open heat capped at {PAPER_MAX_HEAT_PCT:.0%} of equity. "
    "This tab only displays the paper file."
)

OPEN_FIELDS = [
    ("Symbol", ("symbol", "ticker")),
    ("Entry", ("entry", "entry_price")),
    ("Qty", ("qty", "shares")),
    ("Stop", ("stop", "stop_price")),
    ("Risk $", ("risk_usd",)),
    ("Setup", ("setup", "entry_setup")),
    ("Opened", ("opened_et", "opened_at")),
]

SCHEMA_MD = """
The PRO MIX Origin repo writes this file. Q-ALPHA reads it.

`results/pro_mix/paper_book.json`, or `PRO_MIX_PAPER_BOOK`.

```json
{
  "version": 1,
  "strategy": "pro_mix",
  "alias": "LUCA'S STRATEGY",
  "mode": "PAPER",
  "side": "long_only",
  "updated_et": "2026-09-25T16:00:00-04:00",
  "risk_card": {"starting_equity_usd": 5000, "risk_per_trade_pct": 0.01, "max_heat_pct": 0.20},
  "universe": [{"symbol": "ABC", "heat": 1.4, "trend": "new", "rank": 1}],
  "equity_curve": [{"date": "2026-09-25", "equity_usd": 5000}],
  "open": [{
    "symbol": "ABC", "entry": 10.0, "qty": 50, "stop": 9.5,
    "risk_usd": 25.0, "setup": "pullback", "opened_et": ""
  }],
  "closed": [{
    "symbol": "ABC", "entry": 10.0, "exit": 11.0,
    "pnl_usd": 50.0, "pnl_pct": 0.10, "reason": "target", "closed_et": ""
  }],
  "totals": {
    "equity_usd": 5000, "heat_pct": 0.0, "realized_pnl_usd": 0,
    "n_open": 0, "n_closed": 0
  }
}
```

`setup` is `pullback` or `break_retest`. Entries are long only.
`universe` is the Camillo heat list the Origin repo already built.
"""


def load_pro_mix_book() -> dict:
    """Load the PRO MIX paper ledger (repo file or `PRO_MIX_PAPER_BOOK`)."""
    return load_strategy_paper_book(
        env_var=ENV_VAR,
        repo_relative=REPO_RELATIVE,
        strategy=STRATEGY,
        alias=ALIAS,
    )


def render_pro_mix_tab() -> None:
    """Streamlit tab: display the PRO MIX paper file. No strategy engine."""
    book = load_pro_mix_book()
    render_paper_strategy_tab(
        title="PRO MIX",
        subtitle="LUCA'S STRATEGY",
        rule=RULE,
        book=book,
        open_fields=OPEN_FIELDS,
        schema_md=SCHEMA_MD,
        source_note=SOURCE_NOTE,
        after_open_book=lambda: _render_heat_universe(book),
    )


def _render_heat_universe(book: dict[str, Any]) -> None:
    """Show heat-list rows stored on the paper book. Does not screen tickers."""
    import pandas as pd
    import streamlit as st

    rows = _order_universe_rows(list(book.get("universe") or []))
    st.subheader("Camillo heat universe")
    st.caption(
        "Names on this paper file, written by the PRO MIX Origin repo. "
        f"Reading `{display_path(book.get('path'))}`."
    )
    if rows:
        shown = rows[:UNIVERSE_ROW_LIMIT]
        st.dataframe(pd.DataFrame(shown), use_container_width=True, hide_index=True)
        if len(rows) > len(shown):
            st.caption(f"Showing {len(shown)} of {len(rows)} names.")
        return
    st.info(
        "Heat shortlist pending. The PRO MIX repo fills `universe` on "
        "`results/pro_mix/paper_book.json`. This dashboard does not build that list."
    )


def _order_universe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Put symbol, rank, and heat first. Keep every other writer field."""
    preferred = (
        "symbol",
        "ticker",
        "rank",
        "heat",
        "heat_score",
        "score",
        "trend",
        "trend_state",
        "sector",
        "notes",
    )
    ordered: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item: dict[str, Any] = {}
        for key in preferred:
            if key in row:
                item[key] = row.get(key)
        for key, val in row.items():
            if key in item or str(key).startswith("_"):
                continue
            item[key] = val
        ordered.append(item)
    return ordered
