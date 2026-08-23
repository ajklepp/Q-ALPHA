"""
strategy_lab/matrix.py — entry × exit backtest matrix.

Foundation for the live decision engine. For every setup in results/setups.json,
runs all 4 entry models × both exits (Strategy A Trailing, Strategy B Target)
= 8 combinations.

  Entry price/time comes from the entry model. None → no_entry (skip — do NOT
  force a trade). Kill stop stays profile safe_max_stop_pct (~7% fallback),
  measured from the model's entry price. Tranche / trail / target / 20-day
  hold logic unchanged in strategy_a / strategy_b.

Writes:
  results/matrix.json
  results/matrix_per_setup.csv

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

from entry_models import MODELS, run_all_models  # noqa: E402
from strategy_a import (  # noqa: E402
    BARS_DIR,
    HISTORY_PATH,
    fetch_daily_after,
    load_minute_bars,
    load_profile,
    run_strategy_a,
)
from strategy_b import run_strategy_b  # noqa: E402

SETUPS_PATH = LAB / "results" / "setups.json"
DAILY_CACHE_DIR = LAB / "results" / "daily_cache"
MATRIX_JSON = LAB / "results" / "matrix.json"
MATRIX_CSV = LAB / "results" / "matrix_per_setup.csv"

ENTRY_NAMES = ("immediate", "orb_reclaim", "vwap_reclaim", "sweep_reclaim")
EXIT_NAMES = ("A_trailing", "B_target")
EXIT_FNS = {
    "A_trailing": run_strategy_a,
    "B_target": run_strategy_b,
}

MFE_BUCKETS = (
    ("<10%", 0.0, 10.0),
    ("10-25%", 10.0, 25.0),
    (">25%", 25.0, 1e9),
)
CONF_ORDER = ("HIGH", "MEDIUM", "LOW", "INSUFFICIENT")
TOP_N_SEGMENTS = 3


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

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
    time.sleep(0.12)
    return bars


def load_unique_setups() -> list[tuple[str, str]]:
    doc = json.loads(SETUPS_PATH.read_text(encoding="utf-8"))
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for s in doc.get("setups") or []:
        t = str(s.get("ticker") or "").upper().strip()
        d = str(s.get("flag_date") or "")[:10]
        if not t or not d:
            continue
        key = (t, d)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    out.sort(key=lambda td: (td[1], td[0]))
    return out


def mfe_bucket(mfe_pct: float | None) -> str:
    if mfe_pct is None:
        return "unknown"
    for label, lo, hi in MFE_BUCKETS:
        if lo <= float(mfe_pct) < hi:
            return label
    return "unknown"


def trade_killed(res: dict[str, Any]) -> bool:
    """True if the kill-all stop fired on any tranche."""
    counts = res.get("exit_reason_counts") or {}
    if int(counts.get("kill") or 0) > 0:
        return True
    for t in res.get("tranches") or []:
        if str(t.get("exit_reason") or "") == "kill":
            return True
    return False


def combo_key(entry_name: str, exit_name: str) -> str:
    return f"{entry_name}__{exit_name}"


# ---------------------------------------------------------------------------
# Per-setup evaluation
# ---------------------------------------------------------------------------

def eval_setup(
    ticker: str,
    flag_date: str,
    hist_row: dict,
) -> dict[str, Any] | None:
    """
    Run all entry models × A/B on one setup with shared bars/profile.
    Returns None only if bars are missing (cannot evaluate any model).
    """
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
    signals = run_all_models(minute_bars)

    by_entry: dict[str, Any] = {}
    for entry_name in ENTRY_NAMES:
        sig = signals.get(entry_name)
        if sig is None:
            by_entry[entry_name] = {
                "status": "no_entry",
                "entry_time": None,
                "entry_price": None,
                "bar_index": None,
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
            "A_trailing": exits.get("A_trailing"),
            "B_target": exits.get("B_target"),
        }

    return {
        "ticker": ticker,
        "flag_date": flag_date,
        "confidence": confidence,
        "by_entry": by_entry,
    }


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def summarize_combo(
    rows: list[dict[str, Any]],
    entry_name: str,
    exit_name: str,
) -> dict[str, Any]:
    """Aggregate one entry×exit combo across all setups."""
    n_setups = len(rows)
    entered = 0
    skipped = 0
    returns: list[float] = []
    days: list[int] = []
    kills = 0
    ok_trades = 0

    for row in rows:
        cell = (row.get("by_entry") or {}).get(entry_name) or {}
        if cell.get("status") != "entered":
            skipped += 1
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

    wins = sum(1 for r in returns if r > 0)
    return {
        "entry_model": entry_name,
        "exit_strategy": exit_name,
        "n_setups": n_setups,
        "n_entered": entered,
        "n_skipped_no_entry": skipped,
        "n_ok_trades": ok_trades,
        "entry_rate_pct": round(100.0 * entered / n_setups, 2) if n_setups else None,
        "avg_return_pct": round(sum(returns) / len(returns), 4) if returns else None,
        "win_rate_pct": round(100.0 * wins / len(returns), 2) if returns else None,
        "avg_days_held": round(sum(days) / len(days), 2) if days else None,
        "kill_stop_rate_pct": (
            round(100.0 * kills / ok_trades, 2) if ok_trades else None
        ),
        "n_killed": kills,
    }


def segment_combo(
    rows: list[dict[str, Any]],
    entry_name: str,
    exit_name: str,
    *,
    by: str,
) -> dict[str, Any]:
    """Segment one combo by confidence or MFE bucket (entered+ok trades only)."""
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        cell = (row.get("by_entry") or {}).get(entry_name) or {}
        if cell.get("status") != "entered":
            continue
        ex = cell.get(exit_name) or {}
        if ex.get("status") != "ok" or ex.get("return_pct") is None:
            continue
        if by == "confidence":
            label = str(row.get("confidence") or "INSUFFICIENT").upper()
        else:
            label = mfe_bucket(ex.get("mfe_pct"))
        buckets[label].append({
            "return_pct": float(ex["return_pct"]),
            "killed": bool(ex.get("killed")),
            "days_held": int(ex.get("days_held") or 0),
        })

    if by == "confidence":
        labels = [c for c in CONF_ORDER if c in buckets] + [
            c for c in buckets if c not in CONF_ORDER
        ]
    else:
        pref = [b[0] for b in MFE_BUCKETS] + ["unknown"]
        labels = [c for c in pref if c in buckets] + [
            c for c in buckets if c not in pref
        ]

    out: dict[str, Any] = {}
    for label in labels:
        group = buckets[label]
        rets = [g["return_pct"] for g in group]
        kills = sum(1 for g in group if g["killed"])
        wins = sum(1 for r in rets if r > 0)
        out[label] = {
            "n": len(group),
            "avg_return_pct": round(sum(rets) / len(rets), 4),
            "win_rate_pct": round(100.0 * wins / len(rets), 2),
            "kill_stop_rate_pct": round(100.0 * kills / len(group), 2),
            "avg_days_held": round(
                sum(g["days_held"] for g in group) / len(group), 2
            ),
        }
    return out


def build_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    combos: dict[str, Any] = {}
    for entry_name in ENTRY_NAMES:
        for exit_name in EXIT_NAMES:
            key = combo_key(entry_name, exit_name)
            combos[key] = summarize_combo(rows, entry_name, exit_name)

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
            "n_ok_trades": c["n_ok_trades"],
            "n_entered": c["n_entered"],
            "n_skipped_no_entry": c["n_skipped_no_entry"],
        }
        for c in sorted(
            combos.values(),
            key=lambda x: (
                x["kill_stop_rate_pct"] is None,
                x["kill_stop_rate_pct"] if x["kill_stop_rate_pct"] is not None else 999,
            ),
        )
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "informational_only": True,
        "n_setups": len(rows),
        "entry_models": list(ENTRY_NAMES),
        "exit_strategies": list(EXIT_NAMES),
        "combos": combos,
        "summary_matrix_avg_return_pct": matrix,
        "kill_rate_comparison": kill_comparison,
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
        "confidence",
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
                    "confidence": row.get("confidence"),
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


# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------

def _fmt(x: float | None, suffix: str = "", *, signed: bool = False) -> str:
    if x is None:
        return "—"
    if signed:
        return f"{x:+.2f}{suffix}"
    return f"{x:.2f}{suffix}"


def print_report(report: dict[str, Any]) -> None:
    print()
    print("=" * 88)
    print("ENTRY × EXIT MATRIX — Strategy Lab")
    print("=" * 88)
    print(f"  Setups: {report['n_setups']}")
    print(f"  Entries: {', '.join(report['entry_models'])}")
    print(f"  Exits:   {', '.join(report['exit_strategies'])}")

    print()
    print("1) KILL-STOP RATE + HEADLINE METRICS (return/kill on entered trades)")
    print("-" * 88)
    print(
        f"  {'combo':<36} {'enter':>5} {'skip':>5}  "
        f"{'avg%':>8} {'win%':>7} {'kill%':>7} {'days':>6}"
    )
    for entry_name in ENTRY_NAMES:
        for exit_name in EXIT_NAMES:
            c = report["combos"][combo_key(entry_name, exit_name)]
            label = f"{entry_name} × {exit_name.replace('_', ' ')}"
            print(
                f"  {label:<36} {c['n_entered']:>5} {c['n_skipped_no_entry']:>5}  "
                f"{_fmt(c['avg_return_pct'], signed=True):>8} "
                f"{_fmt(c['win_rate_pct']):>7} "
                f"{_fmt(c['kill_stop_rate_pct']):>7} "
                f"{_fmt(c['avg_days_held']):>6}"
            )

    print()
    print("2) SUMMARY MATRIX — avg return % per entered setup")
    print("-" * 88)
    mat = report["summary_matrix_avg_return_pct"]
    print(f"  {'entry model':<18} {'A Trailing':>14} {'B Target':>14}")
    print("  " + "-" * 48)
    best_entry = best_exit = None
    best_val: float | None = None
    for entry_name in ENTRY_NAMES:
        a = mat[entry_name]["A_trailing"]
        b = mat[entry_name]["B_target"]
        print(
            f"  {entry_name:<18} {_fmt(a, '%', signed=True):>14} "
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
    print("   Kill-rate drop vs immediate (same exit):")
    for exit_name in EXIT_NAMES:
        base = report["combos"][combo_key("immediate", exit_name)]["kill_stop_rate_pct"]
        print(f"   [{exit_name}] immediate kill% = {_fmt(base)}")
        for entry_name in ENTRY_NAMES:
            if entry_name == "immediate":
                continue
            k = report["combos"][combo_key(entry_name, exit_name)]["kill_stop_rate_pct"]
            delta = None if (base is None or k is None) else (k - base)
            print(
                f"      {entry_name:<16} kill%={_fmt(k):>6}  "
                f"Δ vs immediate = {_fmt(delta, signed=True)}"
            )

    print()
    print(f"3) SEGMENTS — top {len(report['top_combos'])} combos by avg return")
    print("-" * 88)
    for c in report["top_combos"]:
        key = c["combo"]
        print(
            f"\n  ▸ {key}  avg={_fmt(c['avg_return_pct'], signed=True)}%  "
            f"kill={_fmt(c['kill_stop_rate_pct'])}%  "
            f"entered={c['n_entered']}/{c['n_setups']}"
        )
        seg = report["segments_top_combos"][key]

        print("    BY CONFIDENCE")
        print(f"    {'conf':<14} {'n':>4}  {'avg%':>8} {'win%':>7} {'kill%':>7}")
        for label, s in seg["by_confidence"].items():
            print(
                f"    {label:<14} {s['n']:>4}  "
                f"{_fmt(s['avg_return_pct'], signed=True):>8} "
                f"{_fmt(s['win_rate_pct']):>7} "
                f"{_fmt(s['kill_stop_rate_pct']):>7}"
            )

        print("    BY MFE BUCKET")
        print(f"    {'bucket':<14} {'n':>4}  {'avg%':>8} {'win%':>7} {'kill%':>7}")
        for label, s in seg["by_mfe_bucket"].items():
            print(
                f"    {label:<14} {s['n']:>4}  "
                f"{_fmt(s['avg_return_pct'], signed=True):>8} "
                f"{_fmt(s['win_rate_pct']):>7} "
                f"{_fmt(s['kill_stop_rate_pct']):>7}"
            )

    print("=" * 88)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    missing = set(ENTRY_NAMES) - set(MODELS.keys())
    if missing:
        raise SystemExit(f"entry_models.MODELS missing: {missing}")

    setups = load_unique_setups()
    hist_doc = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    history = hist_doc.get("history") or {}

    print(
        f"Matrix: {len(setups)} setups × {len(ENTRY_NAMES)} entries × "
        f"{len(EXIT_NAMES)} exits"
    )
    print("Running (daily bars cached; no_entry skips are intentional)...\n")

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
            if cell["status"] != "entered":
                bits.append(f"{en[:4]}=skip")
            else:
                a = (cell.get("A_trailing") or {}).get("return_pct")
                b = (cell.get("B_target") or {}).get("return_pct")
                bits.append(
                    f"{en[:4]}=A{_fmt(a, signed=True)}/B{_fmt(b, signed=True)}"
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
            "confidence": r["confidence"],
            "by_entry": {
                en: {
                    "status": cell.get("status"),
                    "entry_time": cell.get("entry_time"),
                    "entry_price": cell.get("entry_price"),
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
