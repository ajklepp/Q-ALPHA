"""
Entitled-venue L2 policy for the research logger (no BATS/BEX required).

WHY: Aaron's paper TWS (verified) has ARCA / NYSE / IEX depth and is missing
NASDAQ TotalView, BATS, and BEX. IB Error 2152 for those three is expected.
IB Error 309 caps concurrent reqMktDepth at 3. Pure functions so tests never
open TWS. This module does not send Telegram.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable

# IB paper account (2026-09): Error 309 — max concurrent market depth requests.
IB_ACCOUNT_DEPTH_CAP = 3
DEFAULT_DEPTH_MAX = 3

# Honest JSONL labels. Never "TotalView". SMART aggregate here is ARCA+NYSE+IEX
# only; NASDAQ/BATS/BEX are not entitled and are not required.
DEPTH_SOURCE_PARTIAL_SMART = "PARTIAL_ARCA_NYSE_IEX"
DEPTH_SOURCE_IEX_NATIVE = "IEX_NATIVE"
DEPTH_SOURCE_L1_ONLY = "L1_ONLY"
# Backward-compatible alias used by the first logger revision (understated ARCA/NYSE).
DEPTH_SOURCE_LEGACY_PARTIAL = "PARTIAL_IEX_SMART"

# Default production SMART-path label.
DEPTH_SOURCE = DEPTH_SOURCE_PARTIAL_SMART

# Error 2152: missing depth on these venues is EXPECTED until NASDAQ TotalView.
EXPECTED_MISSING_DEPTH_VENUES = frozenset({"NASDAQ", "BATS", "BEX"})
# Depth actually present on the paper account (do not require BATS/BEX).
ENTITLED_DEPTH_VENUES = frozenset({"ARCA", "NYSE", "IEX"})
# Listing venues whose primary book is more likely to show up in the entitled
# SMART aggregate. Used only to rank depth slots inside the L1 universe.
ENTITLED_LISTING_EXCHANGES = frozenset(
    {
        "NYSE",
        "ARCA",
        "AMEX",
        "NYSEARCA",
        "NYSEAMERICAN",
        "IEX",
        "ARCAPLUS",
    }
)

# Canonicalize IB venue tokens found in Error 2152 strings.
_VENUE_ALIASES: tuple[tuple[str, str], ...] = (
    ("NASDAQOMX", "NASDAQ"),
    ("NYSEAMERICAN", "AMEX"),
    ("NYSEARCA", "ARCA"),
    ("NASDAQ", "NASDAQ"),
    ("ISLAND", "NASDAQ"),
    ("NSDQ", "NASDAQ"),
    ("AMEX", "AMEX"),
    ("ARCA", "ARCA"),
    ("NYSE", "NYSE"),
    ("BATS", "BATS"),
    ("BZX", "BATS"),
    ("BYX", "BATS"),
    ("BEX", "BEX"),
    ("IEX", "IEX"),
    ("NMS", "NASDAQ"),
)

# Seconds to wait for SMART DOM before considering IEX-native fallback.
IEX_FALLBACK_WAIT_SEC = 2.0

# Informational IB codes (ib_insync already mostly ignores these).
IB_ERR_DEPTH_PERMISSION = 2152
IB_ERR_MAX_DEPTH = 309
IB_ERR_INFORMATIONAL = frozenset({2104, 2106, 2107, 2108, 2158})

_EXPECTED_2152_ONCE = (
    "EXPECTED IB 2152 (once/process): NASDAQ/BATS/BEX depth not entitled; "
    "ARCA/NYSE/IEX are. SMART book labeled PARTIAL_ARCA_NYSE_IEX. "
    "Not an alarm; not Telegram. Further 2152s suppressed."
)
_UNEXPECTED_2152_ONCE = (
    "IB 2152 mentioned an entitled venue (ARCA/NYSE/IEX) as missing — "
    "logged once. Still no BATS/BEX requirement; still no Telegram."
)
_ERROR_309_ONCE = (
    "IB 309 (once/process): max concurrent market depth reached. "
    "Logger caps reqMktDepth at --depth-max (default 3). Further 309s suppressed."
)


def parse_2152_venues(error_string: str | None) -> set[str]:
    """
    Extract canonical venue names from an IB Error 2152 message.

    WHY: distinguish expected NASDAQ/BATS/BEX gaps from a missing entitled venue.
    """
    text = str(error_string or "").upper()
    found: set[str] = set()
    for raw, canon in _VENUE_ALIASES:
        if re.search(rf"\b{re.escape(raw)}\b", text):
            found.add(canon)
    return found


def is_expected_2152(error_string: str | None) -> bool:
    """
    True if 2152 is the known paper gap (NASDAQ/BATS/BEX) or a generic SMART 2152.

    False if the message names an entitled venue (ARCA/NYSE/IEX) as missing.
    """
    venues = parse_2152_venues(error_string)
    if not venues:
        return True
    # Known paper gap — even if the same string also names entitled venues as present.
    if venues & EXPECTED_MISSING_DEPTH_VENUES:
        return True
    if venues & ENTITLED_DEPTH_VENUES:
        return False
    return True


def classify_depth_source(
    *,
    has_depth_sub: bool,
    iex_native: bool,
    saw_2152: bool = True,
) -> str:
    """
    Honest depth_source for one symbol.

    L1_ONLY — reqMktData only (beyond the depth-max cap).
    IEX_NATIVE — entitled IEX book, isSmartDepth=False.
    PARTIAL_ARCA_NYSE_IEX — SMART aggregate of entitled venues (2152 expected).
    """
    if not has_depth_sub:
        return DEPTH_SOURCE_L1_ONLY
    if iex_native:
        return DEPTH_SOURCE_IEX_NATIVE
    # SMART path on this account is always partial vs TotalView, 2152 or not.
    _ = saw_2152
    return DEPTH_SOURCE_PARTIAL_SMART


def is_entitled_listing(primary_exchange: str | None) -> bool:
    """True if the listing venue is one whose book we are entitled to."""
    ex = str(primary_exchange or "").strip().upper()
    return ex in ENTITLED_LISTING_EXCHANGES


def select_depth_symbols(
    symbols: list[str],
    depth_max: int,
    *,
    depth_symbols: list[str] | None = None,
    primary_exchange: dict[str, str] | None = None,
) -> list[str]:
    """
    Pick at most depth_max names for reqMktDepth (IB Error 309).

    Override (--depth-symbols) wins, still capped, still must be in `symbols`
    (L1/tape set). Otherwise prefer NYSE/ARCA/IEX listings, then original order.

    HOW to target levels_n>=5: pass names that actually delivered >=5 levels in
    JSONL as --depth-symbols. The listing heuristic is only a prior, not a probe.
    """
    n = max(0, int(depth_max))
    if n == 0:
        return []
    universe: list[str] = []
    seen: set[str] = set()
    for raw in symbols:
        sym = str(raw).strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        universe.append(sym)
    uni_set = set(universe)
    if not universe:
        return []

    if depth_symbols:
        out: list[str] = []
        got: set[str] = set()
        for raw in depth_symbols:
            sym = str(raw).strip().upper()
            if not sym or sym in got or sym not in uni_set:
                continue
            got.add(sym)
            out.append(sym)
            if len(out) >= n:
                break
        return out

    px = {str(k).upper(): str(v).upper() for k, v in (primary_exchange or {}).items()}
    index = {sym: i for i, sym in enumerate(universe)}

    def _key(sym: str) -> tuple[int, int]:
        preferred = 0 if is_entitled_listing(px.get(sym, "")) else 1
        return (preferred, index[sym])

    return sorted(universe, key=_key)[:n]


def should_try_iex_fallback(dom_bids: Any, dom_asks: Any) -> bool:
    """
    True when SMART DOM is empty (no priced levels).

    WHY: 2152 alone is not a reason to drop ARCA/NYSE — only a dead SMART book is.
    L1 bid/ask on the ticker do not count as depth.
    """
    def _has_level(rows: Any) -> bool:
        if not rows:
            return False
        for row in rows:
            try:
                px = float(getattr(row, "price", 0) or 0)
                sz = float(getattr(row, "size", 0) or 0)
            except (TypeError, ValueError):
                continue
            if px > 0 and sz >= 0:
                return True
        return False

    return not _has_level(dom_bids) and not _has_level(dom_asks)


def expected_2152_log_line() -> str:
    """Single process-level line for expected NASDAQ/BATS/BEX 2152."""
    return _EXPECTED_2152_ONCE


class ExpectedDepthErrorGate:
    """
    Rate-limit IB 2152 / 309 to one stdout line each per process.

    Never sends Telegram. Repeats are counted silently.
    """

    def __init__(self) -> None:
        self.count_2152 = 0
        self.count_309 = 0
        self.logged_expected_2152 = False
        self.logged_unexpected_2152 = False
        self.logged_309 = False

    def handle(
        self,
        code: int,
        error_string: str | None = None,
        contract: Any = None,
    ) -> str | None:
        """Return a line to print, or None if this event is suppressed."""
        if code in IB_ERR_INFORMATIONAL:
            return None
        if code == IB_ERR_DEPTH_PERMISSION:
            self.count_2152 += 1
            if is_expected_2152(error_string):
                if self.logged_expected_2152:
                    return None
                self.logged_expected_2152 = True
                extra = _symbol_hint(contract)
                return _EXPECTED_2152_ONCE + extra
            if self.logged_unexpected_2152:
                return None
            self.logged_unexpected_2152 = True
            extra = _symbol_hint(contract)
            detail = f" msg={str(error_string or '')[:180]}"
            return _UNEXPECTED_2152_ONCE + extra + detail
        if code == IB_ERR_MAX_DEPTH:
            self.count_309 += 1
            if self.logged_309:
                return None
            self.logged_309 = True
            return _ERROR_309_ONCE
        return None


def _symbol_hint(contract: Any) -> str:
    sym = getattr(contract, "symbol", None) if contract is not None else None
    if not sym:
        return ""
    return f" first_symbol={sym}"


def message_is_filtered_depth_error(msg: str) -> bool:
    """True if an ib_insync log line is 2152 or 309 (we emit our own once)."""
    text = msg or ""
    if "2152" in text:
        return True
    if "309" in text and ("depth" in text.lower() or "Error 309" in text or " 309," in text):
        return True
    return False


class IbDepthLogFilter(logging.Filter):
    """Drop ib_insync ERROR spam for expected 2152 / 309. Logger prints once."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        return not message_is_filtered_depth_error(msg)


_FILTER_INSTALLED = False


def install_ib_depth_log_filter() -> None:
    """Attach the 2152/309 filter to ib_insync loggers (once per process)."""
    global _FILTER_INSTALLED
    if _FILTER_INSTALLED:
        return
    filt = IbDepthLogFilter()
    for name in ("ib_insync", "ib_insync.wrapper", "ib_insync.client", "ib_insync.ib"):
        logging.getLogger(name).addFilter(filt)
    _FILTER_INSTALLED = True


def merge_l1_and_depth_symbols(
    l1_symbols: Iterable[str],
    depth_symbols: Iterable[str] | None,
) -> list[str]:
    """L1/tape set plus any --depth-symbols not already included (still unique)."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in list(l1_symbols) + list(depth_symbols or []):
        sym = str(raw).strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out
