"""
strategy_lab/fetch_premarket.py

For each setup in strategy_lab/results/setups.json:
  Fetch Polygon 1-minute bars for flag_date (same helpers as fetch_history /
  candidates/entry_study), isolate PREMARKET 04:00–09:30 ET, and store:
    - full premarket bars
    - premarket_median (median of 1-min closes)
    - premarket_vwap (volume-weighted typical price)
    - premarket_low / high / volume

Thin / missing PM data → premarket_available=false (no crash).

Writes:
  results/premarket.json          keyed by TICKER|YYYY-MM-DD
  results/bars/{T}_{d}_premarket.json   (session labeled; not RTH)

Usage (from repo root):
  py -3 strategy_lab/fetch_premarket.py
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "candidates"))

from entry_study import (  # noqa: E402
    fetch_minute_bars,
    load_polygon_key,
)

ET = ZoneInfo("America/New_York")
# Premarket window: 04:00 inclusive → 09:30 exclusive (RTH open is not PM).
PM_START_MIN = 4 * 60
PM_END_MIN = 9 * 60 + 30
RTH_OPEN_MIN = 9 * 60 + 30
SLEEP_SEC = 0.15
# Below this bar count → treat as thin / unavailable for median & VWAP.
MIN_PM_BARS = 5

LAB = Path(__file__).resolve().parent
SETUPS_PATH = LAB / "results" / "setups.json"
RESULTS_DIR = LAB / "results"
BARS_DIR = RESULTS_DIR / "bars"
OUT_PATH = RESULTS_DIR / "premarket.json"


def _et_minutes(ts_ms: int) -> int:
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=ET)
    return dt.hour * 60 + dt.minute


def _et_iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=ET).isoformat()


def _slim_bars(raw: list[dict]) -> list[dict]:
    """Normalize Polygon aggs into lab bar shape (t, t_et, o/h/l/c/v)."""
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


def filter_premarket(raw: list[dict]) -> list[dict]:
    """Keep bars with ET minute-of-day in [04:00, 09:30)."""
    out: list[dict] = []
    for b in raw:
        try:
            ts = int(b["t"])
        except (KeyError, TypeError, ValueError):
            continue
        m = _et_minutes(ts)
        if PM_START_MIN <= m < PM_END_MIN:
            out.append(b)
    return out


def rth_open_price(raw: list[dict]) -> float | None:
    """09:30 ET open (first RTH bar open), for eyeball vs PM anchors."""
    rth = []
    for b in raw:
        try:
            ts = int(b["t"])
        except (KeyError, TypeError, ValueError):
            continue
        if _et_minutes(ts) >= RTH_OPEN_MIN:
            rth.append(b)
    if not rth:
        return None
    try:
        return float(rth[0]["o"])
    except (KeyError, TypeError, ValueError):
        return None


def premarket_stats(pm_slim: list[dict]) -> dict[str, Any]:
    """
    Median close + VWAP from typical price (h+l+c)/3.
    Returns available=false when bars are missing/thin.
    """
    if len(pm_slim) < MIN_PM_BARS:
        return {
            "premarket_available": False,
            "n_premarket_bars": len(pm_slim),
            "reason": "thin_or_missing",
            "premarket_median": None,
            "premarket_vwap": None,
            "premarket_low": None,
            "premarket_high": None,
            "premarket_volume": None,
        }

    closes = [b["c"] for b in pm_slim]
    lows = [b["l"] for b in pm_slim]
    highs = [b["h"] for b in pm_slim]
    vol = sum(b["v"] for b in pm_slim)

    cum_pv = 0.0
    cum_v = 0.0
    for b in pm_slim:
        tp = (b["h"] + b["l"] + b["c"]) / 3.0
        v = b["v"]
        cum_pv += tp * v
        cum_v += v
    vwap = (cum_pv / cum_v) if cum_v > 0 else statistics.mean(closes)

    return {
        "premarket_available": True,
        "n_premarket_bars": len(pm_slim),
        "reason": None,
        "premarket_median": round(statistics.median(closes), 4),
        "premarket_vwap": round(vwap, 4),
        "premarket_low": round(min(lows), 4),
        "premarket_high": round(max(highs), 4),
        "premarket_volume": round(vol, 2),
    }


def load_unique_setups() -> list[dict[str, Any]]:
    doc = json.loads(SETUPS_PATH.read_text(encoding="utf-8"))
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for s in doc.get("setups") or []:
        t = str(s.get("ticker") or "").upper().strip()
        d = str(s.get("flag_date") or "")[:10]
        if not t or not d or (t, d) in seen:
            continue
        seen.add((t, d))
        out.append({"ticker": t, "flag_date": d, "source": s.get("source")})
    out.sort(key=lambda r: (r["flag_date"], r["ticker"]))
    return out


def main() -> int:
    if not SETUPS_PATH.exists():
        print(f"Missing {SETUPS_PATH} — run collect_setups.py first.")
        return 1

    api_key = load_polygon_key()
    setups = load_unique_setups()
    BARS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Fetching premarket (04:00–09:30 ET) for {len(setups)} setups...")
    print(f"  Min bars for available: {MIN_PM_BARS}")
    print()

    records: dict[str, dict[str, Any]] = {}
    n_ok = 0
    n_thin = 0
    n_fetch_fail = 0

    for i, s in enumerate(setups, start=1):
        ticker = s["ticker"]
        flag_date = s["flag_date"]
        key_id = f"{ticker}|{flag_date}"
        print(f"[{i}/{len(setups)}] {key_id}")

        try:
            raw = fetch_minute_bars(ticker, flag_date, api_key)
            time.sleep(SLEEP_SEC)
        except Exception as exc:
            n_fetch_fail += 1
            print(f"    fetch error: {exc}")
            records[key_id] = {
                "ticker": ticker,
                "flag_date": flag_date,
                "premarket_available": False,
                "n_premarket_bars": 0,
                "reason": f"fetch_error: {exc}",
                "premarket_median": None,
                "premarket_vwap": None,
                "premarket_low": None,
                "premarket_high": None,
                "premarket_volume": None,
                "open_0930": None,
                "premarket_bars_path": None,
            }
            continue

        pm_raw = filter_premarket(raw)
        pm_slim = _slim_bars(pm_raw)
        stats = premarket_stats(pm_slim)
        open_0930 = rth_open_price(raw)

        pm_path = BARS_DIR / f"{ticker}_{flag_date}_premarket.json"
        pm_path.write_text(
            json.dumps({
                "ticker": ticker,
                "flag_date": flag_date,
                "session": "premarket",
                "window_et": "04:00-09:30",
                "window_note": "Inclusive of 04:00 ET; exclusive of 09:30 RTH open",
                "n_bars": len(pm_slim),
                "bars": pm_slim,
            }, indent=2),
            encoding="utf-8",
        )
        rel = str(pm_path.relative_to(ROOT)).replace("\\", "/")

        if stats["premarket_available"]:
            n_ok += 1
            print(
                f"    ok  bars={stats['n_premarket_bars']}  "
                f"median={stats['premarket_median']}  "
                f"vwap={stats['premarket_vwap']}  "
                f"open={open_0930}"
            )
        else:
            n_thin += 1
            print(
                f"    thin/none  bars={stats['n_premarket_bars']}  "
                f"(marked unavailable)"
            )

        records[key_id] = {
            "ticker": ticker,
            "flag_date": flag_date,
            "source": s.get("source"),
            **stats,
            "open_0930": round(open_0930, 4) if open_0930 is not None else None,
            "premarket_bars_path": rel,
        }

        if i % 25 == 0:
            print(f"  ... progress {i}/{len(setups)}  "
                  f"(usable={n_ok} thin={n_thin} fail={n_fetch_fail})")

    # Eyeball table: 10 examples with usable PM + known open
    examples = [
        r for r in records.values()
        if r.get("premarket_available") and r.get("open_0930") is not None
    ]
    examples.sort(key=lambda r: (r["flag_date"], r["ticker"]))
    # Prefer spread of dates: take first 5 + last 5 if enough, else first 10
    if len(examples) >= 10:
        sample = examples[:5] + examples[-5:]
    else:
        sample = examples[:10]

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "informational_only": True,
        "window_et": "04:00-09:30",
        "min_pm_bars": MIN_PM_BARS,
        "summary": {
            "n_setups": len(setups),
            "n_usable_premarket": n_ok,
            "n_thin_or_missing": n_thin,
            "n_fetch_fail": n_fetch_fail,
        },
        "examples": [
            {
                "ticker": r["ticker"],
                "flag_date": r["flag_date"],
                "premarket_median": r["premarket_median"],
                "premarket_vwap": r["premarket_vwap"],
                "open_0930": r["open_0930"],
                "median_vs_open_pct": (
                    round(
                        100.0 * (r["premarket_median"] - r["open_0930"])
                        / r["open_0930"],
                        2,
                    )
                    if r["open_0930"]
                    else None
                ),
                "vwap_vs_open_pct": (
                    round(
                        100.0 * (r["premarket_vwap"] - r["open_0930"])
                        / r["open_0930"],
                        2,
                    )
                    if r["open_0930"]
                    else None
                ),
            }
            for r in sample
        ],
        "premarket": records,
    }

    OUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print()
    print("=" * 78)
    print("PREMARKET FETCH SUMMARY")
    print("=" * 78)
    print(f"  Setups processed : {len(setups)}")
    print(f"  Usable PM data   : {n_ok}")
    print(f"  Thin / none      : {n_thin}")
    print(f"  Fetch failures   : {n_fetch_fail}")
    print(f"  Bars dir         : results/bars/*_premarket.json")
    print(f"  Wrote            : {OUT_PATH.relative_to(ROOT)}")
    print()
    print("  Examples (premarket anchors vs 09:30 open)")
    print("-" * 78)
    print(
        f"  {'ticker':<8} {'date':<12} "
        f"{'pm_med':>8} {'pm_vwap':>8} {'open':>8} "
        f"{'med-open%':>10} {'vwap-open%':>10}"
    )
    for r in sample:
        o = r["open_0930"]
        med = r["premarket_median"]
        vwap = r["premarket_vwap"]
        med_pct = 100.0 * (med - o) / o if o else None
        vwap_pct = 100.0 * (vwap - o) / o if o else None
        print(
            f"  {r['ticker']:<8} {r['flag_date']:<12} "
            f"{med:>8.4f} {vwap:>8.4f} {o:>8.4f} "
            f"{med_pct:>+9.2f}% {vwap_pct:>+9.2f}%"
        )
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
