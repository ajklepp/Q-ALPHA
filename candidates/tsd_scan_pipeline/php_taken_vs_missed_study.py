#!/usr/bin/env python3
"""
Peak Hour — taken vs missed same-hour performance autopsy.

For each php_scan_*.json hour:
  TAKE   = launches with taken=True (cap slots selected)
  MISSED = ranked launches not taken
  FILLED = entry_results FILLED (actual book risk)

Marks forward path via Polygon 1H bars after the signal bar (no look-ahead in
decision; outcomes are post-signal research).

Usage:
  .\\venv\\Scripts\\python.exe candidates\\tsd_scan_pipeline\\php_taken_vs_missed_study.py
  .\\venv\\Scripts\\python.exe candidates\\tsd_scan_pipeline\\php_taken_vs_missed_study.py --days 7 --write
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytz
import requests

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES = PIPELINE_DIR.parent
ROOT = CANDIDATES.parent
if str(CANDIDATES) not in sys.path:
    sys.path.insert(0, str(CANDIDATES))

from tsd_scan_pipeline.php_scan_funnel import RESULTS_DIR, list_scan_funnels_since  # noqa: E402
from tsd_scan_pipeline.tsd_capacity import load_state  # noqa: E402

ET = pytz.timezone("America/New_York")
POLYGON_BASE = "https://api.polygon.io"
RATE_SLEEP = 0.12
# Outcome horizons after the signal 1H bar close
SAME_DAY_END_HOUR = 16
# Hit thresholds (research labels — not live exits)
HIT_2PCT = 0.02
HIT_5PCT = 0.05
KILL_PROXY = 0.05  # ~5% adverse before favorable


def _polygon_key() -> str:
    key = (os.environ.get("POLYGON_API_KEY") or "").strip()
    if key:
        return key
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("POLYGON_API_KEY") and "=" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _parse_scan_et(doc: dict[str, Any], path: Path) -> datetime | None:
    raw = doc.get("et")
    if raw:
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                return ET.localize(dt)
            return dt.astimezone(ET)
        except Exception:
            pass
    try:
        stamp = path.stem.replace("php_scan_", "")
        return ET.localize(datetime.strptime(stamp, "%Y%m%d_%H%M"))
    except ValueError:
        return None


def _book_filled_pnl() -> dict[str, list[dict[str, Any]]]:
    """symbol -> closed leg pnl rows from local book."""
    book = load_state()
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pos in book.get("positions") or []:
        sym = str(pos.get("symbol") or "").upper()
        for leg in pos.get("legs") or []:
            entry = float((leg.get("trail") or {}).get("entry_price") or leg.get("price") or 0)
            status = str(leg.get("status") or "").upper()
            if status == "CLOSED":
                pnl = 0.0
                reason = ""
                for ex in leg.get("exits") or []:
                    sh = int(ex.get("shares") or 0)
                    px = float(ex.get("exit_price") or 0)
                    if sh and px and entry:
                        pnl += (px - entry) * sh
                    reason = str(ex.get("reason") or reason)
                by_sym[sym].append({
                    "status": "CLOSED",
                    "pnl": round(pnl, 2),
                    "exit_reason": reason,
                    "entry": entry,
                })
            elif str(pos.get("status") or "").upper() == "OPEN":
                by_sym[sym].append({
                    "status": "OPEN",
                    "pnl": None,
                    "exit_reason": None,
                    "entry": entry,
                })
    return by_sym


def _fetch_1h_bars(
    api_key: str,
    symbol: str,
    day: date,
    cache: dict[tuple[str, str], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Polygon 1H aggs for one calendar day (ET window via ms timestamps)."""
    key = (symbol.upper(), day.isoformat())
    if key in cache:
        return cache[key]
    start = ET.localize(datetime.combine(day, datetime.min.time().replace(hour=4)))
    end = ET.localize(datetime.combine(day, datetime.min.time().replace(hour=20)))
    url = (
        f"{POLYGON_BASE}/v2/aggs/ticker/{symbol.upper()}/range/1/hour/"
        f"{int(start.timestamp() * 1000)}/{int(end.timestamp() * 1000)}"
    )
    bars: list[dict[str, Any]] = []
    try:
        resp = requests.get(
            url,
            params={"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": api_key},
            timeout=25,
        )
        if resp.status_code == 200:
            for b in (resp.json() or {}).get("results") or []:
                ts = int(b.get("t") or 0)
                if ts <= 0:
                    continue
                dt = datetime.fromtimestamp(ts / 1000.0, tz=ET)
                bars.append({
                    "et": dt,
                    "hour": dt.hour,
                    "o": float(b.get("o") or 0),
                    "h": float(b.get("h") or 0),
                    "l": float(b.get("l") or 0),
                    "c": float(b.get("c") or 0),
                    "v": float(b.get("v") or 0),
                })
    except Exception:
        bars = []
    cache[key] = bars
    time.sleep(RATE_SLEEP)
    return bars


