"""Hourly winner existence + 1-slot vs 2-slot top-3 capture on EXP-0021 corpus."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "experiments" / "EXP-0021" / "corpus_htf_universe.csv"
PEAK = {7, 11, 12, 13}


def soft_admit(r: pd.Series) -> bool:
    """Admit through hour 15 for this study (3pm bar close)."""
    h = int(r["hour"])
    if h >= 16:
        return False
    if h < 10 and h not in PEAK:
        return False
    scan = float(r["scan_score"])
    if scan >= 75 or str(r["bar_state"]) == "extended":
        return False
    launch = float(r["launch_score"]) if pd.notna(r["launch_score"]) else 0.0
    if launch < 40 and scan > 55:
        return False
    return bool(r["buy_signal"]) or bool(r["early_bull"])


def main() -> None:
    c = pd.read_csv(CSV)
    print("=== Winners present by hour (all HTF signals) ===")
    for h in range(7, 17):
        g = c[c["hour"] == h]
        if g.empty:
            continue
        nh = int(g["hit_1r"].sum())
        n5 = int((g["day_mfe"] >= 0.05).sum())
        print(
            f"h={h:02d} n={len(g):4d} hit1r={nh:4d} ({nh/len(g):5.1%}) "
            f"mfe>=5%={n5:4d} ({n5/len(g):5.1%}) best_day={g['day_mfe'].max():6.1%}"
        )

    pool = c[c.apply(soft_admit, axis=1)].copy()
    print(f"\nadmit-thru-15 pool: {len(pool)} rows")

    print("\n=== Ranker slot(s) intersect hour top-3 by day_mfe ===")
    for k_slots in (1, 2):
        rows = []
        for (d, h), g in pool.groupby(["signal_date", "hour"]):
            truth = set(g.nlargest(min(3, len(g)), "day_mfe")["symbol"])
            scored = g.sort_values("continuation_score_v1", ascending=False).head(k_slots)
            caught = len(set(scored["symbol"]) & truth) > 0
            rows.append(
                {
                    "hour": int(h),
                    "caught": caught,
                    "best_mfe": float(g["day_mfe"].max()),
                    "picked_mfe": float(scored["day_mfe"].max()),
                    "n": len(g),
                }
            )
        det = pd.DataFrame(rows)
        rate = det["caught"].mean()
        print(f"\n--- {k_slots}-slot --- top3-hit={det['caught'].sum()}/{len(det)} ({rate:.1%})")
        by = det.groupby("hour").agg(
            clocks=("caught", "size"),
            top3_hit=("caught", "mean"),
            med_best=("best_mfe", "median"),
            med_picked=("picked_mfe", "median"),
        )
        print(by.to_string(float_format=lambda x: f"{x:.3f}"))

    print("\n=== 1-slot: truth rank of chosen name (1=best day_mfe that hour) ===")
    ranks = []
    for (_, h), g in pool.groupby(["signal_date", "hour"]):
        g2 = g.sort_values("day_mfe", ascending=False).reset_index(drop=True)
        g2["truth_rank"] = np.arange(1, len(g2) + 1)
        pick_sym = g.sort_values("continuation_score_v1", ascending=False).iloc[0]["symbol"]
        tr = int(g2.loc[g2["symbol"] == pick_sym, "truth_rank"].iloc[0])
        ranks.append({"hour": int(h), "truth_rank": tr, "n": len(g)})
    rdf = pd.DataFrame(ranks)
    print(
        f"overall top1={(rdf.truth_rank == 1).mean():.1%}  "
        f"top3={(rdf.truth_rank <= 3).mean():.1%}  "
        f"top5={(rdf.truth_rank <= 5).mean():.1%}  "
        f"median_rank={rdf.truth_rank.median():.1f}"
    )
    byh = rdf.groupby("hour").apply(
        lambda x: pd.Series(
            {
                "clocks": len(x),
                "top1": (x.truth_rank == 1).mean(),
                "top3": (x.truth_rank <= 3).mean(),
                "med_rank": x.truth_rank.median(),
            }
        ),
        include_groups=False,
    )
    print(byh.to_string(float_format=lambda x: f"{x:.3f}"))

    # Gate for 1-slot: need top3 consistently — propose threshold
    top3 = (rdf.truth_rank <= 3).mean()
    print(
        f"\n1-slot gate: top3-in-hour rate={top3:.1%} "
        f"({'PASS candidate' if top3 >= 0.50 else 'KEEP 2-slot — not superb yet'}; "
        f"target ~50%+ for superb)"
    )


if __name__ == "__main__":
    main()
