"""
Named constants for the research-only L2 + tape logger.

WHY: magic numbers stay documented in one place. Paper TWS only.
clientId 72 is dedicated — never collide with Peak Hour / options bridge.
"""
from __future__ import annotations

from datetime import time
from pathlib import Path

# --- Isolation / connection (paper TWS only) ---------------------------------
TWS_HOST = "127.0.0.1"
TWS_PAPER_PORT = 7497
TWS_LIVE_PORT = 7496  # refused — logger never targets live
TWS_CLIENT_ID = 72

# Occupied / reserved IDs this logger must never use.
FORBIDDEN_CLIENT_IDS = frozenset(
    {1, 5, 71, 75, 76, 85, 86, 88, 89} | set(range(93, 100))
)

# Bound IB waits (seconds). Same lesson as the options bridge: never hang
# the main thread on ib.sleep; schedule on the IB worker-loop thread.
IB_CONNECT_TIMEOUT_SEC = 12.0
IB_CALL_TIMEOUT_SEC = 10.0
IB_QUALIFY_TIMEOUT_SEC = 8.0
IB_SUBSCRIBE_TIMEOUT_SEC = 8.0
IB_DISCONNECT_TIMEOUT_SEC = 5.0
IB_WORKER_READY_TIMEOUT_SEC = 20.0

# --- Depth honesty ------------------------------------------------------------
# Paper SMART depth is IEX/smart-routed and often incomplete (IB Error 2152
# missing NASDAQ/BATS/ARCA/NYSE). Never claim TotalView / full book.
DEPTH_SOURCE = "PARTIAL_IEX_SMART"
DEPTH_ROWS = 5
SMART_DEPTH = True

# --- Cadence / windows --------------------------------------------------------
BOOK_SNAPSHOT_SEC = 1.0
TAPE_POLL_SEC = 0.1
PRINT_VWAP_WINDOW_SEC = 5.0
PRINT_IMB_WINDOW_SEC = 5.0
UPTICK_WINDOW_SEC = 30.0
BOOK_DELTA_1S = 1.0
BOOK_DELTA_5S = 5.0
QUOTE_FLICKER_WINDOW_SEC = 5.0
TAPE_BURST_BUCKET_SEC = 5.0
TAPE_BURST_BASELINE_SEC = 20 * 60
# Warm-up: need this many 5s buckets before tape_burst_z is defined.
TAPE_BURST_MIN_BUCKETS = 12  # 60s; full 20m baseline fills after that
LARGE_PRINT_K = 5.0
LARGE_PRINT_MEDIAN_MIN_N = 5
PRINT_MEDIAN_LOOKBACK_SEC = 20 * 60
PRINT_MEDIAN_MAX_N = 400

# --- Session (US/Eastern) -----------------------------------------------------
PRE_START = time(4, 0)
RTH_START = time(9, 30)
RTH_END = time(16, 0)
POST_END = time(20, 0)
IDLE_POLL_SEC = 15.0

# --- Universe -----------------------------------------------------------------
DEFAULT_TOP_N = 8
# L2/depth probes: IB paper often fails when too many reqMktDepth at once.
# --once / --probe must stay tiny (1–3). Continuous logger may still use top 8.
PROBE_MAX_SYMBOLS = 3
PROBE_DEFAULT_SYMBOLS = ("SPY",)
# Hard ceiling for any depth subscribe path (never Cap-scale / 100+ names).
ABSOLUTE_MAX_DEPTH_SYMBOLS = 8
FALLBACK_SYMBOLS = (
    "SPY",
    "QQQ",
    "IWM",
    "AAPL",
    "MSFT",
    "NVDA",
    "TSLA",
    "AMD",
)
FALLBACK_REASON = (
    "Peak Hour watchlist artifacts empty or missing — using default liquid "
    "ETFs/stocks for dry structure only (not a Peak Hour signal)."
)

# Artifact paths relative to repo root (read-only JSON; no TSD imports).
LAUNCH_ARTIFACT = Path("candidates/tsd_scan_pipeline/results/last_1h_launch.json")
QUEUE_ARTIFACT = Path("candidates/tsd_watch_queue.json")
WATCHLIST_ARTIFACT = Path("candidates/tsd_scan_pipeline/results/last_watchlist.json")

# --- Output -------------------------------------------------------------------
JSONL_ROOT_REL = Path("logs/microstructure")
SOURCE_L2 = "qalpha_l2"
SOURCE_TAPE = "qalpha_tape"

# TEST-01 hard schema — every JSONL row must include these keys (nulls OK).
TEST01_REQUIRED_KEYS = (
    "ts_utc",
    "symbol",
    "bid1",
    "ask1",
    "bid_sz1",
    "ask_sz1",
    "mid",
    "imbalance_l1",
    "spread_bps",
    "tape_burst_z",
    "print_imb_5s",
)

# Full A+B row keys (identity + book + tape + provenance).
ROW_KEYS = (
    "ts_utc",
    "symbol",
    "session_tag",
    "bid1",
    "ask1",
    "bid_sz1",
    "ask_sz1",
    "mid",
    "microprice",
    "spread_bps",
    "imbalance_l1",
    "imbalance_l3",
    "book_pressure_delta_1s",
    "book_pressure_delta_5s",
    "quote_flicker",
    "print_vwap_5s",
    "print_imb_5s",
    "large_print_flag",
    "uptick_ratio_30s",
    "tape_burst_z",
    "source",
    "depth_source",
    "levels_n",
)
