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

from candidates.supabase_sync import (  # noqa: E402
    SupabaseSync,
    fetch_ticker_profile_anon,
    list_ticker_profile_tickers_anon,
    upsert_ticker_profile_safe,
)

PROFILES_DIR = ROOT / "profiles"
SYSTEM_VERSION = "3.0"
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


def load_profile_local(ticker: str) -> dict | None:
    """Load profiles/{T}_profile.json only (no network)."""
    path = profile_path(ticker)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = dict(data)
            data["_profile_source"] = "local"
        return data
    except Exception:
        return None


def load_profile(ticker: str) -> dict | None:
    """
    Prefer Supabase ticker_profiles (anon), else local JSON.

    Sets ``_profile_source`` to ``supabase`` | ``local``. Returns None if missing.
    Never calls Polygon.
    """
    t = str(ticker or "").upper().strip()
    if not t:
        return None
    remote = fetch_ticker_profile_anon(t)
    if remote:
        return remote
    return load_profile_local(t)


def profile_source_label(profile: dict | None, ticker: str = "") -> str:
    """Human caption: supabase | local | missing."""
    if profile and profile.get("_profile_source") == "supabase":
        return "supabase"
    if profile and profile.get("_profile_source") == "local":
        return "local"
    if profile:
        # Legacy dict without marker — treat as local if file exists
        if ticker and profile_path(ticker).exists():
            return "local"
        return "supabase"
    if ticker and profile_path(ticker).exists():
        return "local"
    return "missing"


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


def format_profile_rr_cell(ticker: str) -> str:
    """
    Live Status R:R column text (display only):

      meaningful R:R >= 1.5 → "1.80"
      meaningful R:R < 1.5  → "0.74 ⚠️"
      insufficient / stats suppressed → "n/a"
      missing profile → "" (blank)

    Never show ⚠️ for insufficient profiles — that would imply bad R:R
    when the truth is unknown. The ticker's ** flag already marks no data.
    """
    try:
        profile = load_profile(ticker)
    except Exception:
        return ""
    if not profile:
        return ""
    try:
        if _profile_insufficient(profile):
            return "n/a"
        if profile.get("stats_meaningful") is False:
            return "n/a"
        outcomes = profile.get("outcomes") or {}
        rr = outcomes.get("reward_risk")
        if rr is None:
            return "n/a"
        rr_f = float(rr)
        text = f"{rr_f:.2f}"
        if rr_f < RR_WARN_THRESHOLD:
            return f"{text} ⚠️"
        return text
    except Exception:
        return ""


def profile_rr_unfavorable(ticker: str) -> bool:
    """
    True when a meaningful profile has R:R < RR_WARN_THRESHOLD.
    Insufficient / missing profile → False (not a bad-R:R warning).
    """
    cell = format_profile_rr_cell(ticker)
    return cell.endswith("⚠️")


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


def list_profile_option_tickers(
    watch_tickers: list[str] | None = None,
) -> list[str]:
    """
    Select-box options: today's watchlist ∪ remote Supabase ∪ local cache.
    Order preserved; first-seen wins.
    """
    options: list[str] = []
    seen: set[str] = set()
    for t in (watch_tickers or []) + list_ticker_profile_tickers_anon() + list_cached_profile_tickers():
        u = str(t or "").upper().strip()
        if u and u not in seen:
            options.append(u)
            seen.add(u)
    return options


def compute_and_save_profile(ticker: str) -> dict:
    """
    Run ticker_profiler.build_ticker_profile, write local JSON, upsert Supabase.

    EXPENSIVE (many Polygon 1-min calls). Call only from an explicit button —
    never on dashboard load / autorefresh. Supabase upsert fails soft.
    """
    ensure_polygon_key_from_secrets()
    from ticker_profiler import build_ticker_profile, save_profile_json

    profile = build_ticker_profile(ticker.upper())
    save_profile_json(profile)
    upsert_ticker_profile_safe(profile)
    profile = dict(profile)
    profile["_profile_source"] = "local"
    return profile
