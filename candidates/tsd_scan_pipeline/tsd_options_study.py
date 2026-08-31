"""
Q-ALPHA TSD pipeline — weekly options overlay retrospective study.

Reads scan snapshots from the last N trading days, labels forward outcomes
for tier cohorts (top-100 by score, signals, watch-10, trade-3, filled),
and compares scan_score ranking vs a lightweight options overlay.

Does NOT affect live trading. Run offline after the week (Friday 5 PM ET).

Usage:
  py -3 candidates/tsd_scan_pipeline/tsd_options_study.py
  py -3 candidates/tsd_scan_pipeline/tsd_options_study.py --days 5 --write
  py -3 candidates/tsd_scan_pipeline/tsd_options_study.py --write --skip-options
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytz

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

from state_paths import is_trading_day  # noqa: E402
from tsd_scan_pipeline.tsd_profiler import (  # noqa: E402
    HOLD_DAYS_PRIMARY,
    fetch_daily_bars_polygon,
    _measure_analog_mae_mfe,
)
from tsd_scan_pipeline.universe_tsd import POLYGON_BASE, load_polygon_key, polygon_get  # noqa: E402

ET = pytz.timezone("America/New_York")
RESULTS_DIR = PIPELINE_DIR / "results"

TOP_N_SCORE = 100
WATCH_N = 10
TRADE_N = 3
FALLBACK_KILL_PCT = 0.07
SCAN_SCORE_MIN = 60
OPTIONS_NEAR_DTE = 45
OPTIONS_STRIKE_PCT = 0.10
OPTIONS_SLEEP_SEC = 0.12


def _trading_days_back(n: int, from_date: date | None = None) -> list[date]:
    """Last n trading days ending at from_date (default today ET)."""
    end = from_date or datetime.now(ET).date()
    out: list[date] = []
    d = end
    while len(out) < n:
        if is_trading_day(d):
            out.append(d)
        d -= timedelta(days=1)
        if (end - d).days > n * 3:
            break
    return sorted(out)


def _load_scan_snapshots(since: date) -> list[dict[str, Any]]:
    snaps: list[dict[str, Any]] = []
    for path in sorted(RESULTS_DIR.glob("scan_*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        scanned = str(doc.get("scanned_at") or "")[:10]
        if scanned and date.fromisoformat(scanned) >= since:
            doc["_path"] = str(path.name)
            snaps.append(doc)
    return snaps


def _parse_signal_date(scanned_at: str) -> date | None:
    try:
        return date.fromisoformat(str(scanned_at)[:10])
    except ValueError:
        return None


def _outcome_label(mfe_pct: float | None, mae_pct: float | None, kill_pct: float) -> str:
    """WIN if MFE >= 2× kill before MAE breaches kill; else LOSS or FLAT."""
    if mfe_pct is None or mae_pct is None:
        return "UNKNOWN"
    if mfe_pct >= 2.0 * kill_pct and mae_pct < kill_pct:
        return "WIN"
    if mae_pct >= kill_pct:
        return "LOSS"
    return "FLAT"


def _forward_close_return(
    daily_bars: list[dict],
    entry_date: date,
    entry_price: float,
    hold_days: int,
) -> float | None:
    future = [b for b in daily_bars if b["date"] > entry_date][:hold_days]
    if not future:
        return None
    return (future[-1]["close"] - entry_price) / entry_price


def _fetch_options_day_volume(
    api_key: str,
    symbol: str,
    as_of: date,
    spot: float,
) -> dict[str, Any]:
    """
    Best-effort same-day call/put volume near ATM via Polygon options contracts.

    Returns empty dict when the API tier blocks options or no chain exists.
    """
    out: dict[str, Any] = {
        "options_available": False,
        "call_volume": 0,
        "put_volume": 0,
        "pc_ratio": None,
        "contracts_checked": 0,
    }
    if spot <= 0:
        return out

    exp_gte = as_of.isoformat()
    url = f"{POLYGON_BASE}/v3/reference/options/contracts"
    params: dict[str, Any] = {
        "underlying_ticker": symbol.upper(),
        "expiration_date.gte": exp_gte,
        "limit": 250,
        "sort": "expiration_date",
        "order": "asc",
    }
    try:
        data = polygon_get(url, params, api_key)
    except Exception as exc:
        out["options_error"] = str(exc)
        return out

    contracts = data.get("results") or []
    if not contracts:
        out["options_error"] = "no_contracts"
        return out

    exp_cutoff = as_of + timedelta(days=OPTIONS_NEAR_DTE)
    call_vol = 0
    put_vol = 0
    checked = 0

    for c in contracts:
        exp_raw = c.get("expiration_date")
        if not exp_raw:
            continue
        try:
            exp_d = date.fromisoformat(str(exp_raw)[:10])
        except ValueError:
            continue
        if exp_d > exp_cutoff:
            break

        strike = float(c.get("strike_price") or 0)
        if strike <= 0 or abs(strike - spot) / spot > OPTIONS_STRIKE_PCT:
            continue

        ticker = str(c.get("ticker") or "")
        if not ticker:
            continue

        agg_url = (
            f"{POLYGON_BASE}/v2/aggs/ticker/{ticker}/range/1/day/"
            f"{as_of}/{as_of}"
        )
        try:
            agg = polygon_get(agg_url, {"adjusted": "true"}, api_key)
        except Exception:
            continue

        vol = 0
        for bar in agg.get("results") or []:
            vol += int(bar.get("v") or 0)

        ctype = str(c.get("contract_type") or "").lower()
        if ctype == "call":
            call_vol += vol
        elif ctype == "put":
            put_vol += vol
        checked += 1
        time.sleep(OPTIONS_SLEEP_SEC)

        if checked >= 40:
            break

    if checked == 0:
        out["options_error"] = "no_near_atm_contracts"
        return out

    out["options_available"] = True
    out["call_volume"] = call_vol
    out["put_volume"] = put_vol
    out["contracts_checked"] = checked
    if call_vol > 0:
        out["pc_ratio"] = round(put_vol / call_vol, 4)
    elif put_vol > 0:
        out["pc_ratio"] = 999.0
    else:
        out["pc_ratio"] = None
    return out


def compute_options_score(ctx: dict[str, Any]) -> float | None:
    """
    Map same-day call/put volume context to 0-100 score.

    Higher = more bullish positioning support for a long swing.
    """
    if not ctx.get("options_available"):
        return None

    call_vol = int(ctx.get("call_volume") or 0)
    put_vol = int(ctx.get("put_volume") or 0)
    total = call_vol + put_vol
    if total <= 0:
        return 50.0

    call_share = call_vol / total
    pc = ctx.get("pc_ratio")
    pc_component = 0.5
    if pc is not None and pc < 999:
        pc_component = max(0.0, min(1.0, 1.0 - (float(pc) / 2.0)))

    activity = min(1.0, total / 50_000.0)
    score = (call_share * 50.0) + (pc_component * 30.0) + (activity * 20.0)
    return round(min(100.0, max(0.0, score)), 2)


def _composite_score(scan_score: float, options_score: float | None, weight: float = 0.3) -> float:
    if options_score is None:
        return scan_score
    return round(scan_score * (1.0 - weight) + options_score * weight, 2)


def _build_scan_events(scan: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten one scan snapshot into tier-tagged signal rows."""
    scanned_at = str(scan.get("scanned_at") or "")
    signal_date = _parse_signal_date(scanned_at)
    if signal_date is None:
        return []

    all_rows = list(scan.get("all_rows") or [])
    signal_syms = {r.get("symbol") for r in (scan.get("signal_candidates") or [])}
    watch_syms = [r.get("symbol") for r in (scan.get("watch_top_10") or [])]
    trade_syms = [r.get("symbol") for r in (scan.get("trade_top_3") or [])]
    filled_syms = {
        r.get("symbol")
        for r in (scan.get("entry_results") or [])
        if r.get("status") == "FILLED"
    }

    watch_set = set(watch_syms)
    trade_set = set(trade_syms)

    ranked = sorted(
        [r for r in all_rows if r.get("scan_score") is not None],
        key=lambda r: float(r.get("scan_score") or 0),
        reverse=True,
    )
    top100_set = {r.get("symbol") for r in ranked[:TOP_N_SCORE]}

    events: list[dict[str, Any]] = []
    for row in all_rows:
        sym = row.get("symbol")
        if not sym:
            continue
        tiers: list[str] = ["scanned"]
        if sym in top100_set:
            tiers.append("top100_score")
        if sym in signal_syms:
            tiers.append("signal")
        if sym in watch_set:
            tiers.append("watch10")
        if sym in trade_set:
            tiers.append("trade3")
        if sym in filled_syms:
            tiers.append("filled")

        events.append(
            {
                "scan_path": scan.get("_path"),
                "scanned_at": scanned_at,
                "signal_date": signal_date.isoformat(),
                "symbol": sym,
                "tiers": tiers,
                "scan_score": row.get("scan_score"),
                "trend_strength": row.get("trend_strength"),
                "buy_signal": bool(row.get("buy_signal")),
                "pass": bool(row.get("pass")),
                "reject_reason": row.get("reject_reason"),
                "entry_price": row.get("close"),
                "kill_pct": FALLBACK_KILL_PCT,
            }
        )
    return events


