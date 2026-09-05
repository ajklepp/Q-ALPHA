"""
Enrich HTF corpus admits with causal Polygon news + StockTwits (X optional).

Writes corpus_htf_universe_social.csv and refreshes Study 4 in STUDY_DEEP_EDGE.md.

Usage (from repo root):
  python experiments/EXP-0021/enrich_social_corpus.py
  python experiments/EXP-0021/enrich_social_corpus.py --limit 200
  python experiments/EXP-0021/enrich_social_corpus.py --include-x
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytz

EXP_DIR = Path(__file__).resolve().parent
ROOT = EXP_DIR.parents[1]
CANDIDATES = ROOT / "candidates"
sys.path.insert(0, str(CANDIDATES))
sys.path.insert(0, str(EXP_DIR))

from tsd_scan_pipeline.tsd_social import fetch_social_bundle  # noqa: E402
from tsd_scan_pipeline.universe_tsd import load_polygon_key  # noqa: E402

ET = pytz.timezone("US/Eastern")
IN_PATH = EXP_DIR / "corpus_htf_universe.csv"
OUT_PATH = EXP_DIR / "corpus_htf_universe_social.csv"
CACHE_PATH = EXP_DIR / "social_day_cache.json"
METRICS_PATH = EXP_DIR / "study_deep_edge_metrics.json"
STUDY_MD = EXP_DIR / "STUDY_DEEP_EDGE.md"

SOCIAL_COLS = [
    "social_missing",
    "guidance_cut",
    "print",
    "outlook",
    "news_velocity_24h",
    "news_velocity_72h",
    "news_headline_count_48h",
    "dilution_flag",
    "distress_flag",
    "unresolved",
    "catalyst_type",
    "st_msg_24h",
    "st_bull_ratio",
    "st_ok",
    "x_posts_24h",
    "x_authors_24h",
    "x_engage_24h",
    "x_sent_lex",
    "x_ok",
]


def _as_of_from_row(row: pd.Series) -> datetime:
    """Causal as_of = signal timestamp (no future news)."""
    ts = row.get("signal_ts")
    if pd.isna(ts):
        d = str(row.get("signal_date") or "")[:10]
        return ET.localize(datetime.strptime(d, "%Y-%m-%d").replace(hour=12))
    if isinstance(ts, datetime):
        dt = ts
    else:
        dt = pd.Timestamp(ts).to_pydatetime()
    if dt.tzinfo is None:
        return ET.localize(dt)
    return dt.astimezone(ET)


def enrich(
    df: pd.DataFrame,
    *,
    api_key: str,
    include_x: bool,
    limit: int | None,
    every_n: int,
    cache_path: Path,
) -> pd.DataFrame:
    """Fetch causal Polygon news for unique (symbol, signal_date); merge onto rows.

    StockTwits / X are skipped here (no historical as_of → look-ahead risk).
    Live path still attaches ST+X via tsd_social at scan time.
    """
    work = df.copy()
    if "all_hours_admit" in work.columns:
        admits = work[work["all_hours_admit"] == 1]
    else:
        admits = work

    keys = admits[["symbol", "signal_date"]].drop_duplicates()
    if every_n > 1:
        keys = keys.iloc[::every_n]
    if limit:
        keys = keys.head(limit)

    disk: dict[str, dict[str, Any]] = {}
    if cache_path.exists():
        try:
            disk = json.loads(cache_path.read_text(encoding="utf-8"))
            print(f"Loaded {len(disk)} cached symbol-days from {cache_path.name}")
        except Exception:
            disk = {}

    print(
        f"Fetching news for {len(keys)} unique symbol-days "
        f"(ST/X off for causal corpus; X flag ignored={include_x})…"
    )
    cache: dict[tuple[str, str], dict[str, Any]] = {}
    t0 = time.time()
    for i, (_, kr) in enumerate(keys.iterrows(), 1):
        sym = str(kr["symbol"]).upper()
        day = str(kr["signal_date"])[:10]
        ck = f"{sym}|{day}"
        if ck in disk:
            cache[(sym, day)] = disk[ck]
        else:
            as_of = ET.localize(datetime.strptime(day, "%Y-%m-%d").replace(hour=12))
            sub = admits[
                (admits["symbol"] == sym) & (admits["signal_date"].astype(str).str[:10] == day)
            ]
            if len(sub):
                as_of = _as_of_from_row(sub.iloc[0])
            bundle = fetch_social_bundle(
                sym,
                api_key=api_key,
                as_of=as_of,
                include_x=False,
                include_st=False,
            )
            cache[(sym, day)] = bundle
            disk[ck] = bundle
            if i % 50 == 0:
                cache_path.write_text(json.dumps(disk), encoding="utf-8")

        if i % 25 == 0 or i == 1 or i == len(keys):
            n_news = sum(1 for v in cache.values() if float(v.get("news_velocity_24h") or 0) > 0)
            print(f"  [{i}/{len(keys)}] elapsed={time.time()-t0:.0f}s news>0={n_news}")

    cache_path.write_text(json.dumps(disk), encoding="utf-8")
    print(f"Wrote cache {cache_path} ({len(disk)} keys)")

    # Vectorized merge
    rows = []
    for (sym, day), bundle in cache.items():
        rec = {"symbol": sym, "signal_date_key": day}
        for col in SOCIAL_COLS:
            rec[col] = bundle.get(col)
        rows.append(rec)
    soc = pd.DataFrame(rows)
    work["signal_date_key"] = work["signal_date"].astype(str).str[:10]
    work["symbol"] = work["symbol"].astype(str).str.upper()

    # Drop existing social cols then join
    drop_cols = [c for c in SOCIAL_COLS if c in work.columns]
    if drop_cols:
        work = work.drop(columns=drop_cols)
    work = work.merge(soc, how="left", on=["symbol", "signal_date_key"])
    work = work.drop(columns=["signal_date_key"])

    # Defaults for rows without a fetch
    for col in SOCIAL_COLS:
        if col not in work.columns:
            continue
        if col in ("print", "outlook", "catalyst_type"):
            work[col] = work[col].fillna("unknown" if col != "catalyst_type" else "none")
        elif col in ("st_bull_ratio",):
            work[col] = pd.to_numeric(work[col], errors="coerce").fillna(0.5)
        else:
            work[col] = pd.to_numeric(work[col], errors="coerce").fillna(0)

    # Rows never fetched → social_missing=1
    fetched = set(cache.keys())
    mask_unfetched = ~work.apply(
        lambda r: (str(r["symbol"]).upper(), str(r["signal_date"])[:10]) in fetched,
        axis=1,
    )
    work.loc[mask_unfetched, "social_missing"] = 1

    miss = float(work["social_missing"].mean())
    news_pos = int((work["news_velocity_24h"].fillna(0) > 0).sum())
    print(f"Done. social_missing_mean={miss:.3f} rows_news>0={news_pos}/{len(work)}")
    return work


def study4_from_df(df: pd.DataFrame) -> dict[str, Any]:
    social_miss = float(df["social_missing"].mean()) if "social_missing" in df.columns else 1.0
    news_col = "news_velocity_24h" if "news_velocity_24h" in df.columns else None
    result: dict[str, Any] = {
        "htf_social_missing_rate": social_miss,
        "htf_has_news_column": bool(news_col),
        "verdict": "",
        "htf": None,
        "st": None,
    }
    if news_col and "all_hours_admit" in df.columns:
        sub = df[df["all_hours_admit"] == 1].copy()
        has = sub[news_col].fillna(0) > 0
        result["htf"] = {
            "n_with_news": int(has.sum()),
            "n_without": int((~has).sum()),
            "wr_with": float(sub.loc[has, "hit_1r"].mean()) if has.any() else None,
            "wr_without": float(sub.loc[~has, "hit_1r"].mean()) if (~has).any() else None,
            "exp_with": float(sub.loc[has, "r_multiple"].mean()) if has.any() else None,
            "exp_without": float(sub.loc[~has, "r_multiple"].mean()) if (~has).any() else None,
        }
    if "st_ok" in df.columns and "all_hours_admit" in df.columns:
        sub = df[df["all_hours_admit"] == 1].copy()
        has = sub["st_ok"].fillna(0).astype(int) == 1
        bull = sub["st_bull_ratio"].fillna(0.5) >= 0.55
        result["st"] = {
            "n_st_ok": int(has.sum()),
            "wr_st_ok": float(sub.loc[has, "hit_1r"].mean()) if has.any() else None,
            "wr_st_missing": float(sub.loc[~has, "hit_1r"].mean()) if (~has).any() else None,
            "n_bullish": int((has & bull).sum()),
            "wr_bullish": float(sub.loc[has & bull, "hit_1r"].mean()) if (has & bull).any() else None,
        }

    n_news = (result.get("htf") or {}).get("n_with_news") or 0
    if social_miss > 0.95 and n_news == 0:
        result["verdict"] = "INCONCLUSIVE — social still empty after enrich."
    elif n_news < 80:
        result["verdict"] = (
            f"PARTIAL — news>0 on {n_news} admits; use as soft context only, not hard gates."
        )
    else:
        result["verdict"] = (
            "POWERED — enough news coverage to compare with/without; still soft-rank only."
        )
    return result


def patch_metrics_and_md(s4: dict[str, Any]) -> None:
    metrics: dict[str, Any] = {}
    if METRICS_PATH.exists():
        metrics = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
    metrics["study4"] = s4
    metrics["study4_enriched_at"] = datetime.now(ET).isoformat()
    METRICS_PATH.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    h = s4.get("htf") or {}
    st = s4.get("st") or {}
    block = [
        "",
        "## Study 4 refresh (powered social enrich)",
        "",
        f"- Verdict: **{s4.get('verdict')}**",
        f"- social_missing_rate={s4.get('htf_social_missing_rate'):.3f}",
        (
            f"- Admits news>0={h.get('n_with_news')} without={h.get('n_without')} "
            f"WR {h.get('wr_with')} vs {h.get('wr_without')} "
            f"exp {h.get('exp_with')} vs {h.get('exp_without')}"
        ),
        (
            f"- StockTwits ok={st.get('n_st_ok')} WR {st.get('wr_st_ok')} vs missing {st.get('wr_st_missing')}; "
            f"bullish≥0.55 n={st.get('n_bullish')} WR={st.get('wr_bullish')}"
        ),
        "- Live path: `tsd_social.py` attached on 1H rank (never hard-vetoes).",
        "",
    ]
    if STUDY_MD.exists():
        text = STUDY_MD.read_text(encoding="utf-8")
        marker = "## Study 4 refresh (powered social enrich)"
        if marker in text:
            pre = text.split(marker)[0].rstrip()
            text = pre + "\n" + "\n".join(block)
        else:
            text = text.rstrip() + "\n" + "\n".join(block)
        STUDY_MD.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Enrich HTF corpus with news/social")
    parser.add_argument("--limit", type=int, default=None, help="Max unique symbol-days")
    parser.add_argument("--every-n", type=int, default=1, help="Sample every Nth unique day")
    parser.add_argument("--include-x", action="store_true")
    parser.add_argument("--input", type=Path, default=IN_PATH)
    parser.add_argument("--output", type=Path, default=OUT_PATH)
    args = parser.parse_args()

    # Load .env if present
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    if not args.input.exists():
        print(f"Missing {args.input}")
        return 1

    api_key = load_polygon_key()
    df = pd.read_csv(args.input)
    print(f"Loaded {len(df)} rows from {args.input.name}")
    enriched = enrich(
        df,
        api_key=api_key,
        include_x=bool(args.include_x),
        limit=args.limit,
        every_n=max(1, int(args.every_n)),
        cache_path=CACHE_PATH,
    )
    enriched.to_csv(args.output, index=False)
    print(f"Wrote {args.output}")

    s4 = study4_from_df(enriched)
    print(json.dumps(s4, indent=2, default=str))
    patch_metrics_and_md(s4)
    print(f"Updated {METRICS_PATH.name} + {STUDY_MD.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
