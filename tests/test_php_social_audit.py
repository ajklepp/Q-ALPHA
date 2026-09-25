"""Social fields survive Peak Hour scan JSON, and the selection filter is explicit."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "candidates"))

# Launch-scan import pulls ib_insync. Stub it so this audit test stays offline.
import types  # noqa: E402

if "ib_insync" not in sys.modules:
    _ib = types.ModuleType("ib_insync")
    for _name in ("IB", "LimitOrder", "MarketOrder", "StopLimitOrder", "Stock", "util", "Order"):
        setattr(_ib, _name, type(_name, (), {}))
    sys.modules["ib_insync"] = _ib

from tsd_scan_pipeline.php_scan_funnel import build_scan_funnel_doc  # noqa: E402
from tsd_scan_pipeline.php_social_recent_study import (  # noqa: E402
    outcome_label,
    would_skip,
)
from tsd_scan_pipeline.tsd_1h_launch_scan import _write_launch_artifact  # noqa: E402
from tsd_scan_pipeline.tsd_social import social_audit_fields  # noqa: E402

ET = pytz.timezone("America/New_York")


def test_audit_fields_null_when_never_attached() -> None:
    snap = social_audit_fields({"symbol": "AAA"})
    assert snap["news_velocity_24h"] is None
    assert snap["social_missing"] is None
    assert snap["st_bull_ratio"] is None


def test_funnel_keeps_social_fields() -> None:
    now = ET.localize(datetime(2026, 9, 23, 10, 15))
    ranked = [{
        "symbol": "NUAI",
        "pass": True,
        "htf_1h_bar_hour": 10,
        "htf_1h_close": 7.0,
        "combined_rank_score": 80,
        "news_velocity_24h": 3.0,
        "st_msg_24h": 12.0,
        "st_bull_ratio": 0.7,
        "st_ok": 1,
        "social_missing": 0,
        "dilution_flag": 0,
        "distress_flag": 0,
        "x_ok": 0,
    }]
    doc = build_scan_funnel_doc(
        now_et=now,
        bar_source="polygon_1h",
        hours=(10,),
        htf_pass_count=1,
        symbols_scanned=1,
        all_rows=ranked,
        ranked=ranked,
        take=ranked,
        live=True,
    )
    launch = doc["launches"][0]
    assert launch["news_velocity_24h"] == 3.0
    assert launch["st_ok"] == 1
    assert launch["social_missing"] == 0
    assert launch["x_ok"] == 0
    assert doc["social_audit"]["news_gt0"] == 1
    assert doc["social_audit"]["rows_with_social_fields"] == 1


def test_launch_artifact_keeps_social_fields() -> None:
    import tsd_scan_pipeline.tsd_1h_launch_scan as scan

    dest = scan.PIPELINE_DIR / "results" / "_tmp_social_launch.json"
    old = scan.LAUNCH_CACHE_PATH
    scan.LAUNCH_CACHE_PATH = dest
    now = ET.localize(datetime(2026, 9, 23, 11, 15))
    ranked = [{
        "symbol": "GENB",
        "htf_1h_bar_hour": 11,
        "news_velocity_24h": 0.0,
        "social_missing": 1,
        "st_msg_24h": 0.0,
        "dilution_flag": 1,
    }]
    try:
        _write_launch_artifact(now_et=now, ranked=ranked, take=[])
        payload = json.loads(dest.read_text(encoding="utf-8"))
    finally:
        scan.LAUNCH_CACHE_PATH = old
        if dest.exists():
            dest.unlink()
    row = payload["rows"][0]
    assert row["social_missing"] == 1
    assert row["dilution_flag"] == 1
    assert row["news_velocity_24h"] == 0.0
    assert row["st_bull_ratio"] is None


def test_selection_filter_does_not_size() -> None:
    assert would_skip({"dilution_flag": 1, "news_velocity_24h": 4}) == "dilution_flag"
    assert would_skip({"distress_flag": 1}) == "distress_flag"
    assert would_skip({"social_missing": 1, "news_velocity_24h": 0, "st_msg_24h": 0}) == "social_missing"
    assert would_skip({
        "social_missing": 0,
        "news_velocity_24h": 0,
        "st_msg_24h": 0,
        "st_ok": 1,
    }) == "low_news_and_st"
    assert would_skip({
        "social_missing": 0,
        "news_velocity_24h": 2,
        "st_msg_24h": 0,
    }) == ""
    assert outcome_label(mfe=0.03, close_ret=-0.01) == "mover"
    assert outcome_label(mfe=0.005, close_ret=-0.02) == "dud"
    assert outcome_label(mfe=None) == "unknown"
