# zdte-bots: rule-based 0DTE options bots for QQQ, SPY and IWM

A small, dependency-free Python system that day-trades same-day-expiry
(0DTE) options on three index ETFs using fixed rules evaluated once per
completed 1-minute candle. It ships with a paper broker, a backtester and
a live dashboard (profit counter, leaderboard, signal scanner).

**This is not AI or machine learning.** Every decision is one of the rules
below, written in plain Python.

> **Risk warning.** 0DTE options can lose their entire premium in minutes.
> The paper broker marks contracts with a Black-Scholes model, not real
> quotes, and the simulated feed is a random walk. Nothing produced by this
> code is a prediction of real returns. Run it in paper mode until you have
> watched it for a long time, and never risk money you cannot afford to lose.

## How the bots decide

One **bot** = one symbol + one strategy. With three symbols and two
strategies you get six bots, each with its own risk book.

Every minute, for each symbol, the engine computes a **Signal** from the
session's candles (`zdte/signals.py`):

| Rule | Trigger (event on this candle) | Bias (current state) |
|---|---|---|
| VWAP | close crossed above / below the session VWAP | close is above / below VWAP |
| 50 EMA | close crossed above / below the 50-period EMA | close is above / below EMA |
| Opening range | close broke out above / below the first 15 minutes' high / low | close is above / inside / below the range |

A **chop filter** blocks entries when price has crossed VWAP three or more
times in the last 20 bars, or when the close is hugging VWAP (within a
quarter of an ATR). Bots "wait for chop to clear" automatically because the
filter re-evaluates every minute.

Two strategies consume the same Signal (`zdte/strategies.py`):

* **OG** – any single trigger is enough. Bullish trigger → buy a call;
  bearish trigger → buy a put.
* **Trending** – a trigger *plus* all three biases agreeing with it
  (price on the same side of VWAP, the EMA and the opening range).

Contracts are same-day expiry at the money (`strike_offset` nudges them
in or out of the money).

## Risk controls (`zdte/risk.py`)

| Control | Default | What it does |
|---|---|---|
| `profit_lock` | +$500 realized/day | bot stops opening trades for the rest of the session |
| `daily_max_loss` | -$500 realized/day | same, on the downside |
| `take_profit_pct` / `stop_loss_pct` | +50% / -35% of premium | auto exit |
| `auto_exit_minutes` | 30 | hard time stop per position |
| `flatten_minutes_before_close` | 10 | nothing is ever held into expiry |
| `no_entry_minutes_before_close` | 45 | no late entries |
| `max_trades_per_day`, `cooldown_minutes` | 6, 3 | pacing |

Each strategy block in `config.toml` has its own `[strategies.risk]` table.

## Quick start

```bash
pip install -r requirements.txt            # only pytest is needed for the core
python -m pytest -q                        # run the tests

# one simulated trading day, as fast as possible, dashboard on :8080
python -m zdte run --sim --dashboard

# the same, replayed at 10 simulated minutes per real second so you can watch it
python -m zdte run --sim --speed 10 --dashboard

# five simulated sessions through the backtester
python -m zdte backtest --days 5
```

Open http://localhost:8080/ for the dashboard. It polls `state/state.json`
(written by the engine every minute) and shows:

* the running **profit counter** and today's P&L,
* a cumulative **equity curve**,
* the **leaderboard** ranking bots by P&L with their current state,
* the **signal scanner** with buy/sell strength per symbol, the VWAP, EMA
  and opening-range levels, active triggers and chop warnings,
* open positions and recent trades.

Run the dashboard separately from the engine with `python -m zdte dashboard`.

### The city view

http://localhost:8080/city is the same state drawn as an isometric
"bot city" (no external libraries, plain Canvas 2D):

* every bot is a **tower** whose height follows its total P&L; losing bots
  glow red,
* a tower **beams light** while it holds a position (blue for a call,
  orange for a put) and its label shows the open return,
* the **treasury** in the middle totals profit across all trading days; tap
  it for the payroll (leaderboard),
* **chart billboards** around the ring show each symbol's 1-minute closes
  with VWAP, EMA50 and the opening-range lines, plus chop and trigger flags,
* the bottom strip shows **daily P&L** and a ticker of recent exits.

Drag to rotate, wheel to zoom. The desk view and the city view link to
each other.

## Paper trading on real (delayed) data

```bash
pip install yfinance
# in config.toml:  feed = "yfinance"
python -m zdte run --dashboard
```

The engine sleeps outside 09:30–16:00 ET and skips weekends and NYSE
holidays (`zdte/clock.py`). Yahoo's 1-minute bars are delayed and
sometimes gappy, which is fine for paper trading.

## Backtesting on real bars

```bash
python -m zdte fetch --symbols SPY,QQQ,IWM --days 5 --out data/bars.csv   # Yahoo allows <= 7 days of 1m bars
python -m zdte backtest --csv data/bars.csv
```

The CSV format is `symbol,ts,open,high,low,close,volume`; any source that
produces 1-minute bars in that shape works. The backtester is the same
engine driven by a simulated clock, so what you test is what runs live.

## Going live

`zdte/broker/alpaca.py` is an **untested** REST adapter for Alpaca's options
API, included as a starting point. Set `mode = "alpaca"`, export
`ALPACA_API_KEY` / `ALPACA_API_SECRET`, keep `alpaca.paper = true` until
you have verified fills, quotes and position handling against your
account. The CLI refuses to start a non-paper Alpaca session without
`--i-understand-live-trading`.

## Layout

```
zdte/
  clock.py        ET clocks, market hours, holidays; RealClock and SimClock
  config.py       TOML config -> dataclasses
  indicators.py   VWAP, EMA, opening range, ATR
  signals.py      Signal evaluation + chop filter + scanner strength
  strategies.py   OG and Trending strategies
  options.py      contracts, OCC symbols, strike selection, Black-Scholes
  risk.py         profit lock, auto exit, daily loss, pacing
  bot.py          one symbol + one strategy + one position
  engine.py       minute loop, state snapshot
  backtest.py     engine + SimClock over many sessions
  ledger.py       JSONL trade log and P&L views
  data/feeds.py   SimulatedFeed, CSVFeed, YFinanceFeed
  broker/         Broker interface, PaperBroker, AlpacaBroker
  dashboard/      stdlib HTTP server + desk view (index.html) + city view (city.html)
tests/            pytest suite covering every module
config.toml       example configuration
```

## Extending

* **New rule**: add it to `evaluate()` in `signals.py` as a trigger and a
  bias; both strategies pick it up.
* **New strategy**: subclass `Strategy` in `strategies.py` and register it
  in `make_strategy()`.
* **New broker**: implement `Broker` in `broker/base.py`.
* **New data source**: implement `MarketDataFeed.session_candles()`.
