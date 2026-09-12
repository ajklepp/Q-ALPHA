"""
Named constants for the BSF options data bridge.

WHAT: loopback bind, TWS paper target, dedicated clientId 71, timeouts.
WHY: keep BSF off ib_insync and off reserved TWS clientIds used by Peak Hour.
"""
from __future__ import annotations

import os

# HTTP bind — loopback only. Never 0.0.0.0 (would expose TWS data off-box).
BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 8787

# Laptop TWS paper API. No cloud Gateway, no IBKR credentials in this process.
TWS_HOST = "127.0.0.1"
TWS_PORT = 7497

# Dedicated BSF options-bridge clientId. Documented in README. Do not reuse.
TWS_CLIENT_ID = 71

# Occupied by Peak Hour / TSD / probes / flatten / MD diagnostics.
RESERVED_CLIENT_IDS = frozenset(
    {1, 5, 75, 76, 85, 86, 88, 89, 93, 94, 95, 96, 97, 98, 99}
)

# BSF 3910x ranges — reserved for Best Strategy Finder's own ib_insync clients.
BSF_CLIENT_ID_RANGES = (
    range(3910, 3920),
    range(39100, 39200),
)

# Connect / request bounds — never hang forever when TWS is down.
CONNECT_TIMEOUT_SEC = 8.0
REQUEST_TIMEOUT_SEC = 20.0
RECONNECT_ATTEMPTS = 2
SNAPSHOT_WAIT_SEC = 1.25
INTER_QUOTE_SLEEP_SEC = 0.15
MAX_BODY_BYTES = 256 * 1024
MAX_QUALIFY_CONTRACTS = 40
MAX_BATCH_QUOTES = 20

# Historical defaults match Phase 9A (1h MIDPOINT, 10 D).
DEFAULT_BAR_SIZE = "1 hour"
DEFAULT_DURATION = "10 D"
DEFAULT_WHAT = "MIDPOINT"

ALLOWED_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})

# Order-like path fragments — any match is refused (403/405), no IB call.
ORDER_PATH_FRAGMENTS = (
    "order",
    "placeorder",
    "place_order",
    "cancel",
    "modify",
    "bracket",
    "whatif",
    "globalcancel",
    "exercise",
    "trade",
    "submit",
)

# POST routes that are read-only data helpers (not order entry).
ALLOWED_POST_PATHS = frozenset(
    {
        "/v1/options/qualify",
        "/v1/options/quotes",
        "/v1/phase9a/put_credit_snapshot",
    }
)

ALLOWED_GET_PATHS = frozenset(
    {
        "/v1/health",
        "/v1/underlying/quote",
        "/v1/options/chain",
        "/v1/options/quote",
        "/v1/options/hist",
    }
)


def env_int(name: str, default: int) -> int:
    """Parse an optional integer environment override."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def env_str(name: str, default: str) -> str:
    """Parse an optional string environment override."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


def is_reserved_client_id(client_id: int) -> bool:
    """True if clientId is reserved by Peak Hour / TSD / BSF 3910x ranges."""
    if int(client_id) in RESERVED_CLIENT_IDS:
        return True
    for rng in BSF_CLIENT_ID_RANGES:
        if int(client_id) in rng:
            return True
    return False


def validate_bind_host(host: str) -> str:
    """
    Refuse non-loopback binds.

    WHY: this process can see TWS paper market data; it must stay on the laptop.
    """
    value = (host or "").strip().lower()
    if value not in ALLOWED_LOOPBACK:
        raise ValueError(
            f"options_bridge must bind loopback only (got {host!r}). "
            "127.0.0.1 is required; 0.0.0.0 is forbidden."
        )
    # Normalize localhost → 127.0.0.1 so the socket is IPv4 paper-local.
    if value in {"localhost", "127.0.0.1"}:
        return "127.0.0.1"
    return host.strip()


def validate_client_id(client_id: int) -> int:
    """Refuse Peak Hour / BSF-reserved TWS clientIds."""
    cid = int(client_id)
    if is_reserved_client_id(cid):
        raise ValueError(
            f"clientId {cid} is reserved (Peak Hour / TSD / BSF 3910x). "
            f"options_bridge must use {TWS_CLIENT_ID}."
        )
    return cid
