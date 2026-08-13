import json
from pathlib import Path

filepath = Path("candidates/paper_trades.json")
data = json.load(open(filepath, encoding="utf-8"))

trades = data["trades"]

# Step 1: Remove all SKIPPED trades
trades = [t for t in trades if t.get("status") != "SKIPPED"]

# Step 2: For each ticker keep only highest ibkr_order_id
seen = {}
for trade in trades:
    ticker = trade["ticker"]
    order_id = trade.get("ibkr_order_id") or 0
    if ticker not in seen or order_id > (seen[ticker].get("ibkr_order_id") or 0):
        seen[ticker] = trade

trades = list(seen.values())

# Step 3: Update summary
data["trades"] = trades
data["summary"]["total_trades"] = len(trades)
data["summary"]["open_trades"] = len([
    t for t in trades if t["status"] not in ("CLOSED", "SKIPPED")
])

with open(filepath, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)

print("Cleaned trades:")
for t in trades:
    print(f"  {t['ticker']} {t['status']} order_id={t.get('ibkr_order_id')}")
print("Done.")
