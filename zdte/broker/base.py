"""Broker interface shared by the paper broker and live adapters."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from zdte.options import OptionContract


@dataclass(frozen=True, slots=True)
class OptionQuote:
    bid: float
    ask: float
    ts: datetime

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


@dataclass(frozen=True, slots=True)
class Fill:
    contract: OptionContract
    side: str            # "buy" | "sell"
    qty: int
    price: float         # per share; contract value = price * 100
    commission: float
    ts: datetime
    order_id: str = ""

    @property
    def notional(self) -> float:
        return self.price * 100.0 * self.qty


@dataclass
class Position:
    owner: str                      # bot name
    contract: OptionContract
    qty: int
    entry_price: float
    entry_ts: datetime
    entry_commission: float = 0.0
    tags: dict = field(default_factory=dict)

    @property
    def cost_basis(self) -> float:
        return self.entry_price * 100.0 * self.qty + self.entry_commission

    def unrealized(self, bid: float) -> float:
        return bid * 100.0 * self.qty - self.cost_basis

    def pct_return(self, bid: float) -> float:
        if self.entry_price <= 0:
            return 0.0
        return (bid - self.entry_price) / self.entry_price

    def to_dict(self, bid: float | None = None) -> dict:
        d = {
            "owner": self.owner,
            "contract": self.contract.to_dict(),
            "qty": self.qty,
            "entry_price": self.entry_price,
            "entry_ts": self.entry_ts.isoformat(),
            "cost_basis": round(self.cost_basis, 2),
        }
        if bid is not None:
            d["bid"] = bid
            d["unrealized"] = round(self.unrealized(bid), 2)
            d["pct_return"] = round(self.pct_return(bid), 4)
        return d


class Broker:
    """Abstract broker. All prices are per-share option premiums."""

    name = "base"

    def quote(self, contract: OptionContract, spot: float, now: datetime) -> OptionQuote:
        raise NotImplementedError  # pragma: no cover

    def buy(self, owner: str, contract: OptionContract, qty: int, spot: float, now: datetime) -> Fill:
        raise NotImplementedError  # pragma: no cover

    def sell(self, position: Position, spot: float, now: datetime) -> Fill:
        raise NotImplementedError  # pragma: no cover

    def positions(self) -> list[Position]:
        raise NotImplementedError  # pragma: no cover

    def cash(self) -> float:
        raise NotImplementedError  # pragma: no cover
