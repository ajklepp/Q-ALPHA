"""
strategy_lab/fetch_history.py

For each setup in strategy_lab/results/setups.json:
  1. Fetch Polygon 1-minute bars for flag_date (reuses
     candidates/entry_study.fetch_minute_bars + load_polygon_key).
  2. Entry = first RTH (09:30 ET+) 1-min bar close (opening candle),
     matching the live watch_and_enter open definition.
  3. Fetch daily bars from flag_date → today; compute since-flagged
     max run-up %, max drawdown %, last vs entry.
  4. Skip/log failures (no data, delisted, weekend) — never crash.

Writes strategy_lab/results/history.json keyed by "TICKER|YYYY-MM-DD".
Intraday bars saved under strategy_lab/results/bars/{TICKER}_{date}.json.

Usage (from repo root):
  py -3 strategy_lab/fetch_history.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

from entry_study import (  # noqa: E402
    POLYGON_BASE,
    REQUEST_TIMEOUT,
    fetch_minute_bars,
    load_polygon_key,
)

ET = ZoneInfo("America/New_York")
RTH_OPEN_MIN = 9 * 60 + 30
SLEEP_SEC = 0.15

SETUPS_PATH = Path(__file__).resolve().parent / "results" / "setups.json"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
BARS_DIR = RESULTS_DIR / "bars"
OUT_PATH = RESULTS_DIR / "history.json"


def fetch_daily_bars(
    ticker: str,
    start: str,
    end: str,
    key: str,
) -> list[dict]:
    """Adjusted daily OHLCV [start, end]. Same pagination style as minute fetch."""
    url = (
        f"{POLYGON_BASE}/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}"
        f"?adjusted=true&sort=asc&limit=50000&apiKey={key}"
    )
    out: list[dict] = []
    for _ in range(10):
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            print(f"    {ticker}: daily request error {exc}")
            return out
        if resp.status_code != 200:
            print(f"    {ticker}: daily HTTP {resp.status_code}")
            return out
        data = resp.json()
        out.extend(data.get("results") or [])
        nxt = data.get("next_url")
        if not nxt:
            break
        url = nxt + f"&apiKey={key}"
        time.sleep(SLEEP_SEC)
    return out


def _et_minutes(ts_ms: int) -> int:
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=ET)
    return dt.hour * 60 + dt.minute


def _et_iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=ET).isoformat()


def _bar_date(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=ET).date().isoformat()


def opening_entry(minute_bars: list[dict]) -> tuple[float, str, int] | None:
    """Entry = close of first 1-min bar at/after 09:30 ET."""
    rth = [
        b for b in minute_bars
        if "t" in b and _et_minutes(int(b["t"])) >= RTH_OPEN_MIN
    ]
    if not rth:
        return None
    b0 = rth[0]
    try:
        px = float(b0["c"])
    except (KeyError, TypeError, ValueError):
        return None
    if px <= 0:
        return None
    ts = int(b0["t"])
    return px, _et_iso(ts), ts


def since_flagged_stats(
    entry_price: float,
    daily_bars: list[dict],
    flag_date: str,
) -> dict[str, Any]:
    """Max run-up / max drawdown vs entry from flag_date forward + last vs entry."""
    empty = {
        "max_runup_pct": None,
        "max_drawdown_pct": None,
        "last_price": None,
        "last_vs_entry_pct": None,
        "n_daily_bars": 0,
    }
    if entry_price <= 0 or not daily_bars:
        return empty

    max_high = None
    min_low = None
    last_close = None
    n = 0
    for b in daily_bars:
        d = _bar_date(int(b["t"]))
        if d < flag_date:
            continue
        n += 1
        h = float(b["h"])
        lo = float(b["l"])
        last_close = float(b["c"])
        max_high = h if max_high is None else max(max_high, h)
        min_low = lo if min_low is None else min(min_low, lo)

    if n == 0 or last_close is None:
        return empty

    return {
        "max_runup_pct": round((max_high - entry_price) / entry_price * 100, 3),
        "max_drawdown_pct": round((min_low - entry_price) / entry_price * 100, 3),
        "last_price": round(last_close, 4),
        "last_vs_entry_pct": round(
            (last_close - entry_price) / entry_price * 100, 3
        ),
        "n_daily_bars": n,
    }


def _slim_bars(raw: list[dict]) -> list[dict]:
    slim: list[dict] = []
    for b in raw:
        try:
            ts = int(b["t"])
            slim.append({
                "t": ts,
                "t_et": _et_iso(ts),
                "o": float(b["o"]),
                "h": float(b["h"]),
                "l": float(b["l"]),
                "c": float(b["c"]),
                "v": float(b.get("v") or 0),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return slim


def _bump(reasons: dict[str, int], reason: str) -> None:
    reasons[reason] = reasons.get(reason, 0) + 1


def main() -> int:
    if not SETUPS_PATH.exists():
        print(f"Missing {SETUPS_PATH} — run collect_setups.py first.")
        return 1

    api_key = load_polygon_key()  # entry_study.load_polygon_key
    payload = json.loads(SETUPS_PATH.read_text(encoding="utf-8"))
    setups = payload.get("setups") or []
    today = datetime.now(ET).date().isoformat()

    print(f"Loaded {len(setups)} setups from {SETUPS_PATH.relative_to(ROOT)}")
    print(f"Daily window end: {today} ET")
    print("Fetching Polygon history (1-min + daily)…\n")

    BARS_DIR.mkdir(parents=True, exist_ok=True)

    earliest: dict[str, str] = {}
    for s in setups:
        t, d = s["ticker"], s["flag_date"]
        if t not in earliest or d < earliest[t]:
            earliest[t] = d

    daily_cache: dict[str, list[dict]] = {}
    history: dict[str, dict] = {}
    ok = 0
    skipped = 0
    skip_reasons: dict[str, int] = {}

    for i, s in enumerate(setups, 1):
        ticker = s["ticker"]
        flag_date = s["flag_date"]
        key_id = f"{ticker}|{flag_date}"
        print(f"[{i}/{len(setups)}] {key_id}")

        try:
            fd = date.fromisoformat(flag_date)
        except ValueError:
            skipped += 1
            _bump(skip_reasons, "bad_date")
            print("    skip: bad flag_date")
            history[key_id] = {
                "ticker": ticker, "flag_date": flag_date,
                "status": "skipped", "reason": "bad_date",
            }
            continue

        if fd.weekday() >= 5:
            skipped += 1
            _bump(skip_reasons, "weekend")
            print("    skip: weekend")
            history[key_id] = {
                "ticker": ticker, "flag_date": flag_date,
                "status": "skipped", "reason": "weekend",
            }
            continue

        if flag_date > today:
            skipped += 1
            _bump(skip_reasons, "future")
            print("    skip: future date")
            history[key_id] = {
                "ticker": ticker, "flag_date": flag_date,
                "status": "skipped", "reason": "future",
            }
            continue

        try:
            # entry_study signature: fetch_minute_bars(ticker, day, key)
            minute_raw = fetch_minute_bars(ticker, flag_date, api_key)
            time.sleep(SLEEP_SEC)
        except Exception as exc:
            skipped += 1
            _bump(skip_reasons, "minute_error")
            print(f"    skip: minute fetch error {exc}")
            history[key_id] = {
                "ticker": ticker, "flag_date": flag_date,
                "status": "skipped", "reason": f"minute_error: {exc}",
            }
            continue

        if not minute_raw:
            skipped += 1
            _bump(skip_reasons, "no_minute_bars")
            print("    skip: no 1-min bars")
            history[key_id] = {
                "ticker": ticker, "flag_date": flag_date,
                "status": "skipped", "reason": "no_minute_bars",
            }
            continue

        entry = opening_entry(minute_raw)
        if entry is None:
            skipped += 1
            _bump(skip_reasons, "no_rth_open")
            print("    skip: no RTH opening bar")
            history[key_id] = {
                "ticker": ticker, "flag_date": flag_date,
                "status": "skipped", "reason": "no_rth_open",
                "n_minute_bars_raw": len(minute_raw),
            }
            continue

        entry_price, entry_time, _ = entry

        if ticker not in daily_cache:
            try:
                daily_cache[ticker] = fetch_daily_bars(
                    ticker, earliest[ticker], today, api_key,
                )
                time.sleep(SLEEP_SEC)
            except Exception as exc:
                print(f"    warn: daily fetch error {exc}")
                daily_cache[ticker] = []

        perf = since_flagged_stats(
            entry_price, daily_cache[ticker], flag_date,
        )

        bars_path = BARS_DIR / f"{ticker}_{flag_date}.json"
        slim = _slim_bars(minute_raw)
        bars_path.write_text(
            json.dumps({
                "ticker": ticker,
                "flag_date": flag_date,
                "n_bars": len(slim),
                "bars": slim,
            }, indent=2),
            encoding="utf-8",
        )

        history[key_id] = {
            "ticker": ticker,
            "flag_date": flag_date,
            "source": s.get("source"),
            "status": "ok",
            "entry_price": round(entry_price, 4),
            "entry_time": entry_time,
            "entry_rule": "first_rth_1min_close",
            "n_minute_bars": len(slim),
            "minute_bars_path": str(
                bars_path.relative_to(ROOT)
            ).replace("\\", "/"),
            "since_flagged": perf,
            "setup_meta": {
                "gap_pct": s.get("gap_pct"),
                "vol_ratio": s.get("vol_ratio"),
                "score": s.get("score"),
            },
        }
        ok += 1
        print(
            f"    ok entry=${entry_price:.2f} @ {entry_time[11:19]}  "
            f"runup={perf.get('max_runup_pct')}%  "
            f"dd={perf.get('max_drawdown_pct')}%  "
            f"last_vs={perf.get('last_vs_entry_pct')}%"
        )

    ranked = [
        h for h in history.values()
        if h.get("status") == "ok"
        and (h.get("since_flagged") or {}).get("last_vs_entry_pct") is not None
    ]
    ranked.sort(
        key=lambda h: h["since_flagged"]["last_vs_entry_pct"], reverse=True,
    )
    best = ranked[:10]
    worst = list(reversed(ranked[-10:])) if ranked else []

    def _row(h: dict) -> dict:
        sf = h["since_flagged"]
        return {
            "ticker": h["ticker"],
            "flag_date": h["flag_date"],
            "entry_price": h["entry_price"],
            "last_vs_entry_pct": sf["last_vs_entry_pct"],
            "max_runup_pct": sf["max_runup_pct"],
            "max_drawdown_pct": sf["max_drawdown_pct"],
        }

    out_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "informational_only": True,
        "as_of": today,
        "summary": {
            "n_setups": len(setups),
            "n_ok": ok,
            "n_skipped": skipped,
            "skip_reasons": skip_reasons,
        },
        "top_best_last_vs_entry": [_row(h) for h in best],
        "top_worst_last_vs_entry": [_row(h) for h in worst],
        "history": history,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out_payload, indent=2), encoding="utf-8")

    print("\n" + "=" * 68)
    print("STRATEGY LAB — HISTORY FETCH SUMMARY")
    print("=" * 68)
    print(f"  Setups total     : {len(setups)}")
    print(f"  Got data (ok)    : {ok}")
    print(f"  Failed / skipped : {skipped}")
    if skip_reasons:
        print("  Skip reasons:")
        for k, v in sorted(skip_reasons.items(), key=lambda kv: -kv[1]):
            print(f"    {k:<20} {v:>4}")

    def _tbl(title: str, rows: list[dict]) -> None:
        print("-" * 68)
        print(title)
        print(
            f"  {'Ticker':<8} {'Flag':<12} {'Entry':>8} "
            f"{'Last%':>8} {'Runup%':>8} {'DD%':>8}"
        )
        for h in rows:
            print(
                f"  {h['ticker']:<8} {h['flag_date']:<12} "
                f"{h['entry_price']:>8.2f} "
                f"{h['last_vs_entry_pct']:>7.1f}% "
                f"{h['max_runup_pct']:>7.1f}% "
                f"{h['max_drawdown_pct']:>7.1f}%"
            )

    if best:
        _tbl("TOP 10 BEST (last vs entry)", [_row(h) for h in best])
    if worst:
        _tbl("TOP 10 WORST (last vs entry)", [_row(h) for h in worst])
    print("=" * 68)
    print(f"Wrote {OUT_PATH.relative_to(ROOT)}")
    print(f"Bars under {BARS_DIR.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