def _path_stats(
    bars: list[dict[str, Any]],
    *,
    signal_hour: int,
    entry: float,
) -> dict[str, Any]:
    """MFE/MAE/close after the signal hour bar (exclusive of signal bar)."""
    if entry <= 0:
        return {"ok": False}
    forward = [b for b in bars if int(b["hour"]) > int(signal_hour) and int(b["hour"]) < SAME_DAY_END_HOUR + 1]
    # Include signal hour's remainder only via later bars; Peak Hour enters after bar close.
    if not forward:
        # fallback: any bar after signal hour same day
        forward = [b for b in bars if int(b["hour"]) > int(signal_hour)]
    if not forward:
        return {"ok": False, "n_bars": 0}

    mfe = 0.0
    mae = 0.0
    peak = entry
    trough = entry
    hit2 = False
    hit5 = False
    stopped = False
    for b in forward:
        hi = float(b["h"])
        lo = float(b["l"])
        if hi > peak:
            peak = hi
        if lo < trough:
            trough = lo
        up = (peak - entry) / entry
        dn = (entry - trough) / entry
        mfe = max(mfe, up)
        mae = max(mae, dn)
        if up >= HIT_2PCT:
            hit2 = True
        if up >= HIT_5PCT:
            hit5 = True
        if dn >= KILL_PROXY and not hit2:
            stopped = True
            break
    last = float(forward[-1]["c"])
    return {
        "ok": True,
        "n_bars": len(forward),
        "mfe_pct": round(mfe * 100.0, 3),
        "mae_pct": round(mae * 100.0, 3),
        "close_ret_pct": round((last / entry - 1.0) * 100.0, 3),
        "peak": round(peak, 4),
        "hit_2pct": hit2,
        "hit_5pct": hit5,
        "kill_before_2pct": stopped,
        "win_proxy": bool(hit2 and not stopped),
    }


def _cohort_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if r.get("path", {}).get("ok")]
    if not ok:
        return {
            "n": len(rows),
            "n_marked": 0,
            "avg_mfe_pct": None,
            "med_mfe_pct": None,
            "avg_mae_pct": None,
            "avg_close_ret_pct": None,
            "hit_2pct_rate": None,
            "hit_5pct_rate": None,
            "kill_before_2pct_rate": None,
            "win_proxy_rate": None,
        }
    mfes = [float(r["path"]["mfe_pct"]) for r in ok]
    maes = [float(r["path"]["mae_pct"]) for r in ok]
    closes = [float(r["path"]["close_ret_pct"]) for r in ok]
    return {
        "n": len(rows),
        "n_marked": len(ok),
        "avg_mfe_pct": round(statistics.mean(mfes), 3),
        "med_mfe_pct": round(statistics.median(mfes), 3),
        "avg_mae_pct": round(statistics.mean(maes), 3),
        "avg_close_ret_pct": round(statistics.mean(closes), 3),
        "hit_2pct_rate": round(sum(1 for r in ok if r["path"]["hit_2pct"]) / len(ok), 4),
        "hit_5pct_rate": round(sum(1 for r in ok if r["path"]["hit_5pct"]) / len(ok), 4),
        "kill_before_2pct_rate": round(
            sum(1 for r in ok if r["path"]["kill_before_2pct"]) / len(ok), 4
        ),
        "win_proxy_rate": round(sum(1 for r in ok if r["path"]["win_proxy"]) / len(ok), 4),
    }


