"""
Q-ALPHA | True Day-1 State Reset

WHAT: Overwrites candidates/paper_trades.json and candidates/pool_state.json
      with clean Day-1 state -- $3,000 pool, zero trades, nothing deployed.

WHY:  Week-1 test data (2026-08-12 to 2026-08-14: NEBX, NBIG, NBIL, APMD,
      MGTX, KEX) left stale PENDING_MOC rows in local state. The EOD monitor
      read those rows and sent a false "STOP HIT" Telegram report.
      2026-08-17 is true Day 1 of v1.0.0, so both files must start empty.

Key order matches PaperTradesStore._empty() in paper_trader.py and
default_pool_state() in position_sizer.py so existing loaders see the
structure they already expect.

Usage: python candidates/reset_day1.py
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

CANDIDATES_DIR = Path(__file__).resolve().parent
PAPER_TRADES_PATH = CANDIDATES_DIR / "paper_trades.json"
POOL_STATE_PATH = CANDIDATES_DIR / "pool_state.json"

# Both state files are gitignored, so previous contents are only recoverable
# from a local copy. Backups land here and are ignored by their own .gitignore.
BACKUP_DIR = CANDIDATES_DIR / "_state_backups"

STARTING_POOL = 3000.0  # Day-1 account size in USD (Q_ALPHA_HANDOFF.md)
JSON_INDENT = 2  # matches json.dumps(data, indent=2) used by paper_trader.py
TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S"  # same format PaperTradesStore.save() writes
BACKUP_STAMP_FMT = "%Y%m%d_%H%M%S"  # filename-safe stamp for backup copies
EASTERN_FALLBACK_OFFSET_HOURS = -4  # EDT offset when zoneinfo/tzdata unavailable


def now_et() -> datetime:
    """Current time in US/Eastern; mirrors now_et() in position_sizer.py."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        from datetime import timezone
        return datetime.now(timezone(timedelta(hours=EASTERN_FALLBACK_OFFSET_HOURS)))


def backup_existing(path: Path, stamp: str) -> Path | None:
    """
    Copy a state file to BACKUP_DIR before it is overwritten.

    These files are gitignored, so without this copy the stale Week-1 data
    would be unrecoverable if the reset ever needs to be inspected.
    """
    if not path.exists():
        return None

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    # Keep trade data out of git even if someone runs `git add .` later.
    (BACKUP_DIR / ".gitignore").write_text("*\n", encoding="utf-8")

    destination = BACKUP_DIR / f"{path.stem}_{stamp}{path.suffix}"
    shutil.copy2(path, destination)
    return destination


def build_paper_trades(timestamp: str) -> dict:
    """Empty trade ledger matching PaperTradesStore._empty() key order."""
    return {
        "trades": [],
        "last_updated": timestamp,
        "summary": {
            "total_trades": 0,
            "open_trades": 0,
            "closed_trades": 0,
            "total_pnl": 0,
            "win_rate": 0.0,
        },
    }


def build_pool_state(timestamp: str) -> dict:
    """Fresh $3,000 pool matching default_pool_state() key order."""
    return {
        "pool": STARTING_POOL,
        "starting_pool": STARTING_POOL,
        "deployed": 0.0,
        "peak_pool": STARTING_POOL,
        "total_trades": 0,
        "winning_trades": 0,
        "open_positions": 0,
        "tranche3_only": 0,
        "eligible_for_reentry": [],
        "last_updated": timestamp,
    }


def write_json(path: Path, payload: dict) -> None:
    """Write a state file with the same indentation the trading code uses."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=JSON_INDENT), encoding="utf-8")


def main() -> None:
    """Reset both local state files to true Day-1 values and confirm writes."""
    started = now_et()
    timestamp = started.strftime(TIMESTAMP_FMT)
    stamp = started.strftime(BACKUP_STAMP_FMT)

    print("=" * 55)
    print("  Q-ALPHA | TRUE DAY-1 RESET")
    print(f"  {timestamp} ET")
    print("=" * 55)

    targets = [
        (PAPER_TRADES_PATH, build_paper_trades(timestamp)),
        (POOL_STATE_PATH, build_pool_state(timestamp)),
    ]

    for path, payload in targets:
        backup = backup_existing(path, stamp)
        if backup:
            print(f"Backed up  {path.name} -> {backup.relative_to(CANDIDATES_DIR)}")
        else:
            print(f"No existing {path.name} to back up")

        write_json(path, payload)
        print(f"WROTE      {path}")

    print("-" * 55)
    print("paper_trades.json : 0 trades, summary zeroed")
    print(f"pool_state.json   : pool=${STARTING_POOL:,.2f}, deployed=$0.00, "
          f"0 open positions")
    print(f"Runtime: {(now_et() - started).total_seconds():.2f} seconds")
    print("=" * 55)
    print("NOTE: Modal volume 'qalpha-state' is NOT updated by this script.")
    print("      Push both files with `modal volume put` to reset /state/.")


if __name__ == "__main__":
    main()
