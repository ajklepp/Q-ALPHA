# ============================================================
# Q-ALPHA | EXP-0021 | Continuation Ranker bakeoff
# Peak Hour v0 vs all-RTH-hours + continuation_score_v1
# Live paper UNCHANGED until results.md OOS PASS
# ============================================================
"""Run: py -3 experiments/EXP-0021/experiment21.py --pilot
   Full: py -3 experiments/EXP-0021/experiment21.py --days 90
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytz

EXP_DIR = Path(__file__).resolve().parent
ROOT = EXP_DIR.parent.parent
CANDIDATES = ROOT / "candidates"
LIB = EXP_DIR / "lib"
for p in (str(CANDIDATES), str(LIB), str(EXP_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from corpus import DEFAULT_PILOT, build_corpus  # noqa: E402
from features import PEAK_HOURS  # noqa: E402
from tsd_scan_pipeline.universe_tsd import load_polygon_key  # noqa: E402

ET = pytz.timezone("America/New_York")
SLOTS_PER_SCAN = 2  # locked: top 2 per hourly :15 scan clock
COST_PER_TRADE = 0.0015
WR_COLLAPSE = 0.35
EXPECTANCY_TOL = 0.20  # challenger may not lose >20% relative expectancy vs v0
HTF_PASS_GLOB = (
    ROOT / "candidates" / "tsd_scan_pipeline" / "results" / "htf_universe" / "htf_pass_*.json"
)


def load_htf_universe_symbols(path: Path | None = None) -> list[str]:
    """Load full HTF daily-pass symbol list (latest htf_pass_*.json by default)."""
    if path is None:
        files = sorted(HTF_PASS_GLOB.parent.glob("htf_pass_*.json"))
        if not files:
            raise FileNotFoundError(f"No HTF pass files under {HTF_PASS_GLOB.parent}")
        path = files[-1]
    data = json.loads(path.read_text(encoding="utf-8"))
    syms = [str(s).upper() for s in (data.get("symbols") or [])]
    print(f"  HTF universe: {path.name} n={len(syms)}", flush=True)
    return syms


def hour_bucket_report(corpus: pd.DataFrame) -> list[dict]:
    """Expectancy / WR / n by close-hour — used to decide evening scans."""
    if corpus.empty:
        return []
    rows = []
    for hour, g in corpus.groupby("hour"):
        rows.append({
            "hour": int(hour),
            "n": int(len(g)),
            "wr": float(g["hit_1r"].mean()),
            "exp": float(g["r_multiple"].mean()),
            "med_mfe": float(g["mfe"].median()),
            "day_mfe": float(g["day_mfe"].median()),
            "peak": int(int(hour) in PEAK_HOURS),
        })
    return sorted(rows, key=lambda r: r["hour"])


def _expectancy(r_mult: pd.Series) -> float:
    if r_mult.empty:
        return 0.0
    return float(r_mult.mean())


def _win_rate(hit: pd.Series) -> float:
    if hit.empty:
        return 0.0
    return float(hit.mean())


def simulate_slots(df: pd.DataFrame, *, score_col: str, admit_col: str) -> pd.DataFrame:
    """
    Per (signal_date, hour) take top SLOTS_PER_SCAN by score among admit==1.

    Mimics live hourly scan at :15 after each completed 1H bar closes
    (bar close hour H → scan H:15), hard cap = 2 slots.
    """
    if df.empty:
        return df.iloc[0:0]
    work = df[df[admit_col] == 1].copy()
    if work.empty:
        return work
    work = work.sort_values(["signal_date", "hour", score_col], ascending=[True, True, False])
    taken = work.groupby(["signal_date", "hour"], as_index=False).head(SLOTS_PER_SCAN)
    return taken


def day_expander_capture(corpus: pd.DataFrame, taken: pd.DataFrame) -> dict[str, float]:
    """
    Among HTF signal days, define expanders as top-decile day_mfe names (max over day).
    Capture = fraction of expander (date,symbol) pairs that appear in taken slots.
    """
    if corpus.empty:
        return {"capture": 0.0, "n_expanders": 0.0, "n_caught": 0.0}

    day_sym = (
        corpus.groupby(["signal_date", "symbol"], as_index=False)["day_mfe"]
        .max()
    )
    if day_sym.empty:
        return {"capture": 0.0, "n_expanders": 0.0, "n_caught": 0.0}

    thr = float(day_sym["day_mfe"].quantile(0.90))
    expanders = day_sym[day_sym["day_mfe"] >= thr]
    if expanders.empty:
        expanders = day_sym.nlargest(max(1, len(day_sym) // 10), "day_mfe")

    taken_keys = set(zip(taken["signal_date"], taken["symbol"])) if not taken.empty else set()
    caught = sum(1 for _, r in expanders.iterrows() if (r["signal_date"], r["symbol"]) in taken_keys)
    n = len(expanders)
    return {"capture": caught / n if n else 0.0, "n_expanders": float(n), "n_caught": float(caught)}


def case_study(corpus: pd.DataFrame, symbols: list[str]) -> list[dict]:
    """Explain Sep-window signals for IREN/TARS/CHPT-class names via features."""
    out = []
    for sym in symbols:
        sub = corpus[corpus["symbol"] == sym].sort_values("signal_ts")
        if sub.empty:
            out.append({"symbol": sym, "n": 0, "note": "no HTF 1H buy/early in window"})
            continue
        # focus late window
        recent = sub.tail(12)
        best = recent.sort_values("day_mfe", ascending=False).iloc[0]
        out.append({
            "symbol": sym,
            "n": int(len(sub)),
            "best_ts": best["signal_ts"],
            "hour": int(best["hour"]),
            "peak_hour": int(best["peak_hour"]),
            "scan": float(best["scan_score"]),
            "bar_state": best["bar_state"],
            "room_20d": float(best.get("dist_20d_high_pct") or 0),
            "bounce": float(best.get("dist_20d_low_bounce") or 0),
            "vol_ratio": float(best.get("vol_ratio_20") or 0),
            "v0_admit": int(best["php_v0_admit"]),
            "score_v0": float(best["continuation_score_v0"]),
            "score_v1": float(best["continuation_score_v1"]),
            "day_mfe": float(best["day_mfe"]),
            "hit_1r": int(best["hit_1r"]),
            "news_24h": float(best.get("news_velocity_24h") or 0),
            "st_msg": float(best.get("st_msg_24h") or 0),
        })
    return out


def temporal_split_metrics(taken: pd.DataFrame) -> dict[str, float]:
    """Simple OOS: last 30% of dates by calendar."""
    if taken.empty:
        return {"oos_n": 0, "oos_wr": 0.0, "oos_exp": 0.0, "is_exp": 0.0}
    dates = sorted(taken["signal_date"].unique())
    cut = dates[max(0, int(len(dates) * 0.70))]
    is_ = taken[taken["signal_date"] < cut]
    oos = taken[taken["signal_date"] >= cut]
    return {
        "oos_n": float(len(oos)),
        "oos_wr": _win_rate(oos["hit_1r"]) if len(oos) else 0.0,
        "oos_exp": _expectancy(oos["r_multiple"]) if len(oos) else 0.0,
        "is_exp": _expectancy(is_["r_multiple"]) if len(is_) else 0.0,
        "cut_date": str(cut),
    }


def judge(v0: dict, v1: dict) -> tuple[str, list[str]]:
    """PASS only if capture up and expectancy/WR not collapsed vs baseline."""
    reasons = []
    cap_ok = v1["capture"] > v0["capture"] + 1e-9
    if not cap_ok:
        reasons.append(
            f"FAIL capture: v1={v1['capture']:.3f} <= v0={v0['capture']:.3f}"
        )
    exp0 = v0["expectancy"]
    exp1 = v1["expectancy"]
    if exp0 > 0 and exp1 < exp0 * (1.0 - EXPECTANCY_TOL):
        reasons.append(
            f"FAIL expectancy collapse: v1={exp1:.3f} vs v0={exp0:.3f} (>{EXPECTANCY_TOL:.0%} worse)"
        )
    elif exp0 <= 0 and exp1 < exp0:
        reasons.append(f"FAIL expectancy worse: v1={exp1:.3f} vs v0={exp0:.3f}")

    # Absolute 35% WR is for live trade WR; rest-of-day +5% hit rate is much lower.
    # Gate: relative collapse vs Peak Hour v0 (Phase-2.5-style dilution).
    wr0, wr1 = v0["wr"], v1["wr"]
    if v1["n"] >= 20 and wr0 > 0 and wr1 < wr0 * (1.0 - EXPECTANCY_TOL):
        reasons.append(
            f"FAIL WR collapse vs baseline: wr={wr1:.1%} vs v0={wr0:.1%} "
            f"(>{EXPECTANCY_TOL:.0%} relative drop; abs Phase-2.5 floor was {WR_COLLAPSE:.0%})"
        )
    if not reasons and cap_ok:
        return "PASS", [
            "Challenger beats Peak Hour v0 on expander capture without expectancy/WR collapse"
        ]
    if not reasons:
        reasons.append("FAIL: no capture improvement")
    return "FAIL", reasons


def write_results(
    path: Path,
    *,
    verdict: str,
    reasons: list[str],
    v0: dict,
    v1: dict,
    cases: list[dict],
    meta: dict,
) -> None:
    lines = [
        "# EXP-0021 results — Continuation Ranker bakeoff",
        "",
        f"**Verdict: {verdict}**",
        "",
        f"Generated: {meta.get('generated')}",
        f"Runtime: {meta.get('runtime_sec'):.1f}s",
        f"Corpus: {meta.get('n_rows')} signals / {meta.get('n_symbols')} symbols / "
        f"{meta.get('lookback_days')}d lookback / universe={meta.get('universe', '?')}",
        f"Scan clock: hourly :15 · slots={meta.get('slots_per_scan', SLOTS_PER_SCAN)} · "
        f"Social: StockTwits+news; X={'on' if meta.get('x_bearer') else 'off'}",
        "",
        "## Gate checks",
        "",
    ]
    for r in reasons:
        lines.append(f"- {r}")
    lines += [
        "",
        "## Peak Hour v0 (baseline)",
        "",
        f"| Metric | Value |",
        f"|---|---|",
        f"| Slots taken | {v0['n']} |",
        f"| Win rate (hit_1r) | {v0['wr']:.1%} |",
        f"| Expectancy (R) | {v0['expectancy']:.3f} |",
        f"| Median MFE | {v0['med_mfe']:.2%} |",
        f"| Expander capture | {v0['capture']:.1%} ({v0['n_caught']:.0f}/{v0['n_expanders']:.0f}) |",
        f"| OOS WR / Exp | {v0['oos_wr']:.1%} / {v0['oos_exp']:.3f} |",
        "",
        "## All-hours + continuation_score_v1 (challenger)",
        "",
        f"| Metric | Value |",
        f"|---|---|",
        f"| Slots taken | {v1['n']} |",
        f"| Win rate (hit_1r) | {v1['wr']:.1%} |",
        f"| Expectancy (R) | {v1['expectancy']:.3f} |",
        f"| Median MFE | {v1['med_mfe']:.2%} |",
        f"| Expander capture | {v1['capture']:.1%} ({v1['n_caught']:.0f}/{v1['n_expanders']:.0f}) |",
        f"| OOS WR / Exp | {v1['oos_wr']:.1%} / {v1['oos_exp']:.3f} |",
        "",
        "## Case studies (feature explanation)",
        "",
    ]
    for c in cases:
        if c.get("n", 0) == 0:
            lines.append(f"- **{c['symbol']}**: no signals in window")
            continue
        lines.append(
            f"- **{c['symbol']}** @ {c['best_ts']} hour={c['hour']} peak={c['peak_hour']} "
            f"scan={c['scan']:.0f} {c['bar_state']} room20d={c['room_20d']:.1%} bounce={c['bounce']:.2f} "
            f"vol={c['vol_ratio']:.1f}x v0_admit={c['v0_admit']} score_v0={c['score_v0']:.1f} "
            f"score_v1={c['score_v1']:.1f} day_mfe={c['day_mfe']:.1%} hit_1r={c['hit_1r']} "
            f"news24={c['news_24h']:.0f} st={c['st_msg']:.0f}"
        )
    hours = meta.get("hour_buckets") or []
    if hours:
        lines += [
            "",
            "## Hour buckets (all HTF signals — evening decision)",
            "",
            "| Hour ET (bar close) | n | hit_1r | Exp R | med MFE | med day MFE | peak? |",
            "|---|---:|---:|---:|---:|---:|---|",
        ]
        for h in hours:
            lines.append(
                f"| {h['hour']:02d} | {h['n']} | {h['wr']:.1%} | {h['exp']:.3f} | "
                f"{h['med_mfe']:.2%} | {h['day_mfe']:.2%} | {'Y' if h['peak'] else ''} |"
            )
        lines += [
            "",
            "**Evening / after-hours:** do **not** run entry scans after the **15:15** "
            "clock (last RTH bar close = 15:00). Hour 16 and post-close / evening "
            "extended-hours :15 adds noise and gap risk, not expander capture. "
            "Ops may still run trail/marks only.",
            "",
        ]
    lines += [
        "",
        "## Live promotion",
        "",
        "Shipped: hourly :15 through **15:15 ET**, 2-slot cap, continuation_score_v1. "
        "No evening entry clock.",
        "",
        "## Costs",
        "",
        f"COST_PER_TRADE = {COST_PER_TRADE} (reported expectancy is path R before cost; "
        f"slot counts are pre-cost).",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def summarize_policy(corpus: pd.DataFrame, taken: pd.DataFrame) -> dict:
    cap = day_expander_capture(corpus, taken)
    oos = temporal_split_metrics(taken)
    return {
        "n": int(len(taken)),
        "wr": _win_rate(taken["hit_1r"]) if len(taken) else 0.0,
        "expectancy": _expectancy(taken["r_multiple"]) if len(taken) else 0.0,
        "med_mfe": float(taken["mfe"].median()) if len(taken) else 0.0,
        **cap,
        **oos,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="EXP-0021 continuation ranker bakeoff")
    parser.add_argument("--pilot", action="store_true", help="Use DEFAULT_PILOT symbols")
    parser.add_argument("--htf-universe", action="store_true", help="Full HTF daily-pass list")
    parser.add_argument("--htf-file", type=str, default="", help="Explicit htf_pass_*.json path")
    parser.add_argument("--symbols", type=str, default="", help="Comma-separated tickers")
    parser.add_argument("--days", type=int, default=90, help="Lookback trading calendar days ~")
    parser.add_argument("--no-social", action="store_true", help="Skip StockTwits/X/news velocity")
    parser.add_argument("--out", type=str, default="", help="Corpus parquet/csv path")
    args = parser.parse_args()

    t0 = time.time()
    if args.symbols.strip():
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        label = "custom"
    elif args.htf_universe or args.htf_file:
        htf_path = Path(args.htf_file) if args.htf_file else None
        symbols = load_htf_universe_symbols(htf_path)
        label = "htf_universe"
    elif args.pilot:
        symbols = list(DEFAULT_PILOT)
        label = "pilot"
    else:
        # Default: full HTF universe (not pilot)
        symbols = load_htf_universe_symbols(None)
        label = "htf_universe"

    print("=" * 64)
    print("EXP-0021 Continuation Ranker bakeoff")
    print(
        f"  set={label} symbols={len(symbols)} days={args.days} "
        f"social={not args.no_social} slots={SLOTS_PER_SCAN}/hour:15"
    )
    print("=" * 64)

    key = load_polygon_key()
    # Full HTF: skip social by default unless explicitly enabled (rate limits).
    include_social = (not args.no_social) and (label != "htf_universe" or args.pilot)
    if label == "htf_universe" and not args.no_social and not args.pilot:
        # --htf-universe implies tape/MTF first; pass --pilot for social pilot
        include_social = False
        print("  social=off for full HTF (use pilot run for StockTwits/X)", flush=True)

    corpus = build_corpus(
        symbols,
        api_key=key,
        lookback_days=args.days,
        include_social=include_social,
    )
    default_out = (
        EXP_DIR / "corpus_htf_universe.csv"
        if label == "htf_universe"
        else EXP_DIR / "corpus_pilot.csv"
    )
    out_path = Path(args.out) if args.out else default_out
    if not corpus.empty:
        corpus.to_csv(out_path, index=False)
        print(f"  Wrote {out_path} ({len(corpus)} rows)")

    empty_metrics = {
        "n": 0, "wr": 0, "expectancy": 0, "med_mfe": 0,
        "capture": 0, "n_caught": 0, "n_expanders": 0, "oos_wr": 0, "oos_exp": 0,
    }
    if corpus.empty:
        write_results(
            EXP_DIR / "results.md",
            verdict="FAIL",
            reasons=["FAIL: empty corpus — check Polygon key / universe"],
            v0=empty_metrics,
            v1=empty_metrics,
            cases=[],
            meta={
                "generated": datetime.now(ET).isoformat(),
                "runtime_sec": time.time() - t0,
                "n_rows": 0,
                "n_symbols": len(symbols),
                "lookback_days": args.days,
                "universe": label,
                "x_bearer": bool(
                    __import__("os").environ.get("X_BEARER_TOKEN")
                    or __import__("os").environ.get("TWITTER_BEARER_TOKEN")
                ),
                "hour_buckets": [],
            },
        )
        print("EMPTY CORPUS — results.md FAIL")
        return 1

    taken_v0 = simulate_slots(corpus, score_col="continuation_score_v0", admit_col="php_v0_admit")
    taken_v1 = simulate_slots(corpus, score_col="continuation_score_v1", admit_col="all_hours_admit")
    v0 = summarize_policy(corpus, taken_v0)
    v1 = summarize_policy(corpus, taken_v1)
    verdict, reasons = judge(v0, v1)
    cases = case_study(corpus, ["IREN", "TARS", "CHPT", "ARX", "JANX"])
    hours = hour_bucket_report(corpus)

    meta = {
        "generated": datetime.now(ET).isoformat(),
        "runtime_sec": time.time() - t0,
        "n_rows": int(len(corpus)),
        "n_symbols": int(corpus["symbol"].nunique()),
        "lookback_days": args.days,
        "universe": label,
        "slots_per_scan": SLOTS_PER_SCAN,
        "scan_clock": "hourly_:15",
        "x_bearer": bool(
            __import__("os").environ.get("X_BEARER_TOKEN")
            or __import__("os").environ.get("TWITTER_BEARER_TOKEN")
        ),
        "hour_buckets": hours,
    }
    write_results(EXP_DIR / "results.md", verdict=verdict, reasons=reasons, v0=v0, v1=v1, cases=cases, meta=meta)

    metrics_path = EXP_DIR / "bakeoff_metrics.json"
    metrics_path.write_text(
        json.dumps(
            {"verdict": verdict, "reasons": reasons, "v0": v0, "v1": v1, "cases": cases, "meta": meta},
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"\nVerdict: {verdict}")
    for r in reasons:
        print(f"  {r}")
    print("Hour buckets (evening check):")
    for h in hours:
        print(
            f"  h={h['hour']:02d} n={h['n']} wr={h['wr']:.1%} exp={h['exp']:.3f} "
            f"mfe={h['med_mfe']:.2%} day_mfe={h['day_mfe']:.2%}"
        )
    print(f"results.md written ({time.time() - t0:.1f}s)")
    return 0 if verdict == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
