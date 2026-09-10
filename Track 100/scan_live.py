"""
Read-only NDX100 scanner for Track100_v2 entry (OS<=-53 + early_bull).

Does not place orders. Does not touch Q-ALPHA live state.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytz

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from data import fetch_1h_bars, load_polygon_key  # noqa: E402
from universe import NDX100  # noqa: E402
from wave import enrich_wave, WT_OS  # noqa: E402

ET = pytz.timezone("America/New_York")


def main() -> int:
    key = load_polygon_key()
    end = datetime.now(ET).date()
    start = (end - timedelta(days=60)).isoformat()
    end_s = end.isoformat()
    hits = []
    print(f"Track100_v2 scan {end_s} (last completed 1H bar)")
    print(f"Rule: early_bull & wt_min <= {WT_OS}")
    print("-" * 56)
    for i, sym in enumerate(NDX100, 1):
        try:
            df = fetch_1h_bars(sym, start=start, end=end_s, api_key=key)
            if df is None or len(df) < 80:
                continue
            en = enrich_wave(df)
            # Last fully known bar
            row = en.iloc[-1]
            prev = en.iloc[-2] if len(en) > 1 else row
            # Prefer prior completed bar if last is still forming (hour not closed)
            now = datetime.now(ET)
            use = prev if (now - en.index[-1].to_pydatetime()).total_seconds() < 3500 else row
            ts = en.index[en.index.get_loc(use.name)] if False else (
                en.index[-2] if use is prev else en.index[-1]
            )
            # simpler: always evaluate last two bars for fresh signal
            for bar_i in (-2, -1):
                b = en.iloc[bar_i]
                if bool(b.get("early_bull")) and float(b["wt_min"]) <= WT_OS:
                    hits.append({
                        "symbol": sym,
                        "ts": str(en.index[bar_i]),
                        "close": float(b["close"]),
                        "wt1": float(b["wt1"]),
                        "wt2": float(b["wt2"]),
                    })
                    break
        except Exception as exc:
            print(f"  {sym}: err {exc}")
        if i % 25 == 0:
            print(f"  ... {i}/{len(NDX100)}")
    print("-" * 56)
    if not hits:
        print("No current Track100_v2 hits.")
        return 0
    print(f"{len(hits)} hit(s):")
    for h in hits:
        print(
            f"  {h['symbol']:5} {h['ts']} close={h['close']:.2f} "
            f"wt1={h['wt1']:.1f} wt2={h['wt2']:.1f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
