"""
Shared helpers for the Q-ALPHA Streamlit dashboard (Home + pages).

No Streamlit page_config / main() side effects — safe to import from pages/*.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytz

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CANDIDATES_DIR = ROOT / "candidates"
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

from candidates.supabase_sync import SupabaseSync  # noqa: E402

PROFILES_DIR = ROOT / "profiles"
SYSTEM_VERSION = "1.3.6"
_SUPABASE_SYNC_API = "watchlist-v2"
# Match ticker_profiler.RR_WARN_THRESHOLD — target / safe_max_stop
RR_WARN_THRESHOLD = 1.5


def et_today() -> str:
    """Today's calendar date in America/New_York as YYYY-MM-DD."""
    return datetime.now(pytz.timezone("America/New_York")).date().isoformat()


def load_todays_watchlist(scan_date: str | None = None) -> list[dict]:
    """Fresh SupabaseSync watchlist for the given (or today ET) date."""
    day = scan_date or et_today()
    sync = SupabaseSync()
    return sync.get_watchlist(day)


def ensure_polygon_key_from_secrets() -> str | None:
    """
    Make POLYGON_API_KEY available for on-demand profile compute.
    Prefer env; fall back to st.secrets when running under Streamlit.
    Never print the key.
    """
    key = os.environ.get("POLYGON_API_KEY")
    if key:
        return key
    try:
        import streamlit as st
        key = st.secrets.get("POLYGON_API_KEY")
        if key:
            os.environ["POLYGON_API_KEY"] = str(key)
            return str(key)
    except Exception:
        pass
    return None


def profile_path(ticker: str) -> Path:
    """profiles/{TICKER}_profile.json"""
    return PROFILES_DIR / f"{ticker.upper()}_profile.json"


def load_profile(ticker: str) -> dict | None:
    """Load a precomputed profile JSON, or None if missing/corrupt."""
    path = profile_path(ticker)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _profile_insufficient(profile: dict) -> bool:
    """True if profile is ** / INSUFFICIENT (informational-only sample)."""
    if profile.get("history_flag") == "**":
        return True
    if profile.get("flag") == "INSUFFICIENT_HISTORY":
        return True
    if profile.get("confidence") == "INSUFFICIENT":
        return True
    finder = profile.get("analog_finder") or {}
    if finder.get("history_flag") == "**":
        return True
    if finder.get("flag") == "INSUFFICIENT_HISTORY":
        return True
    return False


def profile_history_flag(ticker: str) -> str:
    """Return history_flag from cached profile ('' | '*' | '**'), else ''."""
    try:
        profile = load_profile(ticker)
    except Exception:
        return ""
    if not profile:
        return ""
    flag = profile.get("history_flag")
    if flag is None:
        # Back-compat: derive from confidence / old flag
        if _profile_insufficient(profile):
            return "**"
        conf = profile.get("confidence")
        n = int(profile.get("analog_count") or profile.get("n_analogs_measured") or 0)
        if conf in ("MEDIUM", "LOW") or (0 < n < 10):
            return "*"
        return ""
    return str(flag)


def format_ticker_with_history(ticker: str) -> str:
    """Append history_flag: 'ABUS', 'USDE *', or 'XYZ **'."""
    t = str(ticker or "").upper().strip()
    if not t:
        return t
    flag = profile_history_flag(t)
    if flag:
        return f"{t} {flag}"
    return t


def profile_rr_unfavorable(ticker: str) -> bool:
    """
    True when Live Status should show ⚠️ next to the ticker:
      - unfavorable R:R (target < RR_WARN_THRESHOLD × safe_max_stop), OR
      - history_flag '**' / INSUFFICIENT
    Missing or unreadable profile → False (no icon, no crash).
    """
    try:
        profile = load_profile(ticker)
    except Exception:
        return False
    if not profile:
        return False
    try:
        if _profile_insufficient(profile):
            return True
        outcomes = profile.get("outcomes") or {}
        if outcomes.get("rr_warning"):
            return True
        rr = outcomes.get("reward_risk")
        if rr is not None and float(rr) < RR_WARN_THRESHOLD:
            return True
    except Exception:
        return False
    return False


def list_cached_profile_tickers() -> list[str]:
    """Tickers that already have profiles/{T}_profile.json on disk."""
    if not PROFILES_DIR.exists():
        return []
    out: list[str] = []
    for p in sorted(PROFILES_DIR.glob("*_profile.json")):
        name = p.stem
        if name.endswith("_profile"):
            out.append(name[: -len("_profile")].upper())
    return out


def compute_and_save_profile(ticker: str) -> dict:
    """
    Run ticker_profiler.build_ticker_profile and write profiles/{T}_profile.json.

    EXPENSIVE (many Polygon 1-min calls). Call only from an explicit button —
    never on dashboard load / autorefresh.
    """
    ensure_polygon_key_from_secrets()
    from ticker_profiler import build_ticker_profile, save_profile_json

    profile = build_ticker_profile(ticker.upper())
    save_profile_json(profile)
    return profile
