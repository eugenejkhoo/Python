from datetime import datetime

import pytest

from zdte.clock import ET
from zdte.signals import Direction, Signal
from zdte.strategies import OGStrategy, TrendingStrategy, make_strategy


def sig(**kw):
    base = dict(ts=datetime(2026, 10, 2, 10, 0, tzinfo=ET), close=100.0, ready=True, choppy=False,
                bias={"vwap": 1, "ema": 1, "orb": 1}, triggers=["vwap_cross_up"])
    base.update(kw)
    return Signal(**base)


def test_og_trades_on_any_trigger():
    d = OGStrategy().decide(sig(bias={"vwap": 1, "ema": -1, "orb": 0}))
    assert d.is_trade and d.direction == Direction.BULL and d.option_type == "C"


def test_og_blocks_on_chop_and_warmup():
    assert not OGStrategy().decide(sig(choppy=True, chop_reason="x")).is_trade
    assert not OGStrategy().decide(sig(ready=False)).is_trade
    assert not OGStrategy().decide(sig(triggers=[])).is_trade


def test_og_bearish_put():
    d = OGStrategy().decide(sig(triggers=["orb_break_down"], bias={"vwap": -1, "ema": 1, "orb": -1}))
    assert d.direction == Direction.BEAR and d.option_type == "P"


def test_trending_requires_consensus():
    assert TrendingStrategy().decide(sig()).is_trade
    assert not TrendingStrategy().decide(sig(bias={"vwap": 1, "ema": 1, "orb": 0})).is_trade
    assert not TrendingStrategy().decide(sig(bias={"vwap": 1, "ema": -1, "orb": 1})).is_trade
    d = TrendingStrategy().decide(sig(triggers=["ema_cross_down"], bias={"vwap": -1, "ema": -1, "orb": -1}))
    assert d.direction == Direction.BEAR


def test_make_strategy():
    assert make_strategy("og").name == "og"
    assert make_strategy("trending").name == "trending"
    with pytest.raises(ValueError):
        make_strategy("nope")
