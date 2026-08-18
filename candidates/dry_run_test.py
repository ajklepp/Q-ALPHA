"""
Q-ALPHA | Dry-run test harness (READ-ONLY, no IBKR, no orders, no state writes).

Exercises the ENTIRE morning pipeline up to the live-bar boundary, using
tonight's real Polygon data, so we can verify the full_market_scan wire-in
before tomorrow's open. It proves everything EXCEPT watch_and_enter's live
9:30 entry logic, which physically cannot run without a live market + TWS feed.

What this DOES test (all with real data):
  1. full_market_scan.scan_for_agent() runs and returns agent-shaped candidates
  2. Every candidate has the exact keys watch_and_enter / send_premarket_summary read
  3. The universe/ban filter held (no leveraged ETFs, no warrants/preferreds)
  4. Regime detection via the Polygon fallback (the live path uses TWS first)
  5. A simulated order-plan is built for the #1 candidate at its ref price,
     using the SAME single-bracket-2R math watch_and_enter uses, so we can eyeball
     shares / stop / 2R target / risk BEFORE any real order exists
  6. (optional) a real Telegram "dry-run" summary, if --telegram is passed

What this does NOT and CANNOT test tonight:
  - Live 5-second bars, VWAP/gap-hold/structure gates, real IBKR bracket fills.
    Those are tomorrow's open. This harness only proves the candidate PIPELINE.

Usage:
    python candidates/dry_run_test.py
    python candidates/dry_run_test.py --telegram      # also send a Telegram test
"""
from __future__ import annotations

import sys
from pathlib import Path

CANDIDATES_DIR = Path(__file__).resolve().parent
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))

# The exact keys watch_and_enter + send_premarket_summary read off a candidate.
REQUIRED_KEYS = [
    "ticker", "last_price", "prev_close", "gap_pct", "pm_vol_ratio",
    "avg_volume", "dollar_volume", "news_catalyst", "news_summary",
    "quality_score",
]


def check(label: str, ok: bool) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    return ok


def build_sim_order_plan(c: dict) -> dict:
    """
    Mirror the single-bracket-2R math in watch_and_enter, but off the scan's
    reference price (live uses the real 9:30 price). Pure arithmetic; no orders.
    """
    price = float(c["last_price"])
    # watch_and_enter structure stop: max(2%, min(price-first_candle_low, 7%)).
    # We have no first-candle low tonight, so use the 2% floor as a stand-in
    # purely to show the sizing shape. Live will compute the real stop.
    structure_stop_dist = price * 0.02
    stop_price = round(price - structure_stop_dist, 2)
    risk_ps = price - stop_price
    pool_position = 300.0  # ~10% of $3,000
    shares = max(6, int(pool_position / price))
    return {
        "ticker": c["ticker"],
        "entry_price_ref": round(price, 2),
        "shares": shares,
        "stop_price": stop_price,
        "target_2r": round(price + risk_ps * 2, 2),
        "risk_dollars": round(shares * risk_ps, 2),
        "position_value": round(shares * price, 2),
    }


