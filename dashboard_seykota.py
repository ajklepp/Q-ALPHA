"""
Dashboard viewer — SEYKOTA daily trend paper book.

The strategy engine lives in the seykota-lab Cursor Origin repo. This module
only reads JSON under ``results/seykota/`` and draws it.
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

STRATEGY = "seykota"
ALIAS = "SEYKOTA"
ENV_VAR = "SEYKOTA_PAPER_BOOK"
REPO_RELATIVE = Path("results") / "seykota" / "paper_book.json"

# Separate Cursor Origin project. Q-ALPHA does not clone it and does not run it.
SEYKOTA_ORIGIN_URL = "https://cursor.com/codebase/aaron-klepp-alderson/seykota-lab"
SOURCE_NOTE = (
    "Source of truth: SEYKOTA Cursor Origin "
    f"[seykota-lab]({SEYKOTA_ORIGIN_URL}). "
    "Q-ALPHA does not run the daily trend engine and does not clone that repo. "
    "Publish paper state to `results/seykota/paper_book.json`, or set `SEYKOTA_PAPER_BOOK`."
)

RULE = (
    "SEYKOTA is a long-only daily trend paper book owned by seykota-lab. "
    "A long is allowed when price is above both EMA20 and EMA200, "
    "ADX confirms the trend, and size is set from ATR so the stop risks about "
    f"{PAPER_RISK_PER_TRADE_PCT:.0%} of equity. "
    f"Portfolio heat stays at or under {PAPER_MAX_HEAT_PCT:.0%}. "
    f"Starting equity is ${PAPER_STARTING_EQUITY_USD:,.0f}. "
    "This tab only displays the paper file."
)

OPEN_FIELDS = [
    ("Symbol", ("symbol", "ticker")),
    ("Entry", ("entry", "entry_price")),
    ("Qty", ("qty", "shares")),
    ("Stop", ("stop", "stop_price")),
    ("ATR", ("atr",)),
    ("EMA20", ("ema20",)),
    ("EMA200", ("ema200",)),
    ("ADX", ("adx",)),
    ("Opened", ("opened_et", "opened_at")),
]

SCHEMA_MD = """
seykota-lab writes this file. Q-ALPHA only displays it.

`results/seykota/paper_book.json`, or `SEYKOTA_PAPER_BOOK`.
Origin: https://cursor.com/codebase/aaron-klepp-alderson/seykota-lab

```json
{
  "version": 1,
  "strategy": "seykota",
  "mode": "PAPER",
  "side": "long_only",
  "updated_et": "2026-09-25T16:00:00-04:00",
  "risk_card": {"starting_equity_usd": 5000, "risk_per_trade_pct": 0.01, "max_heat_pct": 0.20},
  "rules": {"timeframe": "daily", "trend": "ema20_above_ema200", "filter": "adx", "sizing": "atr"},
  "equity_curve": [{"date": "2026-09-25", "equity_usd": 5000}],
  "open": [{
    "symbol": "ABC", "entry": 10.0, "qty": 40, "stop": 9.2,
    "atr": 0.4, "ema20": 9.8, "ema200": 8.1, "adx": 28, "opened_et": ""
  }],
  "closed": [{
    "symbol": "ABC", "entry": 10.0, "exit": 9.2,
    "pnl_usd": -32.0, "pnl_pct": -0.08, "reason": "stop", "closed_et": ""
  }],
  "totals": {
    "equity_usd": 5000, "heat_pct": 0.0, "realized_pnl_usd": 0,
    "n_open": 0, "n_closed": 0
  }
}
```

Entries are long only. Daily bars; heat is open risk divided by equity.
"""


def load_seykota_book() -> dict:
    """Load the SEYKOTA paper ledger (repo scaffold or `SEYKOTA_PAPER_BOOK`)."""
    return load_strategy_paper_book(
        env_var=ENV_VAR,
        repo_relative=REPO_RELATIVE,
        strategy=STRATEGY,
        alias=ALIAS,
    )


def render_seykota_tab() -> None:
    """Streamlit tab: display the SEYKOTA paper file. No strategy engine."""
    render_paper_strategy_tab(
        title="SEYKOTA",
        subtitle="Daily trend paper · viewer only",
        rule=RULE,
        book=load_seykota_book(),
        open_fields=OPEN_FIELDS,
        schema_md=SCHEMA_MD,
        source_note=SOURCE_NOTE,
    )
