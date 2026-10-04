# Roadmap: from paper prototype to unattended live trading on Interactive Brokers

**Purpose.** A personal, rule-based system that day-trades 0DTE options on
SPY, QQQ and IWM through Interactive Brokers (IBKR) while its owner is at
work, with no discretionary input, and reports to a phone. Account size
$25k–$100k, so no pattern-day-trader constraint; sizing starts at 1–2
contracts per bot.

**Where we are.** The engine, strategies, risk book, paper broker,
backtester, dashboards and snapshot export exist and are tested. Nothing
has touched a real market: bars come from a simulator or delayed Yahoo
data, option prices come from a Black-Scholes formula, the live adapter is
untested, positions live only in memory, and the process cannot run
unattended. Every P&L number shown so far is synthetic.

The phases below are ordered by dependency and by how much they reduce
the risk of losing money for a reason that is not the strategy.

---

## Phase 1 — IBKR connectivity and real data (gates everything)

Goal: the same engine, driven by IBKR's bars and option quotes, in a paper
account.

- **Client library.** `ib_async` (maintained fork of ib_insync) talking to
  IB Gateway on localhost. Add `zdte/broker/ibkr.py` implementing `Broker`
  and `zdte/data/ibkr_feed.py` implementing `MarketDataFeed`.
- **Bars.** 1-minute bars via historical data with `keepUpToDate`, or
  5-second real-time bars aggregated to 1-minute. Session VWAP, EMA and
  opening range stay computed in `indicators.py` so backtests and live use
  identical code.
- **Option chain.** Resolve the 0DTE expiry and strikes each morning via
  security-definition parameters, qualify the ATM contract per signal, and
  quote it from the live bid/ask. Replace the Black-Scholes mark in paper
  mode with the real quote so paper P&L is believable.
- **Market data subscriptions.** OPRA (US options) and a US equities
  bundle are required for live quotes; share them with the paper account
  in Account Management.
- **Orders.** Marketable limit orders (ask + a tick for entries, bid − a
  tick for exits) rather than market orders; 0DTE spreads punish market
  orders. Model the order lifecycle: submitted, partially filled, filled,
  cancelled, rejected, with a timeout that cancels and re-prices.
- **Reconciliation.** Persist open positions and orders to `state/`, and on
  every startup reconcile against IBKR's positions and open orders. A
  position IBKR holds that the engine doesn't know about is adopted and
  managed by the risk rules, never ignored.

Exit criterion: two weeks of paper trading with real quotes and a trade
log that matches IBKR's statements.

## Phase 2 — Prove the edge before risking money

Goal: evidence that the rules make money after spreads and slippage, or a
clear reason to change them.

- Pull several years of 1-minute SPY/QQQ/IWM bars from IBKR into the CSV
  feed format and run the existing backtester on them.
- Options pricing in backtests is the weak point. Start with model pricing
  calibrated to recorded live quotes from Phase 1 (record every quote the
  engine sees to build the calibration set). Treat results as directional
  until real historical options data is bought.
- Add a slippage and spread model to the paper broker and sweep it: how
  much friction can each rule absorb before the edge disappears?
- Parameter sweeps (EMA period, opening-range length, chop thresholds,
  take-profit / stop-loss / time-stop) with walk-forward splits, not a
  single best fit.
- Per-rule attribution: which of the three triggers actually earns, and
  whether the Trending consensus filter helps or merely trades less.

Exit criterion: a written note stating the measured edge, its variance,
and the parameters chosen, before any live order.

## Phase 3 — Risk hardening

Goal: the system cannot lose more than a known amount in a day for any
non-strategy reason.

- Portfolio-level daily loss limit across all bots (today only per bot).
- Maximum concurrent positions and a buying-power check before entry.
- Sizing as a percentage of net liquidation value, capped by contracts.
- Stale-data guard: no entries if the last bar is older than 2 minutes;
  flatten open positions if the feed is silent for 5.
- Early-close calendar (1pm closes) and the full NYSE holiday list from a
  maintained source instead of the hard-coded set.
- Optional blackout windows around FOMC, CPI and jobs releases.
- Gateway-disconnect behaviour: on reconnect, reconcile first, then
  resume; if reconnect fails within N minutes while holding, alert loudly.

## Phase 4 — Run unattended

Goal: it runs without a laptop open and tells you what it did.

- IB Gateway with IBC for automatic login, the daily restart and weekly
  re-authentication; engine and gateway in Docker Compose on a small VPS
  or an always-on home machine with restart policies and a watchdog.
- Structured logs with retention; a heartbeat file the dashboard surfaces.
- Push alerts (Pushover, Telegram or SMS) on every entry, exit, error,
  gateway disconnect and a daily summary at the close.
- Dashboard reachable from a phone behind authentication (Tailscale is the
  least effort; otherwise HTTPS with basic auth). Add a kill switch that
  flattens everything and a per-bot pause, both usable from the phone.

## Phase 5 — Go live, small

- One symbol, one contract, the OG strategy only, for two to four weeks.
- Compare live fills with paper fills from the same signals; measure the
  real slippage and feed it back into Phase 2's model.
- Scale by evidence: add the second strategy, then the other symbols, then
  size, each step after a review of the ledger.

---

## Not planned (and why)

- Machine learning or signal "optimisation" on live data: the point of
  the system is fixed, auditable rules.
- Multi-user or hosted product: out of scope for a personal system; the
  config and Docker packaging keep the door open.
- Holding positions overnight: everything expires same day and is
  flattened before the close by design.
