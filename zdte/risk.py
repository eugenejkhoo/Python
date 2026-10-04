"""Risk controls: when a bot may enter and when it must exit.

Two headline controls from the brief:
  * profit lock  - once the day's realized P&L reaches the target, the bot
                   stops opening new positions for the rest of the session.
  * auto exit    - positions are closed on a take-profit, a stop-loss, a
                   time stop, or shortly before the close; never held into
                   expiry.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from zdte.broker.base import Position
from zdte.clock import minutes_to_close
from zdte.config import RiskConfig


@dataclass
class DayState:
    realized: float = 0.0
    trades: int = 0
    wins: int = 0
    locked: bool = False
    lock_reason: str = ""
    last_exit: datetime | None = None


class RiskManager:
    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg
        self.day = DayState()

    def reset_day(self) -> None:
        self.day = DayState()

    # -- entries ---------------------------------------------------------
    def entry_block(self, now: datetime) -> str | None:
        """Reason the bot may not enter right now, or ``None`` if it may."""
        d = self.day
        if d.locked:
            return d.lock_reason
        if d.realized >= self.cfg.profit_lock:
            d.locked, d.lock_reason = True, f"profit lock hit (+${d.realized:,.0f})"
            return d.lock_reason
        if d.realized <= self.cfg.daily_max_loss:
            d.locked, d.lock_reason = True, f"daily max loss hit (${d.realized:,.0f})"
            return d.lock_reason
        if d.trades >= self.cfg.max_trades_per_day:
            d.locked, d.lock_reason = True, f"max trades/day ({self.cfg.max_trades_per_day})"
            return d.lock_reason
        if minutes_to_close(now) <= self.cfg.no_entry_minutes_before_close:
            return "too close to the bell"
        if d.last_exit is not None:
            since = (now - d.last_exit).total_seconds() / 60
            if since < self.cfg.cooldown_minutes:
                return f"cooldown ({self.cfg.cooldown_minutes - since:.0f}m left)"
        return None

    # -- exits -----------------------------------------------------------
    def exit_reason(self, pos: Position, bid: float, now: datetime) -> str | None:
        pct = pos.pct_return(bid)
        if pct >= self.cfg.take_profit_pct:
            return f"take profit (+{pct:.0%})"
        if pct <= -self.cfg.stop_loss_pct:
            return f"stop loss ({pct:.0%})"
        held = (now - pos.entry_ts).total_seconds() / 60
        if held >= self.cfg.auto_exit_minutes:
            return f"time stop ({held:.0f}m)"
        if minutes_to_close(now) <= self.cfg.flatten_minutes_before_close:
            return "flatten before close"
        return None

    # -- bookkeeping -----------------------------------------------------
    def record_exit(self, pnl: float, now: datetime) -> None:
        d = self.day
        d.realized += pnl
        d.trades += 1
        if pnl > 0:
            d.wins += 1
        d.last_exit = now
