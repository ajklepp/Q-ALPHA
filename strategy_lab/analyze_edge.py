"""
strategy_lab/analyze_edge.py — how robust is Strategy A's edge over B?

Reads results/per_setup.csv (and scoreboard.json if present). Does NOT modify
agent files. Writes results/edge_analysis.json and prints a clear report.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LAB = Path(__file__).resolve().parent
ROOT = LAB.parent
PER_SETUP_CSV = LAB / "results" / "per_setup.csv"
SCOREBOARD_PATH = LAB / "results" / "scoreboard.json"
OUT_PATH = LAB / "results" / "edge_analysis.json"

MFE_BUCKETS = (
    ("<10%", 0.0, 10.0),
    ("10-25%", 10.0, 25.0),
    (">25%", 25.0, 1e9),
)
CONF_ORDER = ("HIGH", "MEDIUM", "LOW", "INSUFFICIENT")


def load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f"Missing {path} — run scoreboard.py first")
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            a = float(r["a_return_pct"])
            b = float(r["b_return_pct"])
            mfe = float(r["mfe_pct"])
            winner = str(r.get("winner") or "").strip()
            if not winner:
                if a > b:
                    winner = "A"
                elif b > a:
                    winner = "B"
                else:
                    winner = "tie"
            rows.append({
                "ticker": r["ticker"].upper(),
                "flag_date": r["flag_date"][:10],
                "confidence": str(r.get("confidence") or "INSUFFICIENT").upper(),
                "regime": r.get("regime") or "n/a",
                "mfe_pct": mfe,
                "a_return_pct": a,
                "b_return_pct": b,
                "edge_pct": a - b,
                "winner": winner,
            })
    return rows


def mfe_bucket(mfe: float) -> str:
    for label, lo, hi in MFE_BUCKETS:
        if lo <= mfe < hi:
            return label
    return "unknown"


def summarize_pair(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {
            "n": 0,
            "a_avg_return_pct": None,
            "b_avg_return_pct": None,
            "a_win_rate_pct": None,
            "b_win_rate_pct": None,
            "a_beats_b": 0,
            "b_beats_a": 0,
            "ties": 0,
            "edge_A_minus_B_avg_pct": None,
            "total_edge_pct": 0.0,
        }
    a_rets = [r["a_return_pct"] for r in rows]
    b_rets = [r["b_return_pct"] for r in rows]
    a_pos = sum(1 for x in a_rets if x > 0)
    b_pos = sum(1 for x in b_rets if x > 0)
    a_beats = sum(1 for r in rows if r["winner"] == "A")
    b_beats = sum(1 for r in rows if r["winner"] == "B")
    ties = sum(1 for r in rows if r["winner"] == "tie")
    total_edge = sum(r["edge_pct"] for r in rows)
    return {
        "n": n,
        "a_avg_return_pct": round(sum(a_rets) / n, 4),
        "b_avg_return_pct": round(sum(b_rets) / n, 4),
        "a_win_rate_pct": round(100.0 * a_pos / n, 2),
        "b_win_rate_pct": round(100.0 * b_pos / n, 2),
        "a_beats_b": a_beats,
        "b_beats_a": b_beats,
        "ties": ties,
        "edge_A_minus_B_avg_pct": round(total_edge / n, 4),
        "total_edge_pct": round(total_edge, 4),
    }


def by_mfe(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for label, lo, hi in MFE_BUCKETS:
        group = [r for r in rows if lo <= r["mfe_pct"] < hi]
        stats = summarize_pair(group)
        stats["mfe_range"] = [lo, None if hi >= 1e8 else hi]
        out[label] = stats
    return out


def concentration(rows: list[dict[str, Any]], top_ns: tuple[int, ...] = (5, 10)) -> dict[str, Any]:
    """Sort by A-B edge descending; measure how much total edge sits in top-N."""
    ranked = sorted(rows, key=lambda r: r["edge_pct"], reverse=True)
    total_edge = sum(r["edge_pct"] for r in rows)
    # Only sum positive contribution from A-favoring tops (still report vs full total)
    top_lists: dict[str, Any] = {}
    for n in top_ns:
        top = ranked[:n]
        top_edge = sum(r["edge_pct"] for r in top)
        share = (top_edge / total_edge * 100.0) if total_edge != 0 else None
        top_lists[f"top_{n}"] = {
            "n": n,
            "sum_edge_pct": round(top_edge, 4),
            "share_of_total_aggregate_edge_pct": (
                round(share, 2) if share is not None else None
            ),
            "setups": [
                {
                    "ticker": r["ticker"],
                    "flag_date": r["flag_date"],
                    "confidence": r["confidence"],
                    "mfe_pct": round(r["mfe_pct"], 4),
                    "a_return_pct": round(r["a_return_pct"], 4),
                    "b_return_pct": round(r["b_return_pct"], 4),
                    "edge_pct": round(r["edge_pct"], 4),
                }
                for r in top
            ],
        }
    return {
        "total_aggregate_edge_pct": round(total_edge, 4),
        "definition": "edge_pct = a_return_pct - b_return_pct; ranked descending",
        **top_lists,
    }


def leave_out_outliers(rows: list[dict[str, Any]], ns: tuple[int, ...] = (3, 5)) -> dict[str, Any]:
    """Drop the N largest A-favoring edges; recompute A vs B averages / win rates."""
    ranked = sorted(rows, key=lambda r: r["edge_pct"], reverse=True)
    baseline = summarize_pair(rows)
    out: dict[str, Any] = {"full_sample": baseline}
    for n in ns:
        drop = { (r["ticker"], r["flag_date"]) for r in ranked[:n] }
        kept = [r for r in rows if (r["ticker"], r["flag_date"]) not in drop]
        removed = [
            {
                "ticker": r["ticker"],
                "flag_date": r["flag_date"],
                "edge_pct": round(r["edge_pct"], 4),
                "a_return_pct": round(r["a_return_pct"], 4),
                "b_return_pct": round(r["b_return_pct"], 4),
                "mfe_pct": round(r["mfe_pct"], 4),
                "confidence": r["confidence"],
            }
            for r in ranked[:n]
        ]
        stats = summarize_pair(kept)
        out[f"remove_top_{n}_a_favoring"] = {
            "removed": removed,
            "remaining": stats,
            "a_still_beats_b_on_avg": (
                stats["a_avg_return_pct"] is not None
                and stats["b_avg_return_pct"] is not None
                and stats["a_avg_return_pct"] > stats["b_avg_return_pct"]
            ),
            "a_still_leads_head_to_head": stats["a_beats_b"] > stats["b_beats_a"],
        }
    return out


def same_day_clustering(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(r["flag_date"] for r in rows)
    unique = len(counts)
    most_common = counts.most_common(10)
    return {
        "n_setups": len(rows),
        "n_unique_flag_dates": unique,
        "max_setups_on_one_date": most_common[0][1] if most_common else 0,
        "top_dates_by_setup_count": [
            {"flag_date": d, "n_setups": c} for d, c in most_common
        ],
        "note": (
            "Compounding applies each setup as a full-pool trade; many setups "
            "on the same calendar day inflate equity curves."
        ),
    }


def by_confidence(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    labels = [c for c in CONF_ORDER] + sorted(
        {r["confidence"] for r in rows} - set(CONF_ORDER)
    )
    for conf in labels:
        group = [r for r in rows if r["confidence"] == conf]
        if not group:
            continue
        out[conf] = summarize_pair(group)
    return out


def build_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scoreboard_meta: dict[str, Any] | None = None
    if SCOREBOARD_PATH.exists():
        try:
            sb = json.loads(SCOREBOARD_PATH.read_text(encoding="utf-8"))
            scoreboard_meta = {
                "generated_at": sb.get("generated_at"),
                "n_setups": sb.get("n_setups"),
                "headline_final_equity": {
                    "A": (sb.get("headline") or {}).get("A_trailing", {}).get("final_equity"),
                    "B": (sb.get("headline") or {}).get("B_target", {}).get("final_equity"),
                },
            }
        except (json.JSONDecodeError, OSError):
            scoreboard_meta = None

    mfe = by_mfe(rows)
    conc = concentration(rows)
    leave = leave_out_outliers(rows)
    cluster = same_day_clustering(rows)
    conf = by_confidence(rows)
    baseline = summarize_pair(rows)

    # Robustness verdict (informational)
    rem5 = leave["remove_top_5_a_favoring"]["remaining"]
    high = conf.get("HIGH") or {}
    n_gt25 = mfe[">25%"]["n"]
    top10_share = conc["top_10"]["share_of_total_aggregate_edge_pct"]

    verdict_bits = []
    if n_gt25 < 20:
        verdict_bits.append(
            f">25% MFE bucket is small (N={n_gt25}) — tail edge is thinly sampled."
        )
    if top10_share is not None and top10_share >= 50:
        verdict_bits.append(
            f"Top-10 A-favoring setups contribute {top10_share:.1f}% of aggregate edge — "
            "edge is concentrated."
        )
    if leave["remove_top_5_a_favoring"]["a_still_beats_b_on_avg"]:
        verdict_bits.append(
            "After removing top-5 A outliers, A still leads on avg return/setup."
        )
    else:
        verdict_bits.append(
            "After removing top-5 A outliers, A no longer leads on avg return/setup."
        )
    if high.get("n"):
        if (high.get("edge_A_minus_B_avg_pct") or 0) > 0:
            verdict_bits.append(
                f"HIGH-confidence edge holds (avg A−B = {high['edge_A_minus_B_avg_pct']:+.2f}%, "
                f"N={high['n']})."
            )
        else:
            verdict_bits.append(
                f"HIGH-confidence edge does NOT hold (avg A−B = {high.get('edge_A_minus_B_avg_pct')}, "
                f"N={high['n']})."
            )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_csv": str(PER_SETUP_CSV.relative_to(ROOT)).replace("\\", "/"),
        "scoreboard_meta": scoreboard_meta,
        "n_setups": len(rows),
        "baseline": baseline,
        "by_mfe_bucket": mfe,
        "concentration": conc,
        "leave_out_outliers": leave,
        "same_day_clustering": cluster,
        "by_confidence": conf,
        "robustness_notes": verdict_bits,
    }


def _fmt_avg(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.2f}%"


def _fmt_wr(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.1f}%"


def print_report(report: dict[str, Any]) -> None:
    print()
    print("=" * 78)
    print("STRATEGY LAB — EDGE ROBUSTNESS (A Trailing vs B Target)")
    print("=" * 78)
    print(f"  Setups: {report['n_setups']}  |  source: {report['source_csv']}")
    b = report["baseline"]
    print(
        f"  Baseline avg: A {_fmt_avg(b['a_avg_return_pct'])}  vs  "
        f"B {_fmt_avg(b['b_avg_return_pct'])}  |  "
        f"edge {_fmt_avg(b['edge_A_minus_B_avg_pct'])}  |  "
        f"H2H A>B {b['a_beats_b']} / B>A {b['b_beats_a']} / ties {b['ties']}"
    )
    print(
        f"  Aggregate edge (sum A−B): {b['total_edge_pct']:+.2f}%  "
        f"(not compounded)"
    )

    # 1) MFE buckets
    print("\n1) SAMPLE SIZE BY MFE BUCKET")
    print(
        f"  {'bucket':<10} {'N':>5}  {'A avg':>8} {'B avg':>8}  "
        f"{'edge':>8}  {'A>B':>4} {'B>A':>4} {'ties':>4}"
    )
    for label in ("<10%", "10-25%", ">25%"):
        s = report["by_mfe_bucket"][label]
        print(
            f"  {label:<10} {s['n']:>5}  "
            f"{_fmt_avg(s['a_avg_return_pct']):>8} {_fmt_avg(s['b_avg_return_pct']):>8}  "
            f"{_fmt_avg(s['edge_A_minus_B_avg_pct']):>8}  "
            f"{s['a_beats_b']:>4} {s['b_beats_a']:>4} {s['ties']:>4}"
        )
    n25 = report["by_mfe_bucket"][">25%"]["n"]
    print(f"  >>> >25% MFE bucket N = {n25}")

    # 2) Concentration
    conc = report["concentration"]
    print("\n2) CONCENTRATION — TOP A-FAVORING SETUPS (by A_return − B_return)")
    print(f"  Total aggregate edge (sum A−B): {conc['total_aggregate_edge_pct']:+.2f}%")
    for key, title in (("top_5", "TOP 5"), ("top_10", "TOP 10")):
        block = conc[key]
        share = block["share_of_total_aggregate_edge_pct"]
        print(
            f"\n  {title}: sum edge = {block['sum_edge_pct']:+.2f}%  "
            f"({share:.1f}% of total aggregate edge)"
            if share is not None
            else f"\n  {title}: sum edge = {block['sum_edge_pct']:+.2f}%"
        )
        print(
            f"  {'ticker':<8} {'date':<12} {'conf':<12} {'MFE%':>7} "
            f"{'A%':>8} {'B%':>8} {'edge':>8}"
        )
        for s in block["setups"]:
            print(
                f"  {s['ticker']:<8} {s['flag_date']:<12} {s['confidence']:<12} "
                f"{s['mfe_pct']:>6.1f}% "
                f"{s['a_return_pct']:>+7.2f}% {s['b_return_pct']:>+7.2f}% "
                f"{s['edge_pct']:>+7.2f}%"
            )

    # 3) Leave-out
    print("\n3) LEAVE-OUT OUTLIERS — does A still beat B?")
    print(
        f"  {'sample':<28} {'N':>4}  {'A avg':>8} {'B avg':>8}  "
        f"{'edge':>8}  {'A wr':>7} {'B wr':>7}  {'A>B':>4} {'B>A':>4}"
    )
    for key, label in (
        ("full_sample", "Full sample"),
        ("remove_top_3_a_favoring", "Remove top-3 A outliers"),
        ("remove_top_5_a_favoring", "Remove top-5 A outliers"),
    ):
        block = report["leave_out_outliers"][key]
        s = block if key == "full_sample" else block["remaining"]
        print(
            f"  {label:<28} {s['n']:>4}  "
            f"{_fmt_avg(s['a_avg_return_pct']):>8} {_fmt_avg(s['b_avg_return_pct']):>8}  "
            f"{_fmt_avg(s['edge_A_minus_B_avg_pct']):>8}  "
            f"{_fmt_wr(s['a_win_rate_pct']):>7} {_fmt_wr(s['b_win_rate_pct']):>7}  "
            f"{s['a_beats_b']:>4} {s['b_beats_a']:>4}"
        )
        if key != "full_sample":
            still_avg = block["a_still_beats_b_on_avg"]
            still_h2h = block["a_still_leads_head_to_head"]
            print(
                f"    -> A still beats B on avg? {still_avg}  |  "
                f"H2H lead? {still_h2h}"
            )
            rem = ", ".join(
                f"{r['ticker']}|{r['flag_date']} ({r['edge_pct']:+.2f}%)"
                for r in block["removed"]
            )
            print(f"    removed: {rem}")

    # 4) Clustering
    cl = report["same_day_clustering"]
    print("\n4) SAME-DAY CLUSTERING (compounding context)")
    print(f"  Unique flag_dates : {cl['n_unique_flag_dates']}  (of {cl['n_setups']} setups)")
    print(f"  Max setups on one date: {cl['max_setups_on_one_date']}")
    print(f"  Top dates by setup count:")
    for d in cl["top_dates_by_setup_count"][:8]:
        print(f"    {d['flag_date']}  →  {d['n_setups']} setups")

    # 5) Confidence
    print("\n5) BY CONFIDENCE")
    print(
        f"  {'conf':<14} {'N':>4}  {'A avg':>8} {'B avg':>8}  "
        f"{'edge':>8}  {'A>B':>4} {'B>A':>4} {'ties':>4}"
    )
    for conf in CONF_ORDER:
        s = report["by_confidence"].get(conf)
        if not s:
            continue
        print(
            f"  {conf:<14} {s['n']:>4}  "
            f"{_fmt_avg(s['a_avg_return_pct']):>8} {_fmt_avg(s['b_avg_return_pct']):>8}  "
            f"{_fmt_avg(s['edge_A_minus_B_avg_pct']):>8}  "
            f"{s['a_beats_b']:>4} {s['b_beats_a']:>4} {s['ties']:>4}"
        )
    high = report["by_confidence"].get("HIGH")
    if high:
        print(
            f"  >>> HIGH only: edge {_fmt_avg(high['edge_A_minus_B_avg_pct'])}  "
            f"H2H A>B {high['a_beats_b']} vs B>A {high['b_beats_a']}  (N={high['n']})"
        )

    print("\nROBUSTNESS NOTES")
    for note in report["robustness_notes"]:
        print(f"  • {note}")
    print("=" * 78)


def main() -> None:
    rows = load_rows(PER_SETUP_CSV)
    report = build_report(rows)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print_report(report)
    print(f"Wrote {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
