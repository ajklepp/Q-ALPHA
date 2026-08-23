"""
strategy_lab/matrix_pm.py — entry × exit matrix including premarket-limit models.

Same pipeline as matrix.py, with entries:
  immediate, vwap_reclaim, sweep_reclaim,
  premarket_median_limit, premarket_vwap_limit
× exits: Strategy A (Trailing), Strategy B (Target).

Adds:
  - fill / no_fill / no_premarket skip accounting for limit models
  - cluster-split robustness (2026-08-20 vs rest)

Writes results/matrix_pm.json (+ matrix_pm_per_setup.csv).
Does NOT modify agent files. Stop logic unchanged (profile safe_max_stop_pct).
"""
from __future__ import annotations

import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
LAB = Path(__file__).resolve().parent
sys.path.insert(0, str(LAB))
sys.path.insert(0, str(ROOT / "candidates"))

from entry_models import (  # noqa: E402
    MODELS,
    load_premarket_row,
    run_all_models,
)
from matrix import (  # noqa: E402
    EXIT_FNS,
    EXIT_NAMES,
    combo_key,
    load_daily_cached,
    load_unique_setups,
    mfe_bucket,
    segment_combo,
    trade_killed,
    _fmt,
)
from strategy_a import (  # noqa: E402
    BARS_DIR,
    HISTORY_PATH,
    load_minute_bars,
    load_profile,
)

SETUPS_PATH = LAB / "results" / "setups.json"
PREMARKET_PATH = LAB / "results" / "premarket.json"
MATRIX_JSON = LAB / "results" / "matrix_pm.json"
MATRIX_CSV = LAB / "results" / "matrix_pm_per_setup.csv"

ENTRY_NAMES = (
    "immediate",
    "vwap_reclaim",
    "sweep_reclaim",
    "premarket_median_limit",
    "premarket_vwap_limit",
)
PM_LIMIT_ENTRIES = frozenset({
    "premarket_median_limit",
    "premarket_vwap_limit",
})
CLUSTER_DATE = "2026-08-20"
MIN_N_HALF = 15
TOP_N_SEGMENTS = 3


def bars_path_for(ticker: str, flag_date: str, hist_row: dict | None) -> Path:
    if hist_row:
        rel = hist_row.get("minute_bars_path") or hist_row.get("bars_path")
        if rel:
            p = Path(str(rel))
            return p if p.is_absolute() else ROOT / p
    return BARS_DIR / f"{ticker.upper()}_{flag_date}.json"


def _skip_status(
    entry_name: str,
    sig: Any,
    pm_row: dict[str, Any] | None,
) -> str:
    """Classify skip reason for reporting (no_entry / no_premarket / no_fill)."""
    if sig is not None:
        return "entered"
    if entry_name in PM_LIMIT_ENTRIES:
        if not pm_row or not pm_row.get("premarket_available"):
            return "no_premarket"
        return "no_fill"
    return "no_entry"