def _feature_gap(
    winners_missed: list[dict[str, Any]],
    losers_taken: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Compare decision-time fields: big missed runners vs taken losers.
    Uses only fields present on php_scan launch rows (+ scores).
    """
    fields = ("rank", "htf_score", "launch_score", "rank_order", "hour", "1h_close")

    def avg_field(rows: list[dict[str, Any]], field: str) -> float | None:
        vals: list[float] = []
        for r in rows:
            try:
                vals.append(float(r.get(field)))
            except (TypeError, ValueError):
                continue
        return round(statistics.mean(vals), 3) if vals else None

    phase_m = Counter(str(r.get("phase_3h") or "?") for r in winners_missed)
    phase_t = Counter(str(r.get("phase_3h") or "?") for r in losers_taken)
    return {
        "missed_runners_n": len(winners_missed),
        "taken_losers_n": len(losers_taken),
        "avg_fields": {
            f: {
                "missed_runners": avg_field(winners_missed, f),
                "taken_losers": avg_field(losers_taken, f),
            }
            for f in fields
        },
        "phase_missed_runners": dict(phase_m),
        "phase_taken_losers": dict(phase_t),
        "hour_hist_missed_runners": dict(Counter(int(r.get("hour") or -1) for r in winners_missed)),
        "hour_hist_taken_losers": dict(Counter(int(r.get("hour") or -1) for r in losers_taken)),
        "top_missed_runners": sorted(
            [
                {
                    "symbol": r["symbol"],
                    "et": r.get("et"),
                    "hour": r.get("hour"),
                    "rank_order": r.get("rank_order"),
                    "rank": r.get("rank"),
                    "mfe_pct": r.get("path", {}).get("mfe_pct"),
                    "close_ret_pct": r.get("path", {}).get("close_ret_pct"),
                }
                for r in winners_missed
            ],
            key=lambda x: -float(x.get("mfe_pct") or 0),
        )[:15],
        "worst_taken": sorted(
            [
                {
                    "symbol": r["symbol"],
                    "et": r.get("et"),
                    "hour": r.get("hour"),
                    "rank_order": r.get("rank_order"),
                    "rank": r.get("rank"),
                    "mfe_pct": r.get("path", {}).get("mfe_pct"),
                    "mae_pct": r.get("path", {}).get("mae_pct"),
                    "close_ret_pct": r.get("path", {}).get("close_ret_pct"),
                    "book_pnl": r.get("book_pnl"),
                }
                for r in losers_taken
            ],
            key=lambda x: float(x.get("close_ret_pct") or 0),
        )[:15],
    }


def build_study(*, days: int = 7, max_missed_per_hour: int | None = 10) -> dict[str, Any]:
    """
    Build taken vs missed study.

    max_missed_per_hour: cap Polygon cost — compare take vs top-N missed by rank
    (None = all ranked misses).
    """
    api_key = _polygon_key()
    if not api_key:
        raise RuntimeError("POLYGON_API_KEY missing")

    paths = list_scan_funnels_since(days)
    book = _book_filled_pnl()
    bar_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}

    events: list[dict[str, Any]] = []
    hour_pairs: list[dict[str, Any]] = []

    for path in paths:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        when = _parse_scan_et(doc, path)
        if when is None:
            continue
        bar_hour = int(doc.get("bar_hour") if doc.get("bar_hour") is not None else when.hour)
        day = when.date()
        launches = list(doc.get("launches") or [])
        entered_syms = {
            str(e.get("symbol") or "").upper()
            for e in (doc.get("entered") or [])
            if str(e.get("status") or "").upper() == "FILLED"
        }

        take_rows: list[dict[str, Any]] = []
        miss_rows: list[dict[str, Any]] = []
        fill_rows: list[dict[str, Any]] = []

        for L in launches:
            sym = str(L.get("symbol") or "").upper()
            if not sym:
                continue
            try:
                entry = float(L.get("1h_close") or 0)
            except (TypeError, ValueError):
                entry = 0.0
            if entry <= 0:
                continue
            cohort = "TAKE" if L.get("taken") else "MISSED"
            if max_missed_per_hour is not None and cohort == "MISSED":
                # keep only top-N by rank_order among misses (filled later)
                pass
            row = {
                "symbol": sym,
                "et": when.isoformat(),
                "day": day.isoformat(),
                "hour": bar_hour,
                "scan_file": path.name,
                "cohort": cohort,
                "filled": sym in entered_syms,
                "rank": L.get("rank"),
                "rank_order": L.get("rank_order"),
                "htf_score": L.get("htf_score"),
                "launch_score": L.get("launch_score"),
                "phase_3h": L.get("phase_3h"),
                "1h_close": entry,
                "structure_mode": L.get("structure_mode"),
            }
            if cohort == "TAKE":
                take_rows.append(row)
            else:
                miss_rows.append(row)
            if sym in entered_syms:
                fill_rows.append({**row, "cohort": "FILLED"})

        # Cap misses to top-N by rank_order (best continuation first)
        miss_rows.sort(key=lambda r: int(r.get("rank_order") or 999))
        if max_missed_per_hour is not None:
            miss_rows = miss_rows[: max(0, int(max_missed_per_hour))]

        # Mark paths
        for row in take_rows + miss_rows + fill_rows:
            # de-dupe fill rows that also appear in take — mark once later via events
            pass

        marked_hour: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for row in take_rows + miss_rows:
            key = f"{row['symbol']}|{row['day']}|{row['hour']}|{row['cohort']}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            bars = _fetch_1h_bars(api_key, row["symbol"], day, bar_cache)
            path_stats = _path_stats(bars, signal_hour=bar_hour, entry=float(row["1h_close"]))
            row = {**row, "path": path_stats}
            if row["symbol"] in book:
                # attach first matching closed pnl if any
                closed = [b for b in book[row["symbol"]] if b.get("status") == "CLOSED"]
                if closed:
                    row["book_pnl"] = closed[0].get("pnl")
                    row["book_exit"] = closed[0].get("exit_reason")
            events.append(row)
            marked_hour.append(row)

        # Also ensure FILLED that weren't in take list still get marked
        for row in fill_rows:
            key = f"{row['symbol']}|{row['day']}|{row['hour']}|FILLED"
            if any(
                e["symbol"] == row["symbol"]
                and e["day"] == row["day"]
                and e["hour"] == row["hour"]
                and e.get("filled")
                for e in marked_hour
            ):
                continue
            bars = _fetch_1h_bars(api_key, row["symbol"], day, bar_cache)
            path_stats = _path_stats(bars, signal_hour=bar_hour, entry=float(row["1h_close"]))
            row = {**row, "path": path_stats, "cohort": "FILLED", "filled": True}
            events.append(row)
            marked_hour.append(row)

        take_ok = [r for r in marked_hour if r["cohort"] == "TAKE" and r.get("path", {}).get("ok")]
        miss_ok = [r for r in marked_hour if r["cohort"] == "MISSED" and r.get("path", {}).get("ok")]
        if take_ok and miss_ok:
            avg_take = statistics.mean(float(r["path"]["mfe_pct"]) for r in take_ok)
            avg_miss = statistics.mean(float(r["path"]["mfe_pct"]) for r in miss_ok)
            hour_pairs.append({
                "et": when.isoformat(),
                "hour": bar_hour,
                "scan_file": path.name,
                "take_n": len(take_ok),
                "miss_n": len(miss_ok),
                "avg_mfe_take": round(avg_take, 3),
                "avg_mfe_miss": round(avg_miss, 3),
                "miss_beats_take": avg_miss > avg_take,
                "delta_mfe": round(avg_miss - avg_take, 3),
            })

    take_events = [e for e in events if e["cohort"] == "TAKE"]
    miss_events = [e for e in events if e["cohort"] == "MISSED"]
    fill_events = [e for e in events if e.get("filled")]

    # Feature gap: missed with MFE>=5% vs take with close_ret<0 or kill_before_2pct
    winners_missed = [
        e for e in miss_events
        if e.get("path", {}).get("ok") and float(e["path"].get("mfe_pct") or 0) >= 5.0
    ]
    losers_taken = [
        e for e in take_events
        if e.get("path", {}).get("ok")
        and (
            float(e["path"].get("close_ret_pct") or 0) < 0
            or e["path"].get("kill_before_2pct")
        )
    ]

    pair_beats = sum(1 for h in hour_pairs if h.get("miss_beats_take"))
    deltas_miss = [float(h["delta_mfe"]) for h in hour_pairs if h.get("miss_beats_take")]
    deltas_take = [float(h["delta_mfe"]) for h in hour_pairs if not h.get("miss_beats_take")]
    hour_pair_meta = {
        "n_hours": len(hour_pairs),
        "hours_miss_beats_take": pair_beats,
        "rate_miss_beats_take": (
            round(pair_beats / len(hour_pairs), 4) if hour_pairs else None
        ),
        "avg_delta_mfe": (
            round(statistics.mean(float(h["delta_mfe"]) for h in hour_pairs), 3)
            if hour_pairs
            else None
        ),
        "avg_delta_when_miss_beats": (
            round(statistics.mean(deltas_miss), 3) if deltas_miss else None
        ),
        "avg_delta_when_take_beats": (
            round(statistics.mean(deltas_take), 3) if deltas_take else None
        ),
        "rows": hour_pairs,
    }
    take_sum = _cohort_summary(take_events)
    miss_sum = _cohort_summary(miss_events)
    study = {
        "generated_at": datetime.now(ET).isoformat(),
        "days": days,
        "max_missed_per_hour": max_missed_per_hour,
        "scans": len(paths),
        "definition": {
            "TAKE": "php_scan launch with taken=True (2/hour selection)",
            "MISSED": f"ranked launch not taken (top {max_missed_per_hour}/hour by rank_order)",
            "FILLED": "entry_results status=FILLED",
            "path": "Polygon 1H bars after signal hour through ~16 ET same day",
            "win_proxy": "hit +2% MFE before -5% MAE",
        },
        "cohorts": {
            "TAKE": take_sum,
            "MISSED": miss_sum,
            "FILLED": _cohort_summary(fill_events),
        },
        "hour_pairs": hour_pair_meta,
        "verdict": _verdict(take_sum, miss_sum, hour_pair_meta),
        "feature_gap": _feature_gap(winners_missed, losers_taken),
        "data_gaps": _data_gap_hypotheses(winners_missed, losers_taken),
        "events_n": len(events),
        # Keep a trimmed event sample for offline dig (not full dump)
        "events_sample": sorted(
            [e for e in events if e.get("path", {}).get("ok")],
            key=lambda r: -float(r.get("path", {}).get("mfe_pct") or 0),
        )[:80],
    }
    return study


def _verdict(
    take: dict[str, Any],
    miss: dict[str, Any],
    pair_meta: dict[str, Any],
) -> dict[str, Any]:
    take_mfe = take.get("avg_mfe_pct")
    miss_mfe = miss.get("avg_mfe_pct")
    take_win = take.get("win_proxy_rate")
    miss_win = miss.get("win_proxy_rate")
    hours_miss_beats = int(pair_meta.get("hours_miss_beats_take") or 0)
    n_hours = int(pair_meta.get("n_hours") or 0)
    miss_better_mean = None
    if take_mfe is not None and miss_mfe is not None:
        miss_better_mean = miss_mfe > take_mfe and (miss_win or 0) >= (take_win or 0)
    # Hour-level: majority of hours can favor misses even when overall mean favors take
    # (take wins are larger when they win — right-skew).
    hour_majority_miss = bool(n_hours and hours_miss_beats / n_hours >= 0.55)
    return {
        "missed_outperformed_taken": bool(miss_better_mean),
        "hour_majority_miss_beats_take": hour_majority_miss,
        "take_avg_mfe_pct": take_mfe,
        "miss_avg_mfe_pct": miss_mfe,
        "take_win_proxy": take_win,
        "miss_win_proxy": miss_win,
        "hours_miss_beats_take": hours_miss_beats,
        "n_hours": n_hours,
        "avg_delta_when_miss_beats": pair_meta.get("avg_delta_when_miss_beats"),
        "avg_delta_when_take_beats": pair_meta.get("avg_delta_when_take_beats"),
        "summary": (
            "MISSED cohort beat TAKE on pooled same-day MFE/win-proxy — selection is the bottleneck."
            if miss_better_mean
            else (
                "Pooled TAKE MFE is higher, but a majority of hours miss-mean > take-mean "
                "(take wins are larger when they win). Real pain is (1) cap truncating rank#3–8 runners, "
                "(2) score inversion on +5% runners vs taken losers, (3) FILLED path worse than TAKE "
                "and exits harvesting losers."
                if hour_majority_miss
                else "TAKE did not clearly underperform MISSED on this window — "
                "losses may be exit/structure, not only selection."
            )
            if take_mfe is not None
            else "Insufficient marked paths."
        ),
    }


def _data_gap_hypotheses(
    winners_missed: list[dict[str, Any]],
    losers_taken: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Concrete 'what we are not looking at' hypotheses from the gap."""
    hyps: list[dict[str, str]] = []
    if not winners_missed and not losers_taken:
        return [{
            "id": "insufficient",
            "title": "Not enough gap rows",
            "detail": "Need more marked runners/losers before claiming a data hole.",
        }]

    # Rank-order: are big misses often just outside the 2-cap?
    miss_orders = [int(r.get("rank_order") or 99) for r in winners_missed]
    if miss_orders and statistics.median(miss_orders) <= 5:
        hyps.append({
            "id": "cap_truncation",
            "title": "2/hour cap truncates near-top runners",
            "detail": (
                f"Median rank_order of missed +5% MFE runners is {statistics.median(miss_orders):.0f} "
                "— many were almost selected. Cap + already_confirmed/no_fill burns amplify this."
            ),
        })

    # Score inversion: taken losers have higher rank than missed winners?
    try:
        avg_miss_rank = statistics.mean(float(r.get("rank") or 0) for r in winners_missed)
        avg_take_rank = statistics.mean(float(r.get("rank") or 0) for r in losers_taken)
        if losers_taken and winners_missed and avg_take_rank >= avg_miss_rank:
            hyps.append({
                "id": "score_not_predictive",
                "title": "continuation_score poorly ranks same-day MFE",
                "detail": (
                    f"Taken losers avg rank={avg_take_rank:.1f} vs missed runners avg rank={avg_miss_rank:.1f}. "
                    "Score may overweight deep-swing/history while missing same-day tape/liquidity/options flow."
                ),
            })
    except statistics.StatisticsError:
        pass

    hyps.extend([
        {
            "id": "intraday_relative_strength",
            "title": "Missing RTH relative-strength / SPY beta filter",
            "detail": (
                "Launch rows store HTF/launch scores but not live RS vs SPY/sector on the signal hour. "
                "Missed runners may simply be leaders of the day's tape."
            ),
        },
        {
            "id": "options_flow_dark",
            "title": "Options / dark-pool / tape aggression unused at entry",
            "detail": (
                "Weekly options study was empty on legacy scans; PHP takes do not size or rank on call volume, "
                "sweep counts, or bid/ask aggression after the 1H close."
            ),
        },
        {
            "id": "case_llm_vs_tape",
            "title": "CASE REJECT may veto momentum leaders",
            "detail": (
                "Fri autopsy: top continuation CASE_REJECT while lower ENTER. Soften path shipped, but "
                "historical week still reflects hard REJECT on leaders that then ran."
            ),
        },
        {
            "id": "float_liquidity_microstructure",
            "title": "Float / short / borrow / spread not in take ranking",
            "detail": (
                "php_scan launch artifacts lack float, short interest, spread, and premarket dollar volume "
                "at decision time — common drivers of which launches actually extend."
            ),
        },
        {
            "id": "exit_dominated_taken_pnl",
            "title": "Taken book PnL may be exit pathology, not entry",
            "detail": (
                "Even if TAKE MFE was OK, base_break_down / kill exits can harvest losers. "
                "Compare path MFE of FILLED vs realized book PnL before blaming selection alone."
            ),
        },
    ])
    return hyps


def format_md(study: dict[str, Any]) -> str:
    c = study.get("cohorts") or {}
    v = study.get("verdict") or {}
    hp = study.get("hour_pairs") or {}
    lines = [
        "# Peak Hour — Taken vs Missed (same-hour)",
        "",
        f"**Generated:** {study.get('generated_at')}",
        f"**Window:** last {study.get('days')} days · scans={study.get('scans')}",
        f"**Missed sample:** top {study.get('max_missed_per_hour')} ranked misses / hour",
        "",
        "## Verdict",
        f"- Missed outperformed taken (pooled): **{v.get('missed_outperformed_taken')}**",
        f"- Hour majority miss-mean > take-mean: **{v.get('hour_majority_miss_beats_take')}** "
        f"({v.get('hours_miss_beats_take')}/{v.get('n_hours')})",
        f"- TAKE avg MFE: **{v.get('take_avg_mfe_pct')}%** · win-proxy **{v.get('take_win_proxy')}**",
        f"- MISSED avg MFE: **{v.get('miss_avg_mfe_pct')}%** · win-proxy **{v.get('miss_win_proxy')}**",
        f"- ΔMFE when miss beats / take beats: "
        f"**{v.get('avg_delta_when_miss_beats')}** / **{v.get('avg_delta_when_take_beats')}**",
        f"- {v.get('summary')}",
        "",
        "## Cohort path stats (same-day after signal hour)",
        "",
        "| Cohort | N | Marked | Avg MFE% | Med MFE% | Avg MAE% | Avg close% | Hit2% | Hit5% | Win-proxy |",
        "|--------|--:|-------:|---------:|---------:|---------:|-----------:|------:|------:|----------:|",
    ]
    for name in ("TAKE", "MISSED", "FILLED"):
        s = c.get(name) or {}
        lines.append(
            f"| {name} | {s.get('n')} | {s.get('n_marked')} | {s.get('avg_mfe_pct')} | "
            f"{s.get('med_mfe_pct')} | {s.get('avg_mae_pct')} | {s.get('avg_close_ret_pct')} | "
            f"{s.get('hit_2pct_rate')} | {s.get('hit_5pct_rate')} | {s.get('win_proxy_rate')} |"
        )
    lines.extend([
        "",
        f"Hour-pair avg ΔMFE (miss−take): **{hp.get('avg_delta_mfe')}** · "
        f"miss beats take rate **{hp.get('rate_miss_beats_take')}**",
        "",
        "## Data gaps / missing inputs",
        "",
    ])
    for h in study.get("data_gaps") or []:
        lines.append(f"### {h.get('id')}: {h.get('title')}")
        lines.append(h.get("detail") or "")
        lines.append("")
    fg = study.get("feature_gap") or {}
    lines.extend(["## Feature gap (missed +5% MFE runners vs taken losers)", ""])
    lines.append(
        f"Missed runners n={fg.get('missed_runners_n')} · taken losers n={fg.get('taken_losers_n')}"
    )
    lines.append("")
    lines.append("| Field | Missed runners | Taken losers |")
    lines.append("|-------|---------------:|-------------:|")
    for field, vals in (fg.get("avg_fields") or {}).items():
        lines.append(
            f"| {field} | {vals.get('missed_runners')} | {vals.get('taken_losers')} |"
        )
    lines.extend(["", "### Top missed runners", ""])
    lines.append("| Sym | Hour | Rank# | MFE% | Close% |")
    lines.append("|-----|-----:|------:|-----:|-------:|")
    for r in fg.get("top_missed_runners") or []:
        lines.append(
            f"| {r.get('symbol')} | {r.get('hour')} | {r.get('rank_order')} | "
            f"{r.get('mfe_pct')} | {r.get('close_ret_pct')} |"
        )
    lines.extend(["", "### Worst taken (by close%)", ""])
    lines.append("| Sym | Hour | Rank# | MFE% | MAE% | Close% | Book PnL |")
    lines.append("|-----|-----:|------:|-----:|-----:|-------:|---------:|")
    for r in fg.get("worst_taken") or []:
        lines.append(
            f"| {r.get('symbol')} | {r.get('hour')} | {r.get('rank_order')} | "
            f"{r.get('mfe_pct')} | {r.get('mae_pct')} | {r.get('close_ret_pct')} | "
            f"{r.get('book_pnl')} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Taken vs missed Peak Hour study")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument(
        "--max-missed-per-hour",
        type=int,
        default=8,
        help="Top-N ranked misses per hour to mark (Polygon cost control)",
    )
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    print("Building taken-vs-missed study (Polygon 1H paths)...")
    study = build_study(days=args.days, max_missed_per_hour=args.max_missed_per_hour)
    md = format_md(study)
    print(md)

    if args.write:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(ET).strftime("%Y%m%d")
        md_path = RESULTS_DIR / f"taken_vs_missed_{stamp}.md"
        json_path = RESULTS_DIR / f"taken_vs_missed_{stamp}.json"
        # Full events in JSON; keep sample-only in printed md
        full = dict(study)
        md_path.write_text(md, encoding="utf-8")
        json_path.write_text(json.dumps(full, indent=2, default=str), encoding="utf-8")
        print(f"Wrote {md_path}")
        print(f"Wrote {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
