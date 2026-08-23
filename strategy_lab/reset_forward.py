"""
strategy_lab/reset_forward.py — wipe LIVE forward runtime state to a clean $3000 book.

Does NOT touch strategy/backtest code or artifacts:
  - results/oos_r2_backtest.json
  - setups.json, history.json, matrix*.json, profiles/, bars/, etc.

Only clears:
  - results/forward_state.json
  - results/forward_predictions.json
  - results/.live_lock (if present)
  - Supabase strategy_lab_state (latest live book row)

$3000 is a ONE-TIME starting line. After this reset, live_forward.py LIVE mode
resumes/compounds from the persisted book — it does not reset each morning.
Only this script sets pools back to $3000.

Usage (from repo root):
  venv\\Scripts\\python.exe strategy_lab\\reset_forward.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
LAB = Path(__file__).resolve().parent
sys.path.insert(0, str(LAB))

from replay import START_POOL_USD  # noqa: E402

ET = ZoneInfo("America/New_York")
STATE_PATH = LAB / "results" / "forward_state.json"
PREDICTIONS_PATH = LAB / "results" / "forward_predictions.json"
ENTRY_MODEL = "immediate"
MAX_SLOTS = 10


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_et() -> str:
    return datetime.now(ET).date().isoformat()


def _snapshot(label: str, state: dict[str, Any] | None, n_pred: int) -> None:
    """Print a compact before/after line."""
    if not state:
        print(f"  [{label}] (no state)  predictions={n_pred}")
        return
    pa = state.get("pool_A_trailing") or {}
    pb = state.get("pool_B_target") or {}
    a_val = float(pa.get("value_usd") or 0)
    b_val = float(pb.get("value_usd") or 0)
    a_n = len(pa.get("closed_trades") or [])
    b_n = len(pb.get("closed_trades") or [])
    n_r2 = state.get("forward_oos_r2_n")
    if n_r2 is None:
        n_r2 = len([
            r for r in (state.get("forward_predictions") or [])
            if r.get("predicted_mfe") is not None
            and r.get("actual_mfe") is not None
        ])
    print(
        f"  [{label}]  "
        f"A=${a_val:.2f}  B=${b_val:.2f}  "
        f"closed_A={a_n}  closed_B={b_n}  "
        f"predictions={n_pred}  forward_oos_n={n_r2}  "
        f"status={state.get('status')}  flag_date={state.get('flag_date')}"
    )


def _load_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _n_predictions(doc: Any) -> int:
    if doc is None:
        return 0
    if isinstance(doc, list):
        return len(doc)
    if isinstance(doc, dict):
        return len(doc.get("predictions") or [])
    return 0


def build_clean_state(flag_date: str) -> dict[str, Any]:
    """Fresh dual-pool book awaiting first live run. N=0 R² collecting data."""
    from live_forward import compute_forward_oos_stats

    r2_stats = compute_forward_oos_stats([])
    start = float(START_POOL_USD)
    curve = [{"t": _now_iso(), "value_usd": start, "event": "reset"}]
    pool_a = {
        "label": "Strategy A (Trailing)",
        "value_usd": start,
        "start_usd": start,
        "open_positions": {},
        "closed_trades": [],
        "equity_curve": list(curve),
        "slots_open": 0,
        "slots_peak": 0,
        "wins": 0,
        "trades_taken": 0,
        "win_rate_pct": None,
    }
    pool_b = {
        "label": "Strategy B (Target)",
        "value_usd": start,
        "start_usd": start,
        "open_positions": {},
        "closed_trades": [],
        "equity_curve": list(curve),
        "slots_open": 0,
        "slots_peak": 0,
        "wins": 0,
        "trades_taken": 0,
        "win_rate_pct": None,
    }
    return {
        "updated_at": _now_iso(),
        "mode": "live",
        "flag_date": flag_date,
        "entry_model": ENTRY_MODEL,
        "phase": "idle",
        "status": "awaiting_first_live_run",
        "message": (
            "Runtime reset — pools at $3000 / $3000. "
            "Awaiting first LIVE session (compounds from here)."
        ),
        "candidates": [],
        "n_candidates": 0,
        "pool_A_trailing": pool_a,
        "pool_B_target": pool_b,
        "scan": None,
        "premarket": {},
        "eod_summary": None,
        "report": None,
        "forward_predictions": [],
        "forward_oos_r2": None,
        "forward_oos_r2_n": 0,
        "forward_oos_r2_stats": r2_stats,
        "max_slots": MAX_SLOTS,
        "reset_at": _now_iso(),
    }


def main() -> int:
    print("=" * 72)
    print("Q-ALPHA reset_forward — wipe LIVE runtime state (not backtests)")
    print("=" * 72)

    before_state = _load_json(STATE_PATH)
    if not isinstance(before_state, dict):
        before_state = None
    before_pred = _load_json(PREDICTIONS_PATH)
    n_before = _n_predictions(before_pred)

    print("BEFORE:")
    _snapshot("local", before_state, n_before)

    flag_date = _today_et()
    clean = build_clean_state(flag_date)

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(clean, indent=2), encoding="utf-8")

    pred_payload = {
        "updated_at": _now_iso(),
        "n": 0,
        "predictions": [],
        "note": "cleared by reset_forward.py — live OOS R² restarts at N=0",
    }
    PREDICTIONS_PATH.write_text(
        json.dumps(pred_payload, indent=2), encoding="utf-8",
    )

    lock_path = LAB / "results" / ".live_lock"
    if lock_path.exists():
        try:
            lock_path.unlink()
            print("  cleared .live_lock")
        except OSError as exc:
            print(f"  WARN: could not clear .live_lock ({exc})")

    supabase_ok = False
    try:
        from lab_state_sync import reset_throttle, upsert_forward_state

        reset_throttle()
        supabase_ok = bool(upsert_forward_state(clean, force=True))
    except Exception as exc:
        print(f"  WARN: Supabase sync failed ({exc})")

    after_state = _load_json(STATE_PATH)
    if not isinstance(after_state, dict):
        after_state = clean
    n_after = _n_predictions(_load_json(PREDICTIONS_PATH))

    print("AFTER:")
    _snapshot("local", after_state, n_after)
    print(
        f"  [supabase] updated={'yes' if supabase_ok else 'NO — check secrets'}  "
        f"flag_date={flag_date}"
    )
    print()
    print(
        f"Confirmed: pools=${START_POOL_USD:.2f}/${START_POOL_USD:.2f}  "
        f"trades=0  predictions=0  "
        f"R² collecting data (N=0)  "
        f"status=awaiting_first_live_run"
    )
    print(
        "Untouched: oos_r2_backtest.json, setups/history/matrix, "
        "profiles, strategy/entry code."
    )
    print(
        "Note: Monday LIVE run will RESUME this book and compound — "
        "only reset_forward.py sets $3000 again."
    )
    print("=" * 72)
    return 0 if (
        float((after_state.get("pool_A_trailing") or {}).get("value_usd") or 0)
        == float(START_POOL_USD)
        and n_after == 0
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