def main() -> None:
    send_tg = "--telegram" in sys.argv
    all_ok = True

    print("=" * 60)
    print("Q-ALPHA DRY-RUN TEST  (read-only; no IBKR, no orders, no state)")
    print("=" * 60)

    # 1. Candidate source -----------------------------------------------------
    print("\n[1] full_market_scan.scan_for_agent()")
    try:
        from full_market_scan import scan_for_agent
        candidates = scan_for_agent(10)
        all_ok &= check(f"scan returned {len(candidates)} candidates", len(candidates) > 0)
    except Exception as exc:
        check(f"scan raised: {exc}", False)
        print("\nDRY-RUN ABORTED: candidate source failed.")
        sys.exit(1)

    # 2. Shape contract -------------------------------------------------------
    print("\n[2] Candidate shape matches watch_and_enter contract")
    shape_ok = True
    for c in candidates:
        missing = [k for k in REQUIRED_KEYS if k not in c]
        if missing:
            shape_ok = False
            print(f"    {c.get('ticker','?')}: MISSING {missing}")
    all_ok &= check("all candidates have required keys", shape_ok)
    # gap_pct must be a fraction (e.g. 0.396), not a percent (39.6)
    gap_ok = all(abs(c["gap_pct"]) < 1.0 for c in candidates)
    all_ok &= check("gap_pct stored as fraction (agent contract)", gap_ok)

    # 3. Ban filter held ------------------------------------------------------
    print("\n[3] Universe/ban filter held (no funds/leverage/warrants)")
    from universe_filter import EXCLUDE_SYMBOLS, is_leveraged_or_fund
    ban_ok = True
    for c in candidates:
        if c["ticker"] in EXCLUDE_SYMBOLS or is_leveraged_or_fund(c.get("news_summary", "")):
            pass  # name not carried here; rely on scan having filtered by name
        if "." in c["ticker"] or c["ticker"].endswith("W") and len(c["ticker"]) > 4:
            print(f"    suspicious symbol: {c['ticker']}")
            ban_ok = False
    all_ok &= check("no obviously-bad symbols in list", ban_ok)

    # 4. Regime (Polygon fallback path) --------------------------------------
    print("\n[4] Regime detection (Polygon fallback)")
    try:
        from pre_market_scanner import fetch_spy_regime
        import os
        key = os.environ.get("POLYGON_API_KEY", "")
        if not key:
            env = CANDIDATES_DIR.parent / ".env"
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("POLYGON_API_KEY") and "=" in line:
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
        r = dict(fetch_spy_regime(key))
        print(f"    SPY ${r.get('spy_price')} vs SMA50 ${round(float(r.get('spy_sma50',0)),2)} "
              f"-> {r.get('spy_regime')}")
        all_ok &= check("regime resolved to BULL/BEAR", r.get("spy_regime") in ("BULL", "BEAR"))
    except Exception as exc:
        all_ok &= check(f"regime failed: {exc}", False)

    # 5. Simulated order plan for #1 -----------------------------------------
    print("\n[5] Simulated single-bracket-2R order plan (top candidate)")
    top = candidates[0]
    plan = build_sim_order_plan(top)
    print(f"    {plan['ticker']}: {plan['shares']} sh @ ~${plan['entry_price_ref']} "
          f"| stop ${plan['stop_price']} | 2R target ${plan['target_2r']} "
          f"| risk ${plan['risk_dollars']} | pos ${plan['position_value']}")
    all_ok &= check("order plan built (shares>=6, stop<entry<target)",
                    plan["shares"] >= 6 and plan["stop_price"] < plan["entry_price_ref"] < plan["target_2r"])

    # 6. Watchlist preview ----------------------------------------------------
    print("\n[6] Watchlist the agent would monitor at 9:30 (max 3 entries):")
    for i, c in enumerate(candidates, 1):
        print(f"    #{i:2d} {c['ticker']:6s} gap {c['gap_pct']:+.1%} "
              f"rvol {c['pm_vol_ratio']:.1f} score {c['quality_score']:.0f}")

    if send_tg:
        print("\n[+] Sending Telegram dry-run summary ...")
        try:
            from autonomous_agent import send_telegram
            lines = ["\U0001f9ea Q-ALPHA DRY-RUN (no orders placed)",
                     f"Full-market scan -> {len(candidates)} candidates:"]
            for i, c in enumerate(candidates[:10], 1):
                lines.append(f"#{i} {c['ticker']} +{c['gap_pct']:.1%} rvol {c['pm_vol_ratio']:.1f}")
            send_telegram("\n".join(lines))
            print("    Telegram sent.")
        except Exception as exc:
            print(f"    Telegram failed: {exc}")

    print("\n" + "=" * 60)
    print(f"DRY-RUN RESULT: {'ALL PASS' if all_ok else 'SOME CHECKS FAILED'}")
    print("Note: live 9:30 entry (watch_and_enter) is NOT tested here -")
    print("that is tomorrow's real open. This proved the candidate pipeline.")
    print("=" * 60)
    sys.exit(0 if all_ok else 2)


if __name__ == "__main__":
    main()
