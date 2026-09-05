"""
Probe TWS/IB Gateway for news access already on this account.

Connects paper TWS (7497), lists news providers, tries historical news
on a liquid name (AAPL) and one small/mid name if given.

Usage (TWS open, API enabled):
  py -3 candidates/tsd_scan_pipeline/probe_tws_news.py
  py -3 candidates/tsd_scan_pipeline/probe_tws_news.py --symbol IREN
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ib_insync import IB, Stock, util

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "candidates"))

HOST = "127.0.0.1"
PORT = 7497
CLIENT_ID = 88


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()

    util.startLoop()
    ib = IB()
    print(f"Connecting {HOST}:{args.port} clientId={CLIENT_ID}…")
    try:
        ib.connect(HOST, args.port, clientId=CLIENT_ID, timeout=12)
    except Exception as exc:
        print(f"CONNECT FAIL: {exc}")
        print("Open TWS paper, enable API on 7497, retry.")
        return 1

    print(f"CONNECTED. accounts={ib.managedAccounts()}")

    # 1) News providers subscribed / available
    print("\n=== reqNewsProviders ===")
    try:
        providers = ib.reqNewsProviders()
        time.sleep(1.0)
        if not providers:
            print("  (empty) — no news providers returned")
        for p in providers or []:
            print(f"  code={getattr(p,'code',None)}  name={getattr(p,'providerName',None)}")
        provider_codes = ",".join(
            str(getattr(p, "code", "") or "") for p in (providers or []) if getattr(p, "code", None)
        )
    except Exception as exc:
        print(f"  FAIL: {exc}")
        providers = []
        provider_codes = ""

    # 2) Broad bulletins (exchange notices — not ticker news)
    print("\n=== reqNewsBulletins ===")
    try:
        ib.reqNewsBulletins(allMessages=True)
        time.sleep(1.5)
        bullets = list(getattr(ib, "newsBulletins", lambda: [])() or [])
        # ib_insync stores via newsBulletins() method sometimes
        try:
            bullets = ib.newsBulletins()
        except Exception:
            bullets = []
        if not bullets:
            print("  (none active)")
        for b in bullets[:10]:
            print(f"  {b}")
        ib.cancelNewsBulletins()
    except Exception as exc:
        print(f"  FAIL: {exc}")

    # 3) Historical news for symbol
    sym = str(args.symbol).upper()
    print(f"\n=== reqHistoricalNews ({sym}) ===")
    try:
        contract = Stock(sym, "SMART", "USD")
        ib.qualifyContracts(contract)
        print(f"  conId={contract.conId}")
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=5)
        # Probe each provider alone so we see which (if any) are subscribed
        codes_list = [str(getattr(p, "code", "") or "") for p in (providers or []) if getattr(p, "code", None)]
        if not codes_list:
            codes_list = ["BRFG", "BRFUPDN", "DJNL"]
        any_hits = False
        for code in codes_list:
            print(f"  try provider={code}")
            try:
                headlines = ib.reqHistoricalNews(
                    contract.conId,
                    code,
                    start.strftime("%Y-%m-%d %H:%M:%S"),
                    end.strftime("%Y-%m-%d %H:%M:%S"),
                    10,
                )
                time.sleep(1.2)
            except Exception as exc:
                print(f"    FAIL: {exc}")
                continue
            if not headlines:
                print("    (empty / not subscribed)")
                continue
            any_hits = True
            for h in headlines[:5]:
                print(
                    f"    {getattr(h,'time',None)} | {getattr(h,'providerCode',None)} | "
                    f"{getattr(h,'headline',None)}"
                )
        if not any_hits:
            print("  RESULT: no subscribed news providers returned headlines.")
    except Exception as exc:
        print(f"  FAIL: {exc}")

    print("\nDone.")
    ib.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
