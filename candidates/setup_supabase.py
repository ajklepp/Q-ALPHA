"""
Create Q-Alpha Supabase tables (run once).

Usage:
    pip install supabase psycopg2-binary python-dotenv
    python candidates/setup_supabase.py

Requires .env:
    SUPABASE_URL
    SUPABASE_SECRET_KEY
    SUPABASE_PASSWORD   (database password for DDL)
    SUPABASE_PROJECT    (optional if derivable from SUPABASE_URL)
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

CANDIDATES_DIR = Path(__file__).resolve().parent
ROOT = CANDIDATES_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    id SERIAL PRIMARY KEY,
    ticker TEXT NOT NULL,
    entry_date TEXT NOT NULL,
    entry_price FLOAT,
    stop_price FLOAT,
    target_1r FLOAT,
    target_2r FLOAT,
    shares_total INT,
    status TEXT,
    pnl_dollars FLOAT DEFAULT 0,
    pnl_pct FLOAT DEFAULT 0,
    exit_reason TEXT,
    days_held INT DEFAULT 0,
    execution_mode TEXT,
    ibkr_order_id INT,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (ticker, entry_date)
);

CREATE TABLE IF NOT EXISTS pool_snapshots (
    id SERIAL PRIMARY KEY,
    snapshot_date TEXT NOT NULL UNIQUE,
    pool FLOAT,
    deployed FLOAT,
    open_positions INT,
    total_trades INT,
    winning_trades INT,
    total_pnl FLOAT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS daily_scans (
    id SERIAL PRIMARY KEY,
    scan_date TEXT NOT NULL UNIQUE,
    spy_regime TEXT,
    vix_regime TEXT,
    spy_price FLOAT,
    candidates_count INT,
    candidates_json TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS system_health (
    id SERIAL PRIMARY KEY,
    component TEXT,
    last_run TEXT,
    status TEXT,
    message TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
"""


def _project_ref() -> str:
    project = os.environ.get("SUPABASE_PROJECT", "").strip()
    if project:
        return project
    url = os.environ.get("SUPABASE_URL", "")
    match = re.search(r"https://([^.]+)\.supabase\.co", url)
    if match:
        return match.group(1)
    raise RuntimeError("Set SUPABASE_PROJECT or SUPABASE_URL in .env")


def _database_url() -> str:
    explicit = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if explicit:
        return explicit
    password = os.environ.get("SUPABASE_PASSWORD")
    if not password:
        raise RuntimeError("SUPABASE_PASSWORD or DATABASE_URL required for table creation")
    ref = _project_ref()
    return f"postgresql://postgres:{password}@db.{ref}.supabase.co:5432/postgres"


def _verify_supabase_client() -> None:
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SECRET_KEY required in .env")
    create_client(url, key)
    print("Supabase client credentials OK")


def _execute_schema() -> None:
    import psycopg2

    conn = psycopg2.connect(_database_url())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        print("Schema applied successfully.")
    finally:
        conn.close()


def main() -> None:
    print("=" * 55)
    print("  Q-ALPHA | Supabase Setup")
    print("=" * 55)
    _load_env()
    _verify_supabase_client()
    _execute_schema()
    print("Done. Tables: trades, pool_snapshots, daily_scans, system_health")


if __name__ == "__main__":
    main()
