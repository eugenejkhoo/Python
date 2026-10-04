"""The engine owns the bots, the clock, the feed and the broker.

Once per minute inside market hours it:
  1. pulls each symbol's completed candles,
  2. evaluates the shared Signal for that symbol,
  3. lets every bot on the symbol act,
  4. writes a JSON state snapshot for the dashboard.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path

from zdte.bot import Bot
from zdte.broker.base import Broker
from zdte.broker.paper import PaperBroker
from zdte.clock import (Clock, RealClock, floor_minute, in_session, is_trading_day,
                        next_minute, session_close, session_open, to_et)
from zdte.config import EngineConfig
from zdte.data.feeds import MarketDataFeed, make_feed
from zdte.ledger import Ledger
from zdte.indicators import ema_series, vwap_series
from zdte.signals import Signal, evaluate
from zdte.strategies import make_strategy

log = logging.getLogger("zdte.engine")


def make_broker(cfg: EngineConfig) -> Broker:
    if cfg.mode == "paper":
        return PaperBroker(cfg.paper, {s.symbol: s.implied_vol for s in cfg.symbols})
    if cfg.mode == "alpaca":
        from zdte.broker.alpaca import AlpacaBroker
        return AlpacaBroker(cfg.alpaca, cfg.paper.commission_per_contract)
    raise ValueError(cfg.mode)


def next_session_open(now: datetime) -> datetime:
    now = to_et(now)
    day = now.date()
    if is_trading_day(day) and now < session_open(day):
        return session_open(day)
    day += timedelta(days=1)
    while not is_trading_day(day):
        day += timedelta(days=1)
    return session_open(day)


class Engine:
    def __init__(self, cfg: EngineConfig, clock: Clock | None = None,
                 feed: MarketDataFeed | None = None, broker: Broker | None = None,
                 ledger: Ledger | None = None, persist: bool = True):
        self.cfg = cfg
        self.clock = clock or RealClock()
        self.feed = feed or make_feed(cfg)
        self.broker = broker or make_broker(cfg)
        self.state_dir = Path(cfg.state_dir)
        if persist:
            self.state_dir.mkdir(parents=True, exist_ok=True)
        self.ledger = ledger or Ledger(self.state_dir / "trades.jsonl" if persist else None)
        self.persist = persist
        self.bots: list[Bot] = []
        for sym in cfg.symbols:
            for strat in cfg.strategies:
                if strat.enabled:
                    self.bots.append(Bot(sym, strat, make_strategy(strat.name), self.broker, self.ledger))
        self.signals: dict[str, Signal] = {}
        self.spots: dict[str, float] = {}
        self.series: dict[str, dict] = {}
        self.chart_bars = 120
        self._snapshot: dict = {}
        self._lock = threading.Lock()
        self.ticks = 0
        self.stop_event = threading.Event()

    # ------------------------------------------------------------------
    def tick(self, now: datetime) -> None:
        now = floor_minute(to_et(now))
        open_ts = session_open(now.date())
        for sym in self.cfg.symbols:
            candles = self.feed.session_candles(sym.symbol, now)
            if not candles:
                continue
            spot = candles[-1].close
            sig = evaluate(candles, open_ts, self.cfg.signals)
            self.signals[sym.symbol] = sig
            self.spots[sym.symbol] = spot
            self.series[sym.symbol] = self._chart_series(candles)
            for bot in self.bots:
                if bot.symbol == sym.symbol:
                    try:
                        bot.on_tick(now, spot, sig)
                    except Exception:  # keep other bots alive
                        log.exception("%s: tick failed", bot.name)
        self.ticks += 1
        self.refresh_snapshot(now)

    def _chart_series(self, candles) -> dict:
        """Last ``chart_bars`` closes with VWAP and EMA for the dashboard charts."""
        n = self.chart_bars
        closes = [c.close for c in candles]
        vw = vwap_series(candles)
        em = ema_series(closes, self.cfg.signals.ema_period)
        return {
            "ts": [c.ts.isoformat() for c in candles[-n:]],
            "close": [round(v, 4) for v in closes[-n:]],
            "vwap": [round(v, 4) for v in vw[-n:]],
            "ema": [round(v, 4) for v in em[-n:]],
        }

    def end_of_day(self, now: datetime) -> None:
        for bot in self.bots:
            spot = self.spots.get(bot.symbol)
            if bot.position is not None and spot is not None:
                bot.flatten(spot, now, "session close")
        self.refresh_snapshot(now)

    def run(self) -> None:
        """Main loop. Returns when the clock is finished or ``stop()`` is called."""
        log.info("engine started: mode=%s feed=%s bots=%d", self.cfg.mode, self.feed.name, len(self.bots))
        last_day = None
        while not self.stop_event.is_set() and not self.clock.is_finished():
            now = self.clock.now()
            if in_session(now):
                last_day = now.date()
                self.tick(now)
                self.clock.wait_until(next_minute(now))
                continue
            if last_day is not None and now >= session_close(last_day):
                self.end_of_day(now)
                last_day = None
            target = next_session_open(now)
            if self.clock.is_finished():
                break
            self.refresh_snapshot(now)
            log.info("market closed; next session %s", target.isoformat())
            self.clock.wait_until(target)
        if last_day is not None:
            self.end_of_day(self.clock.now())
        log.info("engine stopped after %d ticks", self.ticks)

    def stop(self) -> None:
        self.stop_event.set()

    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._snapshot)

    def refresh_snapshot(self, now: datetime) -> None:
        snap = self.build_snapshot(now)
        with self._lock:
            self._snapshot = snap
        if self.persist:
            self._write_state(snap)

    def build_snapshot(self, now: datetime) -> dict:
        now = to_et(now)
        today = now.date()
        bots = [b.status() for b in self.bots]
        leaderboard = sorted(bots, key=lambda b: (b["total_pnl"], b["day_pnl"]), reverse=True)
        scanner = []
        for sym in self.cfg.symbols:
            sig = self.signals.get(sym.symbol)
            entry = {"symbol": sym.symbol, "price": self.spots.get(sym.symbol)}
            if sig:
                entry.update(sig.to_dict())
            scanner.append(entry)
        open_positions = [b["position"] | {"bot": b["name"]} for b in bots if b["position"]]
        stats = self.ledger.stats()
        return {
            "updated": now.isoformat(),
            "mode": self.cfg.mode,
            "feed": self.feed.name,
            "market_open": in_session(now),
            "cash": round(self.broker.cash(), 2) if self.cfg.mode == "paper" else None,
            "total_pnl": round(self.ledger.total_pnl(), 2),
            "day_pnl": round(self.ledger.day_pnl(today) + 0.0, 2),
            "unrealized": round(sum(p.get("unrealized", 0.0) for p in open_positions), 2),
            "stats": {k: (None if v == float("inf") else v) for k, v in stats.items()},
            "pnl_by_day": [(d.isoformat(), round(v, 2)) for d, v in self.ledger.pnl_by_day().items()],
            "equity_curve": self.ledger.equity_curve()[-500:],
            "leaderboard": leaderboard,
            "scanner": scanner,
            "charts": {sym: self.series[sym] for sym in self.series},
            "open_positions": open_positions,
            "recent_trades": self.ledger.recent(25),
        }

    def _write_state(self, snap: dict) -> None:
        path = self.state_dir / "state.json"
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w") as fh:
            json.dump(snap, fh)
        os.replace(tmp, path)
