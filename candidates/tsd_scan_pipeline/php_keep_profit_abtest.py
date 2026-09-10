"""
A/B exit test: same entries as range replay, Peak Hour keep-profit exits.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytz

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
ROOT = CANDIDATES_DIR.parent
sys.path.insert(0, str(CANDIDATES_DIR))
sys.path.insert(0, str(ROOT / "strategy_lab"))

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())


def _load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_env(ROOT / ".env")

from tsd_scan_pipeline.tsd_1h_signal import _bars_1h_polygon
from tsd_scan_pipeline.tsd_keep_profit import (
    init_php_trail_state,
    php_process_bar,
    resolve_php_kill_pct,
)
from tsd_scan_pipeline.tsd_kill import structure_area_low
from tsd_scan_pipeline.tsd_trail import remaining_shares
from tsd_scan_pipeline.universe_tsd import load_polygon_key, POLYGON_BASE, polygon_get

ET = pytz.timezone("America/New_York")
COST = 0.0015


def _as_et(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize(ET) if t.tzinfo is None else t.tz_convert(ET)


def _daily_close(sym: str, day: str, key: str) -> float | None:
    url = f"{POLYGON_BASE}/v1/open-close/{sym}/{day}"
    try:
        data = polygon_get(url, {"adjusted": "true"}, key, timeout=30)
        time.sleep(0.12)
        c = data.get("close")
        return float(c) if c is not None else None
    except Exception:
        return None


def _pre_lows(bars: pd.DataFrame, entry_date: str, entry_hour: int) -> list[float]:
    lows: list[float] = []
    for ts, row in bars.iterrows():
        start = _as_et(ts)
        d = start.date().isoformat()
        ch = (int(start.hour) + 1) % 24
        if d > entry_date or (d == entry_date and ch > entry_hour):
            break
        l = float(row.get("low") or 0)
        if l > 0:
            lows.append(l)
    return lows[-10:]


def simulate_keep_profit(
    trade: dict[str, Any],
    bars: pd.DataFrame,
    key: str,
    *,
    use_structure_kill: bool = False,
    be_lock_after_t1: bool = False,
    kill_tighten_after_t1: float | None = 0.025,
) -> dict[str, Any]:
    entry = float(trade["entry_price"])
    shares = int(trade["shares"])
    ed = str(trade["entry_date"])
    eh = int(trade["entry_hour"])
    end = "2026-09-10"

    area = structure_area_low(_pre_lows(bars, ed, eh))
    if use_structure_kill:
        kill_pct, kill_src, kill_px = resolve_php_kill_pct(
            entry=entry, structure_level=area, profile=trade.get("tsd_profile"),
        )
    else:
        kill_pct, kill_src = 0.05, "fallback_5pct"
        kill_px = entry * (1.0 - kill_pct)

    trail = init_php_trail_state(
        entry, shares, kill_pct=kill_pct, kill_price=kill_px,
    )
    realized = 0.0
    exits: list[dict[str, Any]] = []

    for ts, row in bars.iterrows():
        start = _as_et(ts)
        d = start.date().isoformat()
        ch = (int(start.hour) + 1) % 24
        if d < ed or (d == ed and ch <= eh):
            continue
        if d > end:
            break
        high = float(row.get("high") or 0)
        low = float(row.get("low") or 0)
        close = float(row.get("close") or 0)
        if high <= 0 or low <= 0:
            continue
        when = f"{d}T{ch:02d}:00:00"
        trail, new_ex = php_process_bar(
            trail, high=high, low=low, close=close, when=when,
            be_lock_after_t1=be_lock_after_t1,
            kill_tighten_after_t1=kill_tighten_after_t1,
        )
        for e in new_ex:
            realized += (float(e["exit_price"]) - entry) * int(e["shares"])
            exits.append({**e, "date": d, "hour": ch})
        if remaining_shares(trail) <= 0:
            break

    rem = remaining_shares(trail)
    if rem > 0:
        mark = _daily_close(str(trade["symbol"]), end, key) or entry
        realized += (mark - entry) * rem
        exits.append({
            "tranche_id": "REMAINING", "shares": rem, "exit_price": mark,
            "reason": "range_eod", "date": end, "hour": 16,
        })

    pnl = realized - entry * shares * COST
    return {
        "symbol": trade["symbol"],
        "entry_date": ed,
        "orig_pnl": float(trade.get("pnl") or 0),
        "new_pnl": round(pnl, 2),
        "lift": round(pnl - float(trade.get("pnl") or 0), 2),
        "kill_pct": kill_pct,
        "kill_source": kill_src,
        "exits": exits,
        "breakeven_locked": bool(trail.get("breakeven_locked")),
    }


def main() -> int:
    replay = json.loads(
        (PIPELINE_DIR / "results" / "php_range_replay_20260831_20260910.json").read_text()
    )
    key = load_polygon_key()
    cache: dict[str, pd.DataFrame] = {}
    modes = [
        ("t1_bank_only", dict(use_structure_kill=False, be_lock_after_t1=False, kill_tighten_after_t1=None)),
        ("t1_bank_tighten_2.5", dict(use_structure_kill=False, be_lock_after_t1=False, kill_tighten_after_t1=0.025)),
        ("t1_bank_be", dict(use_structure_kill=False, be_lock_after_t1=True, kill_tighten_after_t1=None)),
        ("struct+t1_bank", dict(use_structure_kill=True, be_lock_after_t1=False, kill_tighten_after_t1=None)),
        ("struct+t1+tighten", dict(use_structure_kill=True, be_lock_after_t1=False, kill_tighten_after_t1=0.025)),
    ]
    print("Caching bars...")
    for t in replay["trades"]:
        sym = str(t["symbol"]).upper()
        if sym not in cache:
            cache[sym] = _bars_1h_polygon(sym, api_key=key)
            time.sleep(0.12)

    summary = {}
    for name, kwargs in modes:
        rows = []
        for t in replay["trades"]:
            sym = str(t["symbol"]).upper()
            rows.append(simulate_keep_profit(t, cache[sym], key, **kwargs))
        orig = sum(r["orig_pnl"] for r in rows)
        new = sum(r["new_pnl"] for r in rows)
        summary[name] = {
            "orig_pnl": round(orig, 2),
            "new_pnl": round(new, 2),
            "lift": round(new - orig, 2),
            "wins": sum(1 for r in rows if r["new_pnl"] > 0),
            "losses": sum(1 for r in rows if r["new_pnl"] <= 0),
            "trades": rows,
        }
        print(
            f"{name:22} NEW ${new:+8.2f}  LIFT ${new - orig:+8.2f}  "
            f"W{summary[name]['wins']}/L{summary[name]['losses']}"
        )

    path = PIPELINE_DIR / "results" / "php_keep_profit_abtest.json"
    path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
