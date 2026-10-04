"""Clocks and market-session helpers.

Everything in the engine asks a Clock for the time instead of calling
``datetime.now()`` so that the same code runs live (RealClock) and in a
backtest / fast simulation (SimClock).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

MARKET_OPEN = dtime(9, 30)
MARKET_CLOSE = dtime(16, 0)

# NYSE full-day closures. Extend as needed; early-close days are treated as
# normal days (the engine flattens before 16:00 anyway).
NYSE_HOLIDAYS: frozenset[date] = frozenset(
    {
        # 2025
        date(2025, 1, 1), date(2025, 1, 9), date(2025, 1, 20), date(2025, 2, 17),
        date(2025, 4, 18), date(2025, 5, 26), date(2025, 6, 19), date(2025, 7, 4),
        date(2025, 9, 1), date(2025, 11, 27), date(2025, 12, 25),
        # 2026
        date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
        date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
        date(2026, 11, 26), date(2026, 12, 25),
    }
)


def to_et(dt: datetime) -> datetime:
    """Return ``dt`` as an aware datetime in US/Eastern."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ET)
    return dt.astimezone(ET)


def floor_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


def session_open(day: date) -> datetime:
    return datetime.combine(day, MARKET_OPEN, tzinfo=ET)


def session_close(day: date) -> datetime:
    return datetime.combine(day, MARKET_CLOSE, tzinfo=ET)


def is_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in NYSE_HOLIDAYS


def in_session(now: datetime) -> bool:
    now = to_et(now)
    if not is_trading_day(now.date()):
        return False
    return session_open(now.date()) <= now < session_close(now.date())


def minutes_to_close(now: datetime) -> float:
    now = to_et(now)
    return (session_close(now.date()) - now).total_seconds() / 60.0


def next_minute(now: datetime) -> datetime:
    return floor_minute(now) + timedelta(minutes=1)


class Clock:
    """Abstract clock. ``now()`` is always an aware ET datetime."""

    def now(self) -> datetime:  # pragma: no cover - interface
        raise NotImplementedError

    def wait_until(self, when: datetime) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def is_finished(self) -> bool:
        return False


class RealClock(Clock):
    def now(self) -> datetime:
        return datetime.now(tz=ET)

    def wait_until(self, when: datetime) -> None:
        delay = (when - self.now()).total_seconds()
        if delay > 0:
            time.sleep(delay)


@dataclass
class SimClock(Clock):
    """A clock that only moves when the engine asks it to.

    ``wait_until`` jumps straight to the requested time (optionally sleeping a
    real ``real_seconds_per_step`` so a demo can be watched on the dashboard).
    """

    current: datetime
    end: datetime | None = None
    real_seconds_per_step: float = 0.0

    def __post_init__(self) -> None:
        self.current = to_et(self.current)
        if self.end is not None:
            self.end = to_et(self.end)

    def now(self) -> datetime:
        return self.current

    def wait_until(self, when: datetime) -> None:
        if self.real_seconds_per_step > 0:
            time.sleep(self.real_seconds_per_step)
        self.current = to_et(when)

    def is_finished(self) -> bool:
        return self.end is not None and self.current >= self.end
