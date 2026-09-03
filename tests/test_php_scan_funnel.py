"""Unit: Peak Hour scan funnel shape + caption (no live scan)."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "candidates"))

from tsd_scan_pipeline.php_scan_funnel import (  # noqa: E402
    RESULTS_DIR,
    build_reject_summary,
    build_scan_funnel_doc,
    funnel_caption,
    write_scan_funnel,
)

ET = pytz.timezone("America/New_York")


def test_reject_summary_histogram() -> None:
    rows = [
        {"symbol": "AAA", "pass": False, "reject_reason": "no_1h_buy"},
        {"symbol": "BBB", "pass": False, "reject_reason": "no_1h_buy"},
        {"symbol": "CCC", "pass": False, "reject_reason": "extension_phase"},
        {"symbol": "DDD", "pass": False, "reject_reason": "hour_not_allowed:11"},
        {"symbol": "HPE", "pass": True},
    ]
    hist, samples = build_reject_summary(rows)
    assert hist["no_1h_buy"] == 2
    assert hist["extension_phase"] == 1
    assert hist["hour_not_allowed"] == 1
    assert len(samples["no_1h_buy"]) == 2


def test_write_0715_style_artifact() -> None:
    """Shape that today's 07:15 scan should leave (HTF=310, 1 launch, HPE entered)."""
    now = ET.localize(datetime(2026, 9, 3, 7, 15))
    all_rows = [
        {"symbol": "HPE", "pass": True, "htf_1h_bar_hour": 7, "htf_1h_close": 49.9,
         "htf_score": 88, "launch_score": 50, "phase_3h": "LAUNCH"},
    ]
    # Pad rejects to look like a real HTF universe slice
    for i in range(309):
        all_rows.append({
            "symbol": f"R{i}",
            "pass": False,
            "reject_reason": "no_1h_buy" if i % 3 else "extension_phase",
            "htf_1h_bar_hour": 7,
        })
    ranked = [all_rows[0]]
    take = ranked
    doc = build_scan_funnel_doc(
        now_et=now,
        bar_source="polygon_1h",
        hours=(7, 11, 12, 13),
        htf_pass_count=310,
        symbols_scanned=310,
        all_rows=all_rows,
        ranked=ranked,
        take=take,
        queue_results=[{"symbol": "HPE", "status": "WATCHING"}],
        entry_results=[{
            "symbol": "HPE", "status": "FILLED", "shares": 4,
            "fill_price": 49.9, "kind": "NEW",
        }],
        runtime_sec=42.0,
        live=True,
    )
    assert doc["htf_pass_count"] == 310
    assert doc["launches_n"] == 1
    assert doc["entered_n"] == 1
    assert doc["entered"][0]["symbol"] == "HPE"
    assert doc["reject_summary"]
    path = write_scan_funnel(doc, now_et=now)
    assert path.exists()
    assert path.name.startswith("php_scan_20260903_0715")
    nd = RESULTS_DIR / "php_funnel_20260903.ndjson"
    assert nd.exists()
    cap = funnel_caption(doc)
    assert cap == "HTF 310 · launches 1 · entered 1"
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["entered"][0]["symbol"] == "HPE"
    print(f"OK example artifact: {path}")


if __name__ == "__main__":
    test_reject_summary_histogram()
    test_write_0715_style_artifact()
    print("OK php_scan_funnel")
