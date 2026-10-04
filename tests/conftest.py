from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from zdte.clock import session_open
from zdte.data.candles import Candle


def make_candles(prices, day=date(2026, 10, 2), start_minute=0, volume=1000.0, spread=0.05):
    """Build 1-minute candles from a list of closes. Open = previous close."""
    open_ts = session_open(day)
    out = []
    prev = prices[0]
    for i, p in enumerate(prices):
        ts = open_ts + timedelta(minutes=start_minute + i)
        hi = max(prev, p) + spread
        lo = min(prev, p) - spread
        out.append(Candle(ts, prev, hi, lo, p, volume))
        prev = p
    return out


@pytest.fixture
def day():
    return date(2026, 10, 2)


@pytest.fixture
def candles_factory():
    return make_candles


def trend(n, start=100.0, step=0.1):
    return [start + i * step for i in range(n)]


def chop(n, start=100.0, amp=0.3, period=4):
    return [start + amp * math.sin(2 * math.pi * i / period) for i in range(n)]
