"""Entry strategies. Each turns a Signal into a Decision.

* ``OGStrategy``       - the original rule set: any one trigger (VWAP cross,
                         EMA cross or opening-range breakout) is enough,
                         provided the market is not choppy.
* ``TrendingStrategy`` - stricter: needs a trigger *and* all three biases
                         agreeing with it, i.e. price on the same side of
                         VWAP, the 50 EMA and the opening range.
"""
from __future__ import annotations

from dataclasses import dataclass

from zdte.signals import Direction, Signal


@dataclass(frozen=True)
class Decision:
    direction: Direction
    reason: str

    @property
    def is_trade(self) -> bool:
        return self.direction != Direction.NONE

    @property
    def option_type(self) -> str:
        return "C" if self.direction == Direction.BULL else "P"


NO_TRADE = Decision(Direction.NONE, "")


class Strategy:
    name: str = "base"

    def decide(self, sig: Signal) -> Decision:  # pragma: no cover - interface
        raise NotImplementedError

    @staticmethod
    def _gate(sig: Signal) -> Decision | None:
        if not sig.ready:
            return Decision(Direction.NONE, "warming up")
        if sig.choppy:
            return Decision(Direction.NONE, f"chop: {sig.chop_reason}")
        return None


class OGStrategy(Strategy):
    name = "og"

    def decide(self, sig: Signal) -> Decision:
        blocked = self._gate(sig)
        if blocked:
            return blocked
        d = sig.trigger_direction
        if d == Direction.NONE:
            return Decision(Direction.NONE, "no trigger" if not sig.triggers else "mixed triggers")
        return Decision(d, ", ".join(sig.triggers))


class TrendingStrategy(Strategy):
    name = "trending"

    def decide(self, sig: Signal) -> Decision:
        blocked = self._gate(sig)
        if blocked:
            return blocked
        d = sig.trigger_direction
        if d == Direction.NONE:
            return Decision(Direction.NONE, "no trigger")
        if sig.consensus != d:
            return Decision(Direction.NONE, f"{d.label} trigger without consensus")
        return Decision(d, "consensus + " + ", ".join(sig.triggers))


def make_strategy(name: str) -> Strategy:
    table = {"og": OGStrategy, "trending": TrendingStrategy}
    try:
        return table[name]()
    except KeyError:
        raise ValueError(f"unknown strategy {name!r}; choose from {sorted(table)}") from None
