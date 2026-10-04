"""Market data feeds.

Every feed answers two questions for a symbol at a given time:

  * ``session_candles(symbol, now)`` - completed 1-minute candles of the
    session containing ``now`` (bar open time strictly before ``now``).
  * ``last_price(symbol, now)``      - the most recent price (last close).

Feeds:
  * SimulatedFeed - seeded random walk with trend/chop regimes. No network.
  * CSVFeed       - replay historical bars (backtests).
  * YFinanceFeed  - today's 1-minute bars from Yahoo Finance (delayed).
"""
from __future__ import annotations

import csv
import math
import random
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from zdte.clock import ET, floor_minute, session_close, session_open, to_et
from zdte.data.candles import Candle


class MarketDataFeed:
    name = "base"

    def session_candles(self, symbol: str, now: datetime) -> list[Candle]:
        raise NotImplementedError  # pragma: no cover

    def last_price(self, symbol: str, now: datetime) -> float | None:
        candles = self.session_candles(symbol, now)
        return candles[-1].close if candles else None


class SimulatedFeed(MarketDataFeed):
    """Deterministic synthetic intraday prices.

    Each minute's return is drawn from a regime: ``trend`` (persistent
    drift) or ``chop`` (mean-reverting noise). Regimes switch randomly so
    both strategies and the chop filter get exercised.
    """

    name = "sim"

    def __init__(self, start_prices: dict[str, float], seed: int = 7,
                 minute_vol: float = 0.0006):
        self.start_prices = dict(start_prices)
        self.seed = seed
        self.minute_vol = minute_vol
        self._sessions: dict[tuple[str, datetime], list[Candle]] = {}

    def _generate_session(self, symbol: str, open_ts: datetime) -> list[Candle]:
        rng = random.Random(f"{self.seed}:{symbol}:{open_ts.date()}")
        price = self.start_prices.get(symbol, 100.0) * (1 + rng.uniform(-0.01, 0.01))
        close_ts = session_close(open_ts.date())
        candles: list[Candle] = []
        regime = "trend"
        drift = rng.choice([-1, 1]) * self.minute_vol * 0.6
        anchor = price
        ts = open_ts
        while ts < close_ts:
            if rng.random() < 0.03:
                regime = "chop" if regime == "trend" else "trend"
                drift = rng.choice([-1, 1]) * self.minute_vol * rng.uniform(0.3, 0.9)
                anchor = price
            if regime == "trend":
                ret = drift + rng.gauss(0, self.minute_vol)
            else:
                ret = -0.15 * math.log(price / anchor) + rng.gauss(0, self.minute_vol * 0.8)
            o = price
            c = price * math.exp(ret)
            wiggle = abs(rng.gauss(0, self.minute_vol * 0.5)) * price
            h = max(o, c) + wiggle
            lo = min(o, c) - wiggle
            minutes_in = (ts - open_ts).total_seconds() / 60
            vol = rng.uniform(0.6, 1.4) * (3.0 if minutes_in < 30 else 1.0) * 100_000
            candles.append(Candle(ts, round(o, 4), round(h, 4), round(lo, 4), round(c, 4), vol))
            price = c
            ts += timedelta(minutes=1)
        return candles

    def session_candles(self, symbol: str, now: datetime) -> list[Candle]:
        now = floor_minute(to_et(now))
        open_ts = session_open(now.date())
        key = (symbol, open_ts)
        if key not in self._sessions:
            self._sessions[key] = self._generate_session(symbol, open_ts)
        return [c for c in self._sessions[key] if c.ts < now]


class CSVFeed(MarketDataFeed):
    """Replay 1-minute bars from CSV.

    Expected columns: ``symbol,ts,open,high,low,close,volume``. ``ts`` is
    ISO-8601; naive timestamps are assumed to be US/Eastern.
    """

    name = "csv"

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._bars: dict[str, list[Candle]] = defaultdict(list)
        self._load()

    def _load(self) -> None:
        with open(self.path, newline="") as fh:
            for row in csv.DictReader(fh):
                ts = to_et(datetime.fromisoformat(row["ts"]))
                self._bars[row["symbol"].upper()].append(
                    Candle(ts, float(row["open"]), float(row["high"]), float(row["low"]),
                           float(row["close"]), float(row.get("volume") or 0.0))
                )
        for bars in self._bars.values():
            bars.sort(key=lambda c: c.ts)

    @property
    def symbols(self) -> list[str]:
        return sorted(self._bars)

    def trading_days(self) -> list:
        days = {c.ts.date() for bars in self._bars.values() for c in bars}
        return sorted(days)

    def session_candles(self, symbol: str, now: datetime) -> list[Candle]:
        now = floor_minute(to_et(now))
        o, c = session_open(now.date()), session_close(now.date())
        return [b for b in self._bars.get(symbol.upper(), []) if o <= b.ts < min(now, c)]

    @staticmethod
    def write(path: str | Path, rows: Iterable[tuple[str, Candle]]) -> None:
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["symbol", "ts", "open", "high", "low", "close", "volume"])
            for symbol, c in rows:
                w.writerow([symbol, c.ts.isoformat(), c.open, c.high, c.low, c.close, c.volume])


class YFinanceFeed(MarketDataFeed):
    """Today's 1-minute bars from Yahoo Finance via ``yfinance``.

    Yahoo's intraday data is delayed and occasionally gappy. It is good
    enough for paper trading; use a broker feed for anything real.
    """

    name = "yfinance"

    def __init__(self, refresh_seconds: int = 30):
        try:
            import yfinance  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("pip install yfinance to use the yfinance feed") from exc
        self.refresh_seconds = refresh_seconds
        self._cache: dict[str, tuple[datetime, list[Candle]]] = {}

    def _fetch(self, symbol: str, day) -> list[Candle]:
        import yfinance as yf

        start = session_open(day)
        end = session_close(day)
        df = yf.download(symbol, start=start, end=end + timedelta(minutes=1), interval="1m",
                         progress=False, auto_adjust=False, prepost=False)
        candles: list[Candle] = []
        if df is None or df.empty:
            return candles
        # yfinance may return a MultiIndex column frame for single symbols.
        if hasattr(df.columns, "levels"):
            df.columns = [c[0] for c in df.columns]
        for ts, row in df.iterrows():
            ts = to_et(ts.to_pydatetime())
            if not (start <= ts < end):
                continue
            candles.append(Candle(ts, float(row["Open"]), float(row["High"]), float(row["Low"]),
                                  float(row["Close"]), float(row["Volume"])))
        return candles

    def session_candles(self, symbol: str, now: datetime) -> list[Candle]:
        now = to_et(now)
        cached = self._cache.get(symbol)
        if cached is None or (now - cached[0]).total_seconds() >= self.refresh_seconds \
                or cached[1] and cached[1][0].ts.date() != now.date():
            self._cache[symbol] = (now, self._fetch(symbol, now.date()))
        cutoff = floor_minute(now)
        return [c for c in self._cache[symbol][1] if c.ts < cutoff]


def make_feed(cfg) -> MarketDataFeed:
    if cfg.feed == "sim":
        return SimulatedFeed({s.symbol: s.sim_start_price for s in cfg.symbols}, seed=cfg.sim_seed)
    if cfg.feed == "csv":
        return CSVFeed(cfg.csv_path)
    if cfg.feed == "yfinance":
        return YFinanceFeed()
    raise ValueError(f"unknown feed {cfg.feed!r}")


__all__ = ["MarketDataFeed", "SimulatedFeed", "CSVFeed", "YFinanceFeed", "make_feed", "ET"]
