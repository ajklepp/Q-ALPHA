"""
strategy_lab/scoreboard.py — head-to-head backtest of Strategy A vs B.

For each setup in results/setups.json, runs BOTH strategy_a and strategy_b on the
SAME entry / bars / profile. Compounds two independent $3000 pools chronologically
by flag_date, then writes:

  results/scoreboard.json  — headline + segmented breakdowns
  results/per_setup.csv    — one row per setup

Does NOT modify agent files.
"""
from __future__ import annotations

import csv
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
LAB = Path(__file__).resolve().parent
sys.path.insert(0, str(LAB))
sys.path.insert(0, str(ROOT / "candidates"))

from strategy_a import (  # noqa: E402
    BARS_DIR,
    HISTORY_PATH,
    POOL_USD,
    PROFILES_DIR,
    fetch_daily_after,
    load_minute_bars,
    load_profile,
    run_strategy_a,
)
from strategy_b import run_strategy_b  # noqa: E402

SETUPS_PATH = LAB / "results" / "setups.json"
DAILY_CACHE_DIR = LAB / "results" / "daily_cache"
SCOREBOARD_PATH = LAB / "results" / "scoreboard.json"
PER_SETUP_CSV = LAB / "results" / "per_setup.csv"

MFE_BUCKETS = (
    ("<10%", 0.0, 10.0),
    ("10-25%", 10.0, 25.0),
    (">25%", 25.0, 1e9),
)
CONF_ORDER = ("HIGH", "MEDIUM", "LOW", "INSUFFICIENT")


def bars_path_for(ticker: str, flag_date: str, hist_row: dict | None) -> Path:
    if hist_row:
        rel = hist_row.get("minute_bars_path") or hist_row.get("bars_path")
        if rel:
            p = Path(str(rel))
            return p if p.is_absolute() else ROOT / p
    return BARS_DIR / f"{ticker.upper()}_{flag_date}.json"


def load_daily_cached(ticker: str, flag_date: str) -> list[dict]:
    """Fetch daily bars once per setup; cache under results/daily_cache/."""
    DAILY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = DAILY_CACHE_DIR / f"{ticker.upper()}_{flag_date}.json"
    if cache_path.exists():
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        return list(data.get("bars") or [])
    bars = fetch_daily_after(ticker, flag_date)
    cache_path.write_text(
        json.dumps(
            {"ticker": ticker.upper(), "flag_date": flag_date, "bars": bars},
            indent=2,
        ),
        encoding="utf-8",
    )
    # No sleep: Stocks Developer = unlimited REST (15-min delayed feed).
    return bars


def exit_mix(res: dict[str, Any]) -> dict[str, int]:
    """Normalize A/B exit reasons into a shared key set (tranche counts)."""
    counts = {"trail": 0, "target": 0, "kill": 0, "time_cap": 0}
    for t in res.get("tranches") or []:
        r = str(t.get("exit_reason") or "")
        if r in counts:
            counts[r] += 1
    if any(counts.values()):
        return counts
    raw = res.get("exit_reason_counts") or {}
    for k in counts:
        counts[k] = int(raw.get(k) or 0)
    return counts


def mfe_bucket(mfe_pct: float | None) -> str:
    if mfe_pct is None:
        return "unknown"
    for label, lo, hi in MFE_BUCKETS:
        if lo <= float(mfe_pct) < hi:
            return label
    return "unknown"


def summarize_returns(returns: list[float]) -> dict[str, Any]:
    if not returns:
        return {
            "n": 0,
            "win_rate_pct": None,
            "avg_return_pct": None,
            "median_return_pct": None,
        }
    wins = sum(1 for r in returns if r > 0)
    ordered = sorted(returns)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        median = ordered[mid]
    else:
        median = (ordered[mid - 1] + ordered[mid]) / 2.0
    return {
        "n": len(returns),
        "win_rate_pct": round(100.0 * wins / len(returns), 2),
        "avg_return_pct": round(sum(returns) / len(returns), 4),
        "median_return_pct": round(median, 4),
    }


def compound_pool(
    rows: list[dict[str, Any]],
    return_key: str,
    *,
    start_equity: float = POOL_USD,
) -> dict[str, Any]:
    """
    Compound an independent pool by applying each setup's position return %
    in chronological order (rows already sorted by flag_date).
    """
    equity = float(start_equity)
    peak = equity
    max_dd = 0.0
    curve: list[dict[str, Any]] = [
        {"i": 0, "flag_date": None, "equity": round(equity, 4), "drawdown_pct": 0.0}
    ]
    for i, row in enumerate(rows, start=1):
        ret = float(row[return_key])
        equity *= 1.0 + ret / 100.0
        peak = max(peak, equity)
        dd = (equity - peak) / peak if peak > 0 else 0.0
        max_dd = min(max_dd, dd)
        curve.append({
            "i": i,
            "ticker": row["ticker"],
            "flag_date": row["flag_date"],
            "return_pct": round(ret, 4),
            "equity": round(equity, 4),
            "drawdown_pct": round(dd * 100.0, 4),
        })
    total_ret = (equity / start_equity - 1.0) * 100.0 if start_equity else 0.0
    return {
        "start_equity": start_equity,
        "final_equity": round(equity, 4),
        "total_return_pct": round(total_ret, 4),
        "max_drawdown_pct": round(max_dd * 100.0, 4),
        "n_trades": len(rows),
        "equity_curve": curve,
    }


