"""One bot = one symbol + one strategy + its own risk book.

The engine hands every bot the symbol's completed candles and the shared
Signal once per minute. The bot manages at most one open position.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from zdte.broker.base import Broker, Position
from zdte.config import RiskConfig, StrategyConfig, SymbolConfig
from zdte.ledger import Ledger, TradeRecord
from zdte.options import OptionContract, select_strike
from zdte.risk import RiskManager
from zdte.signals import Signal
from zdte.strategies import Decision, Strategy

log = logging.getLogger("zdte.bot")


class Bot:
    def __init__(self, symbol_cfg: SymbolConfig, strategy_cfg: StrategyConfig,
                 strategy: Strategy, broker: Broker, ledger: Ledger):
        self.symbol_cfg = symbol_cfg
        self.symbol = symbol_cfg.symbol
        self.strategy_cfg = strategy_cfg
        self.strategy = strategy
        self.broker = broker
        self.ledger = ledger
        self.name = f"{self.symbol}-{strategy.name}"
        self.risk = RiskManager(strategy_cfg.risk)
        self.position: Position | None = None
        self.day: date | None = None
        self.last_signal: Signal | None = None
        self.last_decision: Decision | None = None
        self.last_action: str = "idle"
        self.last_bid: float | None = None
        self.last_entry_reason: str = ""

    # ------------------------------------------------------------------
    @property
    def risk_cfg(self) -> RiskConfig:
        return self.strategy_cfg.risk

    def _roll_day(self, now: datetime) -> None:
        if self.day != now.date():
            self.day = now.date()
            self.risk.reset_day()
            if self.position is not None:
                # Should never happen: positions are flattened before the
                # close. Drop the stale reference rather than mis-price it.
                log.warning("%s: dropping stale position %s from a prior day",
                            self.name, self.position.contract.occ_symbol)
                self.position = None

    def on_tick(self, now: datetime, spot: float, sig: Signal) -> None:
        self._roll_day(now)
        self.last_signal = sig

        if self.position is not None:
            quote = self.broker.quote(self.position.contract, spot, now)
            self.last_bid = quote.bid
            reason = self.risk.exit_reason(self.position, quote.bid, now)
            if reason:
                self._exit(reason, spot, now)
            else:
                self.last_action = (
                    f"holding {self.position.contract.occ_symbol} "
                    f"({self.position.pct_return(quote.bid):+.0%})"
                )
            return

        block = self.risk.entry_block(now)
        if block:
            self.last_action = block
            self.last_decision = None
            return

        decision = self.strategy.decide(sig)
        self.last_decision = decision
        if decision.is_trade:
            self._enter(decision, spot, now)
        else:
            self.last_action = decision.reason or "scanning"

    # ------------------------------------------------------------------
    def _enter(self, decision: Decision, spot: float, now: datetime) -> None:
        strike = select_strike(spot, self.symbol_cfg.strike_increment,
                               self.strategy_cfg.strike_offset, decision.option_type)
        contract = OptionContract(self.symbol, now.date(), strike, decision.option_type)
        try:
            fill = self.broker.buy(self.name, contract, self.risk_cfg.contracts, spot, now)
        except Exception as exc:  # broker rejected
            log.error("%s: buy %s failed: %s", self.name, contract.occ_symbol, exc)
            self.last_action = f"order rejected: {exc}"
            return
        self.position = next(p for p in self.broker.positions() if p.owner == self.name)
        self.last_entry_reason = decision.reason
        self.last_bid = fill.price
        self.last_action = f"bought {fill.qty}x {contract.occ_symbol} @ {fill.price:.2f}"
        log.info("%s: BUY %s x%d @ %.2f (%s)", self.name, contract.occ_symbol, fill.qty,
                 fill.price, decision.reason)

    def _exit(self, reason: str, spot: float, now: datetime) -> None:
        pos = self.position
        assert pos is not None
        try:
            fill = self.broker.sell(pos, spot, now)
        except Exception as exc:
            log.error("%s: sell %s failed: %s", self.name, pos.contract.occ_symbol, exc)
            self.last_action = f"exit failed: {exc}"
            return
        proceeds = fill.price * 100.0 * fill.qty - fill.commission
        pnl = proceeds - pos.cost_basis
        self.risk.record_exit(pnl, now)
        self.ledger.record(TradeRecord(
            bot=self.name, symbol=self.symbol, strategy=self.strategy.name,
            contract=pos.contract.occ_symbol, option_type=pos.contract.option_type,
            strike=pos.contract.strike, qty=pos.qty,
            entry_ts=pos.entry_ts.isoformat(), entry_price=pos.entry_price,
            exit_ts=now.isoformat(), exit_price=fill.price,
            commission=round(pos.entry_commission + fill.commission, 2), pnl=round(pnl, 2),
            entry_reason=self.last_entry_reason, exit_reason=reason,
            hold_minutes=round((now - pos.entry_ts).total_seconds() / 60, 1),
        ))
        log.info("%s: SELL %s @ %.2f pnl %+.2f (%s)", self.name, pos.contract.occ_symbol,
                 fill.price, pnl, reason)
        self.position = None
        self.last_bid = None
        self.last_action = f"closed {pos.contract.occ_symbol} {pnl:+.0f} ({reason})"

    def flatten(self, spot: float, now: datetime, reason: str = "engine flatten") -> None:
        if self.position is not None:
            self._exit(reason, spot, now)

    # ------------------------------------------------------------------
    def status(self) -> dict:
        d = self.risk.day
        stats = self.ledger.stats(self.name)
        state = "holding" if self.position else ("locked" if d.locked else "scanning")
        return {
            "name": self.name,
            "symbol": self.symbol,
            "strategy": self.strategy.name,
            "state": state,
            "day_pnl": round(d.realized, 2),
            "day_trades": d.trades,
            "day_wins": d.wins,
            "locked": d.locked,
            "lock_reason": d.lock_reason,
            "total_pnl": stats["pnl"],
            "trades": stats["trades"],
            "win_rate": stats["win_rate"],
            "profit_factor": stats["profit_factor"] if stats["profit_factor"] != float("inf") else None,
            "position": self.position.to_dict(self.last_bid) if self.position else None,
            "last_action": self.last_action,
            "last_decision": self.last_decision.reason if self.last_decision else "",
        }
