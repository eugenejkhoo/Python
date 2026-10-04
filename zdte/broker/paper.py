"""Paper broker: fills instantly against a Black-Scholes mark.

Buys fill at the ask and sells at the bid, so every round trip pays the
spread plus commission. This is deliberately pessimistic compared with a
mid-price fill assumption.
"""
from __future__ import annotations

import itertools
from datetime import datetime

from zdte.broker.base import Broker, Fill, OptionQuote, Position
from zdte.config import PaperConfig
from zdte.options import OptionContract, black_scholes, years_to_expiry


class PaperBroker(Broker):
    name = "paper"

    def __init__(self, cfg: PaperConfig, implied_vol: dict[str, float]):
        self.cfg = cfg
        self.iv = implied_vol
        self._cash = cfg.starting_cash
        self._positions: list[Position] = []
        self._ids = itertools.count(1)

    # -- pricing ---------------------------------------------------------
    def mark(self, contract: OptionContract, spot: float, now: datetime) -> float:
        iv = self.iv.get(contract.underlying, 0.20)
        t = years_to_expiry(now, contract.expiry)
        price = black_scholes(spot, contract.strike, t, iv, contract.option_type, self.cfg.risk_free_rate)
        return max(price, 0.01)

    def quote(self, contract: OptionContract, spot: float, now: datetime) -> OptionQuote:
        mid = self.mark(contract, spot, now)
        spread = max(self.cfg.min_spread, mid * self.cfg.spread_pct)
        bid = max(round(mid - spread / 2, 2), 0.01)
        ask = max(round(mid + spread / 2, 2), bid + 0.01)
        return OptionQuote(bid=bid, ask=ask, ts=now)

    # -- orders ----------------------------------------------------------
    def buy(self, owner: str, contract: OptionContract, qty: int, spot: float, now: datetime) -> Fill:
        q = self.quote(contract, spot, now)
        commission = self.cfg.commission_per_contract * qty
        cost = q.ask * 100.0 * qty + commission
        if cost > self._cash:
            raise RuntimeError(f"insufficient cash: need {cost:.2f}, have {self._cash:.2f}")
        self._cash -= cost
        pos = Position(owner=owner, contract=contract, qty=qty, entry_price=q.ask,
                       entry_ts=now, entry_commission=commission)
        self._positions.append(pos)
        return Fill(contract, "buy", qty, q.ask, commission, now, order_id=f"paper-{next(self._ids)}")

    def sell(self, position: Position, spot: float, now: datetime) -> Fill:
        if position not in self._positions:
            raise RuntimeError("position not held by this broker")
        q = self.quote(position.contract, spot, now)
        commission = self.cfg.commission_per_contract * position.qty
        self._cash += q.bid * 100.0 * position.qty - commission
        self._positions.remove(position)
        return Fill(position.contract, "sell", position.qty, q.bid, commission, now,
                    order_id=f"paper-{next(self._ids)}")

    def positions(self) -> list[Position]:
        return list(self._positions)

    def cash(self) -> float:
        return self._cash
