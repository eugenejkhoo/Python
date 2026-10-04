from datetime import date, datetime, timedelta

from zdte.broker.base import Position
from zdte.clock import ET
from zdte.config import RiskConfig
from zdte.options import OptionContract
from zdte.risk import RiskManager


def at(h, m):
    return datetime(2026, 10, 2, h, m, tzinfo=ET)


def cfg(**kw):
    base = dict(profit_lock=500.0, daily_max_loss=-300.0, take_profit_pct=0.5, stop_loss_pct=0.3,
                auto_exit_minutes=30, flatten_minutes_before_close=10, no_entry_minutes_before_close=45,
                max_trades_per_day=3, cooldown_minutes=3)
    base.update(kw)
    return RiskConfig(**base)


def pos(entry_price=2.0, ts=at(10, 0)):
    return Position("bot", OptionContract("SPY", date(2026, 10, 2), 570.0, "C"), 1, entry_price, ts)


def test_profit_lock_stops_entries_for_the_day():
    rm = RiskManager(cfg())
    assert rm.entry_block(at(10, 0)) is None
    rm.record_exit(300, at(10, 5))
    assert rm.entry_block(at(10, 30)) is None
    rm.record_exit(250, at(10, 40))
    assert "profit lock" in rm.entry_block(at(11, 0))
    assert rm.day.locked
    rm.reset_day()
    assert rm.entry_block(at(10, 0)) is None


def test_daily_max_loss_locks():
    rm = RiskManager(cfg())
    rm.record_exit(-350, at(10, 5))
    assert "max loss" in rm.entry_block(at(10, 30))


def test_max_trades_and_cooldown():
    rm = RiskManager(cfg(max_trades_per_day=2))
    rm.record_exit(10, at(10, 0))
    assert "cooldown" in rm.entry_block(at(10, 1))
    assert rm.entry_block(at(10, 3)) is None
    rm.record_exit(10, at(10, 10))
    assert "max trades" in rm.entry_block(at(10, 20))


def test_no_entry_near_close():
    rm = RiskManager(cfg())
    assert rm.entry_block(at(15, 14)) is None
    assert "bell" in rm.entry_block(at(15, 15))


def test_exit_rules():
    rm = RiskManager(cfg())
    p = pos(2.0, at(10, 0))
    assert rm.exit_reason(p, 2.4, at(10, 5)) is None
    assert "take profit" in rm.exit_reason(p, 3.0, at(10, 5))
    assert "stop loss" in rm.exit_reason(p, 1.4, at(10, 5))
    assert "time stop" in rm.exit_reason(p, 2.1, at(10, 30))
    assert "flatten" in rm.exit_reason(pos(2.0, at(15, 40)), 2.1, at(15, 50))
    assert rm.exit_reason(pos(2.0, at(15, 30)), 2.1, at(15, 49)) is None
