"""
EXP-0021 research — winner existence every hour 04→15 ET.

Thesis: expand *which hours* can hitch a ride (with score), not slot size.
Live stays shipped (05-15); this study measured hours we previously filtered out.

Usage:
  py -3 experiments/EXP-0021/study_hours_04_15.py
  py -3 experiments/EXP-0021/study_hours_04_15.py --max-symbols 40  # smoke
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytz

EXP_DIR = Path(__file__).resolve().parent
ROOT = EXP_DIR.parent.parent
CANDIDATES = ROOT / "candidates"
LIB = EXP_DIR / "lib"
for p in (str(CANDIDATES), str(LIB), str(EXP_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from corpus import (  # noqa: E402
    aggs_to_df,
    bar_close_hour_et,
    fetch_aggs,
    prior_ticker_stats,
    rest_of_day_path,
)
from features import (  # noqa: E402
    PEAK_HOURS,
    bar_state_from_ohlc,
    continuation_score_v1,
    daily_mtf_features,
    path_labels_after_entry,
    session_features_at,
)
from experiment21 import load_htf_universe_symbols  # noqa: E402

from tsd_scan_pipeline.tsd_htf_gates import compute_htf_metrics, compute_htf_rank_score  # noqa: E402
from tsd_scan_pipeline.tsd_launch_score import (  # noqa: E402
    compute_continuation_score_v0,
    compute_launch_phase,
    compute_launch_score,
    is_continuation_list_candidate,
)
from tsd_scan_pipeline.tsd_signals import enrich_tsd  # noqa: E402
from tsd_scan_pipeline.universe_tsd import load_polygon_key  # noqa: E402

ET = pytz.timezone("America/New_York")
STUDY_HOURS = frozenset(range(4, 16))  # 04..15 inclusive
KILL_PCT = 0.05
POLYGON_SLEEP = 0.12


def admit_study(feat: dict[str, Any]) -> bool:
    """
    Study admit: hours 04–15 + continuation list floors (no peak-hour hard gate).
    Hour 04–09 were never live; score decides hitch quality.
    """
    hour = int(feat.get("hour") or -1)
    if hour not in STUDY_HOURS:
        return False
    return is_continuation_list_candidate(feat)


def build_ticker_study_rows(
    symbol: str,
    *,
    api_key: str,
    lookback_days: int = 90,
) -> list[dict[str, Any]]:
    """All buy/early_bull on close-hours 04–15 with HTF + labels + score_v1."""
    end = datetime.now(ET).date()
    start = end - timedelta(days=lookback_days + 40)
    daily_start = end - timedelta(days=lookback_days + 400)

    hourly = aggs_to_df(fetch_aggs(api_key, symbol, mult=1, span="hour", start=start, end=end))
    daily = aggs_to_df(fetch_aggs(api_key, symbol, mult=1, span="day", start=daily_start, end=end))
    if hourly.empty or len(hourly) < 80:
        return []

    # Enrich on FULL series (WT needs contiguous history), then select study hours
    enriched = enrich_tsd(hourly.copy())
    enriched["close_hour"] = [bar_close_hour_et(ts) for ts in enriched.index]

    rows_out: list[dict[str, Any]] = []
    prior: list[dict[str, Any]] = []
    min_date = end - timedelta(days=lookback_days)
    daily_dates = pd.Series(daily.index.map(lambda t: t.date()), index=daily.index)

    for i in range(len(enriched)):
        r = enriched.iloc[i]
        ts = enriched.index[i]
        if ts.date() < min_date:
            continue
        hour = int(r["close_hour"])
        if hour not in STUDY_HOURS:
            continue
        buy = bool(r.get("buy_signal"))
        early = bool(r.get("early_bull"))
        if not (buy or early):
            continue

        d_prior = daily.loc[daily_dates < ts.date()]
        if len(d_prior) < 60:
            continue
        htf = compute_htf_metrics(
            d_prior["close"].astype(float).tolist(),
            d_prior["high"].astype(float).tolist(),
            d_prior["low"].astype(float).tolist(),
        )
        if htf.get("insufficient_bars"):
            continue
        if not (
            htf.get("range_ok")
            and htf.get("close_above_sma50")
            and htf.get("sma20_rising")
            and htf.get("price_ok")
        ):
            continue

        o, h, low, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
        scan = float(r["scan_score"]) if pd.notna(r.get("scan_score")) else 0.0
        bar_state = bar_state_from_ohlc(o, h, low, c, scan=scan)
        sess = session_features_at(enriched, i)
        mtf = daily_mtf_features(daily, as_of_date=ts.date(), signal_close=c)
        hist = prior_ticker_stats(prior)

        feat: dict[str, Any] = {
            "symbol": symbol.upper(),
            "signal_ts": str(ts),
            "signal_date": str(ts.date()),
            "hour": hour,
            "peak_hour": int(hour in PEAK_HOURS),
            "open": o,
            "high": h,
            "low": low,
            "close": c,
            "buy_signal": buy,
            "early_bull": early,
            "scan_score": scan,
            "trend_strength": float(r["trend_strength"]) if pd.notna(r.get("trend_strength")) else 0.0,
            "bar_state": bar_state,
            "htf_range_20d_pct": htf.get("range_20d_pct"),
            "htf_dist_sma50_pct": htf.get("dist_sma50_pct"),
            "htf_sma20_slope_pct": htf.get("sma20_slope_pct"),
            "htf_score": compute_htf_rank_score(htf),
            **sess,
            **{k: v for k, v in mtf.items() if k != "insufficient_daily"},
            **hist,
        }
        launch_row = {
            "scan_score": scan,
            "trend_strength": feat["trend_strength"],
            "buy_signal": buy,
            "early_bull": early,
            "open": o,
            "high": h,
            "low": low,
            "close": c,
            "bar_state": bar_state,
            "htf_score": feat["htf_score"],
            "htf_1h_bar_hour": hour,
        }
        feat["launch_score"] = compute_launch_score(launch_row)
        feat["phase"] = compute_launch_phase(launch_row)
        feat["continuation_score_v0"] = compute_continuation_score_v0(
            {**launch_row, "launch_score": feat["launch_score"]}
        )
        feat["continuation_score_v1"] = continuation_score_v1(feat)
        feat["admit"] = int(admit_study(feat))

        fut_h, fut_l, day_mfe = rest_of_day_path(enriched, i)
        labels = path_labels_after_entry(c, fut_h, fut_l, kill_pct=KILL_PCT)
        feat.update(labels)
        feat["day_mfe"] = round(day_mfe, 6)
        if labels["hit_1r"]:
            feat["r_multiple"] = 1.0
        elif labels["killed"]:
            feat["r_multiple"] = -1.0
        else:
            feat["r_multiple"] = float(labels["mfe"]) / KILL_PCT if KILL_PCT else 0.0

        rows_out.append(feat)
        prior.append({"mfe": labels["mfe"], "hit_1r": labels["hit_1r"]})

    return rows_out


def winner_existence_report(df: pd.DataFrame) -> pd.DataFrame:
    """
    Per hour: across trading days, how often does that clock have a hitchable winner?

    winner_day = max day_mfe among admit signals that (date, hour) >= 5%
    OR any hit_1r.
    """
    rows = []
    for hour in range(4, 16):
        g = df[(df["hour"] == hour) & (df["admit"] == 1)]
        if g.empty:
            rows.append({
                "hour": hour,
                "n_signals": 0,
                "n_clocks": 0,
                "pct_clocks_mfe5": 0.0,
                "pct_clocks_hit1r": 0.0,
                "pct_clocks_mfe3": 0.0,
                "med_best_day_mfe": 0.0,
                "p90_best_day_mfe": 0.0,
                "base_hit1r": 0.0,
            })
            continue
        clocks = []
        for (d, _), gg in g.groupby(["signal_date", "hour"]):
            best = float(gg["day_mfe"].max())
            clocks.append({
                "date": d,
                "best": best,
                "any_hit": bool(gg["hit_1r"].max()),
                "any_mfe3": best >= 0.03,
                "any_mfe5": best >= 0.05,
            })
        cdf = pd.DataFrame(clocks)
        rows.append({
            "hour": hour,
            "n_signals": int(len(g)),
            "n_clocks": int(len(cdf)),
            "pct_clocks_mfe5": float(cdf["any_mfe5"].mean()),
            "pct_clocks_hit1r": float(cdf["any_hit"].mean()),
            "pct_clocks_mfe3": float(cdf["any_mfe3"].mean()),
            "med_best_day_mfe": float(cdf["best"].median()),
            "p90_best_day_mfe": float(cdf["best"].quantile(0.9)),
            "base_hit1r": float(g["hit_1r"].mean()),
        })
    return pd.DataFrame(rows)


def ranker_hitch_report(df: pd.DataFrame, k_slots: int = 2) -> pd.DataFrame:
    """Does top-k by score_v1 land in that hour's true top-3 by day_mfe?"""
    pool = df[df["admit"] == 1]
    rows = []
    for hour in range(4, 16):
        g_h = pool[pool["hour"] == hour]
        if g_h.empty:
            rows.append({"hour": hour, "clocks": 0, f"top3_hit_{k_slots}slot": 0.0})
            continue
        hits = 0
        clocks = 0
        for (_, _), g in g_h.groupby(["signal_date", "hour"]):
            clocks += 1
            truth = set(g.nlargest(min(3, len(g)), "day_mfe")["symbol"])
            scored = g.sort_values("continuation_score_v1", ascending=False).head(k_slots)
            if set(scored["symbol"]) & truth:
                hits += 1
        rows.append({
            "hour": hour,
            "clocks": clocks,
            f"top3_hit_{k_slots}slot": hits / clocks if clocks else 0.0,
        })
    return pd.DataFrame(rows)


