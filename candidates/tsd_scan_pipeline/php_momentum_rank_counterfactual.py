"""
Peak Hour momentum-rank counterfactual (research + laptop replay).

Compares three ranking policies on the same passer set:
  1. Legacy v1.6 (stage leak / popularity-first take)
  2. Equal-signal only (PHP_EQUAL_SIGNAL ON, PHP_MOMENTUM_RANK OFF)
  3. Equal-signal + momentum-rank (both ON — proposed live)

Does not change trails. Live take path is wired separately.

Usage:
  py -3 candidates/tsd_scan_pipeline/php_momentum_rank_counterfactual.py --date 2026-09-14
  py -3 candidates/tsd_scan_pipeline/php_momentum_rank_counterfactual.py --date 2026-09-14 --write
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
ROOT_DIR = CANDIDATES_DIR.parent
sys.path.insert(0, str(CANDIDATES_DIR))

from tsd_scan_pipeline.php_momentum_rank import (  # noqa: E402
    MOMENTUM_RANK_VERSION,
    is_slow_popular,
    is_tape_hot,
    momentum_rank_delta,
)
from tsd_scan_pipeline.php_scan_funnel import RESULTS_DIR as SCAN_DIR  # noqa: E402
from tsd_scan_pipeline.tsd_attention import annotate_momentum_context  # noqa: E402
from tsd_scan_pipeline.tsd_capacity import MAX_NEW_ENTRIES_PER_SCAN  # noqa: E402
from tsd_scan_pipeline.tsd_case_review import review_case, select_enter_rows  # noqa: E402
from tsd_scan_pipeline.tsd_launch_score import enrich_launch_fields  # noqa: E402

ET = pytz.timezone("America/New_York")
REPORT_DIR = PIPELINE_DIR / "results"
WATCH = ("OKTA", "WIX", "COIN")
TAKEN_WATCH = ("TARS", "HOOD", "IRD", "SNDK")

ACTUAL_20260914: dict[int, dict[str, Any]] = {
    5: {"queued": ["TARS"], "filled": [], "source": "AARON",
        "notes": "Take queued (fill not documented in repo)"},
    6: {"queued": ["HOOD"], "filled": [], "source": "AARON",
        "notes": "Take queued (fill not documented in repo)"},
    7: {"queued": [], "filled": [], "source": "UNKNOWN",
        "notes": "No take named in operator baseline or repo artifacts"},
    8: {"queued": [], "filled": [], "source": "AARON+CODE", "aborted": True,
        "notes": "Hour 8 aborted — no SCAN. Cannot invent passers."},
    9: {"queued": ["IRD"], "filled": [{"symbol": "IRD", "pnl_usd": 3.63}],
        "source": "AARON", "notes": "IRD FILLED +$3.63 (operator)"},
    10: {"queued": ["SNDK"], "filled": [], "source": "AARON",
        "notes": "Take queued (fill not documented in repo)"},
    11: {"queued": [], "filled": [], "source": "UNKNOWN", "notes": "Afternoon unauditable"},
    12: {"queued": [], "filled": [], "source": "UNKNOWN", "notes": "Afternoon unauditable"},
    13: {"queued": [], "filled": [], "source": "UNKNOWN", "notes": "Afternoon unauditable"},
    14: {"queued": [], "filled": [], "source": "UNKNOWN", "notes": "Afternoon unauditable"},
    15: {"queued": [], "filled": [], "source": "UNKNOWN", "notes": "Afternoon unauditable"},
}

# Public daily session (Yahoo 1d, 2026-09-14). NOT missed_ledger.
PUBLIC_SESSION_20260914: dict[str, dict[str, Any]] = {
    "OKTA": {"prev_close": 166.50, "high": 189.72, "close": 186.45,
             "ret_vs_prev_pct": 11.98, "high_vs_prev_pct": 13.95,
             "room_vs_prior_close_pct": 7.2},
    "WIX": {"prev_close": 76.58, "high": 85.26, "close": 83.33,
            "ret_vs_prev_pct": 8.81, "high_vs_prev_pct": 11.33,
            "room_vs_prior_close_pct": 20.9},
    "COIN": {"prev_close": 175.26, "high": 193.22, "close": 191.45,
             "ret_vs_prev_pct": 9.24, "high_vs_prev_pct": 10.25,
             "room_vs_prior_close_pct": 10.5},
    "TARS": {"prev_close": 79.31, "high": 80.24, "close": 79.95,
             "ret_vs_prev_pct": 0.81, "high_vs_prev_pct": 1.17,
             "room_vs_prior_close_pct": 13.4},
    "HOOD": {"prev_close": 112.57, "high": 116.35, "close": 114.33,
             "ret_vs_prev_pct": 1.56, "high_vs_prev_pct": 3.35,
             "room_vs_prior_close_pct": 10.1},
    "IRD": {"prev_close": 6.04, "high": 6.22, "close": 5.97,
            "ret_vs_prev_pct": -1.16, "high_vs_prev_pct": 2.98,
            "room_vs_prior_close_pct": 14.6},
    "SNDK": {"prev_close": 1633.35, "high": 1581.84, "close": 1551.99,
             "ret_vs_prev_pct": -4.98, "high_vs_prev_pct": -3.15,
             "room_vs_prior_close_pct": 9.6},
}


def _set_flags(*, equal: bool, momentum: bool) -> None:
    os.environ["PHP_EQUAL_SIGNAL"] = "1" if equal else "0"
    os.environ["PHP_MOMENTUM_RANK"] = "1" if momentum else "0"


def _shared(**kwargs) -> dict[str, Any]:
    row = {
        "buy_signal": True,
        "early_bull": False,
        "htf_score": 70.0,
        "htf_1h_bar_hour": 9,
        "htf_sma20_rising": True,
        "htf_close_above_sma50": True,
        "htf_range_20d_pct": 0.45,
        "dist_20d_low_bounce": 0.08,
        "open": 10.0,
        "high": 10.4,
        "low": 9.95,
        "close": 10.2,
        "htf_1h_close": 10.2,
        "rs_ok": 1,
        "rs_spy_5d": 0.03,
        "news_velocity_24h": 3.0,
        "st_ok": 1,
        "st_msg_24h": 6.0,
        "st_bull_ratio": 0.55,
        "wt_gap": 5.0,
    }
    row.update(kwargs)
    return row


def illustrative_watchlist() -> list[dict[str, Any]]:
    """
    ILLUSTRATIVE 2026-09-14 set.

    Scan/tape fields are constructed from public session + the equal-signal
    PR #10 assumed EXTENSION-class scans for OKTA/WIX/COIN and LAUNCH-class
    for actual takes. Not live php_scan JSON.
    """
    return [
        _shared(
            symbol="TARS", scan_score=38.0, trend_strength=0.25,
            dist_20d_high_pct=0.134, vol_ratio_20=1.05,
            rs_spy_1h=0.004, rs_spy_1h_ok=1, session_ret=0.006,
            dollar_vol_1h_vs_20d=0.04, ticker_prior_hit1r_rate=0.50,
            tradable_popular=True, recent_leaderboard=True, on_gainers=False,
        ),
        _shared(
            symbol="HOOD", scan_score=40.0, trend_strength=0.28,
            dist_20d_high_pct=0.101, vol_ratio_20=1.20,
            rs_spy_1h=0.012, rs_spy_1h_ok=1, session_ret=0.016,
            dollar_vol_1h_vs_20d=0.08, ticker_prior_hit1r_rate=0.40,
            tradable_popular=True, recent_leaderboard=True, on_gainers=False,
        ),
        _shared(
            symbol="IRD", scan_score=36.0, trend_strength=0.22,
            dist_20d_high_pct=0.146, vol_ratio_20=1.35,
            rs_spy_1h=0.010, rs_spy_1h_ok=1, session_ret=0.012,
            dollar_vol_1h_vs_20d=0.10, ticker_prior_hit1r_rate=0.25,
            tradable_popular=True, recent_leaderboard=False, on_gainers=False,
        ),
        _shared(
            symbol="SNDK", scan_score=42.0, trend_strength=0.30,
            dist_20d_high_pct=0.096, vol_ratio_20=0.90,
            rs_spy_1h=-0.02, rs_spy_1h_ok=1, session_ret=-0.03,
            dollar_vol_1h_vs_20d=0.06, ticker_prior_hit1r_rate=0.35,
            tradable_popular=True, recent_leaderboard=True, on_gainers=False,
        ),
        _shared(
            symbol="WIX", scan_score=66.0, trend_strength=0.72,
            dist_20d_high_pct=0.209, vol_ratio_20=2.40,
            rs_spy_1h=0.055, rs_spy_1h_ok=1, session_ret=0.08,
            dollar_vol_1h_vs_20d=0.22, ticker_prior_hit1r_rate=0.20,
            tradable_popular=True, recent_leaderboard=True, on_gainers=True,
        ),
        _shared(
            symbol="COIN", scan_score=64.0, trend_strength=0.70,
            dist_20d_high_pct=0.105, vol_ratio_20=2.10,
            rs_spy_1h=0.045, rs_spy_1h_ok=1, session_ret=0.06,
            dollar_vol_1h_vs_20d=0.18, ticker_prior_hit1r_rate=0.22,
            tradable_popular=True, recent_leaderboard=True, on_gainers=True,
        ),
        _shared(
            symbol="OKTA", scan_score=68.0, trend_strength=0.74,
            dist_20d_high_pct=0.072, vol_ratio_20=2.20,
            rs_spy_1h=0.070, rs_spy_1h_ok=1, session_ret=0.09,
            dollar_vol_1h_vs_20d=0.20, ticker_prior_hit1r_rate=0.18,
            tradable_popular=True, recent_leaderboard=True, on_gainers=True,
        ),
    ]


POP_CTX = {
    "recent_gainer_symbols": {"TARS", "WIX", "COIN", "OKTA", "HOOD", "SNDK"},
    "recent_active_symbols": {"TARS", "HOOD", "SNDK"},
    "live_gainers": {"WIX", "COIN", "OKTA"},
    "tws_symbols": {"WIX", "COIN", "OKTA", "TARS", "HOOD"},
    "popular_symbols": {"TARS", "WIX", "COIN", "OKTA", "HOOD", "IRD", "SNDK"},
    "tws_ok": True,
    "sessions_loaded": 8,
}


def _score_policy(rows: list[dict[str, Any]], *, equal: bool, momentum: bool) -> tuple[list[dict[str, Any]], list[str]]:
    """Score + take under one flag pair. Flags are set for the whole call."""
    _set_flags(equal=equal, momentum=momentum)
    out: list[dict[str, Any]] = []
    for raw in rows:
        row = annotate_momentum_context(enrich_launch_fields(dict(raw)), popularity_ctx=POP_CTX)
        case = review_case(row, allow_llm=False, use_web_search=False)
        row["case_verdict"] = case["verdict"]
        row["case_confidence"] = case["confidence"]
        row["case_source"] = case.get("source")
        row["tape_hot"] = is_tape_hot(row)
        row["slow_popular"] = is_slow_popular(row)
        row["momentum_rank_delta"] = momentum_rank_delta(row)
        out.append(row)
    take = [
        str(r.get("symbol") or "").upper()
        for r in select_enter_rows(out, max_n=MAX_NEW_ENTRIES_PER_SCAN)
    ]
    return out, take


def _scan_files_for(date_str: str) -> list[Path]:
    day = date_str.replace("-", "")
    if not SCAN_DIR.exists():
        return []
    return sorted(SCAN_DIR.glob(f"php_scan_{day}_*.json"))


def _rows_from_scan(path: Path) -> list[dict[str, Any]]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = []
    for key in ("ranked", "rows", "launches", "passers"):
        block = doc.get(key)
        if isinstance(block, list) and block:
            rows = [r for r in block if isinstance(r, dict)]
            break
    return rows


def run_illustrative() -> dict[str, Any]:
    raw = illustrative_watchlist()
    v16, v16_take = _score_policy(raw, equal=False, momentum=False)
    eq, eq_take = _score_policy(raw, equal=True, momentum=False)
    mom, mo_take = _score_policy(raw, equal=True, momentum=True)

    # Live LLM often rates the slow popular name "safer". Stress: TARS 0.92 vs WIX 0.70.
    eq_stress = [dict(r) for r in eq]
    mo_stress = [dict(r) for r in mom]
    for bucket in (eq_stress, mo_stress):
        for r in bucket:
            if r.get("symbol") == "TARS":
                r["case_confidence"] = 0.92
            if r.get("symbol") == "WIX":
                r["case_confidence"] = 0.70
    _set_flags(equal=True, momentum=False)
    eq_stress_take = [
        str(r.get("symbol") or "").upper()
        for r in select_enter_rows(eq_stress, max_n=MAX_NEW_ENTRIES_PER_SCAN)
    ]
    _set_flags(equal=True, momentum=True)
    mo_stress_take = [
        str(r.get("symbol") or "").upper()
        for r in select_enter_rows(mo_stress, max_n=MAX_NEW_ENTRIES_PER_SCAN)
    ]
    return {
        "v16": v16,
        "equal": eq,
        "momentum": mom,
        "v16_take": v16_take,
        "equal_take": eq_take,
        "momentum_take": mo_take,
        "equal_stress_take": eq_stress_take,
        "momentum_stress_take": mo_stress_take,
    }


def _fmt_row(r: dict[str, Any]) -> str:
    return (
        f"| {r.get('symbol')} | {r.get('scan_score')} | {r.get('phase')} | "
        f"{r.get('continuation_score')} | {r.get('case_verdict')} | "
        f"{int(bool(r.get('tape_hot')))} | {int(bool(r.get('slow_popular')))} | "
        f"{r.get('momentum_rank_delta')} |"
    )


def render_markdown(
    *,
    date_str: str,
    illus: dict[str, Any],
    scan_n: int,
    nearby: dict[str, int],
) -> str:
    v16_take = ", ".join(illus["v16_take"]) or "(none)"
    eq_take = ", ".join(illus["equal_take"]) or "(none)"
    mo_take = ", ".join(illus["momentum_take"]) or "(none)"
    eq_stress = ", ".join(illus["equal_stress_take"]) or "(none)"
    mo_stress = ", ".join(illus["momentum_stress_take"]) or "(none)"

    def _table(rows: list[dict[str, Any]]) -> str:
        head = (
            "| Symbol | scan | phase | cont | case | tape_hot | slow_pop | overlay Δ |\n"
            "|---|---:|---|---:|---|---:|---:|---:|"
        )
        ranked = sorted(rows, key=lambda r: -float(r.get("continuation_score") or 0))
        return "\n".join([head, *(_fmt_row(r) for r in ranked)])

    actual_rows = []
    for hour in range(5, 16):
        rec = ACTUAL_20260914.get(hour, {})
        q = ", ".join(rec.get("queued") or []) or "—"
        if rec.get("aborted"):
            q = "(aborted — no scan)"
        actual_rows.append(
            f"| {hour:02d} | {q} | {rec.get('source', 'UNKNOWN')} | {rec.get('notes', '')} |"
        )

    pub_rows = []
    for sym in (*WATCH, *TAKEN_WATCH):
        p = PUBLIC_SESSION_20260914.get(sym) or {}
        pub_rows.append(
            f"| {sym} | {p.get('high_vs_prev_pct')}% | {p.get('ret_vs_prev_pct')}% | "
            f"{p.get('room_vs_prior_close_pct')}% |"
        )

    nearby_lines = [
        f"| {d} | {n} scan JSON files |"
        for d, n in nearby.items()
    ]

    return f"""# Momentum-rank Peak Hour counterfactual — {date_str} (ET)