def run_one_setup(
    ticker: str,
    flag_date: str,
    hist_row: dict,
) -> dict[str, Any] | None:
    """Load shared inputs once; run A and B. Return None if bars missing."""
    bars_path = bars_path_for(ticker, flag_date, hist_row)
    if not bars_path.exists():
        print(f"  SKIP {ticker}|{flag_date} — missing bars {bars_path.name}")
        return None

    profile_path = PROFILES_DIR / f"{ticker.upper()}_{flag_date}.json"
    if profile_path.exists():
        profile = load_profile(ticker, flag_date)
    else:
        print(f"  WARN {ticker}|{flag_date} — missing profile, using INSUFFICIENT fallbacks")
        profile = {
            "ticker": ticker.upper(),
            "as_of_date": flag_date,
            "confidence": "INSUFFICIENT",
            "stats_meaningful": False,
            "bracket": {},
            "percentiles": {},
        }

    entry_price = float(hist_row["entry_price"])
    entry_time = str(hist_row["entry_time"])
    minute_bars = load_minute_bars(bars_path)
    if not minute_bars:
        print(f"  SKIP {ticker}|{flag_date} — empty minute bars")
        return None

    daily_bars = load_daily_cached(ticker, flag_date)
    confidence = str(profile.get("confidence") or "INSUFFICIENT").upper()
    regime = (
        (hist_row.get("setup_meta") or {}).get("regime")
        or profile.get("regime")
        or "n/a"
    )

    common = dict(
        ticker=ticker.upper(),
        flag_date=flag_date,
        entry_price=entry_price,
        entry_time=entry_time,
        minute_bars=minute_bars,
        daily_bars=daily_bars,
        profile=profile,
    )
    try:
        res_a = run_strategy_a(**common)
        res_b = run_strategy_b(**common)
    except Exception as exc:
        print(f"  SKIP {ticker}|{flag_date} — run error: {exc}")
        return None

    if res_a.get("status") != "ok" or res_b.get("status") != "ok":
        print(
            f"  SKIP {ticker}|{flag_date} — "
            f"A={res_a.get('status')}/{res_a.get('skip_reason')} "
            f"B={res_b.get('status')}/{res_b.get('skip_reason')}"
        )
        return None

    a_ret = float(res_a["total_return_pct"])
    b_ret = float(res_b["total_return_pct"])
    mfe = max(
        float(res_a.get("mfe_pct") or 0.0),
        float(res_b.get("mfe_pct") or 0.0),
    )

    if a_ret > b_ret:
        winner = "A"
    elif b_ret > a_ret:
        winner = "B"
    else:
        winner = "tie"

    return {
        "ticker": ticker.upper(),
        "flag_date": flag_date,
        "confidence": confidence,
        "regime": str(regime),
        "mfe_pct": round(mfe, 4),
        "mfe_bucket": mfe_bucket(mfe),
        "a_return_pct": round(a_ret, 4),
        "b_return_pct": round(b_ret, 4),
        "a_days_held": int(res_a.get("days_held") or 0),
        "b_days_held": int(res_b.get("days_held") or 0),
        "a_exit_mix": exit_mix(res_a),
        "b_exit_mix": exit_mix(res_b),
        "winner": winner,
        "a_winner": winner == "A",
        "b_winner": winner == "B",
    }


