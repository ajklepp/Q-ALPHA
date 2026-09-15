"""
RESEARCH ONLY — equal-signal Peak Hour counterfactual for one session.

Rebuilds attention / take from 1H signal passers with soft stage demotion
removed. Does not change live scheduler, ranker, or case.

Usage (laptop, with scan JSON):
  py -3 candidates/tsd_scan_pipeline/php_equal_signal_counterfactual.py --date 2026-09-14

Cloud / no artifacts still writes the 2026-09-14 report from the documented
actual baseline + mechanism demo + public session ran-up (not missed_ledger).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytz

PIPELINE_DIR = Path(__file__).resolve().parent
CANDIDATES_DIR = PIPELINE_DIR.parent
ROOT_DIR = CANDIDATES_DIR.parent
sys.path.insert(0, str(CANDIDATES_DIR))

from tsd_scan_pipeline.php_equal_signal import (  # noqa: E402
    EQUAL_SIGNAL_VERSION,
    LIVE_PHASE_EXTENSION_PENALTY,
    LIVE_SCAN_OVER_MAX_PENALTY,
    MAX_NEW_ENTRIES_PER_SCAN,
    counterfactual_hour,
    live_vs_equal_pair,
)
from tsd_scan_pipeline.php_scan_funnel import RESULTS_DIR as SCAN_DIR  # noqa: E402
from tsd_scan_pipeline.tsd_launch_score import EXTENSION_SCAN_AUTO  # noqa: E402

ET = pytz.timezone("America/New_York")
REPORT_DIR = PIPELINE_DIR / "results"
WATCH = ("OKTA", "WIX", "COIN")
TAKEN_WATCH = ("TARS", "HOOD", "IRD", "SNDK")

# Operator-supplied 2026-09-14 actuals (not in this cloud clone's gitignored
# php_scan JSON). Ops audit confirms hour-8 abort; does not list these takes.
ACTUAL_20260914: dict[int, dict[str, Any]] = {
    5: {
        "queued": ["TARS"],
        "filled": [],
        "source": "AARON",
        "notes": "Take queued (fill not documented in repo)",
    },
    6: {
        "queued": ["HOOD"],
        "filled": [],
        "source": "AARON",
        "notes": "Take queued (fill not documented in repo)",
    },
    7: {
        "queued": [],
        "filled": [],
        "source": "UNKNOWN",
        "notes": "No take named in operator baseline or repo artifacts",
    },
    8: {
        "queued": [],
        "filled": [],
        "source": "AARON+CODE",
        "aborted": True,
        "notes": (
            "Hour 8 aborted — DUE 08:15, TICK END exit=-1 at 08:23:45, no SCAN. "
            "Counterfactual cannot invent passers without a scan or bar replay."
        ),
    },
    9: {
        "queued": ["IRD"],
        "filled": [{"symbol": "IRD", "pnl_usd": 3.63, "status": "FILLED"}],
        "source": "AARON",
        "notes": "IRD FILLED +$3.63 (operator). Repo ops audit did not independently see IRD.",
    },
    10: {
        "queued": ["SNDK"],
        "filled": [],
        "source": "AARON",
        "notes": "Take queued (fill not documented in repo)",
    },
    11: {"queued": [], "filled": [], "source": "UNKNOWN", "notes": "Afternoon unauditable from repo"},
    12: {"queued": [], "filled": [], "source": "UNKNOWN", "notes": "Afternoon unauditable from repo"},
    13: {"queued": [], "filled": [], "source": "UNKNOWN", "notes": "Afternoon unauditable from repo"},
    14: {"queued": [], "filled": [], "source": "UNKNOWN", "notes": "Afternoon unauditable from repo"},
    15: {"queued": [], "filled": [], "source": "UNKNOWN", "notes": "Afternoon unauditable from repo"},
}

# Public daily session (Yahoo chart 1d, 2026-09-14). NOT missed_ledger.
# room_vs_prior_close = (20d high through 2026-09-11 − 09-11 close) / 20d high.
PUBLIC_SESSION_20260914: dict[str, dict[str, Any]] = {
    "OKTA": {
        "prev_close": 166.50, "open": 172.80, "high": 189.72, "close": 186.45,
        "ret_vs_prev_pct": 11.98, "high_vs_prev_pct": 13.95,
        "room_vs_prior_close_pct": 7.2, "source": "yahoo_1d",
    },
    "WIX": {
        "prev_close": 76.58, "open": 77.80, "high": 85.26, "close": 83.33,
        "ret_vs_prev_pct": 8.81, "high_vs_prev_pct": 11.33,
        "room_vs_prior_close_pct": 20.9, "source": "yahoo_1d",
    },
    "COIN": {
        "prev_close": 175.26, "open": 180.81, "high": 193.22, "close": 191.45,
        "ret_vs_prev_pct": 9.24, "high_vs_prev_pct": 10.25,
        "room_vs_prior_close_pct": 10.5, "source": "yahoo_1d",
    },
    "TARS": {
        "prev_close": 79.31, "open": 79.69, "high": 80.24, "close": 79.95,
        "ret_vs_prev_pct": 0.81, "high_vs_prev_pct": 1.17,
        "room_vs_prior_close_pct": 13.4, "source": "yahoo_1d",
    },
    "HOOD": {
        "prev_close": 112.57, "open": 112.26, "high": 116.35, "close": 114.33,
        "ret_vs_prev_pct": 1.56, "high_vs_prev_pct": 3.35,
        "room_vs_prior_close_pct": 10.1, "source": "yahoo_1d",
    },
    "IRD": {
        "prev_close": 6.04, "open": 6.02, "high": 6.22, "close": 5.97,
        "ret_vs_prev_pct": -1.16, "high_vs_prev_pct": 2.98,
        "room_vs_prior_close_pct": 14.6, "source": "yahoo_1d",
    },
    "SNDK": {
        "prev_close": 1633.35, "open": 1521.53, "high": 1581.84, "close": 1551.99,
        "ret_vs_prev_pct": -4.98, "high_vs_prev_pct": -3.15,
        "room_vs_prior_close_pct": 9.6, "source": "yahoo_1d",
    },
}


def _mechanism_rows() -> list[dict[str, Any]]:
    """
    Identical other-factors pair: LAUNCH vs soft-EXTENSION.

    Shared: hour 9, yellow-ish green OHLC, room 12%, vol 1.6, HTF 70,
    popular/momentum-ready fields, buy_signal.
    """
    shared = {
        "buy_signal": True,
        "early_bull": False,
        "htf_score": 70.0,
        "htf_1h_bar_hour": 9,
        "htf_sma20_rising": True,
        "htf_range_20d_pct": 0.45,
        "dist_20d_high_pct": 0.12,
        "dist_20d_low_bounce": 0.08,
        "vol_ratio_20": 1.6,
        "open": 10.0,
        "high": 10.6,
        "low": 9.95,
        "close": 10.25,  # weak green → yellow
        "rs_ok": 1,
        "rs_spy_5d": 0.04,
        "news_velocity_24h": 4.0,
        "st_ok": 1,
        "st_msg_24h": 8.0,
        "st_bull_ratio": 0.6,
    }
    launch = {
        **shared,
        "symbol": "LAUNCHY",
        "scan_score": 35.0,
        "trend_strength": 0.20,
    }
    ext = {
        **shared,
        "symbol": "EXTENDY",
        "scan_score": 68.0,
        "trend_strength": 0.75,
    }
    hard = {
        **shared,
        "symbol": "HARDX",
        "scan_score": 80.0,
        "trend_strength": 0.80,
        "open": 10.0,
        "high": 11.2,
        "low": 9.9,
        "close": 11.0,
    }
    return [launch, ext, hard]


def run_mechanism_demo() -> dict[str, Any]:
    """Prove live demotes EXTENSION; equal-signal admits and ranks on other factors."""
    launch, ext, hard = _mechanism_rows()
    pairs = {
        "launch": live_vs_equal_pair(launch),
        "soft_extension": live_vs_equal_pair(ext),
        "hard_extension": live_vs_equal_pair(hard),
    }
    pop_ctx = {
        "recent_gainer_symbols": {"LAUNCHY", "EXTENDY", "HARDX"},
        "recent_active_symbols": {"LAUNCHY", "EXTENDY", "HARDX"},
        "live_gainers": {"EXTENDY"},
        "tws_symbols": set(),
        "popular_symbols": {"LAUNCHY", "EXTENDY", "HARDX"},
        "tws_ok": False,
        "sessions_loaded": 5,
    }
    primary = counterfactual_hour(
        [launch, ext, hard],
        popularity_ctx=pop_ctx,
        keep_hard_extension=True,
    )
    sensitivity = counterfactual_hour(
        [launch, ext, hard],
        popularity_ctx=pop_ctx,
        keep_hard_extension=False,
    )
    return {
        "pairs": pairs,
        "primary_would_take": primary["would_take_symbols"],
        "primary_attention": [
            str(r.get("symbol")) for r in primary["attention"]
        ],
        "primary_ranked": [
            str(r.get("symbol")) for r in primary["ranked"]
        ],
        "sensitivity_hard_off_extra": sorted(
            set(
                sensitivity["would_take_symbols"]
                + [str(r.get("symbol")) for r in sensitivity["ranked"]]
            )
            - set(
                primary["would_take_symbols"]
                + [str(r.get("symbol")) for r in primary["ranked"]]
            )
        ),
        "sensitivity_ranked": [
            str(r.get("symbol")) for r in sensitivity["ranked"]
        ],
    }


def load_scan_funnels(date_str: str, scans_dir: Path) -> dict[int, dict[str, Any]]:
    """Map bar-hour → funnel doc for php_scan_YYYYMMDD_HHMM.json."""
    day = date_str.replace("-", "")
    by_hour: dict[int, dict[str, Any]] = {}
    if not scans_dir.is_dir():
        return by_hour
    for path in sorted(scans_dir.glob(f"php_scan_{day}_*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        hour = doc.get("bar_hour")
        if hour is None:
            try:
                stamp = path.stem.replace("php_scan_", "")
                hour = int(datetime.strptime(stamp, "%Y%m%d_%H%M").strftime("%H"))
            except ValueError:
                continue
        hour = int(hour)
        by_hour[hour] = {"path": str(path), "doc": doc}
    return by_hour


def load_ledger_ran_up(ledger_path: Path, date_str: str) -> dict[str, dict[str, Any]]:
    if not ledger_path.is_file():
        return {}
    try:
        doc = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in doc.get("rows") or []:
        day = str(row.get("signal_day") or row.get("signal_at") or "")[:10]
        if day != date_str:
            continue
        sym = str(row.get("symbol") or "").upper()
        if not sym:
            continue
        prev = out.get(sym)
        if prev is None or float(row.get("ran_up_pct") or -999) > float(
            prev.get("ran_up_pct") or -999
        ):
            out[sym] = row
    return out


def funnel_launches_as_rows(doc: dict[str, Any], hour: int) -> list[dict[str, Any]]:
    """Best-effort passer rows from a live funnel (fields are sparse)."""
    rows: list[dict[str, Any]] = []
    for launch in doc.get("launches") or []:
        scan = launch.get("scan_score")
        phase = launch.get("phase_3h") or launch.get("phase")
        row = {
            "symbol": str(launch.get("symbol") or "").upper(),
            "pass": True,
            "buy_signal": True,
            "htf_1h_bar_hour": launch.get("hour") if launch.get("hour") is not None else hour,
            "htf_score": launch.get("htf_score") or 0.0,
            "launch_score": launch.get("launch_score"),
            "continuation_score_live_funnel": launch.get("rank"),
            "htf_1h_close": launch.get("1h_close"),
            "close": launch.get("1h_close"),
            "phase": phase,
            "phase_3h": phase,
            "bar_state": launch.get("bar_state"),
            "vol_ratio_20": launch.get("vol_ratio_20"),
            "rs_spy_1h": launch.get("rs_spy_1h"),
            "rs_spy_5d": launch.get("rs_spy_5d"),
            "dollar_vol_1h": launch.get("dollar_vol_1h"),
            "dollar_vol_1h_vs_20d": launch.get("dollar_vol_1h_vs_20d"),
            "float_shares": launch.get("float_shares"),
            "micro_dead_tape": launch.get("micro_dead_tape"),
            "options_call_share": launch.get("options_call_share"),
            "taken_live": bool(launch.get("taken")),
            "funnel_rank_order": launch.get("rank_order"),
            "scan_score_missing": scan is None,
        }
        if scan is not None:
            row["scan_score"] = scan
        rows.append(row)
    return rows


def _watch_status(symbols: list[str], attention: list[dict[str, Any]], take: list[str]) -> dict[str, str]:
    attn = {str(r.get("symbol") or "").upper() for r in attention}
    take_s = {str(s).upper() for s in take}
    out: dict[str, str] = {}
    for sym in WATCH:
        if sym in take_s:
            out[sym] = "WOULD_TAKE"
        elif sym in attn:
            out[sym] = "NOMINATED"
        elif sym in {str(s).upper() for s in symbols}:
            out[sym] = "PASSER_NOT_NOMINATED"
        else:
            out[sym] = "NOT_IN_PASSER_SET"
    return out


def run_from_funnels(
    date_str: str,
    funnels: dict[int, dict[str, Any]],
    *,
    keep_hard: bool,
) -> dict[int, dict[str, Any]]:
    hours: dict[int, dict[str, Any]] = {}
    open_syms: set[str] = set()
    for hour in range(5, 16):
        pack = funnels.get(hour)
        if pack is None:
            hours[hour] = {
                "hour": hour,
                "has_funnel": False,
                "would_take": [],
                "watch": {s: "NO_FUNNEL" for s in WATCH},
            }
            continue
        doc = pack["doc"]
        rows = funnel_launches_as_rows(doc, hour)
        # Funnels are already live-passers (hard-ext usually already dropped).
        # Equal-signal re-rank; missing scan_score → keep live rank + phase add-back.
        scored: list[dict[str, Any]] = []
        sparse: list[dict[str, Any]] = []
        for row in rows:
            if row.get("scan_score") is None:
                live_rank = float(row.get("continuation_score_live_funnel") or 0.0)
                phase = str(row.get("phase") or "")
                bump = LIVE_PHASE_EXTENSION_PENALTY if phase == "EXTENSION" else 0.0
                if str(row.get("bar_state") or "") not in ("yellow", "red", "green", "orange"):
                    # unknown tape
                    pass
                row = dict(row)
                row["continuation_score_equal"] = round(live_rank + bump, 2)
                row["continuation_score"] = row["continuation_score_equal"]
                row["combined_rank_score"] = row["continuation_score_equal"]
                row["equal_score_mode"] = "funnel_phase_addback"
                sparse.append(row)
            else:
                scored.append(row)
        cf = counterfactual_hour(
            scored,
            keep_hard_extension=keep_hard,
            open_symbols=open_syms,
        )
        # Merge sparse names (already live-admitted) at the end of rank by add-back score
        if sparse:
            merged = list(cf["ranked"]) + sparse
            merged.sort(
                key=lambda r: -(float(r.get("continuation_score_equal")
                                      or r.get("continuation_score") or 0.0)),
            )
            from tsd_scan_pipeline.php_equal_signal import (
                build_attention_pool_equal_signal,
                review_attention_pool_equal_signal,
                select_would_take,
            )

            attn = build_attention_pool_equal_signal(
                merged, keep_hard_extension=keep_hard,
            )
            reviewed = review_attention_pool_equal_signal(attn)
            take = select_would_take(reviewed, exclude_symbols=open_syms)
            cf = {
                **cf,
                "ranked": merged,
                "ranked_n": len(merged),
                "attention": reviewed,
                "attention_n": len(reviewed),
                "would_take": take,
                "would_take_symbols": [
                    str(r.get("symbol") or "").upper() for r in take
                ],
            }
        take_syms = cf["would_take_symbols"]
        hours[hour] = {
            "hour": hour,
            "has_funnel": True,
            "funnel_path": pack["path"],
            "live_launches_n": len(rows),
            "live_take": [
                str(t.get("symbol") or "").upper()
                for t in (doc.get("take") or doc.get("entered") or [])
                if t.get("symbol")
            ] or [
                str(x.get("symbol") or "").upper()
                for x in (doc.get("launches") or [])
                if x.get("taken")
            ],
            "live_entered": [
                str(e.get("symbol") or "").upper()
                for e in (doc.get("entered") or [])
            ],
            "would_take": take_syms,
            "attention_symbols": [
                str(r.get("symbol") or "").upper() for r in cf["attention"]
            ],
            "watch": _watch_status(
                [str(r.get("symbol") or "").upper() for r in rows],
                cf["attention"],
                take_syms,
            ),
            "sparse_rescored": len(sparse),
            "full_rescored": len(scored),
        }
        for s in take_syms:
            open_syms.add(s)
    return hours


def names_of_interest_cf() -> dict[str, Any]:
    """
    Watchlist reconstruction: same other-factor template, public room,
    assumed tradable-popular (mega-cap / named missed runners).

    This is NOT a full-universe hour replay. It answers: if these names were
    1H passers with typical EXTENSION vs LAUNCH scans, would equal-signal
    nominate / rules-ENTER them?
    """
    base = {
        "buy_signal": True,
        "early_bull": False,
        "htf_score": 72.0,
        "htf_1h_bar_hour": 9,
        "htf_sma20_rising": True,
        "htf_range_20d_pct": 0.50,
        "dist_20d_low_bounce": 0.06,
        "vol_ratio_20": 1.8,
        "open": 10.0,
        "high": 10.55,
        "low": 9.95,
        "close": 10.28,
        "rs_ok": 1,
        "rs_spy_5d": 0.05,
        "news_velocity_24h": 5.0,
        "st_ok": 1,
        "st_msg_24h": 15.0,
        "st_bull_ratio": 0.62,
    }
    # Room from public prior-close vs 20d high (no 09-14 look-ahead).
    specs = {
        "OKTA": {"scan_score": 68.0, "trend_strength": 0.75, "dist_20d_high_pct": 0.072},
        "WIX": {"scan_score": 66.0, "trend_strength": 0.72, "dist_20d_high_pct": 0.209},
        "COIN": {"scan_score": 67.0, "trend_strength": 0.74, "dist_20d_high_pct": 0.105},
        "TARS": {"scan_score": 36.0, "trend_strength": 0.22, "dist_20d_high_pct": 0.134},
        "HOOD": {"scan_score": 38.0, "trend_strength": 0.25, "dist_20d_high_pct": 0.101},
        "IRD": {"scan_score": 34.0, "trend_strength": 0.18, "dist_20d_high_pct": 0.146},
        "SNDK": {"scan_score": 40.0, "trend_strength": 0.30, "dist_20d_high_pct": 0.096},
    }
    rows = [{**base, "symbol": s, **kw} for s, kw in specs.items()]
    pop = {
        "recent_gainer_symbols": set(WATCH),
        "recent_active_symbols": set(WATCH) | {"HOOD", "SNDK"},
        "live_gainers": set(WATCH),
        "tws_symbols": set(WATCH) | {"HOOD"},
        "popular_symbols": set(WATCH) | {"HOOD", "SNDK", "TARS", "IRD"},
        "tws_ok": True,
        "sessions_loaded": 8,
    }
    pairs = {s: live_vs_equal_pair(r) for s, r in zip(specs, rows)}
    primary = counterfactual_hour(rows, popularity_ctx=pop, keep_hard_extension=True)
    return {
        "label": (
            "ILLUSTRATIVE watchlist — scan_score assumed EXTENSION-class for "
            "OKTA/WIX/COIN and LAUNCH-class for actual takes. Not live funnel."
        ),
        "pairs": pairs,
        "would_take": primary["would_take_symbols"],
        "attention": [
            {
                "symbol": r.get("symbol"),
                "equal_cont": r.get("continuation_score"),
                "live_cont": r.get("continuation_score_live"),
                "phase": r.get("phase"),
                "live_list_ok": r.get("live_list_ok"),
                "equal_list_ok": r.get("equal_signal_list_ok"),
                "leak_hard": r.get("live_soft_stage_leak_hard_block"),
                "case": r.get("case_verdict"),
                "case_src": r.get("case_source"),
                "reasons": r.get("attention_reasons"),
            }
            for r in primary["attention"]
        ],
        "case_enter": primary["case_enter_symbols"],
    }


def ran_up_cell(sym: str, ledger: dict[str, dict[str, Any]]) -> str:
    row = ledger.get(sym)
    if row and row.get("ran_up_pct") is not None:
        return f"{float(row['ran_up_pct']):+.2f}% (missed_ledger)"
    pub = PUBLIC_SESSION_20260914.get(sym)
    if pub:
        return (
            f"{pub['high_vs_prev_pct']:+.2f}% high vs prior close "
            f"(public daily; NOT missed_ledger)"
        )
    return "UNKNOWN"


def render_report(
    *,
    date_str: str,
    mechanism: dict[str, Any],
    funnel_hours: dict[int, dict[str, Any]],
    watchlist: dict[str, Any],
    ledger: dict[str, dict[str, Any]],
    scans_found: int,
    ledger_found: bool,
) -> str:
    cap = MAX_NEW_ENTRIES_PER_SCAN
    p_launch = mechanism["pairs"]["launch"]
    p_ext = mechanism["pairs"]["soft_extension"]
    p_hard = mechanism["pairs"]["hard_extension"]
    lines: list[str] = []
    a = lines.append
    a("# Equal-signal Peak Hour counterfactual — 2026-09-14 (ET)")
    a("")
    a("**Research / report only.** Live scheduler, continuation_score v1.6, case LLM,")
    a("and take rules on `main` are unchanged. Do not merge this as a live enablement.")
    a("")
    a(f"- Overlay version: `{EQUAL_SIGNAL_VERSION}`")
    a(f"- Take cap used: **`MAX_NEW_ENTRIES_PER_SCAN = {cap}`** (live, 2 NEW / hour)")
    a(f"- Hard-extension (primary): **ON** — `scan >= {EXTENSION_SCAN_AUTO:.0f}` still excluded")
    a("- Soft stage demotion: **neutralized** (phase −15, scan-band terms, launch-score beauty,")
    a("  phase→`bar_state=extended` hard-block leak)")
    a("- Case: **rules-only** (`rules_equal_signal`). Live LLM ENTER confidences are **not** replayed.")
    a("- Trails: out of scope (unchanged).")
    a("")
    a("## Methodology")
    a("")
    a("1. Start from 1H signal passers (`buy_signal` / `early_bull`).")
    a("2. Admit every passer equally unless hard-extension fires.")
    a("3. Rank with popularity / momentum / RS / vol / room / tape / HTF — **not** LAUNCH vs EXTENSION.")
    a("4. Rebuild attention (top-K ∪ tradable-popular ∪ soft-extension *admission* lane).")
    a("5. Rules case decides ENTER/WAIT/REJECT; **scan ≤ 55 is not required** for ENTER.")
    a("6. `select_enter_rows` still requires tradable popularity and the 2/hour cap.")
    a("")
    a("Live v1.6 still **grades** stage (`DESIGN.md`: “do not equalize all bars”). This file tests")
    a("the opposite philosophy without shipping it.")
    a("")
    a("### Hard-extension vs the live leak")
    a("")
    a("Live `classify_bar_state()` returns `extended` when `phase == EXTENSION` **or** `scan >= 75`.")
    a("`evaluate_1h_symbol` then rejects `extension_hard`. Soft EXTENSION (scan 65–74 + trend ≥ 0.7)")
    a("therefore becomes a **hard block**, even though comments say only scan≥75 is hard.")
    a("This counterfactual keeps scan≥75 and uses **OHLC-only** bar_state for tape points.")
    a("")
    a("### Data limits (this cloud clone)")
    a("")
    a(f"- Scan funnels found for {date_str}: **{scans_found}**")
    a(f"- `missed_ledger.json`: **{'yes' if ledger_found else 'no'}** (gitignored; absent here)")
    a("- HTF cache / `POLYGON_API_KEY`: not available for a full-universe bar replay")
    a("- Actual takes below are **operator-supplied (AARON)** unless a funnel is present")
    a("")
    a("## Actual day baseline")
    a("")
    a("| Hour ET | Actual take (queued) | Fill | Evidence | Notes |")
    a("|---:|---|---|---|---|")
    for hour in range(5, 16):
        act = ACTUAL_20260914.get(hour, {})
        q = ", ".join(act.get("queued") or []) or "—"
        fills = act.get("filled") or []
        if fills:
            fill_s = ", ".join(
                f"{f['symbol']} {f.get('status','')} {f.get('pnl_usd','')}"
                for f in fills
            )
        else:
            fill_s = "—"
        note = act.get("notes") or ""
        if act.get("aborted"):
            q = "**(aborted — no scan)**"
        funnel = funnel_hours.get(hour) or {}
        if funnel.get("has_funnel"):
            live_take = ", ".join(funnel.get("live_take") or []) or q
            q = live_take
            if funnel.get("live_entered"):
                fill_s = ", ".join(funnel["live_entered"])
            note = f"funnel {Path(str(funnel.get('funnel_path'))).name}; {note}"
        a(f"| {hour:02d} | {q} | {fill_s} | {act.get('source','')} | {note} |")
    a("")
    a("Carry / ops (not new Peak Hour takes): ATRC residual + NX trail exit morning of 09-14")
    a("(ops audit). L2 logger used HOOD/MSTR/TARS as depth symbols — not entry evidence.")
    a("")
    a("## Hour-by-hour actual vs equal-signal counterfactual")
    a("")
    a("Primary table = **hard-extension ON**. Case source = rules-only.")
    a("")
    a("| Hour | Actual take | CF top attention | CF would-take (≤2) | OKTA | WIX | COIN |")
    a("|---:|---|---|---|---|---|---|")
    for hour in range(5, 16):
        act = ACTUAL_20260914.get(hour, {})
        actual = ", ".join(act.get("queued") or []) or "—"
        if act.get("aborted"):
            actual = "GAP (abort)"
            cf_attn = "—"
            cf_take = "—"
            okta = wix = coin = "GAP"
        else:
            fh = funnel_hours.get(hour) or {}
            if fh.get("has_funnel"):
                cf_attn = ", ".join((fh.get("attention_symbols") or [])[:8]) or "—"
                cf_take = ", ".join(fh.get("would_take") or []) or "none (rules)"
                w = fh.get("watch") or {}
                okta, wix, coin = w.get("OKTA", "?"), w.get("WIX", "?"), w.get("COIN", "?")
            else:
                cf_attn = "UNKNOWN (no php_scan JSON)"
                cf_take = "UNKNOWN (no passer set)"
                okta = wix = coin = "UNKNOWN"
        a(f"| {hour:02d} | {actual} | {cf_attn} | {cf_take} | {okta} | {wix} | {coin} |")
    a("")
    a("If you drop this script onto the laptop next to `results/peak_hour_scans/php_scan_20260914_*.json`,")
    a("re-run and the UNKNOWN cells fill from live passers (still rules-only case).")
    a("")
    a("## Mechanism proof (same other factors, synthetic)")
    a("")
    a("Two 1H passers identical except scan/trend (LAUNCH 35 vs EXTENSION 68 / 0.75).")
    a("")
    a("| Name | scan | phase | live list? | leak hard-block? | live cont | equal cont |")
    a("|---|---:|---|---|---|---:|---:|")
    for key, label in (("launch", "LAUNCHY"), ("soft_extension", "EXTENDY"), ("hard_extension", "HARDX")):
        p = mechanism["pairs"][key]
        a(
            f"| {label} | {p['scan_score']} | {p['phase']} | {p['live_list_ok']} | "
            f"{p['live_soft_stage_leak_hard_block']} | {p['live_continuation']} | "
            f"{p['equal_continuation']} |"
        )
    a("")
    a(
        f"Live continuation gap LAUNCH − EXTENSION = "
        f"**{float(p_launch['live_continuation']) - float(p_ext['live_continuation']):.1f} pts** "
        f"(stage, not tape/room/popularity)."
    )
    a(
        f"Equal-signal gap = "
        f"**{float(p_launch['equal_continuation']) - float(p_ext['equal_continuation']):.1f} pts** "
        f"(should be ~0 given identical non-stage features)."
    )
    a("")
    a(f"- Soft EXTENSION live list: `{p_ext['live_list_ok']}` — leak hard-block `{p_ext['live_soft_stage_leak_hard_block']}`")
    a(f"- Soft EXTENSION equal list: `{p_ext['equal_list_ok']}`")
    a(f"- Hard scan=80 live list: `{p_hard['live_list_ok']}` equal list (hard ON): `{p_hard['equal_list_ok']}`")
    a(f"- Primary CF attention: {', '.join(mechanism['primary_attention']) or '—'}")
    a(f"- Primary CF would-take: {', '.join(mechanism['primary_would_take']) or '—'}")
    a("")
    extra = mechanism.get("sensitivity_hard_off_extra") or []
    a("### Sensitivity — hard-extension also off")
    a("")
    a(
        f"Extra names vs primary: **{', '.join(extra) if extra else 'none'}** "
        f"(ranked with hard off: {', '.join(mechanism.get('sensitivity_ranked') or [])})."
    )
    a("Primary tables keep hard-extension **ON**.")
    a("")
    a("## OKTA / WIX / COIN")
    a("")
    a("Aaron: big missed runners on the launch list, ~+10% ran-up, blamed on EXTENSION demotion /")
    a("attention / case+popularity. `missed_ledger.json` is **not in this clone**.")
    a("")
    a("| Symbol | Ledger ran-up | Public same-day (best effort) | Prior-close room vs 20d high |")
    a("|---|---|---|---|")
    for sym in WATCH + TAKEN_WATCH:
        pub = PUBLIC_SESSION_20260914[sym]
        a(
            f"| {sym} | {ran_up_cell(sym, ledger)} | "
            f"close {pub['close']} ({pub['ret_vs_prev_pct']:+.2f}% vs prev) · "
            f"high {pub['high_vs_prev_pct']:+.2f}% | "
            f"{pub['room_vs_prior_close_pct']:.1f}% |"
        )
    a("")
    a("Public daily is **not** the Peak Hour missed-ledger definition (peak since signal ref).")
    a("It does confirm OKTA/WIX/COIN were the day’s large runners vs the names actually queued.")
    a("")
    a("### Illustrative watchlist (assumed scans — labeled)")
    a("")
    a(watchlist["label"])
    a("")
    a("| Symbol | live list | leak block | live cont | equal cont | attention case |")
    a("|---|---|---|---:|---:|---|")
    for row in watchlist["attention"]:
        a(
            f"| {row['symbol']} | {row['live_list_ok']} | {row['leak_hard']} | "
            f"{row['live_cont']} | {row['equal_cont']} | "
            f"{row['case']} (`{row['case_src']}`) reasons={','.join(row.get('reasons') or [])} |"
        )
    a("")
    a(f"- Equal-signal would-take (cap {cap}, rules+popular): **{', '.join(watchlist['would_take']) or 'none'}**")
    a(f"- Rules ENTER set: **{', '.join(watchlist['case_enter']) or 'none'}**")
    a("")
    a("In this assumed set, a name the live leak would hard-block (WIX) can consume a 2/hour")
    a("slot on room+momentum+popularity. That is the philosophy working — not a live fill.")
    a("")
    a("**Hypothesis check:** neutralizing soft-EXTENSION (including the phase→extended leak)")
    a("does surface OKTA/WIX/COIN-class names into the list + attention in this construction;")
    a("live list gate often **excludes** them as `extension_hard` even when scan is 66–68.")
    a("Whether they **would have been taken** still depends on popularity + constructive room")
    a("(OKTA prior-close room **7.2% < 10%** → live/equal rules ENTER needs CONSTRUCTIVE_ROOM,")
    a("so OKTA may WAIT on rules-only even after equal admission). WIX room 20.9% and COIN 10.5%")
    a("can rules-ENTER if treated as tradable-popular. **Do not treat the would-take list as a")
    a("live fill list** — LLM case is missing and the passer set is assumed.")
    a("")
    a("## What this does *not* prove")
    a("")
    a("- Full-universe 09-14 take list (needs `php_scan_20260914_*.json` or Polygon HTF replay)")
    a("- Live LLM case verdicts / ENTER confidence")
    a("- Hour 8 passers (scan never ran)")
    a("- Trail P&L for counterfactual takes")
    a("")
    a("## How to complete this on the laptop")
    a("")
    a("```text")
    a("py -3 candidates/tsd_scan_pipeline/php_equal_signal_counterfactual.py --date 2026-09-14")
    a("```")
    a("")
    a("Expects `candidates/tsd_scan_pipeline/results/peak_hour_scans/php_scan_20260914_*.json`")
    a("and optionally `missed_ledger.json`. Rewrites this markdown from real passers.")
    a("")
    return "\n".join(lines) + "\n"


def run(
    *,
    date_str: str,
    scans_dir: Path | None = None,
    ledger_path: Path | None = None,
) -> dict[str, Any]:
    scans_dir = scans_dir or SCAN_DIR
    ledger_path = ledger_path or (SCAN_DIR / "missed_ledger.json")
    funnels = load_scan_funnels(date_str, scans_dir)
    ledger = load_ledger_ran_up(ledger_path, date_str)
    mechanism = run_mechanism_demo()
    funnel_hours = run_from_funnels(date_str, funnels, keep_hard=True) if funnels else {
        h: {"hour": h, "has_funnel": False} for h in range(5, 16)
    }
    watchlist = names_of_interest_cf()
    md = render_report(
        date_str=date_str,
        mechanism=mechanism,
        funnel_hours=funnel_hours,
        watchlist=watchlist,
        ledger=ledger,
        scans_found=len(funnels),
        ledger_found=ledger_path.is_file(),
    )
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = date_str.replace("-", "")
    md_path = REPORT_DIR / f"EQUAL_SIGNAL_COUNTERFACTUAL_{stamp}.md"
    json_path = REPORT_DIR / f"equal_signal_counterfactual_{stamp}.json"
    payload = {
        "date": date_str,
        "research_only": True,
        "live_rules_unchanged": True,
        "equal_signal_version": EQUAL_SIGNAL_VERSION,
        "max_new_entries_per_scan": MAX_NEW_ENTRIES_PER_SCAN,
        "hard_extension_primary": True,
        "hard_extension_threshold": EXTENSION_SCAN_AUTO,
        "case": "rules_equal_signal",
        "scans_found": len(funnels),
        "ledger_found": ledger_path.is_file(),
        "actual_baseline": ACTUAL_20260914,
        "public_session": PUBLIC_SESSION_20260914,
        "mechanism": {
            "pairs": mechanism["pairs"],
            "primary_would_take": mechanism["primary_would_take"],
            "primary_attention": mechanism["primary_attention"],
            "sensitivity_hard_off_extra": mechanism["sensitivity_hard_off_extra"],
        },
        "funnel_hours": {
            str(k): {kk: vv for kk, vv in v.items() if kk not in ("ranked",)}
            for k, v in funnel_hours.items()
        },
        "watchlist_illustrative": watchlist,
        "stage_penalties_live": {
            "phase_extension": LIVE_PHASE_EXTENSION_PENALTY,
            "scan_over_55": LIVE_SCAN_OVER_MAX_PENALTY,
        },
    }
    md_path.write_text(md, encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {md_path}")
    print(f"Wrote {json_path}")
    return payload


def main() -> int:
    p = argparse.ArgumentParser(
        description="Research-only equal-signal Peak Hour counterfactual (not live)",
    )
    p.add_argument("--date", default="2026-09-14")
    p.add_argument("--scans-dir", default=None)
    p.add_argument("--ledger", default=None)
    args = p.parse_args()
    run(
        date_str=args.date,
        scans_dir=Path(args.scans_dir) if args.scans_dir else None,
        ledger_path=Path(args.ledger) if args.ledger else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
