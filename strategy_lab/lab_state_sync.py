"""
strategy_lab/lab_state_sync.py — push/pull forward_state via Supabase.

WRITE (live_forward): service role via candidates.supabase_sync.SupabaseSync
  (SUPABASE_URL + SUPABASE_SECRET_KEY) — bypasses RLS.

READ (public dashboard Strategy Lab tab): anon key only
  (SUPABASE_URL + SUPABASE_ANON_KEY / SUPABASE_PUBLISHABLE_KEY) — RLS SELECT
  on strategy_lab_state only.

Best-effort: sync failures only warn; local forward_state.json remains source
of truth for the runner.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
LAB = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "candidates"))

from supabase_sync import SupabaseSync, _load_env  # noqa: E402

TABLE = "strategy_lab_state"
# Throttle mid-run pushes; final/eod always force-pushes.
PUSH_MIN_INTERVAL_SEC = 45.0

_last_push_monotonic: float = 0.0
_client: SupabaseSync | None = None
_client_failed = False
_anon_client: Any = None
_anon_failed = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_anon_credentials() -> tuple[str, str]:
    """
    Resolve URL + anon/publishable key for public read-only dashboard access.
    Never returns the service-role key.
    """
    try:
        import streamlit as st
        if hasattr(st, "secrets"):
            url = st.secrets.get("SUPABASE_URL")
            key = (
                st.secrets.get("SUPABASE_ANON_KEY")
                or st.secrets.get("SUPABASE_PUBLISHABLE_KEY")
            )
            if url and key:
                return str(url), str(key)
    except Exception:
        pass
    _load_env()
    url = os.environ.get("SUPABASE_URL", "")
    key = (
        os.environ.get("SUPABASE_ANON_KEY", "")
        or os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")
    )
    return url, key


def _get_anon_client() -> Any | None:
    """Lazy supabase client authenticated as anon (RLS applies)."""
    global _anon_client, _anon_failed
    if _anon_failed:
        return None
    if _anon_client is not None:
        return _anon_client
    try:
        from supabase import create_client

        url, key = _get_anon_credentials()
        if not url or not key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_ANON_KEY "
                "(or SUPABASE_PUBLISHABLE_KEY) must be set for dashboard reads"
            )
        _anon_client = create_client(url, key)
        return _anon_client
    except Exception as exc:
        _anon_failed = True
        print(f"[lab_state_sync] WARN: anon Supabase unavailable ({exc})")
        return None


def _row_to_state(row: dict[str, Any], *, source: str) -> dict[str, Any] | None:
    blob = row.get("state")
    if not isinstance(blob, dict) or not blob:
        return None
    out = dict(blob)
    out.setdefault("_lab_state_source", source)
    out.setdefault("_lab_state_flag_date", row.get("flag_date"))
    out.setdefault("_lab_state_updated_at", row.get("updated_at"))
    return out


def _get_sync() -> SupabaseSync | None:
    """Lazy SupabaseSync; cache None after hard credential failure."""
    global _client, _client_failed
    if _client_failed:
        return None
    if _client is not None:
        return _client
    try:
        _client = SupabaseSync()
        return _client
    except Exception as exc:
        _client_failed = True
        print(f"[lab_state_sync] WARN: Supabase unavailable ({exc})")
        return None


def reset_throttle() -> None:
    """Allow an immediate push (e.g. start of a new run_day)."""
    global _last_push_monotonic
    _last_push_monotonic = 0.0


def upsert_forward_state(
    state: dict[str, Any],
    *,
    force: bool = False,
) -> bool:
    """
    Upsert full state blob keyed by flag_date.

    Returns True if a push was attempted and succeeded.
    Throttled to ~PUSH_MIN_INTERVAL_SEC unless force=True (use at EOD).
    Never raises.
    """
    global _last_push_monotonic

    flag_date = str(state.get("flag_date") or "")[:10]
    if not flag_date:
        print("[lab_state_sync] WARN: skip upsert — missing flag_date")
        return False

    now = time.monotonic()
    if not force and (now - _last_push_monotonic) < PUSH_MIN_INTERVAL_SEC:
        return False

    sync = _get_sync()
    if sync is None:
        return False

    row = {
        "flag_date": flag_date,
        "state": state,
        "updated_at": state.get("updated_at") or _now_iso(),
    }
    try:
        (
            sync.client.table(TABLE)
            .upsert(row, on_conflict="flag_date")
            .execute()
        )
        _last_push_monotonic = now
        print(
            f"[lab_state_sync] upserted {TABLE} flag_date={flag_date} "
            f"(force={force})"
        )
        return True
    except Exception as exc:
        # Still advance throttle so a missing table / outage does not hammer API.
        _last_push_monotonic = now
        print(f"[lab_state_sync] WARN: upsert failed ({exc})")
        return False


def fetch_latest_forward_state() -> dict[str, Any] | None:
    """
    Service-role read (lab tooling). Dashboard must use
    fetch_latest_forward_state_anon() instead.
    """
    sync = _get_sync()
    if sync is None:
        return None
    try:
        result = (
            sync.client.table(TABLE)
            .select("flag_date, state, updated_at")
            .order("updated_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            return None
        return _row_to_state(rows[0], source="supabase_service")
    except Exception as exc:
        print(f"[lab_state_sync] WARN: fetch failed ({exc})")
        return None


def fetch_latest_forward_state_anon() -> dict[str, Any] | None:
    """
    Public dashboard read: anon key + RLS SELECT on strategy_lab_state only.
    Never uses the service-role key. Returns None on failure. Never raises.
    """
    client = _get_anon_client()
    if client is None:
        return None
    try:
        result = (
            client.table(TABLE)
            .select("flag_date, state, updated_at")
            .order("updated_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            return None
        return _row_to_state(rows[0], source="supabase_anon")
    except Exception as exc:
        print(f"[lab_state_sync] WARN: anon fetch failed ({exc})")
        return None


def fetch_forward_state_for_date(flag_date: str) -> dict[str, Any] | None:
    """Load one day's state by flag_date (service role). Never raises."""
    sync = _get_sync()
    if sync is None:
        return None
    day = str(flag_date)[:10]
    try:
        result = (
            sync.client.table(TABLE)
            .select("flag_date, state, updated_at")
            .eq("flag_date", day)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            return None
        return _row_to_state(rows[0], source="supabase_service")
    except Exception as exc:
        print(f"[lab_state_sync] WARN: fetch({day}) failed ({exc})")
        return None