def _enrich_outcomes(
    events: list[dict[str, Any]],
    api_key: str,
    *,
    skip_options: bool,
) -> list[dict[str, Any]]:
    """Attach forward returns, MAE/MFE, and optional options overlay."""
    daily_cache: dict[str, list[dict]] = {}
    enriched: list[dict[str, Any]] = []

    for ev in events:
        row = dict(ev)
        sym = str(row["symbol"]).upper()
        sig_date = date.fromisoformat(row["signal_date"])
        entry_price = float(row.get("entry_price") or 0)
        kill_pct = float(row.get("kill_pct") or FALLBACK_KILL_PCT)

        if entry_price <= 0:
            row["outcome"] = "UNKNOWN"
            enriched.append(row)
            continue

        if sym not in daily_cache:
            start = sig_date - timedelta(days=5)
            end = sig_date + timedelta(days=HOLD_DAYS_PRIMARY + 15)
            try:
                daily_cache[sym] = fetch_daily_bars_polygon(api_key, sym, start, end)
            except Exception as exc:
                daily_cache[sym] = []
                row["outcome_error"] = str(exc)
            time.sleep(OPTIONS_SLEEP_SEC)

        daily = daily_cache[sym]
        mm = _measure_analog_mae_mfe(entry_price, sig_date, daily, HOLD_DAYS_PRIMARY)
        if mm:
            row["mae_pct"] = round(mm["mae_pct"], 4)
            row["mfe_pct"] = round(mm["mfe_pct"], 4)
            row["outcome"] = _outcome_label(mm["mfe_pct"], mm["mae_pct"], kill_pct)
        else:
            row["outcome"] = "UNKNOWN"

        ret = _forward_close_return(daily, sig_date, entry_price, HOLD_DAYS_PRIMARY)
        if ret is not None:
            row["return_5d"] = round(ret, 4)

        if not skip_options and "signal" in row.get("tiers", []):
            ctx = _fetch_options_day_volume(api_key, sym, sig_date, entry_price)
            row["options"] = ctx
            row["options_score"] = compute_options_score(ctx)
            row["composite_score"] = _composite_score(
                float(row.get("scan_score") or 0),
                row.get("options_score"),
            )

        enriched.append(row)

    return enriched


