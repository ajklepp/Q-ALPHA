"""
Read-only probe of the laptop options bridge at 127.0.0.1:8787.

Writes bridge_probe.json. Does not place orders and does not compute P&L.
The bridge history default (about 10 days of 1-hour bars) is not a backfill
for the 2023–2025 ITM study. Modal cannot reach this loopback address.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

BRIDGE = "http://127.0.0.1:8787"
OUT = Path(__file__).resolve().parent / "bridge_probe.json"


def _get(path: str, timeout: float) -> dict:
    """GET one bridge path. Errors stay in the payload; no exception escapes."""
    try:
        with urlopen(BRIDGE + path, timeout=timeout) as response:
            raw = response.read(20000)
            return {"http": response.status, "body": raw.decode("utf-8", errors="replace")[:1500]}
    except URLError as exc:
        return {"error": f"URLError: {exc.reason}"[:240]}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"[:240]}


def main() -> None:
    """Record health and, if the socket answers, a SPY chain byte count."""
    payload = {
        "bridge": BRIDGE,
        "tws_paper": "127.0.0.1:7497",
        "client_id": 71,
        "orders_sent": False,
        "pnl_usd": None,
        "note": (
            "Read-only. A live chain or a short /v1/options/hist window is not "
            "the EXP-0026 backtest. Do not turn these bytes into fills."
        ),
        "health": _get("/v1/health", 3),
    }
    if "http" in payload["health"]:
        payload["chain"] = _get("/v1/options/chain?symbol=SPY", 25)
    OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"wrote": str(OUT), "pnl_usd": None, "health_error": payload["health"].get("error")}, indent=2))


if __name__ == "__main__":
    main()
