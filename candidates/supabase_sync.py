"""
Sync Q-Alpha state to Supabase for the Streamlit dashboard.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
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

TSD_POSITION_FIELDS = (
    "symbol",
    "entry_date",
    "leg_opened_at",
    "entry_price",
    "shares",
    "kill_price",
    "current_price",
    "pnl_dollars",
    "pnl_pct",
    "status",
    "last_bar_time",
    "scan_score",
    "peak_high",
    "kill_pct",
    "trail_pct",
    "trading_day",
    "t4_only",
    "tranche_summary",
    "structure_stop",
    "rth_armed",
    "structure_stop_reason",
    "breakeven_locked",
    "tranche_json",
    "t1_trigger_price",
    "next_trail_stop",
    "launch_score",
    "phase",
    "pre_catalyst",
    "mfe_r",
    "last_updated",
)

TSD_CLOSED_LEG_FIELDS = (
    "symbol",
    "leg_opened_at",
    "entry_date",
    "entry_price",
    "shares",
    "exit_price",
    "exit_reason",
    "exit_layer",
    "pnl_dollars",
    "pnl_pct",
    "closed_at",
    "scan_score",
    "launch_score",
    "phase",
    "last_updated",
)

TSD_WATCHLIST_FIELDS = (
    "symbol",
    "rank",
    "scan_score",
    "trend_strength",
    "mfi",
    "buy_signal",
    "profiler_pass",
    "in_book",
    "trade_pick",
    "status_label",
    "entry_price",
    "kill_price",
    "launch_score",
    "phase",
    "wt_gap",
    "early_bull",
    "analog_count",
    "analog_win_rate",
    "pre_catalyst",
    "tags",
    "scan_at",
    "updated_at",
)

TSD_WATCH_QUEUE_FIELDS = (
    "symbol",
    "status",
    "signal_lane",
    "launch_score",
    "launch_score_display",
    "phase",
    "scan_score",
    "wt_gap",
    "cross_level",
    "early_bull",
    "buy_signal",
    "pre_catalyst",
    "analog_count",
    "analog_win_rate",
    "gates",
    "quality_gates",
    "tags",
    "size_mult",
    "news_summary",
    "catalyst_tier",
    "sentiment_score",
    "regime",
    "skip_reason",
    "added_at",
    "updated_at",
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

    def upsert_trade_marks_only(self, trade: dict) -> None:
        """
        Update unrealized P&L fields only — never touch status/shares.

        Used by Modal intraday_monitor so stale volume rows cannot re-OPEN
        a trade that local TWS sync already marked CLOSED in Supabase.
        Only applies when the Cloud row is still in an open ledger status.
        """
        ticker = str(trade.get("ticker") or "")
        entry_date = str(trade.get("entry_date") or "")
        if not ticker or not entry_date:
            return

        open_statuses = (
            "OPEN", "T1_HIT", "T2_HIT", "T3_TRAIL", "PENDING_MOC",
        )
        patch = {
            "current_price": trade.get("current_price"),
            "pnl_dollars": trade.get("pnl_dollars"),
            "pnl_pct": trade.get("pnl_pct"),
            "r_multiple": trade.get("r_multiple"),
            "dist_to_stop": trade.get("dist_to_stop"),
            "dist_to_target": trade.get("dist_to_target"),
            "last_updated": trade.get("last_updated") or datetime.now().isoformat(),
        }
        (
            self.client.table("trades")
            .update(patch)
            .eq("ticker", ticker)
            .eq("entry_date", entry_date)
            .in_("status", list(open_statuses))
            .execute()
        )

    def _upsert_strip_unknown(
        self,
        table: str,
        record: dict,
        *,
        on_conflict: str,
        max_strips: int = 12,
    ) -> None:
        """Upsert; on PGRST204 unknown-column, drop that key and retry."""
        import re

        payload = dict(record)
        for _ in range(max_strips):
            try:
                self.client.table(table).upsert(
                    payload, on_conflict=on_conflict
                ).execute()
                return
            except Exception as exc:
                err = str(exc)
                m = re.search(r"Could not find the '([^']+)' column", err)
                if "PGRST204" not in err or not m:
                    raise
                col = m.group(1)
                if col not in payload:
                    raise
                payload.pop(col, None)

    def upsert_tsd_position(self, row: dict) -> None:
        """Insert or update one TSD open leg (tsd_positions table)."""
        record = {field: row.get(field) for field in TSD_POSITION_FIELDS}
        record["symbol"] = str(row.get("symbol") or "").upper()
        record["entry_date"] = str(row.get("entry_date") or "")[:10]
        record["leg_opened_at"] = str(row.get("leg_opened_at") or "")
        record["status"] = str(row.get("status") or "OPEN").upper()
        record["last_updated"] = (
            row.get("last_updated") or datetime.now(timezone.utc).isoformat()
        )
        self._upsert_strip_unknown(
            "tsd_positions", record, on_conflict="symbol,leg_opened_at"
        )

    def get_tsd_positions(self, *, status: str = "OPEN") -> list:
        """Fetch TSD positions for dashboard (default: open legs only)."""
        q = self.client.table("tsd_positions").select("*")
        if status:
            q = q.eq("status", status.upper())
        result = q.order("symbol").execute()
        return result.data or []

    def prune_stale_tsd_positions(
        self,
        open_keys: list[tuple[str, str]],
    ) -> int:
        """
        Remove OPEN rows no longer in local book (flat / closed legs).
        open_keys: list of (symbol, leg_opened_at).
        """
        result = (
            self.client.table("tsd_positions")
            .select("symbol,leg_opened_at")
            .eq("status", "OPEN")
            .execute()
        )
        keep = {(str(s).upper(), str(l)) for s, l in open_keys}
        pruned = 0
        for row in result.data or []:
            key = (
                str(row.get("symbol") or "").upper(),
                str(row.get("leg_opened_at") or ""),
            )
            if key not in keep:
                (
                    self.client.table("tsd_positions")
                    .delete()
                    .eq("symbol", key[0])
                    .eq("leg_opened_at", key[1])
                    .execute()
                )
                pruned += 1
        return pruned

    def upsert_tsd_pool_snapshot(self, state: dict) -> None:
        """Save TSD pool snapshot (separate from gap pool_snapshots)."""
        snapshot = {
            "snapshot_date": str(state.get("snapshot_date") or date.today().isoformat()),
            "pool": float(state.get("pool") or 0),
            "deployed": float(state.get("deployed") or 0),
            "open_positions": int(state.get("open_positions") or 0),
            "open_names": int(state.get("open_names") or 0),
            "starting_pool": float(state.get("starting_pool") or 3000.0),
            "spy_regime": state.get("spy_regime"),
            "vix_regime": state.get("vix_regime") or "NORMAL",
            "sizing_pct": state.get("sizing_pct") or "100%",
            "last_updated": state.get("last_updated") or datetime.now(timezone.utc).isoformat(),
        }
        try:
            self.client.table("tsd_pool_snapshots").upsert(
                snapshot, on_conflict="snapshot_date"
            ).execute()
        except Exception as exc:
            # Live schema may lag ALTER for regime/sizing columns.
            err = str(exc)
            if "PGRST204" not in err and "sizing_pct" not in err and "spy_regime" not in err:
                raise
            core = {
                k: snapshot[k]
                for k in (
                    "snapshot_date",
                    "pool",
                    "deployed",
                    "open_positions",
                    "open_names",
                    "starting_pool",
                    "last_updated",
                )
            }
            self.client.table("tsd_pool_snapshots").upsert(
                core, on_conflict="snapshot_date"
            ).execute()

    def get_latest_tsd_pool(self) -> dict | None:
        """Most recent TSD pool snapshot."""
        result = (
            self.client.table("tsd_pool_snapshots")
            .select("*")
            .order("snapshot_date", desc=True)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        return rows[0] if rows else None

    def get_tsd_pool_history(self) -> list:
        result = (
            self.client.table("tsd_pool_snapshots")
            .select("*")
            .order("snapshot_date")
            .execute()
        )
        return result.data or []

    def replace_tsd_watchlist(self, rows: list[dict]) -> None:
        """Replace current TSD watch-10 (delete stale symbols, upsert rows)."""
        symbols = {str(r.get("symbol") or "").upper() for r in rows if r.get("symbol")}
        existing = self.client.table("tsd_watchlist").select("symbol").execute()
        for row in existing.data or []:
            sym = str(row.get("symbol") or "").upper()
            if sym and sym not in symbols:
                self.client.table("tsd_watchlist").delete().eq("symbol", sym).execute()
        if not rows:
            return
        clean = []
        for r in rows:
            item = {field: r.get(field) for field in TSD_WATCHLIST_FIELDS}
            item["symbol"] = str(r.get("symbol") or "").upper()
            item["updated_at"] = (
                r.get("updated_at") or datetime.now(timezone.utc).isoformat()
            )
            clean.append(item)
        try:
            self.client.table("tsd_watchlist").upsert(
                clean, on_conflict="symbol"
            ).execute()
        except Exception:
            # Strip unknown optional columns (analog_*, launch_score, …) and retry.
            for r in clean:
                self._upsert_strip_unknown("tsd_watchlist", r, on_conflict="symbol")

    def get_tsd_watchlist(self) -> list:
        result = (
            self.client.table("tsd_watchlist")
            .select("*")
            .order("rank")
            .execute()
        )
        return result.data or []

    def replace_tsd_watch_queue(self, rows: list[dict]) -> None:
        """Replace-all UTS v2 entry pipeline queue."""
        symbols = {str(r.get("symbol") or "").upper() for r in rows if r.get("symbol")}
        try:
            existing = self.client.table("tsd_watch_queue").select("symbol").execute()
            for row in existing.data or []:
                sym = str(row.get("symbol") or "").upper()
                if sym and sym not in symbols:
                    self.client.table("tsd_watch_queue").delete().eq("symbol", sym).execute()
        except Exception:
            pass
        if not rows:
            return
        clean = []
        for r in rows:
            item = {field: r.get(field) for field in TSD_WATCH_QUEUE_FIELDS}
            item["symbol"] = str(r.get("symbol") or "").upper()
            item["updated_at"] = (
                r.get("updated_at") or datetime.now(timezone.utc).isoformat()
            )
            clean.append(item)
        self.client.table("tsd_watch_queue").upsert(
            clean, on_conflict="symbol"
        ).execute()

    def get_tsd_watch_queue(self) -> list:
        try:
            result = (
                self.client.table("tsd_watch_queue")
                .select("*")
                .order("added_at", desc=True)
                .execute()
            )
            return result.data or []
        except Exception as exc:
            err = str(exc)
            if "PGRST205" in err or "tsd_watch_queue" in err:
                return []
            raise

    def upsert_tsd_closed_leg(self, row: dict) -> None:
        """Insert or update one completed TSD leg (tsd_closed_legs table)."""
        record = {field: row.get(field) for field in TSD_CLOSED_LEG_FIELDS}
        record["symbol"] = str(row.get("symbol") or "").upper()
        record["leg_opened_at"] = str(row.get("leg_opened_at") or "")
        record["last_updated"] = (
            row.get("last_updated") or datetime.now(timezone.utc).isoformat()
        )
        self.client.table("tsd_closed_legs").upsert(
            record, on_conflict="symbol,leg_opened_at"
        ).execute()

    def get_tsd_closed_legs(self) -> list:
        """All closed TSD legs, newest first."""
        try:
            result = (
                self.client.table("tsd_closed_legs")
                .select("*")
                .order("closed_at", desc=True)
                .execute()
            )
            return result.data or []
        except Exception as exc:
            err = str(exc)
            if "PGRST205" in err or "tsd_closed_legs" in err:
                return []
            raise

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

    def upsert_ticker_profile(self, profile: dict) -> None:
        """
        Insert/update one setup profile for Cloud Ticker Profiles.

        Service role only. Schema: candidates/sql/ticker_profiles.sql.
        """
        ticker = str(profile.get("ticker") or "").upper().strip()
        if not ticker:
            raise ValueError("upsert_ticker_profile: missing ticker")
        as_of = profile.get("as_of_date")
        if as_of is not None:
            as_of = str(as_of)[:10] or None
        row = {
            "ticker": ticker,
            "as_of_date": as_of,
            "profile": profile,
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }
        self.client.table("ticker_profiles").upsert(
            row, on_conflict="ticker"
        ).execute()


def upsert_ticker_profile_safe(profile: dict) -> bool:
    """
    Best-effort profile upsert. Logs and returns False on failure —
    never raises (morning agent / dashboard Refresh must not crash).
    """
    try:
        SupabaseSync().upsert_ticker_profile(profile)
        ticker = str(profile.get("ticker") or "").upper()
        print(f"  Supabase ticker_profiles upserted {ticker}")
        return True
    except Exception as exc:
        ticker = str((profile or {}).get("ticker") or "?")
        print(f"  ⚠️ Supabase ticker_profiles upsert failed ({ticker}): {exc}")
        return False


def _get_anon_credentials() -> tuple[str, str]:
    """URL + anon/publishable key for public dashboard reads (never service)."""
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


_anon_client = None
_anon_failed = False


def _get_anon_client():
    """Lazy anon supabase client (RLS applies)."""
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
                "(or SUPABASE_PUBLISHABLE_KEY) required for anon profile reads"
            )
        _anon_client = create_client(url, key)
        return _anon_client
    except Exception as exc:
        _anon_failed = True
        print(f"  ⚠️ anon Supabase unavailable for ticker_profiles: {exc}")
        return None


def fetch_ticker_profile_anon(ticker: str) -> dict | None:
    """
    Read one profile blob via anon key + RLS SELECT.
    Returns the profile dict, or None if missing / unavailable.
    """
    client = _get_anon_client()
    if client is None:
        return None
    t = str(ticker or "").upper().strip()
    if not t:
        return None
    try:
        result = (
            client.table("ticker_profiles")
            .select("ticker,as_of_date,profile,updated_at")
            .eq("ticker", t)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            return None
        blob = rows[0].get("profile")
        if not isinstance(blob, dict) or not blob:
            return None
        out = dict(blob)
        out.setdefault("ticker", t)
        out["_profile_source"] = "supabase"
        out["_profile_updated_at"] = rows[0].get("updated_at")
        return out
    except Exception as exc:
        print(f"  ⚠️ ticker_profiles anon fetch failed ({t}): {exc}")
        return None


def list_ticker_profile_tickers_anon() -> list[str]:
    """Tickers present in ticker_profiles (anon SELECT). Empty on failure."""
    client = _get_anon_client()
    if client is None:
        return []
    try:
        result = (
            client.table("ticker_profiles")
            .select("ticker")
            .order("ticker")
            .execute()
        )
        out: list[str] = []
        for row in result.data or []:
            t = str(row.get("ticker") or "").upper().strip()
            if t:
                out.append(t)
        return out
    except Exception as exc:
        print(f"  ⚠️ ticker_profiles list failed: {exc}")
        return []


def sync_to_supabase_safe(callback) -> bool:
    """Run a sync callback; never raise if Supabase is unavailable."""
    try:
        callback(SupabaseSync())
        return True
    except Exception as exc:
        print(f"  Supabase sync skipped: {exc}")
        return False


def sync_live_book_safe(
    trade: dict | None = None,
    pool_state: dict | None = None,
    *,
    label: str = "",
) -> bool:
    """
    Mid-day fill-truth sync: upsert trade and/or pool snapshot immediately.

    Call after successful fill booking and after reconcile_unfilled_opens so
    Cloud Cash/Deployed/opens match local pool_state without waiting for EOD.
    Fail-soft — never raises. Returns True when all requested upserts succeed.
    """
    tag = label
    if not tag and trade is not None:
        tag = (
            f"{trade.get('ticker')} entry={trade.get('entry_date')} "
            f"status={trade.get('status')}"
        )
    if not tag and pool_state is not None:
        tag = "pool_snapshot"

    def _run(sync: SupabaseSync) -> None:
        if trade is not None:
            sync.upsert_trade(trade)
            st = str(trade.get("status") or "").upper()
            if st == "CLOSED":
                print(
                    f"  >>> Supabase CLOSED {trade.get('ticker')} "
                    f"exit={trade.get('exit_price')} "
                    f"pnl=${float(trade.get('pnl_dollars') or 0):+.2f}"
                )
            else:
                print(
                    f"  Supabase upsert {trade.get('ticker')} "
                    f"status={trade.get('status')}"
                )
        if pool_state is not None:
            sync.upsert_pool_snapshot(pool_state)
            print("  Supabase pool snapshot upserted")

    ok = sync_to_supabase_safe(_run)
    if not ok:
        print(f"  *** Supabase SYNC FAILED [{tag}] ***")
    return ok
