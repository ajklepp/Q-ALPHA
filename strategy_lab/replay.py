"""
strategy_lab/replay.py — SIM / Polygon-paper day-replay harness.

Proves two-pool, shared-entry, A/B-fork forward logic on ONE historical day
without any live scheduler or IBKR.

  Pool A = Strategy A (Trailing)
  Pool B = Strategy B (Target)
  Same entry / bars / profile per ticker; independent sizing + P&L per pool.
  Cap: 10 concurrent open positions per pool.

Does NOT modify agent files.

Usage:
  py -3 strategy_lab/replay.py          # self-test 2026-08-21
  from replay import run_day
  run_day("2026-08-21")
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
LAB = Path(__file__).resolve().parent
sys.path.insert(0, str(LAB))
sys.path.insert(0, str(ROOT / "candidates"))

import strategy_a as sa  # noqa: E402
from strategy_a import (  # noqa: E402
    BARS_DIR,
    HISTORY_PATH,
    POOL_USD,
    RISK_FRAC,
    extract_levels,
    fetch_daily_after,
    load_minute_bars,
    load_profile,
    run_strategy_a,
    size_shares,
)
from strategy_b import run_strategy_b  # noqa: E402

SETUPS_PATH = LAB / "results" / "setups.json"
DAILY_CACHE_DIR = LAB / "results" / "daily_cache"

# Hardcoded self-test day: 3 setups (ABUS, JOBY, USDE) — NOT 2026-08-20 (90).
SELF_TEST_DATE = "2026-08-21"

START_POOL_USD = float(POOL_USD)  # $3000
MAX_SLOTS = 10
MODE_LABEL = "SIM / Polygon-paper (NO live scheduler, NO IBKR)"


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def bars_path_for(ticker: str, flag_date: str, hist_row: dict | None) -> Path:
    if hist_row:
        rel = hist_row.get("minute_bars_path") or hist_row.get("bars_path")
        if rel:
            p = Path(str(rel))
            return p if p.is_absolute() else ROOT / p
    return BARS_DIR / f"{ticker.upper()}_{flag_date}.json"


def load_daily_cached(
    ticker: str,
    flag_date: str,
    *,
    refresh: bool = False,
) -> list[dict]:
    """Fetch daily bars once per setup; cache under results/daily_cache/."""
    DAILY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = DAILY_CACHE_DIR / f"{ticker.upper()}_{flag_date}.json"
    if cache_path.exists() and not refresh:
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
    # No sleep: Stocks Developer = unlimited REST (15-min delayed feed).
    return bars


def load_day_setups(flag_date: str) -> list[dict[str, str]]:
    """Unique setups for flag_date from results/setups.json, sorted by ticker."""
    doc = json.loads(SETUPS_PATH.read_text(encoding="utf-8"))
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for s in doc.get("setups") or []:
        d = str(s.get("flag_date") or "")[:10]
        if d != flag_date:
            continue
        t = str(s.get("ticker") or "").upper().strip()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append({"ticker": t, "flag_date": d})
    out.sort(key=lambda r: r["ticker"])
    return out


# ---------------------------------------------------------------------------
# Sizing / run
# ---------------------------------------------------------------------------

def size_for_pool(pool_value: float, entry_price: float, kill_pct: float) -> int:
    """(pool * 1% risk) / (entry * kill%), capped by buying power. Whole shares."""
    if pool_value <= 0 or entry_price <= 0 or kill_pct <= 0:
        return 0
    by_risk = int(math.floor((pool_value * RISK_FRAC) / (entry_price * kill_pct)))
    by_pool = int(math.floor(pool_value / entry_price))
    return max(0, min(by_risk, by_pool))


def run_with_pool_sizing(
    run_fn,
    *,
    pool_value: float,
    ticker: str,
    flag_date: str,
    entry_price: float,
    entry_time: str,
    minute_bars: list[dict],
    daily_bars: list[dict],
    profile: dict[str, Any],
    n_shares: int | None = None,
    close_at_data_end: bool = True,
) -> dict[str, Any]:
    """
    Call strategy_a/b run sized to this pool's equity.

    size_shares() reads sa.POOL_USD; temporarily set it so both strategies
    size off the live pool without editing those modules.
    Pass n_shares / close_at_data_end through for live settle.
    """
    old = sa.POOL_USD
    sa.POOL_USD = float(pool_value)
    try:
        return run_fn(
            ticker=ticker,
            flag_date=flag_date,
            entry_price=entry_price,
            entry_time=entry_time,
            minute_bars=minute_bars,
            daily_bars=daily_bars,
            profile=profile,
            n_shares=n_shares,
            close_at_data_end=close_at_data_end,
        )
    finally:
        sa.POOL_USD = old


def tranche_rows(res: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not res:
        return []
    rows = []
    for t in res.get("tranches") or []:
        rows.append({
            "id": t.get("id"),
            "shares": t.get("shares"),
            "exit_price": t.get("exit_price"),
            "exit_time": t.get("exit_time"),
            "exit_reason": t.get("exit_reason"),
            "return_pct": t.get("return_pct"),
            "pnl_usd": t.get("pnl_usd"),
        })
    return rows


def pool_stats(
    trades: list[dict[str, Any]],
    start: float,
    end: float,
    peak_slots: int,
) -> dict[str, Any]:
    taken = [t for t in trades if t.get("taken")]
    wins = sum(1 for t in taken if float(t.get("pnl_usd") or 0) > 0)
    n = len(taken)
    return {
        "start_usd": round(start, 4),
        "end_usd": round(end, 4),
        "realized_pnl_usd": round(end - start, 4),
        "return_pct": round((end / start - 1.0) * 100.0, 4) if start else None,
        "trades_taken": n,
        "trades_skipped": sum(1 for t in trades if not t.get("taken")),
        "wins": wins,
        "win_rate_pct": round(100.0 * wins / n, 2) if n else None,
        "slots_used_peak": peak_slots,
        "max_slots": MAX_SLOTS,
    }


def fmt_pct(x: Any) -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x):+.2f}%"
    except (TypeError, ValueError):
        return "—"


def fmt_money(x: Any) -> str:
    if x is None:
        return "—"
    try:
        return f"${float(x):+.2f}"
    except (TypeError, ValueError):
        return "—"


# ---------------------------------------------------------------------------
# Core day replay
# ---------------------------------------------------------------------------

def run_day(date: str = SELF_TEST_DATE) -> dict[str, Any]:
    """
    Replay one flag_date end-to-end: shared entry, dual pools, A/B fork.

    Defaults to the hardcoded self-test day (2026-08-21). Callable later for
    other dates or a live schedule.
    """
    flag_date = str(date)[:10]
    setups = load_day_setups(flag_date)
    hist_doc = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    history = hist_doc.get("history") or {}

    pool_a = float(START_POOL_USD)
    pool_b = float(START_POOL_USD)
    slots_a = 0
    slots_b = 0
    peak_slots_a = 0
    peak_slots_b = 0

    per_ticker: list[dict[str, Any]] = []
    trades_a: list[dict[str, Any]] = []
    trades_b: list[dict[str, Any]] = []

    print("=" * 78)
    print(f"STRATEGY LAB REPLAY — {flag_date}")
    print(f"Mode: {MODE_LABEL}")
    print(
        f"Pools: A Trailing + B Target  |  start ${START_POOL_USD:.0f} each  |  "
        f"cap {MAX_SLOTS} slots/pool"
    )
    print(f"Setups this day: {len(setups)}  -> {[s['ticker'] for s in setups]}")
    print("=" * 78)

    for setup in setups:
        ticker = setup["ticker"]
        key = f"{ticker}|{flag_date}"
        hist_row = history.get(key)

        print(f"\n--- {ticker} | {flag_date} ---")

        if not hist_row or hist_row.get("status") != "ok":
            print("  SKIP — no ok history / entry")
            per_ticker.append({
                "ticker": ticker,
                "flag_date": flag_date,
                "skipped": True,
                "skip_reason": "missing_history",
            })
            continue

        bars_path = bars_path_for(ticker, flag_date, hist_row)
        if not bars_path.exists():
            print(f"  SKIP — missing bars {bars_path.name}")
            per_ticker.append({
                "ticker": ticker,
                "flag_date": flag_date,
                "skipped": True,
                "skip_reason": "missing_bars",
            })
            continue

        entry_price = float(hist_row["entry_price"])
        entry_time = str(hist_row["entry_time"])
        minute_bars = load_minute_bars(bars_path)
        daily_bars = load_daily_cached(ticker, flag_date)

        profile_path = LAB / "profiles" / f"{ticker}_{flag_date}.json"
        if profile_path.exists():
            profile = load_profile(ticker, flag_date)
        else:
            print("  WARN — missing profile; INSUFFICIENT fallbacks")
            profile = {
                "ticker": ticker,
                "as_of_date": flag_date,
                "confidence": "INSUFFICIENT",
                "stats_meaningful": False,
                "bracket": {},
                "percentiles": {},
            }

        levels = extract_levels(profile)
        kill_pct = float(levels["kill_pct"])
        confidence = str(
            profile.get("confidence") or levels.get("confidence") or "?"
        )

        print(f"  SHARED ENTRY  ${entry_price:.4f} @ {entry_time}")
        print(
            f"  kill={kill_pct * 100:.2f}%  conf={confidence}  "
            f"levels={levels.get('source')}"
        )

        # ---- Pool A (Trailing) ----
        a_rec: dict[str, Any] = {
            "pool": "A_trailing",
            "ticker": ticker,
            "taken": False,
        }
        shares_a = size_for_pool(pool_a, entry_price, kill_pct)

        if slots_a >= MAX_SLOTS:
            a_rec["skip_reason"] = "slots_full"
            print(f"  A  SKIP slots full ({slots_a}/{MAX_SLOTS})")
        elif shares_a < 1:
            a_rec["skip_reason"] = "size_lt_1"
            print(f"  A  SKIP size < 1 share (pool=${pool_a:.2f})")
        else:
            slots_a += 1
            peak_slots_a = max(peak_slots_a, slots_a)
            print(
                f"  A  OPEN  slots {slots_a}/{MAX_SLOTS}  "
                f"size={shares_a} sh  pool=${pool_a:.2f}  "
                f"risk=${pool_a * RISK_FRAC:.2f}"
            )
            res_a = run_with_pool_sizing(
                run_strategy_a,
                pool_value=pool_a,
                ticker=ticker,
                flag_date=flag_date,
                entry_price=entry_price,
                entry_time=entry_time,
                minute_bars=minute_bars,
                daily_bars=daily_bars,
                profile=profile,
            )
            if res_a.get("status") != "ok":
                a_rec["skip_reason"] = (
                    res_a.get("skip_reason") or str(res_a.get("status"))
                )
                print(f"  A  SKIP run status={res_a.get('status')}")
                slots_a = max(0, slots_a - 1)
            else:
                pnl_a = float(res_a.get("total_pnl_usd") or 0.0)
                pool_a += pnl_a
                slots_a = max(0, slots_a - 1)
                a_rec.update({
                    "taken": True,
                    "shares": int(res_a.get("n_shares") or shares_a),
                    "pnl_usd": round(pnl_a, 4),
                    "return_pct": res_a.get("total_return_pct"),
                    "days_held": res_a.get("days_held"),
                    "exit_reason_counts": res_a.get("exit_reason_counts"),
                    "mfe_pct": res_a.get("mfe_pct"),
                    "pool_after_usd": round(pool_a, 4),
                    "slots_after_close": slots_a,
                    "tranches": tranche_rows(res_a),
                })
                print(
                    f"  A  CLOSE ret={res_a.get('total_return_pct'):+.2f}%  "
                    f"pnl=${pnl_a:+.2f}  pool->${pool_a:.2f}  "
                    f"slots {slots_a}/{MAX_SLOTS}  "
                    f"exits={res_a.get('exit_reason_counts')}"
                )
        trades_a.append(a_rec)

        # ---- Pool B (Target) — same entry / bars / profile ----
        b_rec: dict[str, Any] = {
            "pool": "B_target",
            "ticker": ticker,
            "taken": False,
        }
        shares_b = size_for_pool(pool_b, entry_price, kill_pct)

        if slots_b >= MAX_SLOTS:
            b_rec["skip_reason"] = "slots_full"
            print(f"  B  SKIP slots full ({slots_b}/{MAX_SLOTS})")
        elif shares_b < 1:
            b_rec["skip_reason"] = "size_lt_1"
            print(f"  B  SKIP size < 1 share (pool=${pool_b:.2f})")
        else:
            slots_b += 1
            peak_slots_b = max(peak_slots_b, slots_b)
            print(
                f"  B  OPEN  slots {slots_b}/{MAX_SLOTS}  "
                f"size={shares_b} sh  pool=${pool_b:.2f}  "
                f"risk=${pool_b * RISK_FRAC:.2f}"
            )
            res_b = run_with_pool_sizing(
                run_strategy_b,
                pool_value=pool_b,
                ticker=ticker,
                flag_date=flag_date,
                entry_price=entry_price,
                entry_time=entry_time,
                minute_bars=minute_bars,
                daily_bars=daily_bars,
                profile=profile,
            )
            if res_b.get("status") != "ok":
                b_rec["skip_reason"] = (
                    res_b.get("skip_reason") or str(res_b.get("status"))
                )
                print(f"  B  SKIP run status={res_b.get('status')}")
                slots_b = max(0, slots_b - 1)
            else:
                pnl_b = float(res_b.get("total_pnl_usd") or 0.0)
                pool_b += pnl_b
                slots_b = max(0, slots_b - 1)
                b_rec.update({
                    "taken": True,
                    "shares": int(res_b.get("n_shares") or shares_b),
                    "pnl_usd": round(pnl_b, 4),
                    "return_pct": res_b.get("total_return_pct"),
                    "days_held": res_b.get("days_held"),
                    "exit_reason_counts": res_b.get("exit_reason_counts"),
                    "mfe_pct": res_b.get("mfe_pct"),
                    "pool_after_usd": round(pool_b, 4),
                    "slots_after_close": slots_b,
                    "tranches": tranche_rows(res_b),
                })
                print(
                    f"  B  CLOSE ret={res_b.get('total_return_pct'):+.2f}%  "
                    f"pnl=${pnl_b:+.2f}  pool->${pool_b:.2f}  "
                    f"slots {slots_b}/{MAX_SLOTS}  "
                    f"exits={res_b.get('exit_reason_counts')}"
                )
        trades_b.append(b_rec)

        # Side-by-side tranche table
        print("  SIDE-BY-SIDE TRANCHES")
        print(
            f"  {'id':<4} {'A exit':>10} {'A reason':<10} {'A ret%':>8}  "
            f"{'B exit':>10} {'B reason':<10} {'B ret%':>8}"
        )
        a_tr = {t["id"]: t for t in (a_rec.get("tranches") or [])}
        b_tr = {t["id"]: t for t in (b_rec.get("tranches") or [])}
        ids = sorted(set(a_tr) | set(b_tr))
        if not ids:
            print("  (no tranches — one or both pools skipped)")
        for tid in ids:
            ta, tb = a_tr.get(tid), b_tr.get(tid)
            ax = (ta or {}).get("exit_price")
            bx = (tb or {}).get("exit_price")
            print(
                f"  {tid:<4} "
                f"{(f'{ax:.4f}' if ax is not None else '—'):>10} "
                f"{str((ta or {}).get('exit_reason') or '—'):<10} "
                f"{fmt_pct((ta or {}).get('return_pct')):>8}  "
                f"{(f'{bx:.4f}' if bx is not None else '—'):>10} "
                f"{str((tb or {}).get('exit_reason') or '—'):<10} "
                f"{fmt_pct((tb or {}).get('return_pct')):>8}"
            )

        per_ticker.append({
            "ticker": ticker,
            "flag_date": flag_date,
            "skipped": False,
            "entry_price": entry_price,
            "entry_time": entry_time,
            "kill_pct": round(kill_pct, 6),
            "confidence": confidence,
            "levels_source": levels.get("source"),
            "A": a_rec,
            "B": b_rec,
        })

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": MODE_LABEL,
        "flag_date": flag_date,
        "start_pool_usd": START_POOL_USD,
        "max_slots": MAX_SLOTS,
        "n_setups": len(setups),
        "tickers": [s["ticker"] for s in setups],
        "per_ticker": per_ticker,
        "pool_A_trailing": {
            **pool_stats(trades_a, START_POOL_USD, pool_a, peak_slots_a),
            "trades": trades_a,
        },
        "pool_B_target": {
            **pool_stats(trades_b, START_POOL_USD, pool_b, peak_slots_b),
            "trades": trades_b,
        },
    }

    if pool_a > pool_b:
        winner, margin = "A_trailing", pool_a - pool_b
    elif pool_b > pool_a:
        winner, margin = "B_target", pool_b - pool_a
    else:
        winner, margin = "tie", 0.0
    report["winner"] = {
        "pool": winner,
        "margin_usd": round(margin, 4),
        "A_end_usd": round(pool_a, 4),
        "B_end_usd": round(pool_b, 4),
    }

    print_summary(report)
    return report


def print_summary(report: dict[str, Any]) -> None:
    a = report["pool_A_trailing"]
    b = report["pool_B_target"]
    w = report["winner"]

    print()
    print("=" * 78)
    print("DAY SUMMARY")
    print("=" * 78)
    print(f"  {'metric':<28} {'A Trailing':>14} {'B Target':>14}")
    print("-" * 78)
    rows = [
        ("Start pool $", f"${a['start_usd']:.2f}", f"${b['start_usd']:.2f}"),
        ("End pool $", f"${a['end_usd']:.2f}", f"${b['end_usd']:.2f}"),
        (
            "Realized P&L $",
            f"${a['realized_pnl_usd']:+.2f}",
            f"${b['realized_pnl_usd']:+.2f}",
        ),
        (
            "Pool return %",
            f"{a['return_pct']:+.2f}%",
            f"{b['return_pct']:+.2f}%",
        ),
        ("Trades taken", str(a["trades_taken"]), str(b["trades_taken"])),
        ("Win rate %", str(a["win_rate_pct"]), str(b["win_rate_pct"])),
        (
            "Peak slots used",
            f"{a['slots_used_peak']}/{MAX_SLOTS}",
            f"{b['slots_used_peak']}/{MAX_SLOTS}",
        ),
    ]
    for label, av, bv in rows:
        print(f"  {label:<28} {av:>14} {bv:>14}")
    print("-" * 78)

    print("\n  Per-ticker position returns:")
    print(
        f"  {'ticker':<8} {'entry':>8} {'kill%':>7}  "
        f"{'A sh':>5} {'A ret%':>8} {'A pnl$':>9}  "
        f"{'B sh':>5} {'B ret%':>8} {'B pnl$':>9}"
    )
    for row in report["per_ticker"]:
        if row.get("skipped"):
            print(f"  {row['ticker']:<8} SKIP ({row.get('skip_reason')})")
            continue
        ar, br = row["A"], row["B"]
        a_sh = ar.get("shares") if ar.get("taken") else "—"
        b_sh = br.get("shares") if br.get("taken") else "—"
        a_pnl = fmt_money(ar.get("pnl_usd")) if ar.get("taken") else "—"
        b_pnl = fmt_money(br.get("pnl_usd")) if br.get("taken") else "—"
        print(
            f"  {row['ticker']:<8} ${row['entry_price']:>7.4f} "
            f"{row['kill_pct'] * 100:>6.2f}%  "
            f"{a_sh:>5} {fmt_pct(ar.get('return_pct')):>8} {a_pnl:>9}  "
            f"{b_sh:>5} {fmt_pct(br.get('return_pct')):>8} {b_pnl:>9}"
        )

    print()
    if w["pool"] == "tie":
        print(f"  WINNER: TIE — both pools ended at ${w['A_end_usd']:.2f}")
    elif w["pool"] == "A_trailing":
        print(
            f"  WINNER: Strategy A (Trailing) by ${w['margin_usd']:.2f} "
            f"(A ${w['A_end_usd']:.2f} vs B ${w['B_end_usd']:.2f})"
        )
    else:
        print(
            f"  WINNER: Strategy B (Target) by ${w['margin_usd']:.2f} "
            f"(B ${w['B_end_usd']:.2f} vs A ${w['A_end_usd']:.2f})"
        )
    print("=" * 78)


def main() -> None:
    # Hardcoded self-test: 2026-08-21 (ABUS, JOBY, USDE) — 3 setups, under slot cap.
    report = run_day(SELF_TEST_DATE)
    out_path = LAB / "results" / f"replay_{SELF_TEST_DATE}.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
