"""Sanity: print TSLA OS+green signals in study window (should near chart circles)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backtest import setup_catalog, collect_signals  # noqa: E402
from data import fetch_1h_bars, load_polygon_key, study_window  # noqa: E402
from wave import enrich_wave  # noqa: E402


def main() -> None:
    key = load_polygon_key()
    fetch_start, signal_start, signal_end = study_window(months=3, warmup_days=45)
    df = fetch_1h_bars("TSLA", start=fetch_start, end=signal_end, api_key=key)
    en = enrich_wave(df)
    print(f"TSLA bars={len(en)} window {signal_start}->{signal_end}")
    for spec in setup_catalog():
        if spec.name not in ("B_os50_green", "D_trough_os_green", "G_os_green_uptrend", "A_green_any"):
            continue
        idxs = collect_signals(en, spec, signal_start=signal_start, signal_end=signal_end)
        print(f"\n{spec.name} n={len(idxs)}")
        for i in idxs[-12:]:
            row = en.iloc[i]
            print(
                f"  {en.index[i]} close={row['close']:.2f} "
                f"wt1={row['wt1']:.1f} wt2={row['wt2']:.1f} "
                f"buy={bool(row['buy_signal'])} early={bool(row['early_bull'])}"
            )


if __name__ == "__main__":
    main()
