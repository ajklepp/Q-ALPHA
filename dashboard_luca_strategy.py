"""
Dashboard viewer — LUCA'S STRATEGY paper book.

The strategy engine will live in its own Cursor Origin repo. This module only
reads JSON under ``results/luca_strategy/`` and draws it. It does not place
TWS orders and it does not run PRO MIX or SEYKOTA.
"""
from __future__ import annotations

from pathlib import Path

from dashboard_strategy_paper import (
    PAPER_MAX_HEAT_PCT,
    PAPER_RISK_PER_TRADE_PCT,
    PAPER_STARTING_EQUITY_USD,
    load_strategy_paper_book,
    render_paper_strategy_tab,
)

STRATEGY = "luca_strategy"
ALIAS = "LUCA'S STRATEGY"
ENV_VAR = "LUCA_STRATEGY_PAPER_BOOK"
REPO_RELATIVE = Path("results") / "luca_strategy" / "paper_book.json"

# Placeholder until that Origin repo is published. Not the PROMIX project.
LUCA_ORIGIN_PLACEHOLDER = (
    "Origin link pending — the LUCA'S STRATEGY repo is not published yet. "
    "Replace this text with the Cursor Origin URL when it is."
)
SOURCE_NOTE = (
    "Source of truth: LUCA'S STRATEGY Cursor Origin repo. "
    f"{LUCA_ORIGIN_PLACEHOLDER} "
    "Q-ALPHA does not run this strategy and does not clone that repo. "
    "Publish paper state to `results/luca_strategy/paper_book.json`, "
    "or set `LUCA_STRATEGY_PAPER_BOOK`."
)

RULE = (
    "LUCA'S STRATEGY is a long-only paper book owned by its Cursor Origin repo. "
    "That repo is not published yet. When the writer omits a risk card, this "
    f"viewer uses ${PAPER_STARTING_EQUITY_USD:,.0f} starting equity, "
    f"{PAPER_RISK_PER_TRADE_PCT:.0%} of equity at risk on each new long, "
    f"and open heat capped at {PAPER_MAX_HEAT_PCT:.0%} of equity. "
    "Status is PAPER. This tab only displays the paper file and does not send TWS orders."
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
The LUCA'S STRATEGY Origin repo writes this file. Q-ALPHA only displays it.
This is not the PRO MIX book and it is not `results/pro_mix/paper_book.json`.

`results/luca_strategy/paper_book.json`, or `LUCA_STRATEGY_PAPER_BOOK`.
Origin: not published yet. Update the placeholder in `dashboard_luca_strategy.py`
when the new repo URL exists. Do not point this tab at PROMIX.

```json
{
  "version": 1,
  "strategy": "luca_strategy",
  "alias": "LUCA'S STRATEGY",
  "mode": "PAPER",
  "side": "long_only",
  "updated_et": "2026-09-25T16:00:00-04:00",
  "risk_card": {"starting_equity_usd": 5000, "risk_per_trade_pct": 0.01, "max_heat_pct": 0.20},
  "equity_curve": [{"date": "2026-09-25", "equity_usd": 5000}],
  "open": [{
    "symbol": "ABC", "entry": 10.0, "qty": 50, "stop": 9.5,
    "risk_usd": 25.0, "setup": "", "opened_et": ""
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

Entries are long only. Status stays PAPER. This dashboard does not send TWS orders.
"""


def load_luca_strategy_book() -> dict:
    """Load the LUCA'S STRATEGY paper ledger (repo file or `LUCA_STRATEGY_PAPER_BOOK`)."""
    return load_strategy_paper_book(
        env_var=ENV_VAR,
        repo_relative=REPO_RELATIVE,
        strategy=STRATEGY,
        alias=ALIAS,
    )


def render_luca_strategy_tab() -> None:
    """Streamlit tab: display the LUCA'S STRATEGY paper file. No strategy engine."""
    render_paper_strategy_tab(
        title="LUCA'S STRATEGY",
        subtitle="Paper viewer only",
        rule=RULE,
        book=load_luca_strategy_book(),
        open_fields=OPEN_FIELDS,
        schema_md=SCHEMA_MD,
        source_note=SOURCE_NOTE,
    )
