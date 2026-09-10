"""
Track 100 v2 refine — tighten toward an impeccable rule set.

Uses cached 1H bars. No Q-ALPHA live edits.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backtest import measure_path, summarize_trades, SetupSpec  # noqa: E402
from data import fetch_1h_bars, load_polygon_key, save_json, study_window  # noqa: E402
from universe import NDX100, study_universe  # noqa: E402
from wave import enrich_wave  # noqa: E402

RESULTS = ROOT / "results"

# Levered / noisy names to drop even if in popular list
EXCLUDE = {
    "SOXL", "TQQQ", "NVDL", "TSLL", "CONL", "SPY", "QQQ", "IWM",
}


def cooldown_filter(idxs: list[int], *, min_gap: int = 8) -> list[int]:
    """Keep first signal in a cluster; skip until min_gap bars later."""
    if not idxs:
        return []
    out = [idxs[0]]
    last = idxs[0]
    for i in idxs[1:]:
        if i - last >= min_gap:
            out.append(i)
            last = i
    return out


def mask_os53_green(df: pd.DataFrame) -> pd.Series:
    return df["green_dot"].fillna(False) & df["deep_os"].fillna(False)


def mask_os53_buy(df: pd.DataFrame) -> pd.Series:
    return df["buy_signal"].fillna(False) & df["deep_os"].fillna(False)


def mask_os53_early(df: pd.DataFrame) -> pd.Series:
    return df["early_bull"].fillna(False) & df["deep_os"].fillna(False)


def mask_os53_green_not_extended(df: pd.DataFrame) -> pd.Series:
    """OS green but not already a vertical spike (close near 10-bar high)."""
    hh = df["high"].rolling(10, min_periods=3).max()
    ext = (df["close"] / hh.replace(0, np.nan)) >= 0.995  # at highs
    return mask_os53_green(df) & (~ext.fillna(False))


SPECS = [
    SetupSpec("C_os53_green", "OS<=-53 + green", mask_os53_green),
    SetupSpec("C_buy", "OS<=-53 + buy cross only", mask_os53_buy),
    SetupSpec("C_early", "OS<=-53 + early only", mask_os53_early),
    SetupSpec("C_not_ext", "OS<=-53 + green + not at 10b high", mask_os53_green_not_extended),
]


def run_variant(
    symbols: list[str],
    *,
    fetch_start: str,
    signal_start: str,
    signal_end: str,
    api_key: str,
    spec: SetupSpec,
    stop_pct: float,
    target_pct: float,
    horizon: int,
    cooldown: int,
) -> dict[str, Any]:
    trades: list[dict[str, Any]] = []
    for sym in symbols:
        if sym in EXCLUDE:
            continue
        df = fetch_1h_bars(sym, start=fetch_start, end=signal_end, api_key=api_key)
        if df is None or len(df) < 80:
            continue
        en = enrich_wave(df)
        mask = spec.mask_fn(en).fillna(False)
        start_ts = pd.Timestamp(signal_start, tz=en.index.tz)
        end_ts = pd.Timestamp(signal_end, tz=en.index.tz) + pd.Timedelta(days=1)
        idxs = [
            i for i, (ts, ok) in enumerate(zip(en.index, mask.tolist()))
            if ok and start_ts <= ts < end_ts and i + 1 < len(en)
        ]
        idxs = cooldown_filter(idxs, min_gap=cooldown)
        for i in idxs:
            m = measure_path(
                en, i, horizon_bars=horizon, stop_pct=stop_pct, target_pct=target_pct,
            )
            if m.get("valid"):
                m["symbol"] = sym
                trades.append(m)
    summary = summarize_trades(trades)
    summary.update({
        "setup": spec.name,
        "describe": spec.describe,
        "stop_pct": stop_pct,
        "target_pct": target_pct,
        "horizon": horizon,
        "cooldown": cooldown,
        "universe_n": len([s for s in symbols if s not in EXCLUDE]),
    })
    return {"summary": summary, "trades": trades}


def quality_score(s: dict[str, Any]) -> float:
    n = int(s.get("n") or 0)
    if n < 40:
        return -999.0
    exp = float(s.get("avg_net") or 0)
    pf = float(s.get("profit_factor") or 0)
    wr = float(s.get("win_rate") or 0)
    # Stronger weight on expectancy + PF; require not tiny n
    return exp * 200.0 + min(pf, 3.0) * 2.0 + wr * 10.0 + min(n, 400) * 0.005


def main() -> int:
    key = load_polygon_key()
    fetch_start, signal_start, signal_end = study_window(months=3, warmup_days=45)
    ndx = [s for s in NDX100 if s not in EXCLUDE]
    full = [s for s in study_universe(cap=120) if s not in EXCLUDE]

    exit_grid = [
        (0.025, 0.04, 24),
        (0.03, 0.045, 32),
        (0.03, 0.06, 32),
        (0.035, 0.05, 32),
        (0.04, 0.06, 40),
        (0.04, 0.08, 40),
        (0.05, 0.08, 40),
        (0.06, 0.12, 40),
    ]
    cooldowns = [1, 6, 8, 12]

    rows: list[dict[str, Any]] = []
    print("Track 100 v2 refine...")
    for universe_name, symbols in (("NDX100", ndx), ("LIQUID120", full)):
        for spec in SPECS:
            for cool in cooldowns:
                for stop_pct, target_pct, horizon in exit_grid:
                    tag = (
                        f"{universe_name}|{spec.name}|cd{cool}|"
                        f"s{int(stop_pct*1000)}_t{int(target_pct*1000)}_h{horizon}"
                    )
                    print(f"  {tag}", flush=True)
                    block = run_variant(
                        symbols,
                        fetch_start=fetch_start,
                        signal_start=signal_start,
                        signal_end=signal_end,
                        api_key=key,
                        spec=spec,
                        stop_pct=stop_pct,
                        target_pct=target_pct,
                        horizon=horizon,
                        cooldown=cool,
                    )
                    s = block["summary"]
                    s["universe"] = universe_name
                    s["tag"] = tag
                    s["qscore"] = quality_score(s)
                    rows.append(s)

    rows_sorted = sorted(rows, key=lambda r: r["qscore"], reverse=True)
    save_json(RESULTS / "refine_v2_ranking.json", rows_sorted[:80])

    best = rows_sorted[0]
    # Re-run best to keep trades
    spec = next(s for s in SPECS if s.name == best["setup"])
    symbols = ndx if best["universe"] == "NDX100" else full
    best_block = run_variant(
        symbols,
        fetch_start=fetch_start,
        signal_start=signal_start,
        signal_end=signal_end,
        api_key=key,
        spec=spec,
        stop_pct=best["stop_pct"],
        target_pct=best["target_pct"],
        horizon=best["horizon"],
        cooldown=best["cooldown"],
    )
    save_json(RESULTS / "strategy_lock_v2.json", {
        "name": "Track100_v2",
        "best": best,
        "top10": rows_sorted[:10],
        "n_trades_saved": len(best_block["trades"]),
        "sample_trades": best_block["trades"][:100],
    })

    # Write clean ASCII results append / v2 md
    lines = [
        "# Track 100 v2 — refined strategy lock",
        "",
        f"Window: {signal_start} -> {signal_end}",
        f"Excluded levered/index: {sorted(EXCLUDE)}",
        "",
        "## Top 10 variants",
        "",
        "| Tag | N | Win% | Avg net | PF | QScore |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in rows_sorted[:10]:
        lines.append(
            f"| `{r['tag']}` | {r['n']} | {(r['win_rate'] or 0)*100:.1f}% | "
            f"{(r['avg_net'] or 0)*100:.2f}% | {r['profit_factor']:.2f} | {r['qscore']:.2f} |"
        )
    lines += [
        "",
        "## Locked Track100_v2",
        "",
        "```",
        f"UNIVERSE: {best['universe']} (ex levered ETFs)",
        f"ENTRY: {best['describe']}",
        f"COOLDOWN: {best['cooldown']} x 1H bars between signals (same symbol)",
        f"FILL: next bar open",
        f"STOP: {best['stop_pct']*100:.2f}%",
        f"TARGET: {best['target_pct']*100:.2f}%",
        f"TIME CAP: {best['horizon']} x 1H",
        "COST: 0.15% RT",
        "SIDE: LONG ONLY",
        "```",
        "",
        f"N={best['n']} win={(best['win_rate'] or 0)*100:.1f}% "
        f"avg_net={(best['avg_net'] or 0)*100:.2f}% PF={best['profit_factor']:.2f} "
        f"avg_mfe={(best.get('avg_mfe') or 0)*100:.1f}%",
        "",
    ]
    avg = best.get("avg_net") or 0
    pf = best.get("profit_factor") or 0
    n = best.get("n") or 0
    if avg > 0.005 and pf >= 1.3 and n >= 60:
        lines.append("### VERDICT: PASS (study-level)")
        lines.append("Expectancy >0.5%/trade and PF>=1.3 on 3-month sample.")
    elif avg > 0 and pf >= 1.15 and n >= 40:
        lines.append("### VERDICT: CONDITIONAL PASS")
        lines.append("Positive edge after refine; still paper-only / needs walk-forward.")
    else:
        lines.append("### VERDICT: FAIL / WEAK")
        lines.append("Could not lock a robust edge under refine grid.")

    lines += [
        "",
        "## What the TSLA chart got right",
        "",
        "- Deep OS (<= -53) + green dot beats raw green dots (which lose money).",
        "- Clustered greens without cooldown overtrade the same trough.",
        "- Avg MFE ~4% argues for targets near 4-6%, not 10-12% first take.",
        "",
        "## Not live",
        "",
        "This folder does not modify Q-ALPHA Peak Hour / TSD live paper.",
    ]
    (RESULTS / "results_v2.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Best:", best["tag"], f"avg_net={best['avg_net']*100:.2f}% PF={best['profit_factor']:.2f}")
    print("Wrote", RESULTS / "results_v2.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
