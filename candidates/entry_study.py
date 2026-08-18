#!/usr/bin/env python3
"""
Q-ALPHA | Entry-Timing Study Harness  (READ-ONLY, OFFLINE)
==========================================================

PURPOSE
-------
This script does NOT trade and does NOT touch live state. It is the data
pipeline for the self-learning layer's #1 question: WHEN and ON WHAT
CONFIRMATION should we enter?

For each candidate that passed a given day's scan, it pulls the full
9:30-11:00 ET one-minute bar path from Polygon (ground truth, independent of
the IBKR data subscription) and REPLAYS several entry rules against that path,
recording the R-multiple each rule WOULD have achieved with the live
single-bracket 2R model.

Because it scores every candidate under every rule -- entered OR skipped -- the
resulting dataset is free of the survivorship/selection bias you get from only
logging trades you actually took. That is what makes it honest training data.

ENTRY RULES REPLAYED
--------------------
  R1  immediate      : enter at first bar >= 9:32 (min-wait baseline)
  R2  vwap_reclaim   : enter first minute close > session VWAP after 9:32
  R3  orb_breakout   : enter on break above first 5-minute high
  R4  pullback_go    : enter on reclaim of VWAP after a dip to/below it
  R5  live_logic     : faithful replay of watch_and_enter's actual gate
                       (gap-hold + above-VWAP + vol-confirm + not-dumping +
                        structure-intact + min-wait) -- THE BASELINE TO BEAT

EXIT MODEL (matches live single-bracket 2R)
-------------------------------------------
  stop_dist = max(entry*0.02, min(entry - first_candle_low, entry*0.07))
  stop      = entry - stop_dist
  target_2r = entry + 2*stop_dist
  Walk bars forward from entry:
    - if a bar's low <= stop  -> exit STOP  (-1.0R)   (stop checked first = conservative)
    - elif bar's high >= 2R    -> exit TARGET (+2.0R)
    - else at 11:00 -> exit TIME at last close (fractional R)
  Also records MFE (max favorable excursion) in R for each rule.

USAGE
-----
  python entry_study.py 2026-08-14              # one date
  python entry_study.py 2026-08-11 2026-08-15   # inclusive date range
  python entry_study.py                          # defaults to most recent scan file

OUTPUT (read-only, never touches trading state)
------------------------------------------------
  candidates/entry_study/<DATE>.jsonl   per-candidate-per-rule rows
  candidates/entry_study/rollup.csv     appended summary you can open in Excel

SAFETY
------
  * No IB import, no order placement, no writes to paper_trades/pool_state.
  * Reads Polygon via the same key the scanner uses; the key is never printed.
  * Runs after the close; zero live-trading impact.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# --- config (mirrors the live system) ---------------------------------------
POLYGON_BASE = "https://api.polygon.io"
REQUEST_TIMEOUT = 15
ET = timezone(timedelta(hours=-4))  # US/Eastern in summer (DST). See note below.

WINDOW_START_MIN = 9 * 60 + 30   # 9:30
WINDOW_END_MIN = 11 * 60         # 11:00  (entry search + exit walk end)
EXIT_WALK_END_MIN = 16 * 60      # 16:00  let a trade run to close for the exit walk

MIN_WAIT_MIN = 2                 # live: minutes_since_open >= 2
ORB_MINUTES = 5                  # opening-range = first 5 minutes

HERE = Path(__file__).resolve().parent
STUDY_DIR = HERE / "entry_study"
ROLLUP_CSV = STUDY_DIR / "rollup.csv"

RULES = ["immediate", "vwap_reclaim", "orb_breakout", "pullback_go", "live_logic"]


# --- key loading (never printed) ---------------------------------------------
def load_polygon_key() -> str:
    key = os.environ.get("POLYGON_API_KEY") or os.environ.get("POLYGON_KEY")
    if key:
        return key.strip()
    env_path = HERE.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if "POLYGON" in k.upper():
                return v.strip().strip('"').strip("'")
    raise SystemExit("No POLYGON key found (env or .env). Cannot run study.")


# --- polygon fetch ------------------------------------------------------------
def fetch_minute_bars(ticker: str, day: str, key: str) -> list[dict]:
    """Full 1-minute bars for `day` (paginates via next_url). Returns [] on miss."""
    url = (
        f"{POLYGON_BASE}/v2/aggs/ticker/{ticker}/range/1/minute/{day}/{day}"
        f"?adjusted=true&sort=asc&limit=50000&apiKey={key}"
    )
    out: list[dict] = []
    for _ in range(10):  # safety cap on pagination
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            print(f"    {ticker}: request error {exc}")
            return out
        if resp.status_code != 200:
            print(f"    {ticker}: HTTP {resp.status_code}")
            return out
        data = resp.json()
        out.extend(data.get("results") or [])
        nxt = data.get("next_url")
        if not nxt:
            break
        url = nxt + f"&apiKey={key}"
        time.sleep(0.15)
    return out


def et_minutes(ts_ms: int) -> int:
    """Minute-of-day in ET for a Polygon ms timestamp."""
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=ET)
    return dt.hour * 60 + dt.minute


# --- bar helpers --------------------------------------------------------------
class Bar:
    __slots__ = ("t", "o", "h", "l", "c", "v", "mod")

    def __init__(self, r: dict):
        self.t = int(r["t"])
        self.o = float(r["o"])
        self.h = float(r["h"])
        self.l = float(r["l"])
        self.c = float(r["c"])
        self.v = float(r.get("v", 0) or 0)
        self.mod = et_minutes(self.t)  # minute-of-day ET


def session_bars(raw: list[dict]) -> list[Bar]:
    bars = [Bar(r) for r in raw]
    return [b for b in bars if WINDOW_START_MIN <= b.mod < EXIT_WALK_END_MIN]


def running_vwap(bars: list[Bar]) -> list[float]:
    """Cumulative session VWAP value AT each bar (typical price * vol)."""
    cum_pv = 0.0
    cum_v = 0.0
    out = []
    for b in bars:
        tp = (b.h + b.l + b.c) / 3.0
        cum_pv += tp * b.v
        cum_v += b.v
        out.append(cum_pv / cum_v if cum_v > 0 else b.c)
    return out


# --- exit walk (single-bracket 2R, matches live) -----------------------------
def simulate_exit(bars: list[Bar], entry_idx: int, prev_close: float,
                  first_candle_low: float) -> dict:
    """
    Enter at bars[entry_idx].close; compute stop/2R exactly like the live model;
    walk forward to 16:00 or exit. Stop checked before target in the same bar
    (conservative). Returns dict with realized R, exit reason, MFE in R.
    """
    entry = bars[entry_idx].c
    stop_dist = max(entry * 0.02, min(entry - first_candle_low, entry * 0.07))
    if stop_dist <= 0:
        stop_dist = entry * 0.02
    stop = entry - stop_dist
    target = entry + 2 * stop_dist

    mfe_r = 0.0
    for b in bars[entry_idx + 1:]:
        # track MFE
        fav = (b.h - entry) / stop_dist
        if fav > mfe_r:
            mfe_r = fav
        # stop first (conservative on intrabar ambiguity)
        if b.l <= stop:
            return {"exit_reason": "STOP", "r": -1.0, "mfe_r": round(mfe_r, 2),
                    "entry": round(entry, 4), "stop": round(stop, 4),
                    "target": round(target, 4), "exit_price": round(stop, 4),
                    "exit_mod": b.mod}
        if b.h >= target:
            return {"exit_reason": "TARGET", "r": 2.0, "mfe_r": round(mfe_r, 2),
                    "entry": round(entry, 4), "stop": round(stop, 4),
                    "target": round(target, 4), "exit_price": round(target, 4),
                    "exit_mod": b.mod}
    # timed out at close
    last = bars[-1]
    r = (last.c - entry) / stop_dist
    return {"exit_reason": "TIME", "r": round(r, 3), "mfe_r": round(mfe_r, 2),
            "entry": round(entry, 4), "stop": round(stop, 4),
            "target": round(target, 4), "exit_price": round(last.c, 4),
            "exit_mod": last.mod}


# --- entry-rule triggers ------------------------------------------------------
def find_entry_index(rule: str, bars: list[Bar], vwaps: list[float],
                     prev_close: float, first_candle_high: float,
                     first_candle_low: float) -> int | None:
    """
    Return the index of the bar at which `rule` would trigger entry, or None if
    it never triggers within the 9:30-11:00 entry window.
    """
    open_price = bars[0].o
    for i, b in enumerate(bars):
        if b.mod >= WINDOW_END_MIN:
            return None  # past the entry window, no trigger
        minutes_since_open = b.mod - WINDOW_START_MIN
        price = b.c
        vwap = vwaps[i]

        if rule == "immediate":
            if minutes_since_open >= MIN_WAIT_MIN:
                return i

        elif rule == "vwap_reclaim":
            if minutes_since_open >= MIN_WAIT_MIN and price > vwap:
                return i

        elif rule == "orb_breakout":
            # break above the first-5-min high, after that window closes
            if minutes_since_open >= ORB_MINUTES and price > first_candle_high:
                return i

        elif rule == "pullback_go":
            # require a dip to/below VWAP first, then reclaim -> delegated helper
            return _pullback_go_index(bars, vwaps)

        elif rule == "live_logic":
            if minutes_since_open < MIN_WAIT_MIN:
                continue
            # session VWAP + up/down volume over the trailing minute (~ live 'recent')
            gap_holding = price > prev_close * 1.015
            above_vwap = price > vwap
            not_dumping = price > open_price * 0.97
            broke_structure = price < first_candle_low * 0.99
            gap_filled = price < prev_close * 1.005
            hard_dump = price < open_price * 0.95
            # vol_confirming: up vs down volume across bars so far this session
            up_vol = sum(x.v for x in bars[: i + 1] if x.c >= x.o)
            dn_vol = sum(x.v for x in bars[: i + 1] if x.c < x.o)
            vol_confirming = up_vol > dn_vol * 1.1
            if gap_filled or hard_dump or (broke_structure and minutes_since_open >= 5):
                return None
            if (gap_holding and above_vwap and vol_confirming
                    and not_dumping and not broke_structure):
                return i
    return None


def _pullback_go_index(bars: list[Bar], vwaps: list[float]) -> int | None:
    dipped = False
    for i, b in enumerate(bars):
        if b.mod >= WINDOW_END_MIN:
            return None
        minutes_since_open = b.mod - WINDOW_START_MIN
        if minutes_since_open < MIN_WAIT_MIN:
            continue
        if b.c <= vwaps[i]:
            dipped = True
        elif dipped and b.c > vwaps[i]:
            return i
    return None


# --- per-candidate study ------------------------------------------------------
def study_candidate(cand: dict, day: str, key: str) -> list[dict]:
    ticker = cand.get("ticker")
    prev_close = float(cand.get("prev_close") or 0)
    raw = fetch_minute_bars(ticker, day, key)
    bars = session_bars(raw)
    if len(bars) < 10 or prev_close <= 0:
        print(f"  {ticker:6s}: insufficient data ({len(bars)} bars) -> skipped")
        return []
    vwaps = running_vwap(bars)

    # opening candle = first minute at/after 9:30 (single 1-min bar here)
    first = bars[0]
    first_candle_high = first.h
    first_candle_low = first.l

    rows = []
    for rule in RULES:
        idx = find_entry_index(rule, bars, vwaps, prev_close,
                               first_candle_high, first_candle_low)
        if idx is None:
            rows.append({
                "date": day, "ticker": ticker, "rule": rule,
                "entered": False, "entry_mod": None, "r": None,
                "exit_reason": "NO_ENTRY", "mfe_r": None,
                "prev_close": prev_close, "gap_pct": cand.get("gap_pct"),
                "quality_score": cand.get("quality_score"),
            })
            continue
        ex = simulate_exit(bars, idx, prev_close, first_candle_low)
        entry_mod = bars[idx].mod
        rows.append({
            "date": day, "ticker": ticker, "rule": rule,
            "entered": True,
            "entry_mod": entry_mod,
            "entry_time": f"{entry_mod // 60:02d}:{entry_mod % 60:02d}",
            "r": ex["r"], "exit_reason": ex["exit_reason"], "mfe_r": ex["mfe_r"],
            "entry": ex["entry"], "stop": ex["stop"], "target": ex["target"],
            "prev_close": prev_close, "gap_pct": cand.get("gap_pct"),
            "quality_score": cand.get("quality_score"),
        })
    return rows


# --- scan-file discovery ------------------------------------------------------
def load_scan_candidates(day: str) -> list[dict]:
    """
    Find the scan result for `day` and normalize candidates to the fields this
    study needs. The live scanner writes `daily_scan_<DATE>.json` with:
        scan_date, candidates[ {ticker, prev_close, gap_estimate, pm_vol_ratio,
                                news_catalyst, rank, order_plan{...}, atr_14} ]
    Returns candidates (all that passed the scan -- entered OR not) or [].
    """
    tries = [
        HERE / f"daily_scan_{day}.json",   # real live format
        HERE / f"scan_{day}.json",         # legacy/alt fallbacks
        HERE / f"scan_results_{day}.json",
    ]
    for p in tries:
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        file_day = data.get("scan_date") or data.get("date") if isinstance(data, dict) else None
        if file_day and file_day != day:
            continue
        raw = (data.get("candidates") if isinstance(data, dict) else data) or []
        if not raw:
            continue
        norm = []
        for c in raw:
            op = c.get("order_plan") or {}
            norm.append({
                "ticker": c.get("ticker"),
                "prev_close": c.get("prev_close") or op.get("prev_close"),
                # normalize gap to percent (gap_estimate is a fraction e.g. 0.0756)
                "gap_pct": round((c.get("gap_estimate") or 0) * 100, 3),
                "pm_vol_ratio": c.get("pm_vol_ratio"),
                "news_catalyst": c.get("news_catalyst"),
                # no quality_score in the file; rank is the ordering signal
                "quality_score": c.get("quality_score") if c.get("quality_score") is not None else c.get("rank"),
                "rank": c.get("rank"),
            })
        print(f"  Loaded {len(norm)} candidates from {p.name}")
        return norm
    return []


# --- output -------------------------------------------------------------------
def write_day(day: str, rows: list[dict]) -> None:
    STUDY_DIR.mkdir(parents=True, exist_ok=True)
    out = STUDY_DIR / f"{day}.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    print(f"  Wrote {len(rows)} rows -> {out.name}")


def append_rollup(day: str, rows: list[dict]) -> None:
    """Per-rule summary appended to rollup.csv (Excel-friendly)."""
    STUDY_DIR.mkdir(parents=True, exist_ok=True)
    by_rule: dict[str, list[dict]] = {r: [] for r in RULES}
    for row in rows:
        by_rule.setdefault(row["rule"], []).append(row)

    header = ["date", "rule", "n_candidates", "n_entered", "entry_rate",
              "avg_r", "expectancy_r", "win_rate", "n_target", "n_stop",
              "n_time", "avg_mfe_r", "avg_entry_time"]
    new = not ROLLUP_CSV.exists()
    with ROLLUP_CSV.open("a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(header)
        for rule in RULES:
            rs = by_rule.get(rule, [])
            entered = [r for r in rs if r.get("entered")]
            n = len(rs)
            ne = len(entered)
            rvals = [r["r"] for r in entered if r["r"] is not None]
            wins = [r for r in entered if (r["r"] or 0) > 0]
            targets = [r for r in entered if r["exit_reason"] == "TARGET"]
            stops = [r for r in entered if r["exit_reason"] == "STOP"]
            times = [r for r in entered if r["exit_reason"] == "TIME"]
            mfes = [r["mfe_r"] for r in entered if r.get("mfe_r") is not None]
            emods = [r["entry_mod"] for r in entered if r.get("entry_mod") is not None]
            avg_r = round(sum(rvals) / len(rvals), 3) if rvals else 0
            # expectancy across ALL candidates (no-entry counts as 0R opportunity)
            expectancy = round(sum(rvals) / ne, 3) if ne else 0
            win_rate = round(len(wins) / ne, 3) if ne else 0
            avg_mfe = round(sum(mfes) / len(mfes), 2) if mfes else 0
            avg_entry_mod = round(sum(emods) / len(emods)) if emods else 0
            avg_entry_time = f"{avg_entry_mod // 60:02d}:{avg_entry_mod % 60:02d}" if emods else "-"
            w.writerow([day, rule, n, ne,
                        round(ne / n, 3) if n else 0,
                        avg_r, expectancy, win_rate,
                        len(targets), len(stops), len(times),
                        avg_mfe, avg_entry_time])
    print(f"  Appended rollup summary -> {ROLLUP_CSV.name}")


def print_day_summary(day: str, rows: list[dict]) -> None:
    print(f"\n  === {day} summary (R per entered trade) ===")
    print(f"  {'rule':14s} {'entered':>8s} {'avg_R':>7s} {'win%':>6s} "
          f"{'TARGET':>7s} {'STOP':>5s} {'TIME':>5s} {'avg_entry':>10s}")
    for rule in RULES:
        rs = [r for r in rows if r["rule"] == rule and r.get("entered")]
        if not rs:
            print(f"  {rule:14s} {'0':>8s} {'-':>7s}")
            continue
        rvals = [r["r"] for r in rs if r["r"] is not None]
        wins = sum(1 for r in rs if (r["r"] or 0) > 0)
        tg = sum(1 for r in rs if r["exit_reason"] == "TARGET")
        st = sum(1 for r in rs if r["exit_reason"] == "STOP")
        tm = sum(1 for r in rs if r["exit_reason"] == "TIME")
        emods = [r["entry_mod"] for r in rs if r.get("entry_mod") is not None]
        aem = round(sum(emods) / len(emods)) if emods else 0
        et = f"{aem // 60:02d}:{aem % 60:02d}" if emods else "-"
        print(f"  {rule:14s} {len(rs):>8d} {sum(rvals)/len(rvals):>7.2f} "
              f"{100*wins/len(rs):>5.0f}% {tg:>7d} {st:>5d} {tm:>5d} {et:>10s}")


# --- date handling ------------------------------------------------------------
def daterange(start: str, end: str) -> list[str]:
    d0 = datetime.strptime(start, "%Y-%m-%d").date()
    d1 = datetime.strptime(end, "%Y-%m-%d").date()
    out = []
    d = d0
    while d <= d1:
        if d.weekday() < 5:  # weekdays only
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def run_for_day(day: str, key: str) -> None:
    print(f"\n================ ENTRY STUDY: {day} ================")
    cands = load_scan_candidates(day)
    if not cands:
        print(f"  No scan candidates found for {day}. "
              f"(Need a scan_*.json with that day's candidates.) Skipping.")
        return
    all_rows: list[dict] = []
    for cand in cands:
        all_rows.extend(study_candidate(cand, day, key))
        time.sleep(0.12)  # be polite to Polygon
    if not all_rows:
        print("  No rows produced (all candidates lacked data).")
        return
    write_day(day, all_rows)
    append_rollup(day, all_rows)
    print_day_summary(day, all_rows)


def main() -> None:
    key = load_polygon_key()
    print("Polygon key loaded (not printed).")
    args = sys.argv[1:]
    if len(args) == 0:
        # default: most recent scan file present
        print("No date given; looking for the most recent scan file...")
        candidates = load_scan_candidates(datetime.now(ET).date().isoformat())
        day = datetime.now(ET).date().isoformat()
        run_for_day(day, key)
    elif len(args) == 1:
        run_for_day(args[0], key)
    else:
        for day in daterange(args[0], args[1]):
            run_for_day(day, key)
    print("\nDone. Study is read-only; no trading state was modified.")


if __name__ == "__main__":
    main()