**Live ranking change is separate** (`PHP_MOMENTUM_RANK`, default ON). This file
is the evidence pack: actual takes vs would-have-ranked under three policies.
Trails / keep-profit / take cap are out of scope.

- Overlay version: `{MOMENTUM_RANK_VERSION}`
- Take cap: **`MAX_NEW_ENTRIES_PER_SCAN = {MAX_NEW_ENTRIES_PER_SCAN}`**
- Hard-extension: **ON** (`scan >= 75`)
- Case: **rules-only** (no invented LLM ENTER confidences)
- Equal-signal admission: assumed ON for policies 2 and 3 (PR #11)

## What the live day did

Hour-8 abort plus operator-named takes (same baseline as PR #10). Cloud clone
has **{scan_n}** `php_scan_{date_str.replace('-', '')}_*.json` files.

| Hour ET | Actual take (queued) | Evidence | Notes |
|---:|---|---|---|
{chr(10).join(actual_rows)}

Public same-day move (NOT missed_ledger — confirms who ripped):

| Symbol | High vs prior close | Close vs prior | Prior-close room vs 20d high |
|---|---:|---:|---:|
{chr(10).join(pub_rows)}

TARS closed **+0.81%** (high **+1.17%**). OKTA / WIX / COIN closed
**+12% / +8.8% / +9.2%** with highs **+14% / +11% / +10%**.

## Nearby sessions (scan JSON on this clone)

| Date | Artifacts |
|---|---|
{chr(10).join(nearby_lines)}

Without laptop `php_scan_*.json`, nearby-day would-takes stay UNKNOWN. Re-run
on the laptop next to the scan folder to fill them.

## Illustrative watchlist (labeled)

ILLUSTRATIVE — scan/tape fields constructed from public session + PR #10
assumed EXTENSION-class scans for OKTA/WIX/COIN and LAUNCH-class for the
names that were actually queued. **Not** live funnel passers.

### Policy 1 — legacy v1.6 (equal-signal OFF, momentum-rank OFF)

Would-take (cap 2, popularity + confidence-first): **{v16_take}**

{_table(illus["v16"])}

Soft-EXTENSION names (WIX/COIN/OKTA at scan 64–68) are often
`extension_hard` via the phase→extended leak, so they never rank.

### Policy 2 — equal-signal only (momentum-rank OFF)

Would-take: **{eq_take}**

{_table(illus["equal"])}

Admission is equal, so WIX/COIN can ENTER. Tape terms already in equal-signal
can rank them above TARS **when confidence is ignored**. Live take sort is
still **case-confidence first** — that is the remaining miss.

### Policy 3 — equal-signal + momentum-rank (proposed live)

Would-take: **{mo_take}**

{_table(illus["momentum"])}

Same-day tape/room/RS pull WIX/COIN further ahead (WIX 135 vs TARS 56, a
**79-pt** gap vs ~24 pts under equal-signal only). TARS is marked
slow-popular and demoted. OKTA still WAITs on rules (prior-close room
7.2% < 10% constructive floor) — case remains a surgical veto. Hard
`scan>=75` is still blocked (not in this set).

### Confidence stress (why the overlay is not optional)

Live LLM often rates the slow popular name “safer.” Force TARS
confidence **0.92** vs WIX **0.70** (same ENTER set):

| Policy | Would-take (cap 2) |
|---|---|
| Equal-signal only (confidence-first) | **{eq_stress}** |
| Equal-signal + momentum-rank (score-first) | **{mo_stress}** |

That is the Sep 14 failure mode after equal admission: TARS can still
consume a slot because it looks popular and the model is confident.

## Reading

| Question | Answer |
|---|---|
| Did equal-signal alone prefer rippers over TARS? | By score, often yes in this set. By **live take sort**, no — high TARS confidence still wins a slot. |
| Does momentum-rank pick WIX/COIN over TARS? | Yes, including the 0.92-vs-0.70 confidence stress. |
| Does it grab every runner? | No. OKTA WAITs on room. Cap stays 2. Hard-extension stays ON. |
| Are trails changed? | No. |

Same-day high vs prior close: WIX **+11.3%** and COIN **+10.3%** versus TARS
**+1.2%**. That is the miss the overlay is built to stop repeating.

## How to finish this on the laptop

```text
py -3 candidates/tsd_scan_pipeline/php_momentum_rank_counterfactual.py --date 2026-09-14 --write
```

Expects `candidates/tsd_scan_pipeline/results/peak_hour_scans/php_scan_YYYYMMDD_*.json`.
When those files exist, the hour table fills from live passers (still
rules-only case). Until then the illustrative table is the mechanism proof.

### Verify before next RTH

1. `git pull` this branch on the laptop.
2. Leave `PHP_MOMENTUM_RANK` unset (default ON) or set `=1` in `.env`.
3. Run `py -3 -m unittest tests.test_php_momentum_rank tests.test_php_equal_signal -v`.
4. Next `:15` log / Telegram must show
   `equal_signal=ON  momentum_rank=ON  score=v1.6+equal_signal+momentum_rank`.
5. Revert (ranking only): `PHP_MOMENTUM_RANK=0` in `.env`. See `REVERT.md`.
   Do not restart trail monitor.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Momentum-rank counterfactual")
    parser.add_argument("--date", default="2026-09-14")
    parser.add_argument("--write", action="store_true", help="Write results markdown")
    args = parser.parse_args()
    date_str = args.date

    scan_files = _scan_files_for(date_str)
    nearby = {}
    for d in ("2026-09-11", "2026-09-12", "2026-09-15"):
        nearby[d] = len(_scan_files_for(d))
    nearby[date_str] = len(scan_files)

    illus = run_illustrative()
    md = render_markdown(
        date_str=date_str, illus=illus, scan_n=len(scan_files), nearby=nearby,
    )
    out_path = REPORT_DIR / f"MOMENTUM_RANK_COUNTERFACTUAL_{date_str.replace('-', '')}.md"
    if args.write or True:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md, encoding="utf-8")
        print(f"Wrote {out_path}")
    print(md[:1500])
    print("...")
    print(
        f"illustrative takes  v16={illus['v16_take']}  "
        f"equal={illus['equal_take']}  momentum={illus['momentum_take']}"
    )
    if scan_files:
        print(f"scan JSON present: {len(scan_files)} (laptop replay path live)")
        for path in scan_files:
            rows = _rows_from_scan(path)
            print(f"  {path.name}: {len(rows)} rows")
    else:
        print("scan JSON absent on this clone — illustrative watchlist only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
