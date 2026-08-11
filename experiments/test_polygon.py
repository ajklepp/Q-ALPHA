import os
from dotenv import load_dotenv
load_dotenv()

api_key = os.getenv("POLYGON_API_KEY")
if not api_key:
    print("ERROR: POLYGON_API_KEY not found in .env")
    exit()

print(f"API key loaded: {api_key[:8]}...")

from polygon import RESTClient
client = RESTClient(api_key)

print("\nTest 1: Fetching AAPL daily bars...")
bars = client.get_aggs(
    ticker="AAPL",
    multiplier=1,
    timespan="day",
    from_="2024-01-01",
    to="2024-01-10",
)
print(f"Received {len(bars)} bars")
for bar in bars[:3]:
    print(f"  Close: {bar.close}  Volume: {bar.volume}")

print("\nTest 2: Fetching ticker details...")
details = client.get_ticker_details("AAPL")
print(f"  Name: {details.name}")
print(f"  Exchange: {details.primary_exchange}")

print("\nPolygon API connection successful")
