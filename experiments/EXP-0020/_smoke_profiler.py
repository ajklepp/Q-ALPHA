"""EXP-0020 local smoke: 3 build_ticker_profile calls + Modal import diagnosis."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "candidates"))

# Load .env without printing secrets
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("POLYGON_API_KEY=") and "POLYGON_API_KEY" not in os.environ:
            os.environ["POLYGON_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")

key = os.environ.get("POLYGON_API_KEY")
print("POLYGON_API_KEY present:", bool(key))

# ZoneInfo check (Modal debian_slim often lacks tzdata)
try:
    from zoneinfo import ZoneInfo

    ZoneInfo("America/New_York")
    print("ZoneInfo America/New_York: OK")
except Exception as exc:
    print(f"ZoneInfo America/New_York: FAIL {type(exc).__name__}: {exc}")

import ticker_profiler as tp

print("ticker_profiler import: OK", "MIN_ANALOGS_USABLE=", tp.MIN_ANALOGS_USABLE)

# Known liquid names likely in SMID screener history
CASES = [
    ("MARA", "2024-06-03"),
    ("RIOT", "2024-03-15"),
    ("SMCI", "2024-01-19"),
]

eligible = 0
for ticker, as_of in CASES:
    t0 = time.time()
    try:
        profile = tp.build_ticker_profile(ticker, as_of_date=as_of, api_key=key)
        outs = profile.get("outcomes") or {}
        conf = profile.get("confidence")
        n = profile.get("analog_count")
        meaningful = profile.get("stats_meaningful")
        rr = outs.get("reward_risk")
        ok = bool(meaningful) and conf != "INSUFFICIENT"
        if ok:
            eligible += 1
        print(
            f"{ticker} {as_of}: conf={conf} n={n} meaningful={meaningful} "
            f"rr={rr} eligible={ok} sec={time.time() - t0:.1f}"
        )
        if not ok:
            print(
                f"  finder_n={profile.get('n_analogs_finder')} "
                f"measured={profile.get('n_analogs_measured')} "
                f"flag={profile.get('flag')} lookback={profile.get('actual_lookback_days')}"
            )
    except Exception as exc:
        print(
            f"{ticker} {as_of}: ERROR {type(exc).__name__}: {exc} "
            f"sec={time.time() - t0:.1f}"
        )

print(f"SMOKE eligible {eligible}/{len(CASES)}")
print("PASS" if eligible >= 1 else "FAIL — need diagnosis")