def eval_setup(
    ticker: str,
    flag_date: str,
    hist_row: dict,
) -> dict[str, Any] | None:
    """Run selected entry models × A/B on one setup."""
    bars_path = bars_path_for(ticker, flag_date, hist_row)
    if not bars_path.exists():
        print(f"  SKIP {ticker}|{flag_date} — missing bars")
        return None

    minute_bars = load_minute_bars(bars_path)
    if not minute_bars:
        print(f"  SKIP {ticker}|{flag_date} — empty bars")
        return None

    profile_path = LAB / "profiles" / f"{ticker}_{flag_date}.json"
    if profile_path.exists():
        profile = load_profile(ticker, flag_date)
    else:
        profile = {
            "ticker": ticker,
            "as_of_date": flag_date,
            "confidence": "INSUFFICIENT",
            "stats_meaningful": False,
            "bracket": {},
            "percentiles": {},
        }

    confidence = str(profile.get("confidence") or "INSUFFICIENT").upper()
    daily_bars = load_daily_cached(ticker, flag_date)
    pm_row = load_premarket_row(ticker, flag_date)
    signals = run_all_models(
        minute_bars,
        premarket_row=pm_row,
        ticker=ticker,
        flag_date=flag_date,
    )

    by_entry: dict[str, Any] = {}
    for entry_name in ENTRY_NAMES:
        sig = signals.get(entry_name)
        status = _skip_status(entry_name, sig, pm_row)
        if sig is None:
            by_entry[entry_name] = {
                "status": status,
                "entry_time": None,
                "entry_price": None,
                "bar_index": None,
                "limit_price": (
                    (
                        pm_row.get("premarket_median")
                        if entry_name == "premarket_median_limit"
                        else pm_row.get("premarket_vwap")
                    )
                    if pm_row and pm_row.get("premarket_available")
                    else None
                ),
                "A_trailing": None,
                "B_target": None,
            }
            continue

        entry_price = float(sig.entry_price)
        entry_time = str(sig.entry_time)
        common = dict(
            ticker=ticker,
            flag_date=flag_date,
            entry_price=entry_price,
            entry_time=entry_time,
            minute_bars=minute_bars,
            daily_bars=daily_bars,
            profile=profile,
        )

        exits: dict[str, Any] = {}
        for exit_name, run_fn in EXIT_FNS.items():
            try:
                res = run_fn(**common)
            except Exception as exc:
                exits[exit_name] = {
                    "status": "error",
                    "error": str(exc),
                    "return_pct": None,
                    "days_held": None,
                    "mfe_pct": None,
                    "killed": None,
                    "exit_reason_counts": {},
                }
                continue

            if res.get("status") != "ok":
                exits[exit_name] = {
                    "status": str(res.get("status") or "skipped"),
                    "skip_reason": res.get("skip_reason"),
                    "return_pct": None,
                    "days_held": None,
                    "mfe_pct": None,
                    "killed": None,
                    "exit_reason_counts": {},
                }
                continue

            exits[exit_name] = {
                "status": "ok",
                "return_pct": float(res["total_return_pct"]),
                "days_held": int(res.get("days_held") or 0),
                "mfe_pct": float(res.get("mfe_pct") or 0.0),
                "killed": trade_killed(res),
                "exit_reason_counts": dict(res.get("exit_reason_counts") or {}),
                "n_shares": int(res.get("n_shares") or 0),
            }

        by_entry[entry_name] = {
            "status": "entered",
            "entry_time": entry_time,
            "entry_price": entry_price,
            "bar_index": int(sig.bar_index),
            "limit_price": entry_price if entry_name in PM_LIMIT_ENTRIES else None,
            "A_trailing": exits.get("A_trailing"),
            "B_target": exits.get("B_target"),
        }

    return {
        "ticker": ticker,
        "flag_date": flag_date,
        "confidence": confidence,
        "cluster": (
            "cluster_2026-08-20"
            if flag_date == CLUSTER_DATE
            else "rest"
        ),
        "premarket_available": bool(
            pm_row and pm_row.get("premarket_available")
        ),
        "by_entry": by_entry,
    }


def summarize_combo(
    rows: list[dict[str, Any]],
    entry_name: str,
    exit_name: str,
) -> dict[str, Any]:
    n_setups = len(rows)
    entered = 0
    n_no_entry = 0
    n_no_fill = 0
    n_no_premarket = 0
    returns: list[float] = []
    days: list[int] = []
    kills = 0
    ok_trades = 0

    for row in rows:
        cell = (row.get("by_entry") or {}).get(entry_name) or {}
        st = cell.get("status")
        if st != "entered":
            if st == "no_fill":
                n_no_fill += 1
            elif st == "no_premarket":
                n_no_premarket += 1
            else:
                n_no_entry += 1
            continue
        entered += 1
        ex = cell.get(exit_name) or {}
        if ex.get("status") != "ok" or ex.get("return_pct") is None:
            continue
        ok_trades += 1
        returns.append(float(ex["return_pct"]))
        days.append(int(ex.get("days_held") or 0))
        if ex.get("killed"):
            kills += 1

    skipped = n_no_entry + n_no_fill + n_no_premarket
    wins = sum(1 for r in returns if r > 0)
    # Fill rate: among setups where a limit COULD be placed (PM available).
    pm_eligible = entered + n_no_fill
    if entry_name in PM_LIMIT_ENTRIES:
        fill_rate = (
            round(100.0 * entered / pm_eligible, 2) if pm_eligible else None
        )
    else:
        fill_rate = (
            round(100.0 * entered / n_setups, 2) if n_setups else None
        )

    return {
        "entry_model": entry_name,
        "exit_strategy": exit_name,
        "n_setups": n_setups,
        "n_entered": entered,
        "n_skipped_no_entry": n_no_entry,
        "n_skipped_no_fill": n_no_fill,
        "n_skipped_no_premarket": n_no_premarket,
        "n_skipped_total": skipped,
        "n_ok_trades": ok_trades,
        "entry_rate_pct": round(100.0 * entered / n_setups, 2) if n_setups else None,
        "fill_rate_pct": fill_rate,
        "avg_return_pct": round(sum(returns) / len(returns), 4) if returns else None,
        "win_rate_pct": round(100.0 * wins / len(returns), 2) if returns else None,
        "avg_days_held": round(sum(days) / len(days), 2) if days else None,
        "kill_stop_rate_pct": (
            round(100.0 * kills / ok_trades, 2) if ok_trades else None
        ),
        "n_killed": kills,
    }


