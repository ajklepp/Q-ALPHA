"""
Q-Alpha IBKR order connector.

Connects to Interactive Brokers TWS via ib_insync and places bracket orders
matching Q-Alpha paper trade specs (MOC entry + stop + 2R take profit).

Standalone only — not wired into the Modal scheduler yet.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytz

CANDIDATES_DIR = Path(__file__).resolve().parent
if str(CANDIDATES_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATES_DIR))


class IBKRConnector:
    """TWS connection and bracket order placement for Q-Alpha order plans."""

    PAPER_PORT = 7497
    LIVE_PORT = 7496
    HOST = "127.0.0.1"
    CLIENT_ID = 1

    def __init__(self):
        self.ib = None
        self._paper = True

    def connect(self, paper: bool = True) -> bool:
        """Connect to TWS. paper=True uses port 7497."""
        from ib_insync import IB

        self._paper = paper
        self.ib = IB()
        port = self.PAPER_PORT if paper else self.LIVE_PORT
        self.ib.connect(self.HOST, port, clientId=self.CLIENT_ID)
        print(f"Connected to IBKR {'Paper' if paper else 'LIVE'}")
        accounts = getattr(self.ib.wrapper, "accounts", None) or self.ib.managedAccounts()
        print(f"Account: {accounts}")
        return self.ib.isConnected()

    def disconnect(self) -> None:
        """Disconnect from TWS if connected."""
        if self.ib is not None and self.ib.isConnected():
            self.ib.disconnect()

    def _is_market_hours(self) -> bool:
        """Returns True if current time is during NYSE market hours."""
        et = pytz.timezone("America/New_York")
        now = datetime.now(et)
        market_open = now.replace(hour=9, minute=30, second=0)
        market_close = now.replace(hour=15, minute=58, second=0)
        is_weekday = now.weekday() < 5
        return is_weekday and market_open <= now <= market_close

    def place_bracket_order(self, order_plan: dict) -> dict:
        """
        Place a bracket order in TWS.

        Bracket = 3 linked orders:
          Parent:      MOC buy order (Market On Close)
          Stop loss:   Stop order below entry
          Take profit: Limit order at 2R target

        order_plan must include: ticker, shares, stop_price, target_2r.
        Returns order IDs and status.
        """
        if self.ib is None or not self.ib.isConnected():
            raise RuntimeError("Not connected to TWS — call connect() first")

        from ib_insync import LimitOrder, MarketOrder, Stock, StopOrder

        ticker = order_plan["ticker"]
        shares = int(order_plan["shares"])
        stop = float(order_plan["stop_price"])
        target_2r = float(order_plan["target_2r"])

        if shares <= 0:
            raise ValueError(f"Invalid share count for {ticker}: {shares}")

        contract = Stock(ticker, "SMART", "USD")
        self.ib.qualifyContracts(contract)

        tif = "MOC" if self._is_market_hours() else "DAY"
        print(f"Order type: {tif} ({'market hours' if tif == 'MOC' else 'after hours test'})")

        parent = MarketOrder(
            action="BUY",
            totalQuantity=shares,
            tif=tif,
            transmit=False,
        )
        parent_trade = self.ib.placeOrder(contract, parent)
        parent_id = parent_trade.order.orderId

        stop_loss = StopOrder(
            action="SELL",
            totalQuantity=shares,
            stopPrice=round(stop, 2),
            parentId=parent_id,
            tif="GTC",
            transmit=False,
        )
        self.ib.placeOrder(contract, stop_loss)

        take_profit = LimitOrder(
            action="SELL",
            totalQuantity=shares,
            lmtPrice=round(target_2r, 2),
            parentId=parent_id,
            tif="GTC",
            transmit=True,
        )
        self.ib.placeOrder(contract, take_profit)

        self.ib.sleep(1)

        return {
            "ticker": ticker,
            "parent_id": parent_id,
            "shares": shares,
            "stop": stop,
            "target": target_2r,
            "status": "SUBMITTED",
            "paper": self._paper,
        }

    def get_positions(self) -> list[dict]:
        """Return all current open positions in TWS."""
        if self.ib is None or not self.ib.isConnected():
            raise RuntimeError("Not connected to TWS — call connect() first")

        positions = self.ib.positions()
        return [
            {
                "ticker": p.contract.symbol,
                "shares": p.position,
                "avg_cost": p.avgCost,
            }
            for p in positions
        ]

    def get_account_value(self) -> float:
        """Return current net liquidation value in USD."""
        if self.ib is None or not self.ib.isConnected():
            raise RuntimeError("Not connected to TWS — call connect() first")

        account_values = self.ib.accountValues()
        for av in account_values:
            if av.tag == "NetLiquidation" and av.currency == "USD":
                return float(av.value)
        return 0.0


def _run_standalone_test() -> None:
    """Connect to TWS paper, print account state, place one test bracket."""
    connector = IBKRConnector()

    print("=" * 55)
    print("  Q-ALPHA | IBKR Connector Test")
    print("=" * 55)

    if not connector.connect(paper=True):
        print("ERROR: Could not connect to TWS paper (port 7497)")
        print("Ensure TWS is open, API enabled, and paper account is logged in.")
        return

    try:
        net_liq = connector.get_account_value()
        print(f"Net liquidation: ${net_liq:,.2f}")

        positions = connector.get_positions()
        if positions:
            print("Open positions:")
            for pos in positions:
                print(f"  {pos['ticker']}: {pos['shares']} @ ${pos['avg_cost']:.2f}")
        else:
            print("Open positions: none")

        test_plan = {
            "ticker": "SMCI",
            "shares": 8,
            "stop_price": 31.82,
            "target_2r": 38.33,
        }
        print(f"\nPlacing test bracket: {test_plan}")
        result = connector.place_bracket_order(test_plan)
        print("Order confirmation:")
        for key, value in result.items():
            print(f"  {key}: {value}")
    finally:
        connector.disconnect()
        print("\nDisconnected from TWS")


if __name__ == "__main__":
    _run_standalone_test()
