"""Replay historical (or simulated) sessions through the engine.

A backtest is just the engine with a SimClock and a non-persisting ledger.
The same bots, strategies and risk rules run; only the clock and the data
source differ from live paper trading.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from zdte.clock import SimClock, is_trading_day, session_close, session_open
from zdte.config import EngineConfig
from zdte.data.feeds import CSVFeed, MarketDataFeed, make_feed
from zdte.engine import Engine
from zdte.ledger import Ledger


@dataclass
class BacktestResult:
    engine: Engine
    ledger: Ledger
    days: list[date]

    def summary(self) -> dict:
        return {
            "days": len(self.days),
            "overall": self.ledger.stats(),
            "by_bot": {b.name: self.ledger.stats(b.name) for b in self.engine.bots},
            "by_day": {d.isoformat(): round(v, 2) for d, v in self.ledger.pnl_by_day().items()},
        }

    def report(self) -> str:
        s = self.summary()
        o = s["overall"]
        lines = [
            f"Backtest: {s['days']} session(s), {o['trades']} trades, "
            f"P&L ${o['pnl']:,.2f}, win rate {o['win_rate']:.0%}, profit factor {o['profit_factor']}",
            "",
            f"{'bot':<16}{'trades':>7}{'wins':>6}{'win%':>7}{'pnl':>12}{'avg win':>10}{'avg loss':>10}{'PF':>7}",
        ]
        ranked = sorted(s["by_bot"].items(), key=lambda kv: kv[1]["pnl"], reverse=True)
        for name, st in ranked:
            lines.append(
                f"{name:<16}{st['trades']:>7}{st['wins']:>6}{st['win_rate']:>7.0%}"
                f"{st['pnl']:>12,.2f}{st['avg_win']:>10,.2f}{st['avg_loss']:>10,.2f}{st['profit_factor']:>7}"
            )
        lines.append("")
        lines.append("daily P&L:")
        for d, v in s["by_day"].items():
            lines.append(f"  {d}  {v:>10,.2f}")
        return "\n".join(lines)


def trading_days_between(start: date, end: date) -> list[date]:
    days = []
    d = start
    while d <= end:
        if is_trading_day(d):
            days.append(d)
        d += timedelta(days=1)
    return days


def run_backtest(cfg: EngineConfig, start: date | None = None, end: date | None = None,
                 feed: MarketDataFeed | None = None, days: int | None = None) -> BacktestResult:
    """Run the engine over a range of sessions and return the result.

    * CSV feed: defaults to every day present in the file.
    * Simulated feed: defaults to ``days`` sessions ending today.
    """
    feed = feed or make_feed(cfg)
    if isinstance(feed, CSVFeed):
        all_days = feed.trading_days()
        if start:
            all_days = [d for d in all_days if d >= start]
        if end:
            all_days = [d for d in all_days if d <= end]
    else:
        end = end or date.today()
        if start is None:
            n = days or 5
            start = end
            while len(trading_days_between(start, end)) < n:
                start -= timedelta(days=1)
        all_days = trading_days_between(start, end)
    if not all_days:
        raise ValueError("no trading days to backtest")

    clock = SimClock(session_open(all_days[0]), end=session_close(all_days[-1]) + timedelta(minutes=1))
    ledger = Ledger(None)
    engine = Engine(cfg, clock=clock, feed=feed, ledger=ledger, persist=False)
    engine.run()
    return BacktestResult(engine=engine, ledger=ledger, days=all_days)