def cluster_split_combo(
    rows: list[dict[str, Any]],
    entry_name: str,
    exit_name: str,
) -> dict[str, Any]:
    """Robustness: avg return / kill in 2026-08-20 cluster vs rest."""
    halves: dict[str, dict[str, Any]] = {}
    for label in ("cluster_2026-08-20", "rest"):
        sub = [r for r in rows if r.get("cluster") == label]
        halves[label] = summarize_combo(sub, entry_name, exit_name)

    c = halves["cluster_2026-08-20"]
    r = halves["rest"]
    lift = None
    both_pos = False
    both_neg = False
    if c["avg_return_pct"] is not None and r["avg_return_pct"] is not None:
        both_pos = c["avg_return_pct"] > 0 and r["avg_return_pct"] > 0
        both_neg = c["avg_return_pct"] < 0 and r["avg_return_pct"] < 0
        sufficient = (
            (c["n_ok_trades"] or 0) >= MIN_N_HALF
            and (r["n_ok_trades"] or 0) >= MIN_N_HALF
        )
        survives = sufficient and (both_pos or both_neg)
        lift = round(c["avg_return_pct"] - r["avg_return_pct"], 4)
    else:
        survives = False
        sufficient = False

    overall = summarize_combo(rows, entry_name, exit_name)
    verdict = "insufficient / likely noise"
    if sufficient and overall["avg_return_pct"] is not None:
        if overall["avg_return_pct"] > 0 and both_pos:
            verdict = "edge survives cluster split"
        elif overall["avg_return_pct"] > 0 and not both_pos:
            verdict = "edge ONLY in one half — likely overfitting"
        elif overall["avg_return_pct"] <= 0 and survives:
            verdict = "stable non-edge / negative"
        else:
            verdict = "unstable / no clear edge"
    elif not sufficient:
        verdict = "insufficient / likely noise"

    return {
        "cluster_2026-08-20": {
            "n_ok_trades": c["n_ok_trades"],
            "n_entered": c["n_entered"],
            "avg_return_pct": c["avg_return_pct"],
            "kill_stop_rate_pct": c["kill_stop_rate_pct"],
            "fill_rate_pct": c["fill_rate_pct"],
        },
        "rest": {
            "n_ok_trades": r["n_ok_trades"],
            "n_entered": r["n_entered"],
            "avg_return_pct": r["avg_return_pct"],
            "kill_stop_rate_pct": r["kill_stop_rate_pct"],
            "fill_rate_pct": r["fill_rate_pct"],
        },
        "cluster_minus_rest_pct": lift,
        "both_halves_sufficient": sufficient,
        "edge_survives_both_halves": bool(survives and overall.get("avg_return_pct", 0) > 0),
        "verdict": verdict,
    }


