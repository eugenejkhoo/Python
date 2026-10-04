"""Connectivity and readiness check for Interactive Brokers.

Run this on the machine where IB Gateway (or TWS) is running:

    python -m zdte ibkr-check            # paper gateway on 127.0.0.1:4002
    python -m zdte ibkr-check --port 4001  # live gateway (read-only checks)

It connects, identifies the account, pulls a stock quote, today's 1-minute
bars and the nearest-expiry ATM option quote for each symbol, and prints a
pass/fail checklist. Nothing is ordered. Copy the whole output when
reporting back; it names the exact failing step.
"""
from __future__ import annotations

import math
import sys
from datetime import date, datetime

SYMBOLS = ("SPY", "QQQ", "IWM")

# IBKR error codes that are informational, not failures.
INFO_CODES = {2104, 2106, 2107, 2108, 2158, 2119, 2100}


def _isnum(x) -> bool:
    try:
        return x is not None and not math.isnan(float(x))
    except (TypeError, ValueError):
        return False


def collect(host: str, port: int, client_id: int, symbols=SYMBOLS, timeout: float = 15.0) -> dict:
    """Connect and gather facts. Returns a plain dict for ``format_report``."""
    try:
        from ib_async import IB, Option, Stock
    except ImportError:
        return {"fatal": "ib_async is not installed: pip install 'zdte-bots[ibkr]' (or pip install ib_async)"}

    report: dict = {"host": host, "port": port, "client_id": client_id, "errors": [], "symbols": {}}
    ib = IB()
    ib.errorEvent += lambda reqId, code, msg, *rest: report["errors"].append((reqId, code, str(msg)))
    try:
        ib.connect(host, port, clientId=client_id, timeout=timeout)
    except Exception as exc:
        report["fatal"] = (f"could not connect to {host}:{port}: {exc}. Is IB Gateway running, is the API enabled "
                           f"(Configure > Settings > API > Settings), and is the socket port {port}?")
        return report
    try:
        report["server_version"] = ib.client.serverVersion()
        report["server_time"] = str(ib.reqCurrentTime())
        accounts = ib.managedAccounts()
        report["accounts"] = accounts
        report["paper"] = all(a.startswith("DU") for a in accounts) if accounts else None
        summary = {}
        for row in ib.accountSummary():
            if row.tag in ("NetLiquidation", "BuyingPower", "AvailableFunds", "AccountType") and row.currency in ("USD", ""):
                summary[row.tag] = row.value
        report["summary"] = summary
        ib.reqMarketDataType(1)  # live; falls through to errors if unsubscribed
        today = date.today()
        for sym in symbols:
            entry: dict = {}
            report["symbols"][sym] = entry
            stock = Stock(sym, "SMART", "USD")
            try:
                ib.qualifyContracts(stock)
                entry["conId"] = stock.conId
            except Exception as exc:
                entry["error"] = f"qualify failed: {exc}"
                continue
            t = ib.reqMktData(stock, "", False, False)
            ib.sleep(2.5)
            entry["stock_quote"] = {"bid": t.bid, "ask": t.ask, "last": t.last, "delayed": t.marketDataType == 3}
            ib.cancelMktData(stock)
            try:
                bars = ib.reqHistoricalData(stock, endDateTime="", durationStr="1 D", barSizeSetting="1 min",
                                            whatToShow="TRADES", useRTH=True, formatDate=1)
                entry["bars_today"] = len(bars)
                entry["last_bar"] = str(bars[-1].date) if bars else None
            except Exception as exc:
                entry["bars_error"] = str(exc)
            try:
                chains = ib.reqSecDefOptParams(stock.symbol, "", stock.secType, stock.conId)
                chain = next((c for c in chains if c.exchange == "SMART" and c.tradingClass == sym), None) \
                    or next((c for c in chains if c.exchange == "SMART"), None)
                if chain is None:
                    entry["chain_error"] = "no SMART option chain returned"
                    continue
                expiries = sorted(e for e in chain.expirations if datetime.strptime(e, "%Y%m%d").date() >= today)
                entry["next_expiry"] = expiries[0] if expiries else None
                entry["zero_dte_today"] = bool(expiries) and expiries[0] == today.strftime("%Y%m%d")
                spot = next((v for v in (t.last, t.close, (t.bid + t.ask) / 2 if _isnum(t.bid) and _isnum(t.ask) else None) if _isnum(v)), None)
                if expiries and spot:
                    strike = min(chain.strikes, key=lambda k: abs(k - spot))
                    opt = Option(sym, expiries[0], strike, "C", "SMART", tradingClass=chain.tradingClass)
                    ib.qualifyContracts(opt)
                    ot = ib.reqMktData(opt, "", False, False)
                    ib.sleep(2.5)
                    entry["atm_call"] = {"symbol": opt.localSymbol, "strike": strike, "bid": ot.bid, "ask": ot.ask,
                                         "delayed": ot.marketDataType == 3}
                    ib.cancelMktData(opt)
            except Exception as exc:
                entry["chain_error"] = str(exc)
    finally:
        ib.disconnect()
    return report


