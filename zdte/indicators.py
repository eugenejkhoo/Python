"""Pure functions over candle lists. No state, no I/O.

All three signals the bots use are built from these:
  * session-anchored VWAP
  * 50-period EMA of closes
  * opening range (high/low of the first N minutes after the open)
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Sequence

from zdte.data.candles import Candle


def vwap_series(candles: Sequence[Candle]) -> list[float]:
    """Cumulative (session-anchored) VWAP after each candle.

    The caller passes only the current session's candles, so the anchor is
    the market open. Bars with zero volume fall back to the previous VWAP.
    """
    out: list[float] = []
    pv = 0.0
    vol = 0.0
    for c in candles:
        pv += c.typical_price * c.volume
        vol += c.volume
        if vol > 0:
            out.append(pv / vol)
        else:
            out.append(out[-1] if out else c.close)
    return out


def ema_series(values: Sequence[float], period: int) -> list[float]:
    """Exponential moving average seeded with the first value.

    Matches ``pandas.Series.ewm(span=period, adjust=False)``.
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    k = 2.0 / (period + 1)
    out: list[float] = []
    prev: float | None = None
    for v in values:
        prev = v if prev is None else (v - prev) * k + prev
        out.append(prev)
    return out


def ema_last(values: Sequence[float], period: int) -> float | None:
    if not values:
        return None
    return ema_series(values, period)[-1]


def opening_range(
    candles: Sequence[Candle], session_open: datetime, minutes: int = 15
) -> tuple[float, float] | None:
    """High/low of the first ``minutes`` minutes of the session.

    Returns ``None`` until every bar of the opening window has completed.
    """
    window_end = session_open + timedelta(minutes=minutes)
    window = [c for c in candles if session_open <= c.ts < window_end]
    if not window:
        return None
    last_needed = window_end - timedelta(minutes=1)
    if window[-1].ts < last_needed:
        return None
    return max(c.high for c in window), min(c.low for c in window)


def true_range(prev_close: float | None, c: Candle) -> float:
    if prev_close is None:
        return c.high - c.low
    return max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close))


def atr(candles: Sequence[Candle], period: int = 14) -> float | None:
    """Wilder-style ATR (simple mean of the last ``period`` true ranges)."""
    if not candles:
        return None
    trs: list[float] = []
    prev: float | None = None
    for c in candles:
        trs.append(true_range(prev, c))
        prev = c.close
    window = trs[-period:]
    return sum(window) / len(window)


def sign(x: float) -> int:
    return 1 if x > 0 else -1 if x < 0 else 0