def build_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    combos: dict[str, Any] = {}
    robustness: dict[str, Any] = {}
    for entry_name in ENTRY_NAMES:
        for exit_name in EXIT_NAMES:
            key = combo_key(entry_name, exit_name)
            combos[key] = summarize_combo(rows, entry_name, exit_name)
            robustness[key] = cluster_split_combo(rows, entry_name, exit_name)

    matrix: dict[str, dict[str, float | None]] = {}
    for entry_name in ENTRY_NAMES:
        matrix[entry_name] = {
            exit_name: combos[combo_key(entry_name, exit_name)]["avg_return_pct"]
            for exit_name in EXIT_NAMES
        }

    ranked = sorted(
        [
            c for c in combos.values()
            if c["avg_return_pct"] is not None and c["n_ok_trades"] > 0
        ],
        key=lambda c: (
            c["avg_return_pct"],
            -(c["kill_stop_rate_pct"] if c["kill_stop_rate_pct"] is not None else 100),
        ),
        reverse=True,
    )
    top = ranked[:TOP_N_SEGMENTS]

    segments: dict[str, Any] = {}
    for c in top:
        key = combo_key(c["entry_model"], c["exit_strategy"])
        segments[key] = {
            "by_confidence": segment_combo(
                rows, c["entry_model"], c["exit_strategy"], by="confidence"
            ),
            "by_mfe_bucket": segment_combo(
                rows, c["entry_model"], c["exit_strategy"], by="mfe"
            ),
        }

    kill_comparison = [
        {
            "combo": combo_key(c["entry_model"], c["exit_strategy"]),
            "entry_model": c["entry_model"],
            "exit_strategy": c["exit_strategy"],
            "kill_stop_rate_pct": c["kill_stop_rate_pct"],
            "avg_return_pct": c["avg_return_pct"],
            "fill_rate_pct": c["fill_rate_pct"],
            "n_ok_trades": c["n_ok_trades"],
            "n_entered": c["n_entered"],
            "n_skipped_no_entry": c["n_skipped_no_entry"],
            "n_skipped_no_fill": c["n_skipped_no_fill"],
            "n_skipped_no_premarket": c["n_skipped_no_premarket"],
            "cluster_verdict": robustness[combo_key(c["entry_model"], c["exit_strategy"])][
                "verdict"
            ],
        }
        for c in sorted(
            combos.values(),
            key=lambda x: (
                x["kill_stop_rate_pct"] is None,
                x["kill_stop_rate_pct"] if x["kill_stop_rate_pct"] is not None else 999,
            ),
        )
    ]

    n_cluster = sum(1 for r in rows if r.get("cluster") == "cluster_2026-08-20")
    n_rest = sum(1 for r in rows if r.get("cluster") == "rest")
    n_pm = sum(1 for r in rows if r.get("premarket_available"))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "informational_only": True,
        "n_setups": len(rows),
        "n_cluster_2026_08_20": n_cluster,
        "n_rest": n_rest,
        "n_premarket_available": n_pm,
        "min_n_half": MIN_N_HALF,
        "entry_models": list(ENTRY_NAMES),
        "exit_strategies": list(EXIT_NAMES),
        "combos": combos,
        "summary_matrix_avg_return_pct": matrix,
        "kill_rate_comparison": kill_comparison,
        "cluster_robustness": robustness,
        "ranked_by_avg_return": [
            {
                "combo": combo_key(c["entry_model"], c["exit_strategy"]),
                **c,
                "cluster_verdict": robustness[
                    combo_key(c["entry_model"], c["exit_strategy"])
                ]["verdict"],
            }
            for c in ranked
        ],
        "top_combos": [
            {"combo": combo_key(c["entry_model"], c["exit_strategy"]), **c}
            for c in top
        ],
        "segments_top_combos": segments,
    }


def write_per_setup_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "ticker",
        "flag_date",
        "cluster",
        "confidence",
        "premarket_available",
        "entry_model",
        "status",
        "entry_time",
        "entry_price",
        "A_return_pct",
        "B_return_pct",
        "A_killed",
        "B_killed",
        "A_days_held",
        "B_days_held",
        "A_mfe_pct",
        "B_mfe_pct",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            for entry_name in ENTRY_NAMES:
                cell = (row.get("by_entry") or {}).get(entry_name) or {}
                a = cell.get("A_trailing") or {}
                b = cell.get("B_target") or {}
                w.writerow({
                    "ticker": row["ticker"],
                    "flag_date": row["flag_date"],
                    "cluster": row.get("cluster"),
                    "confidence": row.get("confidence"),
                    "premarket_available": row.get("premarket_available"),
                    "entry_model": entry_name,
                    "status": cell.get("status") or "no_entry",
                    "entry_time": cell.get("entry_time") or "",
                    "entry_price": (
                        cell.get("entry_price")
                        if cell.get("entry_price") is not None
                        else ""
                    ),
                    "A_return_pct": (
                        a.get("return_pct") if a.get("return_pct") is not None else ""
                    ),
                    "B_return_pct": (
                        b.get("return_pct") if b.get("return_pct") is not None else ""
                    ),
                    "A_killed": a.get("killed") if a.get("killed") is not None else "",
                    "B_killed": b.get("killed") if b.get("killed") is not None else "",
                    "A_days_held": (
                        a.get("days_held") if a.get("days_held") is not None else ""
                    ),
                    "B_days_held": (
                        b.get("days_held") if b.get("days_held") is not None else ""
                    ),
                    "A_mfe_pct": a.get("mfe_pct") if a.get("mfe_pct") is not None else "",
                    "B_mfe_pct": b.get("mfe_pct") if b.get("mfe_pct") is not None else "",
                })


