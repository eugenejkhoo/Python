"""Alpaca options adapter (REST, market orders).

This adapter is provided as a starting point for routing real orders. It
has NOT been exercised against a live account in this repository. Before
using it with real money:

  * run it against Alpaca's paper endpoint (``alpaca.paper = true``) first,
  * confirm your account has options trading enabled (level 2+),
  * read Alpaca's current API docs; endpoints and field names change.

Credentials are read from the environment variables named in config
(``ALPACA_API_KEY`` / ``ALPACA_API_SECRET`` by default).
"""
from __future__ import annotations

import os
from datetime import datetime

from zdte.broker.base import Broker, Fill, OptionQuote, Position
from zdte.config import AlpacaConfig
from zdte.options import OptionContract

try:  # optional dependency
    import requests
except ImportError:  # pragma: no cover
    requests = None


class AlpacaBroker(Broker):
    name = "alpaca"

    def __init__(self, cfg: AlpacaConfig, commission_per_contract: float = 0.0):
        if requests is None:
            raise RuntimeError("the 'requests' package is required for the Alpaca broker")
        key = os.environ.get(cfg.key_env)
        secret = os.environ.get(cfg.secret_env)
        if not key or not secret:
            raise RuntimeError(f"set {cfg.key_env} and {cfg.secret_env} in the environment")
        self.cfg = cfg
        self.commission = commission_per_contract
        self.trading_url = "https://paper-api.alpaca.markets" if cfg.paper else "https://api.alpaca.markets"
        self.data_url = "https://data.alpaca.markets"
        self.session = requests.Session()
        self.session.headers.update({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
        self._positions: list[Position] = []

    def _get(self, base: str, path: str, **params):
        r = self.session.get(base + path, params=params, timeout=15)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict):
        r = self.session.post(self.trading_url + path, json=body, timeout=15)
        r.raise_for_status()
        return r.json()

    def quote(self, contract: OptionContract, spot: float, now: datetime) -> OptionQuote:
        data = self._get(self.data_url, "/v1beta1/options/quotes/latest", symbols=contract.occ_symbol)
        q = data["quotes"][contract.occ_symbol]
        return OptionQuote(bid=float(q["bp"]), ask=float(q["ap"]), ts=now)

    def _market_order(self, symbol: str, side: str, qty: int) -> dict:
        order = self._post("/v2/orders", {
            "symbol": symbol, "qty": str(qty), "side": side,
            "type": "market", "time_in_force": "day",
        })
        # Poll briefly for the fill price.
        for _ in range(20):
            o = self._get(self.trading_url, f"/v2/orders/{order['id']}")
            if o.get("status") == "filled":
                return o
            import time
            time.sleep(0.5)
        raise RuntimeError(f"order {order['id']} not filled: {o.get('status')}")

    def buy(self, owner: str, contract: OptionContract, qty: int, spot: float, now: datetime) -> Fill:
        o = self._market_order(contract.occ_symbol, "buy", qty)
        price = float(o["filled_avg_price"])
        commission = self.commission * qty
        pos = Position(owner=owner, contract=contract, qty=qty, entry_price=price,
                       entry_ts=now, entry_commission=commission)
        self._positions.append(pos)
        return Fill(contract, "buy", qty, price, commission, now, order_id=o["id"])

    def sell(self, position: Position, spot: float, now: datetime) -> Fill:
        o = self._market_order(position.contract.occ_symbol, "sell", position.qty)
        price = float(o["filled_avg_price"])
        commission = self.commission * position.qty
        if position in self._positions:
            self._positions.remove(position)
        return Fill(position.contract, "sell", position.qty, price, commission, now, order_id=o["id"])

    def positions(self) -> list[Position]:
        return list(self._positions)

    def cash(self) -> float:
        acct = self._get(self.trading_url, "/v2/account")
        return float(acct["cash"])