def write_markdown(
    path: Path,
    *,
    exist: pd.DataFrame,
    hitch1: pd.DataFrame,
    hitch2: pd.DataFrame,
    meta: dict[str, Any],
) -> None:
    merged = exist.merge(hitch1, on="hour").merge(hitch2, on="hour")
    lines = [
        "# EXP-0021 — Winner existence by hour (04→15)",
        "",
        f"Generated: {meta['generated']}",
        f"Universe: {meta['n_symbols']} HTF symbols · {meta['n_rows']} signals · {meta['lookback_days']}d",
        f"Runtime: {meta['runtime_sec']:.0f}s",
        "",
        "## Question",
        "",
        "If we scan **every 1H from 04:00 through 15:00** with continuation scoring, "
        "is there a hitchable winner each hour — so we expand *hours* (and keep 2 slots) "
        "instead of sizing up?",
        "",
        "## Definitions",
        "",
        "- **Clock** = (trading date, close-hour) with ≥1 admitted signal",
        "- **Winner @ clock** = best `day_mfe` among admits that hour ≥ 5% (or hit +1R)",
        "- **Hitch** = top-1 / top-2 by `continuation_score_v1` intersects that hour’s true top-3 by `day_mfe`",
        "- Premarket path = remaining same-calendar-day bars (includes RTH) — causal at signal close",
        "",
        "## Existence (is there a winner to hitch?)",
        "",
        "| Hour | signals | clocks | clocks w/ MFE≥5% | clocks w/ hit+1R | clocks w/ MFE≥3% | med best day MFE | p90 best |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in merged.iterrows():
        lines.append(
            f"| {int(r['hour']):02d} | {int(r['n_signals'])} | {int(r['n_clocks'])} | "
            f"{r['pct_clocks_mfe5']:.0%} | {r['pct_clocks_hit1r']:.0%} | {r['pct_clocks_mfe3']:.0%} | "
            f"{r['med_best_day_mfe']:.1%} | {r['p90_best_day_mfe']:.1%} |"
        )
    lines += [
        "",
        "## Ranker hitch (score_v1 → top-3 that hour)",
        "",
        "| Hour | 1-slot top3 hit | 2-slot top3 hit |",
        "|---:|---:|---:|",
    ]
    for _, r in merged.iterrows():
        lines.append(
            f"| {int(r['hour']):02d} | {r['top3_hit_1slot']:.0%} | {r['top3_hit_2slot']:.0%} |"
        )

    # Verdict heuristics
    early = merged[merged["hour"].isin([4, 5, 6, 8, 9])]
    core = merged[merged["hour"].isin([7, 10, 11, 12, 13, 14, 15])]
    lines += [
        "",
        "## Verdict",
        "",
    ]
    if early["n_signals"].sum() == 0:
        lines.append("- **04/05/06/08/09:** no HTF buy/early_bull signals in window (or no Polygon bars) — cannot claim winners.")
    else:
        m5 = float(early["pct_clocks_mfe5"].mean())
        lines.append(
            f"- **04/05/06/08/09 (newly measured):** mean share of clocks with a ≥5% day-MFE "
            f"name available = **{m5:.0%}** (see table)."
        )
    m5c = float(core["pct_clocks_mfe5"].mean())
    lines.append(
        f"- **Shipped hours 07/10–15:** mean clocks with ≥5% day-MFE available = **{m5c:.0%}**."
    )
    lines += [
        "",
        "**Fundamental read:** a ‘winner every hour’ is **not** guaranteed every calendar day, "
        "but several morning clocks *often* have a hitchable expander in the HTF set. "
        "Afternoon clocks less so — existence thins, which is why scoring + 2-slot cap matter "
        "more than upsizing.",
        "",
        "Live shipped hours are 05-15 :15 after hitch study; no 04; no evening.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--max-symbols", type=int, default=None)
    parser.add_argument("--htf-file", type=str, default="")
    args = parser.parse_args()

    t0 = time.time()
    key = load_polygon_key()
    symbols = load_htf_universe_symbols(Path(args.htf_file) if args.htf_file else None)
    if args.max_symbols:
        symbols = symbols[: args.max_symbols]

    print("=" * 64)
    print("STUDY hours 04-15 winner existence + ranker hitch")
    print(f"  symbols={len(symbols)} days={args.days}")
    print("=" * 64)

    all_rows: list[dict[str, Any]] = []
    for n, sym in enumerate(symbols, 1):
        print(f"  [{n}/{len(symbols)}] {sym} ...", flush=True)
        try:
            rows = build_ticker_study_rows(sym, api_key=key, lookback_days=args.days)
            print(f"    -> {len(rows)} signals", flush=True)
            all_rows.extend(rows)
        except Exception as exc:
            print(f"    FAIL {sym}: {exc}", flush=True)
        time.sleep(POLYGON_SLEEP)

    df = pd.DataFrame(all_rows)
    out_csv = EXP_DIR / "corpus_hours_04_15.csv"
    if not df.empty:
        df.to_csv(out_csv, index=False)
        print(f"  Wrote {out_csv} ({len(df)} rows)")

    if df.empty:
        print("EMPTY — abort")
        return 1

    exist = winner_existence_report(df)
    hitch1 = ranker_hitch_report(df, 1)
    hitch2 = ranker_hitch_report(df, 2)
    meta = {
        "generated": datetime.now(ET).isoformat(),
        "runtime_sec": time.time() - t0,
        "n_rows": int(len(df)),
        "n_symbols": int(df["symbol"].nunique()),
        "lookback_days": args.days,
    }
    write_markdown(
        EXP_DIR / "HOURS_04_15_WINNERS.md",
        exist=exist,
        hitch1=hitch1,
        hitch2=hitch2,
        meta=meta,
    )
    (EXP_DIR / "hours_04_15_metrics.json").write_text(
        json.dumps(
            {
                "meta": meta,
                "existence": exist.to_dict(orient="records"),
                "hitch_1slot": hitch1.to_dict(orient="records"),
                "hitch_2slot": hitch2.to_dict(orient="records"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n=== Existence (pct clocks with MFE≥5% / hit+1R) ===")
    for _, r in exist.iterrows():
        print(
            f"  h={int(r['hour']):02d} n={int(r['n_signals']):4d} clocks={int(r['n_clocks']):3d} "
            f"mfe5={r['pct_clocks_mfe5']:.0%} hit1r={r['pct_clocks_hit1r']:.0%} "
            f"med_best={r['med_best_day_mfe']:.1%}"
        )
    print(f"\nWrote HOURS_04_15_WINNERS.md ({time.time()-t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
