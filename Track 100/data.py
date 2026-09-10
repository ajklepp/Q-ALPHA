"""Polygon 1H bar fetch + disk cache for Track 100."""
from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytz
import requests

ET = pytz.timezone("America/New_York")
POLYGON_BASE = "https://api.polygon.io"
ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "results" / "bar_cache"
COST_PER_TRADE = 0.0015  # 0.15% round-trip proxy (same as Q-ALPHA experiments)


def load_polygon_key() -> str:
    key = os.environ.get("POLYGON_API_KEY")
    if key:
        return key.strip()
    env_path = ROOT.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("POLYGON_API_KEY") and "=" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("POLYGON_API_KEY not found")


def polygon_get(url: str, params: dict[str, Any], api_key: str, *, timeout: int = 45) -> dict:
    params = {**params, "apiKey": api_key}
    last_err: Exception | None = None
    for attempt in range(4):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last_err = exc
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"polygon_get failed: {last_err}")


def fetch_1h_bars(
    symbol: str,
    *,
    start: str,
    end: str,
    api_key: str | None = None,
    use_cache: bool = True,
) -> pd.DataFrame:
    """
    Fetch 1-hour aggs for [start, end] inclusive (YYYY-MM-DD).
    Cached under results/bar_cache/{SYM}_{start}_{end}.parquet (or csv fallback).
    """
    key = api_key or load_polygon_key()
    sym = symbol.upper()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{sym}_{start}_{end}_1h.parquet"
    csv_path = CACHE_DIR / f"{sym}_{start}_{end}_1h.csv"
    if use_cache and cache_path.exists():
        return pd.read_parquet(cache_path)
    if use_cache and csv_path.exists():
        df = pd.read_csv(csv_path, parse_dates=["ts"])
        df = df.set_index("ts").sort_index()
        return df

    url = f"{POLYGON_BASE}/v2/aggs/ticker/{sym}/range/1/hour/{start}/{end}"
    rows: list[dict[str, Any]] = []
    params: dict[str, Any] = {"adjusted": "true", "sort": "asc", "limit": 50000}
    while url:
        data = polygon_get(url, params if "cursor" not in url else {}, key)
        time.sleep(0.12)
        for row in data.get("results") or []:
            ms = int(row.get("t") or 0)
            if ms <= 0:
                continue
            ts = datetime.fromtimestamp(ms / 1000.0, tz=ET)
            c = float(row.get("c") or 0)
            if c <= 0:
                continue
            rows.append(
                {
                    "ts": ts,
                    "open": float(row.get("o") or c),
                    "high": float(row.get("h") or c),
                    "low": float(row.get("l") or c),
                    "close": c,
                    "volume": float(row.get("v") or 0),
                }
            )
        nxt = data.get("next_url")
        url = nxt if nxt else ""
        params = {}

    if not rows:
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df.index.name = "ts"
        return df

    df = pd.DataFrame(rows).drop_duplicates("ts").set_index("ts").sort_index()
    try:
        df.to_parquet(cache_path)
    except Exception:
        df.reset_index().to_csv(csv_path, index=False)
    return df


def study_window(*, end: date | None = None, months: int = 3, warmup_days: int = 45) -> tuple[str, str, str]:
    """
    Returns (fetch_start, signal_start, end) as YYYY-MM-DD.
    Signals counted only on [signal_start, end]; fetch includes warmup.
    """
    end_d = end or datetime.now(ET).date()
    signal_start = end_d - timedelta(days=int(months * 30.5))
    fetch_start = signal_start - timedelta(days=warmup_days)
    return fetch_start.isoformat(), signal_start.isoformat(), end_d.isoformat()


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