def print_report(report: dict[str, Any]) -> None:
    print()
    print("=" * 96)
    print("ENTRY × EXIT MATRIX (w/ PREMARKET LIMITS) — Strategy Lab")
    print("=" * 96)
    print(
        f"  Setups: {report['n_setups']}  "
        f"(cluster {CLUSTER_DATE}: {report['n_cluster_2026_08_20']}  |  "
        f"rest: {report['n_rest']}  |  PM available: {report['n_premarket_available']})"
    )
    print(f"  Entries: {', '.join(report['entry_models'])}")
    print(f"  Exits:   {', '.join(report['exit_strategies'])}")

    print()
    print("1) RANKED COMBOS — avg return / fill / kill (entered trades)")
    print("-" * 96)
    print(
        f"  {'combo':<42} {'enter':>5} {'fill%':>6} {'skip':>5}  "
        f"{'avg%':>8} {'win%':>7} {'kill%':>7} {'days':>5}  verdict"
    )
    for c in report["ranked_by_avg_return"]:
        label = f"{c['entry_model']} × {c['exit_strategy'].replace('_', ' ')}"
        skip = c["n_skipped_total"]
        print(
            f"  {label:<42} {c['n_entered']:>5} "
            f"{_fmt(c['fill_rate_pct']):>6} {skip:>5}  "
            f"{_fmt(c['avg_return_pct'], signed=True):>8} "
            f"{_fmt(c['win_rate_pct']):>7} "
            f"{_fmt(c['kill_stop_rate_pct']):>7} "
            f"{_fmt(c['avg_days_held']):>5}  {c['cluster_verdict']}"
        )

    print()
    print("2) SUMMARY MATRIX — avg return % per entered setup")
    print("-" * 96)
    mat = report["summary_matrix_avg_return_pct"]
    print(f"  {'entry model':<28} {'A Trailing':>14} {'B Target':>14}")
    print("  " + "-" * 58)
    best_entry = best_exit = None
    best_val: float | None = None
    for entry_name in ENTRY_NAMES:
        a = mat[entry_name]["A_trailing"]
        b = mat[entry_name]["B_target"]
        print(
            f"  {entry_name:<28} {_fmt(a, '%', signed=True):>14} "
            f"{_fmt(b, '%', signed=True):>14}"
        )
        for exit_name, v in (("A_trailing", a), ("B_target", b)):
            if v is None:
                continue
            if best_val is None or v > best_val:
                best_entry, best_exit, best_val = entry_name, exit_name, v
    if best_entry is not None:
        print(
            f"\n  BEST COMBO: {best_entry} × {best_exit}  "
            f"(avg {_fmt(best_val, '%', signed=True)})"
        )

    print()
    print("3) FILL / SKIP BREAKDOWN (limit models)")
    print("-" * 96)
    print(
        f"  {'entry':<28} {'enter':>5} {'no_fill':>7} {'no_pm':>6} "
        f"{'no_entry':>8} {'fill%':>7}"
    )
    for entry_name in ENTRY_NAMES:
        # Use A_trailing row for skip counts (same entry, exit-agnostic)
        c = report["combos"][combo_key(entry_name, "A_trailing")]
        print(
            f"  {entry_name:<28} {c['n_entered']:>5} "
            f"{c['n_skipped_no_fill']:>7} {c['n_skipped_no_premarket']:>6} "
            f"{c['n_skipped_no_entry']:>8} {_fmt(c['fill_rate_pct']):>7}"
        )

    print()
    print("4) KILL-RATE vs IMMEDIATE (same exit)")
    print("-" * 96)
    for exit_name in EXIT_NAMES:
        base = report["combos"][combo_key("immediate", exit_name)]["kill_stop_rate_pct"]
        print(f"  [{exit_name}] immediate kill% = {_fmt(base)}")
        for entry_name in ENTRY_NAMES:
            if entry_name == "immediate":
                continue
            k = report["combos"][combo_key(entry_name, exit_name)]["kill_stop_rate_pct"]
            delta = None if (base is None or k is None) else (k - base)
            print(
                f"     {entry_name:<26} kill%={_fmt(k):>6}  "
                f"Δ vs immediate = {_fmt(delta, signed=True)}"
            )

    print()
    print("5) CLUSTER-SPLIT ROBUSTNESS (2026-08-20 vs rest)")
    print("-" * 96)
    print(
        f"  {'combo':<42} {'cl_n':>4} {'cl%':>7} {'rst_n':>5} {'rst%':>7}  "
        f"survives?  verdict"
    )
    for entry_name in ENTRY_NAMES:
        for exit_name in EXIT_NAMES:
            key = combo_key(entry_name, exit_name)
            rb = report["cluster_robustness"][key]
            cl = rb["cluster_2026-08-20"]
            rst = rb["rest"]
            label = f"{entry_name} × {exit_name.replace('_', ' ')}"
            surv = "YES" if rb["edge_survives_both_halves"] else "no"
            if not rb["both_halves_sufficient"]:
                surv = "n/a"
            print(
                f"  {label:<42} {cl['n_ok_trades']:>4} "
                f"{_fmt(cl['avg_return_pct'], signed=True):>7} "
                f"{rst['n_ok_trades']:>5} "
                f"{_fmt(rst['avg_return_pct'], signed=True):>7}  "
                f"{surv:<9} {rb['verdict']}"
            )

    print("=" * 96)


