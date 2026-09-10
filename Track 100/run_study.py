"""
Track 100 â€” main study runner.

Fetches ~3 months of 1H bars for NDX100 + popular liquid names, ablates
Wave Cross trough setups (TSLA-chart style), locks best rules into results.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytz

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backtest import (  # noqa: E402
    SetupSpec,
    collect_signals,
    measure_path,
    setup_catalog,
    summarize_trades,
)
from data import fetch_1h_bars, load_polygon_key, save_json, study_window  # noqa: E402
from universe import study_universe  # noqa: E402
from wave import enrich_wave  # noqa: E402

ET = pytz.timezone("America/New_York")
RESULTS = ROOT / "results"


def _process_symbol(
    sym: str,
    *,
    fetch_start: str,
    signal_start: str,
    signal_end: str,
    api_key: str,
    setups: list[SetupSpec],
    exit_grid: list[tuple[float, float, int]],
) -> dict[str, Any]:
    out: dict[str, Any] = {"symbol": sym, "ok": False, "trades_by_setup": {}, "error": None}
    try:
        df = fetch_1h_bars(sym, start=fetch_start, end=signal_end, api_key=api_key)
        if df is None or len(df) < 80:
            out["error"] = f"thin_bars={0 if df is None else len(df)}"
            return out
        enriched = enrich_wave(df)
        out["ok"] = True
        out["n_bars"] = len(enriched)
        for spec in setups:
            idxs = collect_signals(
                enriched, spec, signal_start=signal_start, signal_end=signal_end,
            )
            # Default path first (5% stop / 10% target / 40 bars â‰ˆ 1 week RTH-ish on 1H)
            trades = []
            for i in idxs:
                m = measure_path(
                    enriched, i, horizon_bars=40, stop_pct=0.05, target_pct=0.10,
                )
                if m.get("valid"):
                    m["symbol"] = sym
                    m["setup"] = spec.name
                    trades.append(m)
            out["trades_by_setup"][spec.name] = trades

            # Store signal counts for exit-grid on best later
            out.setdefault("signal_idx", {})[spec.name] = idxs
        out["_enriched_meta"] = {
            "last_close": float(enriched.iloc[-1]["close"]),
            "last_wt1": float(enriched.iloc[-1]["wt1"]) if pd.notna(enriched.iloc[-1]["wt1"]) else None,
        }
        # Keep enriched on disk-light path: re-fetch from cache for exit refine
        out["_cache_ok"] = True
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["trace"] = traceback.format_exc()[-800:]
    return out


def aggregate_setups(
    per_symbol: list[dict[str, Any]],
    setups: list[SetupSpec],
) -> dict[str, Any]:
    agg: dict[str, Any] = {}
    for spec in setups:
        trades: list[dict[str, Any]] = []
        for row in per_symbol:
            trades.extend(row.get("trades_by_setup", {}).get(spec.name) or [])
        summary = summarize_trades(trades)
        summary["name"] = spec.name
        summary["describe"] = spec.describe
        # Per-symbol contribution
        by_sym: dict[str, float] = {}
        for t in trades:
            by_sym[t["symbol"]] = by_sym.get(t["symbol"], 0.0) + float(t["net_ret"])
        summary["top_symbols"] = sorted(by_sym.items(), key=lambda x: -x[1])[:10]
        summary["worst_symbols"] = sorted(by_sym.items(), key=lambda x: x[1])[:10]
        agg[spec.name] = {"summary": summary, "n_trades": summary["n"], "trades": trades}
    return agg


def score_setup(summary: dict[str, Any]) -> float:
    """
    Rank setups for 'impeccable': need sample + positive expectancy + edge.
    """
    n = int(summary.get("n") or 0)
    if n < 25:
        return -999.0
    exp = float(summary.get("expectancy") or 0)
    wr = float(summary.get("win_rate") or 0)
    pf = float(summary.get("profit_factor") or 0)
    # Prefer expectancy, then PF, then win rate; soft n bonus
    return exp * 100.0 + min(pf, 5.0) * 0.5 + wr * 5.0 + min(n, 200) * 0.01


def refine_exits(
    *,
    best_name: str,
    per_symbol: list[dict[str, Any]],
    fetch_start: str,
    signal_start: str,
    signal_end: str,
    api_key: str,
    setups_by_name: dict[str, SetupSpec],
) -> dict[str, Any]:
    """Re-measure best setup across exit grid."""
    spec = setups_by_name[best_name]
    grid = [
        (0.03, 0.06, 24),
        (0.04, 0.08, 32),
        (0.05, 0.10, 40),
        (0.05, 0.15, 48),
        (0.06, 0.12, 40),
        (0.04, 0.12, 40),  # 3R
        (0.05, 0.05, 40),  # 1R scalp
        (0.03, 0.09, 40),  # 3R tight
    ]
    results: dict[str, Any] = {}
    for stop_pct, target_pct, horizon in grid:
        key = f"stop{int(stop_pct*100)}_tgt{int(target_pct*100)}_h{horizon}"
        trades: list[dict[str, Any]] = []
        for row in per_symbol:
            if not row.get("ok"):
                continue
            sym = row["symbol"]
            df = fetch_1h_bars(sym, start=fetch_start, end=signal_end, api_key=api_key)
            enriched = enrich_wave(df)
            idxs = collect_signals(
                enriched, spec, signal_start=signal_start, signal_end=signal_end,
            )
            for i in idxs:
                m = measure_path(
                    enriched, i,
                    horizon_bars=horizon,
                    stop_pct=stop_pct,
                    target_pct=target_pct,
                )
                if m.get("valid"):
                    m["symbol"] = sym
                    trades.append(m)
        summary = summarize_trades(trades)
        summary["stop_pct"] = stop_pct
        summary["target_pct"] = target_pct
        summary["horizon_bars"] = horizon
        summary["score"] = score_setup(summary)
        results[key] = {"summary": summary, "trades": trades}
    # Pick best grid cell
    best_key = max(results.keys(), key=lambda k: results[k]["summary"]["score"])
    return {"best_key": best_key, "grid": {k: v["summary"] for k, v in results.items()}, "best": results[best_key]}


def write_results_md(
    path: Path,
    *,
    window: dict[str, str],
    universe_n: int,
    ok_n: int,
    setup_agg: dict[str, Any],
    best_name: str,
    exit_refine: dict[str, Any] | None,
    runtime_s: float,
) -> None:
    lines: list[str] = []
    lines.append("# Track 100 â€” Wave Cross trough study")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now(ET).isoformat()}")
    lines.append(f"**Window (signals):** {window['signal_start']} â†’ {window['signal_end']}")
    lines.append(f"**Fetch warmup from:** {window['fetch_start']}")
    lines.append(f"**Universe:** {universe_n} symbols | bars OK: {ok_n}")
    lines.append(f"**Runtime:** {runtime_s:.1f}s")
    lines.append("")
    lines.append("## Thesis (from TSLA chart)")
    lines.append("")
    lines.append("Long when Wave Cross (TSD) prints a **green buy/early-bull dot** at a")
    lines.append("**deep oversold trough** (WT near/below âˆ’50), on 1H bars, universe =")
    lines.append("Nasdaq-100 + liquid popular names. Long only.")
    lines.append("")
    lines.append("## Setup ablation (default exit: âˆ’5% / +10% / 40Ã—1H bars, cost 0.15%)")
    lines.append("")
    lines.append("| Setup | N | Win% | Avg net | Sum net | Avg MFE | Hit tgt | Hit stop | PF | Score |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    ranked = sorted(
        setup_agg.values(),
        key=lambda x: score_setup(x["summary"]),
        reverse=True,
    )
    for block in ranked:
        s = block["summary"]
        lines.append(
            f"| `{s['name']}` {s['describe'][:40]} | {s['n']} | "
            f"{(s['win_rate'] or 0)*100:.1f}% | {(s['avg_net'] or 0)*100:.2f}% | "
            f"{(s['sum_net'] or 0)*100:.1f}% | {(s['avg_mfe'] or 0)*100:.1f}% | "
            f"{(s['hit_target_rate'] or 0)*100:.0f}% | {(s['hit_stop_rate'] or 0)*100:.0f}% | "
            f"{(s['profit_factor'] or 0):.2f} | {score_setup(s):.2f} |"
        )
    lines.append("")
    lines.append(f"**Best setup by score:** `{best_name}`")
    lines.append("")
    best_s = setup_agg[best_name]["summary"]
    lines.append("### Best setup detail")
    lines.append("")
    lines.append(f"- Describe: {best_s['describe']}")
    lines.append(f"- N={best_s['n']} win_rate={(best_s['win_rate'] or 0)*100:.1f}% "
                 f"expectancy={(best_s['avg_net'] or 0)*100:.2f}% PF={best_s['profit_factor']:.2f}")
    lines.append(f"- Top contributors: {best_s.get('top_symbols')}")
    lines.append(f"- Worst contributors: {best_s.get('worst_symbols')}")
    lines.append("")

    if exit_refine:
        lines.append("## Exit grid on best setup")
        lines.append("")
        lines.append(f"**Best exit cell:** `{exit_refine['best_key']}`")
        lines.append("")
        lines.append("| Exit | N | Win% | Avg net | Sum net | PF | Score |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for k, s in sorted(exit_refine["grid"].items(), key=lambda kv: -kv[1]["score"]):
            lines.append(
                f"| `{k}` | {s['n']} | {(s['win_rate'] or 0)*100:.1f}% | "
                f"{(s['avg_net'] or 0)*100:.2f}% | {(s['sum_net'] or 0)*100:.1f}% | "
                f"{s['profit_factor']:.2f} | {s['score']:.2f} |"
            )
        lines.append("")
        locked = exit_refine["best"]["summary"]
        lines.append("## Locked strategy (Track 100 v1)")
        lines.append("")
        lines.append("```")
        lines.append(f"UNIVERSE: Nasdaq-100 âˆª liquid popular (cap ~120)")
        lines.append(f"TF: 1-hour Polygon aggs")
        lines.append(f"ENTRY: {setup_agg[best_name]['summary']['describe']}")
        lines.append(f"FILL: next bar open after signal close (causal)")
        lines.append(f"STOP: {locked['stop_pct']*100:.1f}%")
        lines.append(f"TARGET: {locked['target_pct']*100:.1f}%")
        lines.append(f"TIME: {locked['horizon_bars']} Ã— 1H bars max")
        lines.append(f"COST: 0.15% per round trip")
        lines.append(f"SIDE: LONG ONLY")
        lines.append("```")
        lines.append("")
        lines.append(
            f"**Locked sample:** N={locked['n']} | win={(locked['win_rate'] or 0)*100:.1f}% | "
            f"avg_net={(locked['avg_net'] or 0)*100:.2f}% | PF={locked['profit_factor']:.2f}"
        )
        if (locked.get("avg_net") or 0) <= 0 or (locked.get("n") or 0) < 25:
            lines.append("")
            lines.append("### VERDICT: FAIL (so far)")
            lines.append("Edge not yet positive / sample thin under locked exits. See ablations.")
        elif (locked.get("avg_net") or 0) > 0 and (locked.get("profit_factor") or 0) >= 1.2:
            lines.append("")
            lines.append("### VERDICT: PASS (study-level)")
            lines.append("Positive expectancy + PFâ‰¥1.2 on 3-month Track 100 sample. Not live.")
        else:
            lines.append("")
            lines.append("### VERDICT: WEAK PASS / NEEDS MORE")
            lines.append("Positive but thin edge â€” extend window or tighten filters before live.")

    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- Isolated from Q-ALPHA live paper; no candidate pipeline edits.")
    lines.append("- Red sell dots ignored for entries (long-only study).")
    lines.append("- Same-bar stop-before-target conservative fill assumption.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    t0 = time.time()
    RESULTS.mkdir(parents=True, exist_ok=True)
    fetch_start, signal_start, signal_end = study_window(months=3, warmup_days=45)
    window = {
        "fetch_start": fetch_start,
        "signal_start": signal_start,
        "signal_end": signal_end,
    }
    print("=" * 64)
    print("TRACK 100 â€” Wave Cross trough study")
    print(f"signals {signal_start} â†’ {signal_end} | fetch from {fetch_start}")
    print("=" * 64)

    api_key = load_polygon_key()
    symbols = study_universe(include_popular_extra=True, cap=120)
    print(f"Universe: {len(symbols)} symbols")
    setups = setup_catalog()
    setups_by_name = {s.name: s for s in setups}

    per_symbol: list[dict[str, Any]] = []
    # Sequential is kinder to Polygon rate limits; still progress every symbol
    for i, sym in enumerate(symbols, 1):
        print(f"[{i}/{len(symbols)}] {sym} ...", flush=True)
        row = _process_symbol(
            sym,
            fetch_start=fetch_start,
            signal_start=signal_start,
            signal_end=signal_end,
            api_key=api_key,
            setups=setups,
            exit_grid=[],
        )
        status = "OK" if row.get("ok") else f"FAIL {row.get('error')}"
        n_b = sum(len(v) for v in (row.get("trades_by_setup") or {}).values())
        print(f"    â†’ {status} trades_all_setups={n_b}", flush=True)
        per_symbol.append(row)
        # Light progress artifact
        if i % 10 == 0:
            save_json(RESULTS / "progress.json", {
                "i": i, "n": len(symbols), "ok": sum(1 for r in per_symbol if r.get("ok")),
            })

    ok_n = sum(1 for r in per_symbol if r.get("ok"))
    setup_agg = aggregate_setups(per_symbol, setups)
    # Strip heavy trade lists for ranking file
    ranking = {
        name: {
            "summary": block["summary"],
            "score": score_setup(block["summary"]),
        }
        for name, block in setup_agg.items()
    }
    best_name = max(ranking.keys(), key=lambda k: ranking[k]["score"])
    print(f"\nBest setup: {best_name} score={ranking[best_name]['score']:.2f}")

    print("\n--- Exit refine on best setup ---")
    exit_refine = refine_exits(
        best_name=best_name,
        per_symbol=per_symbol,
        fetch_start=fetch_start,
        signal_start=signal_start,
        signal_end=signal_end,
        api_key=api_key,
        setups_by_name=setups_by_name,
    )
    print(f"Best exit: {exit_refine['best_key']} "
          f"avg_net={(exit_refine['best']['summary']['avg_net'] or 0)*100:.2f}%")

    runtime = time.time() - t0
    # Persist
    save_json(RESULTS / "window.json", window)
    save_json(RESULTS / "universe.json", {"symbols": symbols, "n": len(symbols)})
    save_json(RESULTS / "setup_ranking.json", ranking)
    save_json(
        RESULTS / "best_trades.json",
        {
            "setup": best_name,
            "default_exit_trades": setup_agg[best_name]["trades"][:500],
            "exit_refine_summary": exit_refine["grid"],
            "locked_exit": exit_refine["best"]["summary"],
            "locked_trades": exit_refine["best"]["trades"][:500],
        },
    )
    # Errors
    errs = [{"symbol": r["symbol"], "error": r.get("error")} for r in per_symbol if not r.get("ok")]
    save_json(RESULTS / "fetch_errors.json", errs)

    write_results_md(
        RESULTS / "results.md",
        window=window,
        universe_n=len(symbols),
        ok_n=ok_n,
        setup_agg=setup_agg,
        best_name=best_name,
        exit_refine=exit_refine,
        runtime_s=runtime,
    )
    # Also copy a strategy lock file
    locked = {
        "name": "Track100_v1",
        "setup": best_name,
        "describe": setup_agg[best_name]["summary"]["describe"],
        "exit": exit_refine["best"]["summary"],
        "window": window,
        "verdict_hint": exit_refine["best"]["summary"],
    }
    save_json(RESULTS / "strategy_lock.json", locked)

    print(f"\nDone in {runtime:.1f}s â†’ {RESULTS / 'results.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

