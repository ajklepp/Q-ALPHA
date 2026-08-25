"""
Q-Alpha universe safety filters.

Single source of truth for "is this symbol a $5-$50 US common stock, or is it a
fund / derivative product we must never trade?".

Deliberately dependency-free (stdlib + state_paths only — no modal, no requests)
so every layer can import it cheaply: the Modal scanner, the local autonomous
agent, the Telegram approval processor, and the IBKR connector.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

CANDIDATES_DIR = Path(__file__).resolve().parent
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

from state_paths import state_path

UNIVERSE_FILE = "universe.json"
# Scanner CS universe (Polygon type=CS, ~4.7k names). The order gate must use
# this file, not the old 300-name universe.json, or every full-market candidate
# fails membership even when it is a real common stock.
FULL_CS_UNIVERSE_CACHE = CANDIDATES_DIR / "full_scan" / "cs_universe_cache.json"

# Cached (mtime, {SYMBOL: {name, exchange}}) for the full CS universe cache.
_full_cs_cache: tuple[float, dict[str, dict]] | None = None

# ── Hand-maintained deny list ───────────────────────────────────────────────
# Symbols that must never be traded regardless of what Polygon reports about
# them. This is the emergency lever: add ONE SYMBOL PER LINE with a comment
# naming the product, then re-run refresh_universe().
EXCLUDE_SYMBOLS = frozenset({
    "NEBX",  # Defiance Daily Target 2X Long NBIS ETF
    "NBIG",  # GraniteShares 2x Long NBIS Daily ETF
    "NBIL",  # T-Rex 2X Long Nebius Daily Target ETF
    "NBIS",  # Nebius Group N.V. — denied by mandate (root of the 2X wrappers)
})

# ── Name-based fund detection ───────────────────────────────────────────────
# STRONG tokens: one of these words in a security name is proof the instrument
# is not an operating company. Verified against 300 real universe names plus 61
# look-alike common stocks with zero false positives.
FUND_NAME_TOKENS = frozenset({
    "ETF", "ETFS", "ETN", "ETNS", "ETP", "ETPS",
    "FUND", "FUNDS",
    "LEVERAGED", "INVERSE", "ULTRAPRO", "ULTRASHORT",
    "WARRANT", "WARRANTS",
    # "DEPOSITARY" alone false-positives ADRs ("American Depositary Shares").
    # Preferreds still caught via FUND_NAME_PHRASES "DEPOSITARY SHARE".
    "ACQUISITION", "ACQUISITIONS",  # pre-merger SPAC shells, units, warrants
    # Issuer brands that only ever appear on fund/derivative products. Asset
    # managers that are themselves tradable common stocks (Invesco/IVZ,
    # WisdomTree/WT, BlackRock/BLK, Franklin/BEN, Virtus/VRTS) are deliberately
    # ABSENT — their products always carry ETF/ETN/Fund/a multiplier anyway.
    "DIREXION", "PROSHARES", "GRANITESHARES", "VELOCITYSHARES",
    "ETRACS", "IPATH", "IPATHA", "MICROSECTORS", "SPDR", "XTRACKERS",
    "YIELDMAX", "ROUNDHILL", "TRADR", "DEFIANCE", "KURV",
})

# STRONG phrases: patterns that are only safe as multi-word matches. A bare
# TRUST / PREFERRED / UNIT would ban Northern Trust (NTRS), Preferred Bank
# (PFBC) and Unit Corporation (UNT), so the fund meaning is pinned down by the
# neighbouring word instead.
FUND_NAME_PHRASES = (
    "EXCHANGE TRADED", "EXCHANGE-TRADED",
    "CLOSED END", "CLOSED-END",
    "ROYALTY TRUST", "GRANTOR TRUST", "UNIT INVESTMENT TRUST",
    "BITCOIN TRUST", "ETHEREUM TRUST", "BULLION TRUST",
    "COMMODITY TRUST", "CURRENCY TRUST", "TERM TRUST",
    "PHYSICAL GOLD", "PHYSICAL SILVER",
    "PREFERRED STOCK", "PREFERRED SHARE", "CUMULATIVE PREFERRED",
    "PERPETUAL PREFERRED", "DEPOSITARY SHARE",
    "SUBORDINATED NOTES", "LINKED NOTES",
    "GLOBAL X", "T-REX", "LEVERAGE SHARES",
)

# WEAK hints: genuine fund words that also occur in genuine company names
# (Ultra Clean Holdings, Northern Trust, Unit Corp, Daily Journal, Target Corp,
# Preferred Bank, Index Industries). One hint proves nothing. Two hints in one
# name has never occurred in a legitimate operating company we tested.
WEAK_FUND_HINTS = frozenset({
    "TRUST", "SHARES", "UNIT", "UNITS", "INDEX", "PORTFOLIO",
    "ULTRA", "BULL", "BEAR", "DAILY", "LONG", "SHORT", "TARGET",
    "PREFERRED", "MUTUAL", "LEVERAGE", "HEDGED", "TRACKING",
})
MIN_WEAK_HINTS_TO_REJECT = 2  # 2 weak hints = fund; 1 = probably a real company

# Leverage multiplier in any form: 2X, 3x, -1X, 1.5X. Generalised from the
# literal "2X"/"3X"/"-1X" strings so a future 4X or 1.75X product is caught too.
LEVERAGE_MULTIPLIER_RE = re.compile(r"(?<![A-Z0-9])-?\d+(?:\.\d+)?X\b")

# Issuer families whose brand ends in "Shares" (iShares, ProShares,
# GraniteShares, LeverageShares...). BANCSHARES is excluded because dozens of
# bank holding companies are named "<Something> Bancshares, Inc." (HOMB, IBOC,
# SBSI, HBAN, SHBI).
ISSUER_SHARES_RE = re.compile(r"\b(?!BANCSHARES\b)[A-Z]+SHARES\b")

_WORD_RE = re.compile(r"[A-Z0-9]+")

# Cached (mtime, entries) so an entry loop pays one universe.json read.
_universe_cache: tuple[float, list[dict]] | None = None


def is_leveraged_or_fund(name: str) -> bool:
    """
    True when a Polygon security NAME describes a fund or derivative product.

    Name-based rather than symbol-based because the ticker of the next leveraged
    wrapper is unknowable, while its name always advertises what it is. An empty
    name returns False — there is nothing to judge — which is exactly why the
    symbol deny list and the CS-universe membership check exist alongside it.
    """
    if not name:
        return False
    upper = name.upper()
    if LEVERAGE_MULTIPLIER_RE.search(upper):
        return True
    if ISSUER_SHARES_RE.search(upper):
        return True
    # ADR operating companies use "American Depositary Shares" — not a fund.
    # Skip depositary phrases for those; preferred depositary shares still match
    # when "AMERICAN DEPOSITARY" is absent.
    is_adr_common = (
        "AMERICAN DEPOSITARY" in upper or "AMERICAN DEPOSITORY" in upper
    )
    for phrase in FUND_NAME_PHRASES:
        if phrase not in upper:
            continue
        if is_adr_common and "DEPOSITARY" in phrase:
            continue
        return True
    words = set(_WORD_RE.findall(upper))
    if words & FUND_NAME_TOKENS:
        return True
    return len(words & WEAK_FUND_HINTS) >= MIN_WEAK_HINTS_TO_REJECT


def load_universe_entries() -> list[dict]:
    """
    Read universe.json and return normalized {symbol, type, name} rows.

    Never calls Polygon — a gate must be cheap enough to run before every order.
    Legacy string-only universes are migrated in memory to type "CS" with an
    empty name, matching pre_market_scanner._normalize_universe().
    """
    global _universe_cache
    path = state_path(UNIVERSE_FILE)
    if not path.exists():
        path = CANDIDATES_DIR / UNIVERSE_FILE
    if not path.exists():
        return []

    mtime = path.stat().st_mtime
    if _universe_cache is not None and _universe_cache[0] == mtime:
        return _universe_cache[1]

    raw = json.loads(path.read_text(encoding="utf-8"))
    entries: list[dict] = []
    for entry in raw.get("tickers", []):
        if isinstance(entry, str):
            entries.append({"symbol": entry.upper(), "type": "CS", "name": ""})
        else:
            entries.append({
                "symbol": str(entry.get("symbol", "")).upper(),
                "type": entry.get("type", "CS"),
                "name": str(entry.get("name", "") or ""),
            })
    _universe_cache = (mtime, entries)
    return entries


def cs_universe_symbols() -> set[str]:
    """Tradable symbols: Polygon type CS, not denied, name not a fund product."""
    return {
        entry["symbol"] for entry in load_universe_entries()
        if entry["type"] == "CS"
        and entry["symbol"]
        and entry["symbol"] not in EXCLUDE_SYMBOLS
        and not is_leveraged_or_fund(entry["name"])
    }


def universe_name(symbol: str) -> str:
    """Polygon name for a symbol, or "" when the universe has no name for it."""
    symbol = (symbol or "").upper().strip()
    for entry in load_universe_entries():
        if entry["symbol"] == symbol:
            return entry["name"]
    return ""


def load_full_cs_universe() -> dict[str, dict]:
    """
    {SYMBOL: {name, exchange}} from full_market_scan's CS universe cache.

    Same source the scanner used to admit the candidate. Never reads
    universe.json. Empty dict if the cache is missing or unreadable.
    """
    global _full_cs_cache
    path = FULL_CS_UNIVERSE_CACHE
    if not path.exists():
        return {}
    mtime = path.stat().st_mtime
    if _full_cs_cache is not None and _full_cs_cache[0] == mtime:
        return _full_cs_cache[1]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    universe: dict[str, dict] = {}
    if isinstance(raw, dict):
        for sym, meta in raw.items():
            key = str(sym or "").upper()
            if not key:
                continue
            if isinstance(meta, dict):
                universe[key] = meta
            else:
                universe[key] = {"name": "", "exchange": ""}
    _full_cs_cache = (mtime, universe)
    return universe


# Instrument types IB may return that are never tradable Q-Alpha equity.
BANNED_STOCK_TYPES = frozenset({
    "RIGHT", "WAR", "WARRANT", "BOND", "CASH", "CMDTY", "FUT", "FOP",
    "OPT", "IOPT", "FWD", "BAG", "NEWS", "PREFERRED", "PREF",
})


def passes_instrument_safety(
    ticker: str,
    *,
    name: str = "",
    stock_type: str = "",
    require_cs_cache: bool = True,
) -> bool:
    """
    Fund/deny/stockType safety. Optionally require Polygon CS-cache membership.

    TWS-sourced names MUST call with require_cs_cache=False — CS membership
    re-blocks NYSE American names (PMI) that scanners surface correctly.
    """
    symbol = (ticker or "").upper().strip()
    if not symbol or symbol in EXCLUDE_SYMBOLS:
        print(f"BLOCKED: {symbol or ticker} failed instrument safety gate")
        return False

    st = (stock_type or "").upper().strip()
    if st in BANNED_STOCK_TYPES or st.startswith("WAR"):
        print(f"BLOCKED: {symbol} stockType={stock_type!r}")
        return False
    # ETF / ETN / fund wrappers from IB stockType
    if st in {"ETF", "ETN", "ETP", "FUND"}:
        print(f"BLOCKED: {symbol} stockType={stock_type!r}")
        return False

    resolved_name = (name or "").strip()
    if not resolved_name and require_cs_cache:
        cs_universe = load_full_cs_universe()
        meta = cs_universe.get(symbol) or {}
        resolved_name = str(meta.get("name") or "")

    if resolved_name and is_leveraged_or_fund(resolved_name):
        print(f"BLOCKED: {symbol} failed fund/name safety ({resolved_name[:48]!r})")
        return False

    if require_cs_cache:
        cs_universe = load_full_cs_universe()
        if resolved_name:
            return True
        if not cs_universe:
            print("  full CS universe cache missing or empty — refusing all entries")
            print(f"BLOCKED: {symbol} failed universe safety gate")
            return False
        if symbol not in cs_universe:
            print(f"BLOCKED: {symbol} failed universe safety gate")
            return False
    return True


def passes_universe_safety_gate(ticker: str) -> bool:
    """
    Last check before an order is placed anywhere in Q-Alpha.

    Blocks leveraged ETFs / funds / deny-list names using the SAME rules as
    full_market_scan (EXCLUDE_SYMBOLS + is_leveraged_or_fund). Does NOT require
    membership in the old 300-name universe.json — that check rejected every
    full-market CS candidate (NVAX, AG, PPC, ...).

    Name comes from the scanner CS cache. If the name is missing, membership
    in that cache is the fallback (fail closed if the cache is empty).

    For TWS-sourced candidates prefer passes_instrument_safety(..., require_cs_cache=False)
    with name/stock_type from the pipeline — do not use this CS-membership path.
    """
    return passes_instrument_safety(ticker, require_cs_cache=True)


def candidate_passes_safety(candidate: dict) -> bool:
    """
    Dispatch safety for agent watchlists.

    TWS pipeline rows carry source='tws_scan' (+ name/stock_type) and must NOT
    require CS-cache membership. Polygon full_market_scan rows keep the legacy gate.
    """
    if not isinstance(candidate, dict):
        return passes_universe_safety_gate(str(candidate))
    ticker = str(candidate.get("ticker") or "")
    source = str(candidate.get("source") or "")
    if source == "tws_scan" or candidate.get("skip_cs_cache"):
        return passes_instrument_safety(
            ticker,
            name=str(candidate.get("name") or ""),
            stock_type=str(candidate.get("stock_type") or ""),
            require_cs_cache=False,
        )
    return passes_universe_safety_gate(ticker)