def main() -> None:
    if not PREMARKET_PATH.exists():
        raise SystemExit(
            f"Missing {PREMARKET_PATH} — run fetch_premarket.py first."
        )
    missing = set(ENTRY_NAMES) - set(MODELS.keys())
    if missing:
        raise SystemExit(f"entry_models.MODELS missing: {missing}")

    setups = load_unique_setups()
    hist_doc = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    history = hist_doc.get("history") or {}

    print(
        f"Matrix PM: {len(setups)} setups × {len(ENTRY_NAMES)} entries × "
        f"{len(EXIT_NAMES)} exits"
    )
    print("Running (daily cached; no_fill / no_premarket skips intentional)...\n")

    rows: list[dict[str, Any]] = []
    n_skip = 0
    t0 = time.time()

    for i, (ticker, flag_date) in enumerate(setups, start=1):
        key = f"{ticker}|{flag_date}"
        hist_row = history.get(key)
        print(f"[{i}/{len(setups)}] {key}", flush=True)
        if not hist_row or hist_row.get("status") != "ok":
            print("  SKIP — no ok history")
            n_skip += 1
            continue
        row = eval_setup(ticker, flag_date, hist_row)
        if row is None:
            n_skip += 1
            continue
        rows.append(row)

        bits = []
        for en in ENTRY_NAMES:
            cell = row["by_entry"][en]
            st = cell["status"]
            if st != "entered":
                bits.append(f"{en[:6]}={st}")
            else:
                a = (cell.get("A_trailing") or {}).get("return_pct")
                b = (cell.get("B_target") or {}).get("return_pct")
                bits.append(
                    f"{en[:6]}=A{_fmt(a, signed=True)}/B{_fmt(b, signed=True)}"
                )
        print("  " + "  ".join(bits), flush=True)

    elapsed = time.time() - t0
    print(
        f"\nDone: scored={len(rows)}  skipped={n_skip}  elapsed={elapsed:.1f}s"
    )
    if not rows:
        raise SystemExit("No setups scored — nothing to report")

    report = build_report(rows)
    report["per_setup"] = [
        {
            "ticker": r["ticker"],
            "flag_date": r["flag_date"],
            "cluster": r["cluster"],
            "confidence": r["confidence"],
            "premarket_available": r["premarket_available"],
            "by_entry": {
                en: {
                    "status": cell.get("status"),
                    "entry_time": cell.get("entry_time"),
                    "entry_price": cell.get("entry_price"),
                    "limit_price": cell.get("limit_price"),
                    "A_trailing": (
                        {
                            k: (cell.get("A_trailing") or {}).get(k)
                            for k in (
                                "status",
                                "return_pct",
                                "killed",
                                "days_held",
                                "mfe_pct",
                            )
                        }
                        if cell.get("A_trailing")
                        else None
                    ),
                    "B_target": (
                        {
                            k: (cell.get("B_target") or {}).get(k)
                            for k in (
                                "status",
                                "return_pct",
                                "killed",
                                "days_held",
                                "mfe_pct",
                            )
                        }
                        if cell.get("B_target")
                        else None
                    ),
                }
                for en, cell in r["by_entry"].items()
            },
        }
        for r in rows
    ]

    MATRIX_JSON.parent.mkdir(parents=True, exist_ok=True)
    MATRIX_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_per_setup_csv(rows, MATRIX_CSV)

    print_report(report)
    print(f"Wrote {MATRIX_JSON.relative_to(ROOT)}")
    print(f"Wrote {MATRIX_CSV.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