def format_report(r: dict) -> str:
    lines = ["IBKR readiness check", "=" * 60]
    if r.get("fatal"):
        lines += [f"FAIL  {r['fatal']}"]
        return "\n".join(lines)
    ok = lambda cond, msg: lines.append(("PASS  " if cond else "FAIL  ") + msg)
    ok(True, f"connected to {r['host']}:{r['port']} (server version {r.get('server_version')}, server time {r.get('server_time')})")
    accts = r.get("accounts") or []
    ok(bool(accts), f"accounts: {', '.join(accts) or 'none'}")
    if r.get("paper") is True:
        lines.append("INFO  paper account (DU...) — correct for Phase 1")
    elif r.get("paper") is False:
        lines.append("WARN  LIVE account (U...) — use the paper login for Phase 1")
    s = r.get("summary", {})
    ok("NetLiquidation" in s, f"account summary: net liq {s.get('NetLiquidation')}, buying power {s.get('BuyingPower')}, available {s.get('AvailableFunds')}")
    for sym, e in r.get("symbols", {}).items():
        lines.append(f"-- {sym}")
        if "error" in e:
            ok(False, e["error"]); continue
        q = e.get("stock_quote", {})
        live = _isnum(q.get("bid")) and _isnum(q.get("ask")) and not q.get("delayed")
        ok(live, f"stock quote bid {q.get('bid')} ask {q.get('ask')} last {q.get('last')}" + (" (DELAYED: no US equities subscription shared with this login)" if q.get("delayed") else ""))
        if "bars_error" in e:
            ok(False, f"1-minute bars: {e['bars_error']}")
        else:
            ok((e.get("bars_today") or 0) > 0, f"1-minute bars today: {e.get('bars_today')} (last {e.get('last_bar')})")
        if "chain_error" in e:
            ok(False, f"option chain: {e['chain_error']}")
        else:
            ok(e.get("next_expiry") is not None, f"next expiry {e.get('next_expiry')}" + (" (0DTE available today)" if e.get("zero_dte_today") else " (no same-day expiry today: weekend/holiday or chain not loaded)"))
            c = e.get("atm_call")
            if c:
                olive = _isnum(c.get("bid")) and _isnum(c.get("ask")) and not c.get("delayed")
                ok(olive, f"ATM call {c['symbol']} bid {c.get('bid')} ask {c.get('ask')}" + (" (DELAYED or empty: OPRA subscription missing or not shared with paper)" if not olive else ""))
    errs = [(rid, code, msg) for rid, code, msg in r.get("errors", []) if code not in INFO_CODES]
    if errs:
        lines.append("-- API messages (non-informational)")
        for rid, code, msg in errs[:20]:
            lines.append(f"      [{code}] {msg}")
    lines.append("=" * 60)
    lines.append("Paste this whole block back when reporting.")
    return "\n".join(lines)


def main(host: str = "127.0.0.1", port: int = 4002, client_id: int = 7) -> int:
    r = collect(host, port, client_id)
    print(format_report(r))
    return 1 if r.get("fatal") else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
