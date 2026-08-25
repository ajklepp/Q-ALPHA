"""
strategy_lab/forward_book.py — entry-only + settle-pass bookkeeping.

ENTRY stores open_positions without calling strategies / booking P&L.
SETTLE re-runs strategy_a/b on refreshed bars; books P&L only when moving
open → closed (idempotent).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from entry_models import immediate, sweep_reclaim
from forward_runtime import (
    cache_stale_for_settle,
    log_feed_lag,
    mfe_hold_window_ready,
    refresh_daily_bars,
    refresh_minute_bars,
)
from oos_r2 import actual_mfe_pct, predicted_mfe_pct
from replay import (
    FULL_SLOT_TRANCHE_IDS,
    MAX_FULL_SLOTS,
    MAX_NEW_ENTRIES_PER_DAY,
    START_POOL_USD,
    full_slots_used,
    residual_tranche_ids,
    run_with_pool_sizing,
    size_for_pool,
    tranche_rows,
)
from strategy_a import (
    BARS_DIR,
    HISTORY_PATH,
    MAX_HOLD_TRADING_DAYS,
    RISK_FRAC,
    extract_levels,
    load_minute_bars,
    load_profile,
    run_strategy_a,
)
from strategy_b import run_strategy_b

LAB = Path(__file__).resolve().parent
ROOT = LAB.parent


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _equity_append(pool: dict[str, Any], value: float, event: str, ticker: str) -> None:
    pool.setdefault("equity_curve", []).append({
        "t": _now_iso(),
        "value_usd": round(value, 4),
        "event": event,
        "ticker": ticker,
    })


def _win_rate(pool: dict[str, Any]) -> None:
    n = int(pool.get("trades_taken") or 0)
    w = int(pool.get("wins") or 0)
    pool["win_rate_pct"] = round(100.0 * w / n, 2) if n else None


def _slots_used(pool: dict[str, Any]) -> int:
    """Aaron full-slot count (T1/T2/T3 still working); T4-only excluded."""
    return full_slots_used(pool)


def _new_open_position(
    *,
    entry_price: float,
    entry_time: str,
    shares: int,
    pool_value: float,
    kill_pct: float,
    trail_pct: float,
    triggers_4: list,
    sweep_tag: str,
    predicted_mfe: float | None,
    flag_date: str,
) -> dict[str, Any]:
    """Build an open_positions record with residual tranche tracking seeded."""
    residuals = ["T1", "T2", "T3", "T4"]
    return {
        "entry_price": entry_price,
        "entry_time": entry_time,
        "shares": shares,
        "pool_value_at_entry": round(pool_value, 4),
        "kill_pct": kill_pct,
        "trail_pct": trail_pct,
        "triggers_4": triggers_4,
        "sweep_reclaim": sweep_tag,
        "predicted_mfe": predicted_mfe,
        "flag_date": flag_date,
        "opened_at": _now_iso(),
        # Until settle refines: all four working → counts as full slot.
        "residual_tranche_ids": residuals,
        "counts_as_full_slot": True,
    }


def load_bars_for_entry(
    ticker: str,
    flag_date: str,
    *,
    refresh: bool,
    hist_row: dict | None,
) -> list[dict]:
    path = BARS_DIR / f"{ticker.upper()}_{flag_date}.json"
    if refresh or not path.exists():
        if refresh:
            try:
                return refresh_minute_bars(ticker, flag_date)
            except Exception as exc:
                print(f"  WARN refresh minute bars {ticker}: {exc}")
        # fall through to disk / hist path
    if hist_row:
        rel = hist_row.get("minute_bars_path") or hist_row.get("bars_path")
        if rel:
            p = Path(str(rel))
            p = p if p.is_absolute() else ROOT / p
            if p.exists():
                return load_minute_bars(p)
    if path.exists():
        return load_minute_bars(path)
    return []


def entry_open_positions(
    candidates: list[dict[str, Any]],
    flag_date: str,
    state: dict[str, Any],
    *,
    refresh_bars: bool,
    lab_telegram: Callable[..., bool],
    dry_run: bool,
    save_state: Callable[[dict], None],
    upsert_prediction: Callable[..., list],
    refresh_r2: Callable[[dict], None],
    after_ticker: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """
    ENTRY RUN: compute shared immediate entry, size, store open_positions.
    Does NOT call run_strategy_* or book P&L (unless after_ticker settles).
    """
    hist_doc = {}
    if HISTORY_PATH.exists():
        hist_doc = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    history = hist_doc.get("history") or {}

    pool_a = float(state["pool_A_trailing"]["value_usd"])
    pool_b = float(state["pool_B_target"]["value_usd"])
    day_start_a = pool_a
    day_start_b = pool_b
    peak_a = max(
        int(state["pool_A_trailing"].get("slots_peak") or 0),
        _slots_used(state["pool_A_trailing"]),
    )
    peak_b = max(
        int(state["pool_B_target"].get("slots_peak") or 0),
        _slots_used(state["pool_B_target"]),
    )

    per_ticker: list[dict[str, Any]] = []
    n_entered = 0

    print()
    print("=" * 78)
    print(f"LIVE FORWARD — ENTRY ONLY  {flag_date}")
    print(f"Entry model: immediate  |  sweep_reclaim = quality tag only")
    print(
        f"Pools: A=${day_start_a:.2f} B=${day_start_b:.2f}  |  "
        f"full slots A={_slots_used(state['pool_A_trailing'])}/{MAX_FULL_SLOTS} "
        f"B={_slots_used(state['pool_B_target'])}/{MAX_FULL_SLOTS}  "
        f"(T1–T3; T4-only free)  |  day cap {MAX_NEW_ENTRIES_PER_DAY}"
    )
    print(f"Candidates: {len(candidates)} → {[c['ticker'] for c in candidates]}")
    print("=" * 78)

    for c in candidates:
        ticker = c["ticker"]
        key = f"{ticker}|{flag_date}"
        hist_row = history.get(key)
        print(f"\n--- entering {ticker} | {flag_date} ---")

        # Day cap: max new names entered (A and/or B counts as one).
        if n_entered >= MAX_NEW_ENTRIES_PER_DAY:
            print(
                f"  SKIP — day_entry_cap "
                f"({n_entered}/{MAX_NEW_ENTRIES_PER_DAY})"
            )
            per_ticker.append({
                "ticker": ticker,
                "flag_date": flag_date,
                "skipped": True,
                "skip_reason": "day_entry_cap",
            })
            continue

        minute_bars = load_bars_for_entry(
            ticker, flag_date, refresh=refresh_bars, hist_row=hist_row,
        )
        if not minute_bars:
            print("  SKIP — missing bars")
            per_ticker.append({
                "ticker": ticker,
                "flag_date": flag_date,
                "skipped": True,
                "skip_reason": "missing_bars",
            })
            continue

        # Feed-lag probe (logging only; expected ~15 min delayed entitlement).
        log_feed_lag(ticker, flag_date, minute_bars)

        sig = immediate(minute_bars)
        if sig is None:
            print("  SKIP — no_0930_bar")
            per_ticker.append({
                "ticker": ticker,
                "flag_date": flag_date,
                "skipped": True,
                "skip_reason": "no_0930_bar",
            })
            continue

        entry_price = float(sig.entry_price)
        entry_time = str(sig.entry_time)
        sw = sweep_reclaim(minute_bars)
        sweep_tag = "pass" if sw is not None else "fail"
        print(
            f"  SHARED ENTRY  ${entry_price:.4f} @ {entry_time}  "
            f"[immediate]  sweep_reclaim={sweep_tag}"
        )

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
        trail_pct = float(levels.get("trail_pct") or 0.0)
        triggers_4 = list(levels.get("triggers_4") or [])
        confidence = str(profile.get("confidence") or levels.get("confidence") or "?")
        print(
            f"  kill={kill_pct * 100:.2f}%  conf={confidence}  "
            f"levels={levels.get('source')}"
        )

        predicted_mfe: float | None = None
        try:
            predicted_mfe = predicted_mfe_pct(profile)
        except Exception:
            predicted_mfe = None
        if predicted_mfe is not None:
            print(f"  predicted MFE p50 = {predicted_mfe:.2f}%")
            if str(state.get("mode") or "").lower() != "replay":
                rows = list(state.get("forward_predictions") or [])
                state["forward_predictions"] = upsert_prediction(
                    rows,
                    ticker=ticker,
                    flag_date=flag_date,
                    predicted_mfe=predicted_mfe,
                    actual_mfe=None,
                )
                refresh_r2(state)
        else:
            print("  predicted MFE p50 = — (missing / insufficient)")

        # ---- Pool A gates ----
        a_rec: dict[str, Any] = {
            "pool": "A_trailing",
            "ticker": ticker,
            "taken": False,
            "sweep_reclaim": sweep_tag,
            "entry_model": "immediate",
            "entry_price": entry_price,
            "entry_time": entry_time,
            "predicted_mfe": predicted_mfe,
        }
        shares_a = size_for_pool(pool_a, entry_price, kill_pct)
        slots_a = _slots_used(state["pool_A_trailing"])
        if slots_a >= MAX_FULL_SLOTS:
            a_rec["skip_reason"] = "slots_full"
            print(
                f"  A  SKIP full slots ({slots_a}/{MAX_FULL_SLOTS}) "
                f"— T1/T2/T3 capacity"
            )
        elif shares_a < 1:
            a_rec["skip_reason"] = "size_lt_1"
            print(f"  A  SKIP size < 1 (pool=${pool_a:.2f})")
        else:
            a_rec["taken"] = True
            a_rec["shares"] = shares_a
            state["pool_A_trailing"]["open_positions"][ticker] = _new_open_position(
                entry_price=entry_price,
                entry_time=entry_time,
                shares=shares_a,
                pool_value=pool_a,
                kill_pct=kill_pct,
                trail_pct=trail_pct,
                triggers_4=triggers_4,
                sweep_tag=sweep_tag,
                predicted_mfe=predicted_mfe,
                flag_date=flag_date,
            )
            slots_a_after = _slots_used(state["pool_A_trailing"])
            peak_a = max(peak_a, slots_a_after)
            state["pool_A_trailing"]["slots_open"] = slots_a_after
            state["pool_A_trailing"]["slots_peak"] = peak_a
            print(
                f"  A  OPEN  full slots {slots_a_after}/{MAX_FULL_SLOTS}  "
                f"size={shares_a} sh  pool=${pool_a:.2f}  "
                f"risk=${pool_a * RISK_FRAC:.2f}"
            )

        # ---- Pool B gates ----
        b_rec: dict[str, Any] = {
            "pool": "B_target",
            "ticker": ticker,
            "taken": False,
            "sweep_reclaim": sweep_tag,
            "entry_model": "immediate",
            "entry_price": entry_price,
            "entry_time": entry_time,
            "predicted_mfe": predicted_mfe,
        }
        shares_b = size_for_pool(pool_b, entry_price, kill_pct)
        slots_b = _slots_used(state["pool_B_target"])
        if slots_b >= MAX_FULL_SLOTS:
            b_rec["skip_reason"] = "slots_full"
            print(
                f"  B  SKIP full slots ({slots_b}/{MAX_FULL_SLOTS}) "
                f"— T1/T2/T3 capacity"
            )
        elif shares_b < 1:
            b_rec["skip_reason"] = "size_lt_1"
            print(f"  B  SKIP size < 1 (pool=${pool_b:.2f})")
        else:
            b_rec["taken"] = True
            b_rec["shares"] = shares_b
            state["pool_B_target"]["open_positions"][ticker] = _new_open_position(
                entry_price=entry_price,
                entry_time=entry_time,
                shares=shares_b,
                pool_value=pool_b,
                kill_pct=kill_pct,
                trail_pct=trail_pct,
                triggers_4=triggers_4,
                sweep_tag=sweep_tag,
                predicted_mfe=predicted_mfe,
                flag_date=flag_date,
            )
            slots_b_after = _slots_used(state["pool_B_target"])
            peak_b = max(peak_b, slots_b_after)
            state["pool_B_target"]["slots_open"] = slots_b_after
            state["pool_B_target"]["slots_peak"] = peak_b
            print(
                f"  B  OPEN  full slots {slots_b_after}/{MAX_FULL_SLOTS}  "
                f"size={shares_b} sh  pool=${pool_b:.2f}  "
                f"risk=${pool_b * RISK_FRAC:.2f}"
            )

        # Telegram only after both pools decided.
        if a_rec.get("taken") or b_rec.get("taken"):
            n_entered += 1
            lab_telegram(
                f"🧪 ENTERED {ticker} @ ${entry_price:.4f} "
                f"(A={'taken' if a_rec.get('taken') else a_rec.get('skip_reason')}, "
                f"B={'taken' if b_rec.get('taken') else b_rec.get('skip_reason')}).",
                dry_run=dry_run,
            )

        state["pool_A_trailing"]["value_usd"] = round(pool_a, 4)
        state["pool_B_target"]["value_usd"] = round(pool_b, 4)
        save_state(state)

        if after_ticker is not None and (a_rec.get("taken") or b_rec.get("taken")):
            after_ticker(ticker)
            # Refresh local pool mirrors after optional per-ticker settle.
            pool_a = float(state["pool_A_trailing"]["value_usd"])
            pool_b = float(state["pool_B_target"]["value_usd"])
            peak_a = max(peak_a, _slots_used(state["pool_A_trailing"]))
            peak_b = max(peak_b, _slots_used(state["pool_B_target"]))

        per_ticker.append({
            "ticker": ticker,
            "flag_date": flag_date,
            "skipped": False,
            "entry_price": entry_price,
            "entry_time": entry_time,
            "entry_model": "immediate",
            "sweep_reclaim": sweep_tag,
            "kill_pct": round(kill_pct, 6),
            "confidence": confidence,
            "A": a_rec,
            "B": b_rec,
        })

    state["pool_A_trailing"]["slots_open"] = _slots_used(state["pool_A_trailing"])
    state["pool_B_target"]["slots_open"] = _slots_used(state["pool_B_target"])
    save_state(state)

    return {
        "generated_at": _now_iso(),
        "mode": state.get("mode"),
        "flag_date": flag_date,
        "phase": "entry",
        "day_start_A_usd": round(day_start_a, 4),
        "day_start_B_usd": round(day_start_b, 4),
        "n_entered": n_entered,
        "tickers": [c["ticker"] for c in candidates],
        "per_ticker": per_ticker,
        "open_A": list((state["pool_A_trailing"].get("open_positions") or {}).keys()),
        "open_B": list((state["pool_B_target"].get("open_positions") or {}).keys()),
    }


def _settle_one_pool(
    *,
    pool_key: str,
    run_fn,
    state: dict[str, Any],
    close_at_data_end: bool,
    refresh_data: bool,
    upsert_prediction: Callable[..., list],
    refresh_r2: Callable[[dict], None],
    allow_finalize_mfe: bool,
) -> list[dict[str, Any]]:
    """Re-run strategy for each open position in one pool. Return newly closed."""
    pool = state[pool_key]
    opens = dict(pool.get("open_positions") or {})
    newly_closed: list[dict[str, Any]] = []
    raw_pool = pool.get("value_usd")
    pool_val = float(START_POOL_USD if raw_pool is None else raw_pool)

    for ticker, pos in list(opens.items()):
        flag_date = str(pos.get("flag_date") or state.get("flag_date") or "")[:10]
        entry_price = float(pos["entry_price"])
        entry_time = str(pos["entry_time"])
        shares = int(pos["shares"])
        pool_at_entry = float(pos.get("pool_value_at_entry") or pool_val)
        predicted_mfe = pos.get("predicted_mfe")

        print(f"\n  settle {pool_key} {ticker}|{flag_date} shares={shares}")

        # Refresh bars (overwrite stale morning caches).
        bars_path = BARS_DIR / f"{ticker}_{flag_date}.json"
        daily_path = (
            LAB / "results" / "daily_cache" / f"{ticker}_{flag_date}.json"
        )
        need_min = refresh_data or cache_stale_for_settle(bars_path, flag_date)
        need_day = refresh_data or cache_stale_for_settle(daily_path, flag_date)
        try:
            if need_min:
                minute_bars = refresh_minute_bars(ticker, flag_date)
            else:
                minute_bars = load_minute_bars(bars_path) if bars_path.exists() else []
        except Exception as exc:
            print(f"    WARN minute refresh failed ({exc})")
            minute_bars = load_minute_bars(bars_path) if bars_path.exists() else []
        try:
            if need_day:
                daily_bars = refresh_daily_bars(ticker, flag_date)
            else:
                from replay import load_daily_cached
                daily_bars = load_daily_cached(ticker, flag_date, refresh=False)
        except Exception as exc:
            print(f"    WARN daily refresh failed ({exc})")
            daily_bars = []

        profile_path = LAB / "profiles" / f"{ticker}_{flag_date}.json"
        if profile_path.exists():
            profile = load_profile(ticker, flag_date)
        else:
            profile = {
                "ticker": ticker,
                "confidence": "INSUFFICIENT",
                "bracket": {},
                "percentiles": {},
            }

        res = run_with_pool_sizing(
            run_fn,
            pool_value=pool_at_entry,
            ticker=ticker,
            flag_date=flag_date,
            entry_price=entry_price,
            entry_time=entry_time,
            minute_bars=minute_bars,
            daily_bars=daily_bars,
            profile=profile,
            n_shares=shares,
            close_at_data_end=close_at_data_end,
        )

        if res.get("status") == "open" or res.get("still_open"):
            mark = res.get("last_close") or entry_price
            residuals = residual_tranche_ids(res)
            pos["mark_price"] = mark
            pos["mark_usd"] = round(float(mark) * shares, 4)
            pos["last_settle_at"] = _now_iso()
            # Persist residual tranche state so capacity can free T4-only runners.
            pos["residual_tranche_ids"] = residuals
            pos["counts_as_full_slot"] = bool(
                FULL_SLOT_TRANCHE_IDS.intersection(residuals)
            )
            if res.get("open_shares") is not None:
                pos["open_shares"] = res.get("open_shares")
            opens[ticker] = pos
            slot_tag = "FULL" if pos["counts_as_full_slot"] else "T4-only"
            print(
                f"    still OPEN  mark=${float(mark):.4f}  "
                f"open_sh={res.get('open_shares')}  "
                f"residuals={residuals}  slot={slot_tag}"
            )
            # Provisional MFE for display only — never into R².
            if predicted_mfe is not None:
                try:
                    prov = actual_mfe_pct(
                        entry_price=entry_price,
                        entry_time=entry_time,
                        flag_date=flag_date,
                        minute_bars=minute_bars,
                        daily_bars=daily_bars,
                    )
                except Exception:
                    prov = None
                if prov is not None:
                    pos["actual_mfe_provisional"] = round(float(prov), 4)
            continue

        if res.get("status") != "ok":
            print(f"    SKIP settle status={res.get('status')} {res.get('skip_reason')}")
            continue

        # Fully closed — book P&L once (idempotent: only when leaving open_positions).
        pnl = float(res.get("total_pnl_usd") or 0.0)
        pool_val += pnl
        closed_rec = {
            "pool": "A_trailing" if "A_" in pool_key else "B_target",
            "ticker": ticker,
            "taken": True,
            "flag_date": flag_date,
            "entry_price": entry_price,
            "entry_time": entry_time,
            "shares": shares,
            "pnl_usd": round(pnl, 4),
            "return_pct": res.get("total_return_pct"),
            "days_held": res.get("days_held"),
            "exit_reason_counts": res.get("exit_reason_counts"),
            "mfe_pct": res.get("mfe_pct"),
            "predicted_mfe": predicted_mfe,
            "tranches": tranche_rows(res),
            "pool_after_usd": round(pool_val, 4),
            "sweep_reclaim": pos.get("sweep_reclaim"),
            "settled_at": _now_iso(),
        }
        opens.pop(ticker, None)
        pool["closed_trades"] = list(pool.get("closed_trades") or []) + [closed_rec]
        pool["trades_taken"] = int(pool.get("trades_taken") or 0) + 1
        if pnl > 0:
            pool["wins"] = int(pool.get("wins") or 0) + 1
        _win_rate(pool)
        _equity_append(pool, pool_val, "close", ticker)
        newly_closed.append(closed_rec)
        print(
            f"    CLOSED ret={res.get('total_return_pct')}  "
            f"pnl=${pnl:+.2f}  pool→${pool_val:.2f}  "
            f"exits={res.get('exit_reason_counts')}"
        )

        # Finalize OOS prediction pair only when hold window complete.
        if (
            allow_finalize_mfe
            and predicted_mfe is not None
            and str(state.get("mode") or "").lower() != "replay"
        ):
            actual: float | None = None
            if mfe_hold_window_ready(
                flag_date, daily_bars, max_hold_days=MAX_HOLD_TRADING_DAYS,
            ):
                try:
                    actual = actual_mfe_pct(
                        entry_price=entry_price,
                        entry_time=entry_time,
                        flag_date=flag_date,
                        minute_bars=minute_bars,
                        daily_bars=daily_bars,
                    )
                except Exception:
                    actual = None  # never fall back to strat_mfe
            if actual is not None:
                rows = list(state.get("forward_predictions") or [])
                state["forward_predictions"] = upsert_prediction(
                    rows,
                    ticker=ticker,
                    flag_date=flag_date,
                    predicted_mfe=float(predicted_mfe),
                    actual_mfe=float(actual),
                )
                refresh_r2(state)
                closed_rec["actual_mfe"] = round(float(actual), 4)
                print(
                    f"    MFE pair finalized act={actual:.2f}% "
                    f"pred={float(predicted_mfe):.2f}%"
                )
            else:
                print("    MFE pair deferred (hold window not complete)")

    pool["open_positions"] = opens
    pool["value_usd"] = round(pool_val, 4)
    pool["slots_open"] = full_slots_used(pool)
    pool["n_open_positions"] = len(opens)
    return newly_closed


def settle_ticker(
    state: dict[str, Any],
    ticker: str,
    *,
    close_at_data_end: bool,
    refresh_data: bool,
    upsert_prediction: Callable[..., list],
    refresh_r2: Callable[[dict], None],
    allow_finalize_mfe: bool,
) -> None:
    """Settle a single ticker in both pools (replay sequential sizing)."""
    for pool_key, run_fn in (
        ("pool_A_trailing", run_strategy_a),
        ("pool_B_target", run_strategy_b),
    ):
        opens = dict((state[pool_key].get("open_positions") or {}))
        if ticker not in opens:
            continue
        saved = opens
        state[pool_key]["open_positions"] = {ticker: opens[ticker]}
        _settle_one_pool(
            pool_key=pool_key,
            run_fn=run_fn,
            state=state,
            close_at_data_end=close_at_data_end,
            refresh_data=refresh_data,
            upsert_prediction=upsert_prediction,
            refresh_r2=refresh_r2,
            allow_finalize_mfe=allow_finalize_mfe,
        )
        cur = dict(state[pool_key].get("open_positions") or {})
        for t, pos in saved.items():
            if t == ticker:
                continue
            cur[t] = pos
        state[pool_key]["open_positions"] = cur
        state[pool_key]["slots_open"] = full_slots_used(state[pool_key])
        state[pool_key]["n_open_positions"] = len(cur)


def settle_open_positions(
    state: dict[str, Any],
    *,
    close_at_data_end: bool,
    refresh_data: bool,
    upsert_prediction: Callable[..., list],
    refresh_r2: Callable[[dict], None],
    allow_finalize_mfe: bool = True,
) -> dict[str, Any]:
    """Settle all open positions in both pools. Idempotent."""
    print()
    print("=" * 78)
    print(
        f"LIVE FORWARD — SETTLE  "
        f"close_at_data_end={close_at_data_end}  refresh={refresh_data}"
    )
    print("=" * 78)

    closed_a = _settle_one_pool(
        pool_key="pool_A_trailing",
        run_fn=run_strategy_a,
        state=state,
        close_at_data_end=close_at_data_end,
        refresh_data=refresh_data,
        upsert_prediction=upsert_prediction,
        refresh_r2=refresh_r2,
        allow_finalize_mfe=allow_finalize_mfe,
    )
    closed_b = _settle_one_pool(
        pool_key="pool_B_target",
        run_fn=run_strategy_b,
        state=state,
        close_at_data_end=close_at_data_end,
        refresh_data=refresh_data,
        upsert_prediction=upsert_prediction,
        refresh_r2=refresh_r2,
        allow_finalize_mfe=allow_finalize_mfe,
    )

    a_val = float(state["pool_A_trailing"]["value_usd"])
    b_val = float(state["pool_B_target"]["value_usd"])
    n_open = (
        len(state["pool_A_trailing"].get("open_positions") or {})
        + len(state["pool_B_target"].get("open_positions") or {})
    )
    return {
        "closed_A": len(closed_a),
        "closed_B": len(closed_b),
        "pool_A_usd": round(a_val, 4),
        "pool_B_usd": round(b_val, 4),
        "open_positions": n_open,
        "closed_trades_A": closed_a,
        "closed_trades_B": closed_b,
    }
