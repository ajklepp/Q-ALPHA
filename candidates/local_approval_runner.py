# =============================================================================
# Q-ALPHA LOCAL APPROVAL RUNNER — 9:25 AM ET on Windows (TWS must be open)
# =============================================================================
#
# Syncs state from Modal volume → runs Telegram approval + IBKR bracket orders
# locally → pushes updated state back to Modal for the 4:15 PM EOD monitor.
#
# WINDOWS TASK SCHEDULER SETUP (run once):
# 1. Open Task Scheduler
# 2. Create Basic Task
# 3. Name: QAlpha Approval Runner
# 4. Trigger: Daily at 9:25 AM
# 5. Action: Start a program
#    Program: C:\Users\ajkle\Documents\Q-ALPHA\venv\Scripts\python.exe
#    Arguments: candidates/local_approval_runner.py
#    Start in: C:\Users\ajkle\Documents\Q-ALPHA
# 6. Finish
#
# Manual test:
#   cd C:\Users\ajkle\Documents\Q-ALPHA
#   venv\Scripts\python.exe candidates/local_approval_runner.py
# =============================================================================
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CANDIDATES = ROOT / "candidates"


def _run_modal_cmd(args: list[str]) -> None:
    """Run a modal CLI command from the repo root."""
    cmd = [sys.executable, "-m", "modal"] + args
    env = os.environ.copy()
    env.pop("MODAL_ENVIRONMENT", None)
    print(f"  $ {' '.join(cmd)}")
    subprocess.run(cmd, cwd=ROOT, check=True, env=env)


def pull_state_from_modal() -> None:
    """Step 1: Pull latest state from Modal volume."""
    print("STEP 1: Pulling state from Modal volume...")
    CANDIDATES.mkdir(parents=True, exist_ok=True)
    _run_modal_cmd([
        "volume", "get", "qalpha-state",
        "pending_approvals.json", str(CANDIDATES / "pending_approvals.json"),
        "--force",
    ])
    _run_modal_cmd([
        "volume", "get", "qalpha-state",
        "pool_state.json", str(CANDIDATES / "pool_state.json"),
        "--force",
    ])


def run_local_approval() -> None:
    """Step 2: Run approval processor locally with IBKR + local state paths."""
    print("STEP 2: Running approval processor locally...")
    os.environ["MODAL_ENVIRONMENT"] = "0"
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    pending_path = CANDIDATES / "pending_approvals.json"
    if pending_path.exists():
        import json
        from datetime import datetime, timezone

        data = json.loads(pending_path.read_text(encoding="utf-8"))
        expires_raw = data.get("expires", "")
        if expires_raw:
            expiry = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
            now_utc = datetime.now(timezone.utc)
            if now_utc > expiry:
                print(
                    f"  REJECTED: approvals expired at {expires_raw} "
                    f"(now {now_utc.isoformat()}). Refusing to process stale "
                    f"approvals to prevent phantom orders."
                )
                return
            print(f"  Approval window open until {expires_raw}")
        else:
            print("  REJECTED: pending_approvals.json has no 'expires' field. Refusing to process.")
            return

        candidates = data.get("candidates", [])
        if not candidates:
            print("  No pending candidates to process. Nothing to do.")
            return

    from candidates.paper_trader import run_approval_processor

    run_approval_processor()


def push_state_to_modal() -> None:
    """Step 3: Push updated state back to Modal volume."""
    print("STEP 3: Pushing state to Modal volume...")
    _run_modal_cmd([
        "volume", "put", "qalpha-state",
        str(CANDIDATES / "paper_trades.json"), "paper_trades.json",
        "--force",
    ])
    _run_modal_cmd([
        "volume", "put", "qalpha-state",
        str(CANDIDATES / "pool_state.json"), "pool_state.json",
        "--force",
    ])
    _run_modal_cmd([
        "volume", "put", "qalpha-state",
        str(CANDIDATES / "pending_approvals.json"), "pending_approvals.json",
        "--force",
    ])


def main() -> None:
    print("=" * 55)
    print("  Q-ALPHA | Local Approval Runner")
    print("=" * 55)
    pull_state_from_modal()
    run_local_approval()
    push_state_to_modal()
    print("✅ Local approval complete — state synced to Modal")


if __name__ == "__main__":
    main()
