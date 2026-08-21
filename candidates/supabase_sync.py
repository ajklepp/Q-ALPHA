"""
Sync Q-Alpha state to Supabase for the Streamlit dashboard.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
        return
    except ImportError:
        pass
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _get_credentials() -> tuple[str, str]:
    """Resolve Supabase credentials from Streamlit secrets or environment."""
    try:
        import streamlit as st
        if hasattr(st, "secrets"):
            url = st.secrets.get("SUPABASE_URL")
            key = st.secrets.get("SUPABASE_SECRET_KEY")
            if url and key:
                return str(url), str(key)
    except Exception:
        pass
    _load_env()
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SECRET_KEY", "")
    return url, key


TRADE_FIELDS = (
    "ticker",
    "entry_date",
    "entry_price",
    "position_size",  # dollars deployed (legacy NOT NULL; see upsert_trade)
    "stop_price",
    "target_1r",
    "target_2r",
    "shares_total",
    "status",
    "pnl_dollars",
    "pnl_pct",
    "exit_reason",
    "days_held",
    "execution_mode",
    "ibkr_order_id",
    "current_price",
    "r_multiple",
    "dist_to_stop",
    "dist_to_target",
    "last_updated",
)

# Columns written to the watchlist table. Keep in sync with
# candidates/sql/watchlist_schema.sql. Only these keys are sent so a missing
# optional column never triggers PGRST204 (same discipline as TRADE_FIELDS).
WATCHLIST_FIELDS = (
    "scan_date",
    "ticker",
    "rank",
    "gap_pct",
    "pm_vol_ratio",
    "score",
    "regime",
)


class SupabaseSync:
    """Read/write Q-Alpha dashboard data in Supabase."""

    def __init__(self):
        from supabase import create_client

        url, key = _get_credentials()
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SECRET_KEY must be set")
        self.client = create_client(url, key)

    def upsert_trade(self, trade: dict) -> None:
        """
        Insert or update a trade record.

        position_size is the DOLLAR value of capital deployed in the position
        (entry_price * shares), matching PoolManager.position_size() / experiment
        BracketPosition sizing — NOT a share count. The live trades table has
        position_size NOT NULL (legacy schema); never send null.
        """
        record = {field: trade.get(field) for field in TRADE_FIELDS}

        try:
            shares = int(trade.get("shares_total") or trade.get("shares") or 0)
        except (TypeError, ValueError):
            shares = 0
        record["shares_total"] = shares

        try:
            entry = float(trade.get("entry_price") or 0)
        except (TypeError, ValueError):
            entry = 0.0
        record["entry_price"] = entry

        # Prefer explicit dollar fields; else entry * shares. Never None.
        ps_raw = trade.get("position_size")
        if ps_raw is None:
            ps_raw = trade.get("position_value")
        try:
            position_size = float(ps_raw) if ps_raw is not None else entry * shares
        except (TypeError, ValueError):
            position_size = entry * shares
        if position_size is None:
            position_size = 0.0
        record["position_size"] = position_size

        # Harden other NOT NULL columns against missing keys.
        record["ticker"] = trade.get("ticker") or ""
        record["entry_date"] = trade.get("entry_date") or date.today().isoformat()

        self.client.table("trades").upsert(
            record, on_conflict="ticker,entry_date"
        ).execute()

    def upsert_pool_snapshot(self, pool_state: dict) -> None:
        """Save daily pool snapshot."""
        snapshot = {
            "snapshot_date": date.today().isoformat(),
            "pool": pool_state["pool"],
            "deployed": pool_state["deployed"],
            "open_positions": pool_state["open_positions"],
            "total_trades": pool_state["total_trades"],
            "winning_trades": pool_state["winning_trades"],
            "total_pnl": pool_state["pool"] - 3000.0,
        }
        self.client.table("pool_snapshots").upsert(
            snapshot, on_conflict="snapshot_date"
        ).execute()

    def upsert_scan(self, scan_result: dict) -> None:
        """Save daily scan results."""
        record = {
            "scan_date": scan_result["scan_date"],
            "spy_regime": scan_result["regime"]["spy_regime"],
            "vix_regime": scan_result["regime"]["vix_regime"],
            "spy_price": scan_result["regime"]["spy_price"],
            "candidates_count": scan_result["total_candidates"],
            "candidates_json": json.dumps(scan_result["candidates"]),
        }
        self.client.table("daily_scans").upsert(
            record, on_conflict="scan_date"
        ).execute()

    def upsert_watchlist(
        self,
        candidates: list[dict],
        scan_date: str,
        regime: str,
    ) -> None:
        """
        Replace the day's watchlist in Supabase so the dashboard shows it
        even when zero trades are placed.

        Deletes any existing rows for scan_date first (re-scans must not
        duplicate), then inserts one row per candidate. Only WATCHLIST_FIELDS
        are written — never invent columns the live table may lack.
        """
        day = str(scan_date)
        self.client.table("watchlist").delete().eq("scan_date", day).execute()
        if not candidates:
            return

        rows: list[dict] = []
        for i, cand in enumerate(candidates, start=1):
            gap = cand.get("gap_pct")
            if gap is None:
                gap = cand.get("gap_estimate")
            record = {
                "scan_date": day,
                "ticker": cand.get("ticker"),
                "rank": int(cand.get("rank") or i),
                "gap_pct": float(gap) if gap is not None else None,
                "pm_vol_ratio": (
                    float(cand["pm_vol_ratio"])
                    if cand.get("pm_vol_ratio") is not None else None
                ),
                "score": float(
                    cand.get("quality_score")
                    if cand.get("quality_score") is not None
                    else cand.get("score")
                    if cand.get("score") is not None
                    else 0.0
                ),
                "regime": regime,
            }
            rows.append({field: record.get(field) for field in WATCHLIST_FIELDS})

        self.client.table("watchlist").insert(rows).execute()

    def get_watchlist(self, scan_date: str | None = None) -> list:
        """
        Fetch watchlist rows for a scan_date (default: today ET calendar date
        as ISO string). Ordered by rank ascending.
        """
        day = scan_date or date.today().isoformat()
        result = (
            self.client.table("watchlist")
            .select("*")
            .eq("scan_date", day)
            .order("rank")
            .execute()
        )
        return result.data or []

    def log_health(self, component: str, status: str, message: str) -> None:
        """Log system component health."""
        self.client.table("system_health").insert({
            "component": component,
            "last_run": datetime.now().isoformat(),
            "status": status,
            "message": message,
        }).execute()

    def get_all_trades(self) -> list:
        result = self.client.table("trades").select("*").execute()
        return result.data or []

    def get_todays_trades(self, entry_date: str | None = None) -> list:
        """
        Trades whose entry_date matches the given day (default: today).
        Used by the dashboard to join watchlist tickers → live Status.
        """
        day = entry_date or date.today().isoformat()
        result = (
            self.client.table("trades")
            .select("*")
            .eq("entry_date", day)
            .execute()
        )
        return result.data or []

    def get_pool_history(self) -> list:
        result = (
            self.client.table("pool_snapshots")
            .select("*")
            .order("snapshot_date")
            .execute()
        )
        return result.data or []

    def get_recent_scans(self, days: int = 30) -> list:
        result = (
            self.client.table("daily_scans")
            .select("*")
            .order("scan_date", desc=True)
            .limit(days)
            .execute()
        )
        return result.data or []

    def get_system_health(self) -> list:
        result = (
            self.client.table("system_health")
            .select("*")
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )
        return result.data or []

    def get_last_health(self, component: str) -> dict | None:
        """Most recent health record for a component."""
        result = (
            self.client.table("system_health")
            .select("*")
            .eq("component", component)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        return rows[0] if rows else None

    def save_daily_review(self, review: dict) -> None:
        """Save daily review from Cursor automation."""
        self.client.table("daily_reviews").upsert(
            review,
            on_conflict="review_date",
        ).execute()

    def get_daily_reviews(self, limit: int = 30) -> list:
        """Fetch recent daily session reviews."""
        result = (
            self.client.table("daily_reviews")
            .select("*")
            .order("review_date", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []


def sync_to_supabase_safe(callback) -> None:
    """Run a sync callback; never raise if Supabase is unavailable."""
    try:
        callback(SupabaseSync())
    except Exception as exc:
        print(f"  Supabase sync skipped: {exc}")