def _segment_stats(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        buckets[str(r.get(key) or "n/a")].append(r)

    labels = list(buckets.keys())
    if key == "confidence":
        labels = [c for c in CONF_ORDER if c in buckets] + [
            c for c in labels if c not in CONF_ORDER
        ]
    elif key == "mfe_bucket":
        pref = [b[0] for b in MFE_BUCKETS] + ["unknown"]
        labels = [c for c in pref if c in buckets] + [
            c for c in labels if c not in pref
        ]

    out: dict[str, Any] = {}
    for label in labels:
        group = buckets[label]
        a_rets = [float(r["a_return_pct"]) for r in group]
        b_rets = [float(r["b_return_pct"]) for r in group]
        a_wins = sum(1 for r in group if r["winner"] == "A")
        b_wins = sum(1 for r in group if r["winner"] == "B")
        ties = sum(1 for r in group if r["winner"] == "tie")
        out[label] = {
            "n": len(group),
            "A": summarize_returns(a_rets),
            "B": summarize_returns(b_rets),
            "a_beats_b": a_wins,
            "b_beats_a": b_wins,
            "ties": ties,
            "edge_A_minus_B_avg_pct": round(
                (sum(a_rets) - sum(b_rets)) / len(group), 4
            ),
        }
    return out


def _sum_exit_mix(rows: list[dict[str, Any]], mix_key: str) -> dict[str, int]:
    totals = {"trail": 0, "target": 0, "kill": 0, "time_cap": 0}
    for r in rows:
        mix = r.get(mix_key) or {}
        for k in totals:
            totals[k] += int(mix.get(k) or 0)
    return totals


def build_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    a_pool = compound_pool(rows, "a_return_pct")
    b_pool = compound_pool(rows, "b_return_pct")
    a_rets = [float(r["a_return_pct"]) for r in rows]
    b_rets = [float(r["b_return_pct"]) for r in rows]
    a_mix = _sum_exit_mix(rows, "a_exit_mix")
    b_mix = _sum_exit_mix(rows, "b_exit_mix")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "informational_only": True,
        "n_setups": len(rows),
        "start_pool_usd": POOL_USD,
        "headline": {
            "A_trailing": {
                "final_equity": a_pool["final_equity"],
                "total_return_pct": a_pool["total_return_pct"],
                "max_drawdown_pct": a_pool["max_drawdown_pct"],
                "n_trades": a_pool["n_trades"],
                **summarize_returns(a_rets),
                "avg_days_held": round(
                    sum(r["a_days_held"] for r in rows) / len(rows), 2
                ) if rows else None,
                "exit_tranche_counts": a_mix,
            },
            "B_target": {
                "final_equity": b_pool["final_equity"],
                "total_return_pct": b_pool["total_return_pct"],
                "max_drawdown_pct": b_pool["max_drawdown_pct"],
                "n_trades": b_pool["n_trades"],
                **summarize_returns(b_rets),
                "avg_days_held": round(
                    sum(r["b_days_held"] for r in rows) / len(rows), 2
                ) if rows else None,
                "exit_tranche_counts": b_mix,
            },
            "head_to_head": {
                "a_beats_b": sum(1 for r in rows if r["winner"] == "A"),
                "b_beats_a": sum(1 for r in rows if r["winner"] == "B"),
                "ties": sum(1 for r in rows if r["winner"] == "tie"),
            },
        },
        "by_confidence": _segment_stats(rows, "confidence"),
        "by_regime": _segment_stats(rows, "regime"),
        "by_mfe_bucket": _segment_stats(rows, "mfe_bucket"),
        "equity_curves": {
            "A_trailing": a_pool["equity_curve"],
            "B_target": b_pool["equity_curve"],
        },
    }


def write_per_setup_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "ticker",
                "flag_date",
                "confidence",
                "regime",
                "mfe_pct",
                "a_return_pct",
                "b_return_pct",
                "a_winner",
                "b_winner",
                "winner",
            ],
        )
        w.writeheader()
        for r in rows:
            w.writerow({
                "ticker": r["ticker"],
                "flag_date": r["flag_date"],
                "confidence": r["confidence"],
                "regime": r["regime"],
                "mfe_pct": r["mfe_pct"],
                "a_return_pct": r["a_return_pct"],
                "b_return_pct": r["b_return_pct"],
                "a_winner": r["a_winner"],
                "b_winner": r["b_winner"],
                "winner": r["winner"],
            })


def _print_segment(title: str, segment: dict[str, Any]) -> None:
    print(f"\n{title}")
    print(
        f"  {'bucket':<14} {'n':>4}  {'A avg%':>8} {'B avg%':>8}  "
        f"{'A win%':>7} {'B win%':>7}  {'A>B':>4} {'B>A':>4}  {'edge':>8}"
    )
    for label, s in segment.items():
        a, b = s["A"], s["B"]
        a_avg = a["avg_return_pct"] if a["avg_return_pct"] is not None else 0.0
        b_avg = b["avg_return_pct"] if b["avg_return_pct"] is not None else 0.0
        a_wr = a["win_rate_pct"] if a["win_rate_pct"] is not None else 0.0
        b_wr = b["win_rate_pct"] if b["win_rate_pct"] is not None else 0.0
        print(
            f"  {label:<14} {s['n']:>4}  "
            f"{a_avg:>7.2f}% {b_avg:>7.2f}%  "
            f"{a_wr:>6.1f}% {b_wr:>6.1f}%  "
            f"{s['a_beats_b']:>4} {s['b_beats_a']:>4}  "
            f"{s['edge_A_minus_B_avg_pct']:>+7.2f}%"
        )