def _cohort_stats(events: list[dict[str, Any]], tier: str) -> dict[str, Any]:
    """Aggregate outcome stats for a tier (one row per symbol-scan; signals only once)."""
    rows = [e for e in events if tier in (e.get("tiers") or [])]
    if tier == "signal":
        rows = [e for e in rows if e.get("pass")]

    returns = [e["return_5d"] for e in rows if e.get("return_5d") is not None]
    outcomes = [e.get("outcome") for e in rows if e.get("outcome") not in (None, "UNKNOWN")]

    wins = sum(1 for o in outcomes if o == "WIN")
    losses = sum(1 for o in outcomes if o == "LOSS")
    flats = sum(1 for o in outcomes if o == "FLAT")

    return {
        "tier": tier,
        "count": len(rows),
        "avg_return_5d": round(sum(returns) / len(returns), 4) if returns else None,
        "win": wins,
        "loss": losses,
        "flat": flats,
        "unknown": len(rows) - len(outcomes),
    }


def _counterfactual_comparison(events: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Per scan cycle: compare top-3 by scan_score vs top-3 by composite_score
    among profiler-eligible signals (pass=True with options_score).
    """
    by_scan: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        if not e.get("pass"):
            continue
        if e.get("options_score") is None:
            continue
        key = str(e.get("scanned_at"))
        by_scan.setdefault(key, []).append(e)

    comparisons: list[dict[str, Any]] = []
    scan_score_wins = 0
    composite_wins = 0
    ties = 0

    for scanned_at, rows in by_scan.items():
        by_scan_score = sorted(rows, key=lambda r: float(r.get("scan_score") or 0), reverse=True)[:TRADE_N]
        by_composite = sorted(rows, key=lambda r: float(r.get("composite_score") or 0), reverse=True)[:TRADE_N]

        ss_ret = [
            r["return_5d"] for r in by_scan_score if r.get("return_5d") is not None
        ]
        comp_ret = [
            r["return_5d"] for r in by_composite if r.get("return_5d") is not None
        ]
        if not ss_ret or not comp_ret:
            continue

        avg_ss = sum(ss_ret) / len(ss_ret)
        avg_comp = sum(comp_ret) / len(comp_ret)
        if avg_comp > avg_ss + 0.001:
            composite_wins += 1
        elif avg_ss > avg_comp + 0.001:
            scan_score_wins += 1
        else:
            ties += 1

        comparisons.append(
            {
                "scanned_at": scanned_at,
                "scan_score_top3": [r["symbol"] for r in by_scan_score],
                "composite_top3": [r["symbol"] for r in by_composite],
                "avg_return_scan": round(avg_ss, 4),
                "avg_return_composite": round(avg_comp, 4),
            }
        )

    return {
        "cycles_compared": len(comparisons),
        "composite_better_cycles": composite_wins,
        "scan_score_better_cycles": scan_score_wins,
        "tie_cycles": ties,
        "details": comparisons,
    }


def build_study(*, days: int = 5, skip_options: bool = False) -> dict[str, Any]:
    """Build full weekly study payload."""
    window_days = _trading_days_back(days)
    since = window_days[0] if window_days else datetime.now(ET).date()
    scans = _load_scan_snapshots(since)

    raw_events: list[dict[str, Any]] = []
    for scan in scans:
        raw_events.extend(_build_scan_events(scan))

    api_key = load_polygon_key()
    events = _enrich_outcomes(raw_events, api_key, skip_options=skip_options)

    cohorts = [
        _cohort_stats(events, tier)
        for tier in ("top100_score", "signal", "watch10", "trade3", "filled")
    ]

    signals_with_options = [
        e for e in events if e.get("pass") and e.get("options_score") is not None
    ]

    return {
        "generated_at": datetime.now(ET).isoformat(),
        "window_trading_days": [d.isoformat() for d in window_days],
        "window_start": since.isoformat(),
        "scans_in_window": len(scans),
        "events_total": len(events),
        "signals_passed": sum(1 for e in events if e.get("pass")),
        "signals_with_options_data": len(signals_with_options),
        "skip_options": skip_options,
        "cohorts": cohorts,
        "counterfactual": _counterfactual_comparison(events),
        "events": events,
    }


def format_study_md(study: dict[str, Any]) -> str:
    """Render study as markdown report."""
    lines = [
        "# TSD Options Overlay Study",
        "",
        f"**Generated:** {study.get('generated_at')}",
        f"**Window:** {study.get('window_start')} to today "
        f"({len(study.get('window_trading_days') or [])} trading days)",
        f"**Scans in window:** {study.get('scans_in_window')}",
        f"**Signals passed:** {study.get('signals_passed')}",
        f"**Signals with options data:** {study.get('signals_with_options_data')}",
        "",
        "## Cohort outcomes (5d hold)",
        "",
        "| Tier | N | Avg 5d return | Win | Loss | Flat |",
        "|------|---|---------------|-----|------|------|",
    ]

    for c in study.get("cohorts") or []:
        avg = c.get("avg_return_5d")
        avg_s = f"{avg:+.2%}" if avg is not None else "n/a"
        lines.append(
            f"| {c.get('tier')} | {c.get('count')} | {avg_s} | "
            f"{c.get('win')} | {c.get('loss')} | {c.get('flat')} |"
        )

    cf = study.get("counterfactual") or {}
    lines.extend(
        [
            "",
            "## Counterfactual ranking (scan_score vs composite)",
            "",
            f"- Cycles compared: **{cf.get('cycles_compared', 0)}**",
            f"- Composite top-3 better: **{cf.get('composite_better_cycles', 0)}**",
            f"- Scan-score top-3 better: **{cf.get('scan_score_better_cycles', 0)}**",
            f"- Ties: **{cf.get('tie_cycles', 0)}**",
            "",
        ]
    )

    if study.get("skip_options"):
        lines.append(
            "*Options overlay skipped (`--skip-options`). Outcome cohorts only.*"
        )
    elif (study.get("signals_with_options_data") or 0) == 0:
        lines.append(
            "*No options data retrieved — check Polygon options tier or re-run without "
            "`--skip-options` when signals exist.*"
        )

    lines.append("")
    lines.append("---")
    lines.append("*Offline study only - does not modify live TSD scoring.*")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="TSD weekly options overlay retrospective")
    parser.add_argument("--days", type=int, default=5, help="Trading days to include")
    parser.add_argument("--write", action="store_true", help="Write results/options_study_*.md/json")
    parser.add_argument(
        "--skip-options",
        action="store_true",
        help="Outcome cohorts only (faster; no Polygon options API calls)",
    )
    args = parser.parse_args()

    study = build_study(days=args.days, skip_options=args.skip_options)
    md = format_study_md(study)
    print(md)

    if args.write:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(ET).strftime("%Y%m%d")
        md_path = RESULTS_DIR / f"options_study_{stamp}.md"
        json_path = RESULTS_DIR / f"options_study_{stamp}.json"
        md_path.write_text(md, encoding="utf-8")
        json_path.write_text(json.dumps(study, indent=2, default=str), encoding="utf-8")
        print(f"Wrote {md_path}")
        print(f"Wrote {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