def print_summary(report: dict[str, Any]) -> None:
    h = report["headline"]
    a, b = h["A_trailing"], h["B_target"]
    hh = h["head_to_head"]

    print()
    print("=" * 78)
    print("STRATEGY LAB SCOREBOARD — A (Trailing) vs B (Target)")
    print("=" * 78)
    print(f"  Setups scored : {report['n_setups']}")
    print(f"  Start pool    : ${report['start_pool_usd']:.0f} each")
    print("-" * 78)
    print(f"  {'metric':<28} {'A Trailing':>14} {'B Target':>14}")
    print("-" * 78)
    table = [
        ("Final pool $", f"${a['final_equity']:.2f}", f"${b['final_equity']:.2f}"),
        ("Total return %", f"{a['total_return_pct']:+.2f}%", f"{b['total_return_pct']:+.2f}%"),
        ("Win rate %", f"{a['win_rate_pct']:.1f}%", f"{b['win_rate_pct']:.1f}%"),
        ("Avg return / setup %", f"{a['avg_return_pct']:+.2f}%", f"{b['avg_return_pct']:+.2f}%"),
        ("Max drawdown %", f"{a['max_drawdown_pct']:.2f}%", f"{b['max_drawdown_pct']:.2f}%"),
        ("Avg days held", f"{a['avg_days_held']:.2f}", f"{b['avg_days_held']:.2f}"),
        (
            "Kill exits (tranches)",
            str(a["exit_tranche_counts"]["kill"]),
            str(b["exit_tranche_counts"]["kill"]),
        ),
        (
            "Time-cap exits",
            str(a["exit_tranche_counts"]["time_cap"]),
            str(b["exit_tranche_counts"]["time_cap"]),
        ),
        (
            "Trail / Target exits",
            str(a["exit_tranche_counts"]["trail"]),
            str(b["exit_tranche_counts"]["target"]),
        ),
    ]
    for label, av, bv in table:
        print(f"  {label:<28} {av:>14} {bv:>14}")
    print("-" * 78)
    print(
        f"  Head-to-head setups:  A beats B = {hh['a_beats_b']}  |  "
        f"B beats A = {hh['b_beats_a']}  |  ties = {hh['ties']}"
    )
    _print_segment("BY CONFIDENCE", report["by_confidence"])
    _print_segment("BY REGIME", report["by_regime"])
    _print_segment("BY MFE BUCKET (max A/B MFE)", report["by_mfe_bucket"])
    print("=" * 78)


def main() -> None:
    setups_doc = json.loads(SETUPS_PATH.read_text(encoding="utf-8"))
    setups = list(setups_doc.get("setups") or [])
    hist_doc = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    history = hist_doc.get("history") or {}

    seen: set[tuple[str, str]] = set()
    ordered: list[tuple[str, str]] = []
    for s in setups:
        t = str(s.get("ticker") or "").upper().strip()
        d = str(s.get("flag_date") or "")[:10]
        if not t or not d:
            continue
        key = (t, d)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    ordered.sort(key=lambda td: (td[1], td[0]))

    print(f"Scoreboard: {len(ordered)} unique setups from {SETUPS_PATH.relative_to(ROOT)}")
    print("Running Strategy A + B on shared inputs (daily bars cached)...\n")

    rows: list[dict[str, Any]] = []
    n_skip = 0
    t0 = time.time()
    for i, (ticker, flag_date) in enumerate(ordered, start=1):
        key = f"{ticker}|{flag_date}"
        hist_row = history.get(key)
        print(f"[{i}/{len(ordered)}] {key}", flush=True)
        if not hist_row or hist_row.get("status") != "ok":
            print("  SKIP — no ok history")
            n_skip += 1
            continue
        row = run_one_setup(ticker, flag_date, hist_row)
        if row is None:
            n_skip += 1
            continue
        rows.append(row)
        print(
            f"  A={row['a_return_pct']:+.2f}%  B={row['b_return_pct']:+.2f}%  "
            f"winner={row['winner']}  conf={row['confidence']}  "
            f"mfe={row['mfe_pct']:.1f}%",
            flush=True,
        )

    elapsed = time.time() - t0
    print(f"\nDone: scored={len(rows)}  skipped={n_skip}  elapsed={elapsed:.1f}s")

    if not rows:
        raise SystemExit("No setups scored — nothing to report")

    report = build_report(rows)
    SCOREBOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCOREBOARD_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_per_setup_csv(rows, PER_SETUP_CSV)

    print_summary(report)
    print(f"Wrote {SCOREBOARD_PATH.relative_to(ROOT)}")
    print(f"Wrote {PER_SETUP_CSV.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
