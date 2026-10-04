"""Command-line entry points.

    python -m zdte run        [--config config.toml] [--sim] [--speed N] [--dashboard]
    python -m zdte backtest   [--config config.toml] [--days N | --start --end] [--csv file]
    python -m zdte dashboard  [--config config.toml] [--port 8080]
    python -m zdte export     [--state state/state.json] [--out export/]
    python -m zdte ibkr-check [--host 127.0.0.1] [--port 4002] [--client-id 7]
    python -m zdte fetch      --symbols SPY,QQQ,IWM --days 5 --out data/bars.csv
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from zdte.backtest import run_backtest
from zdte.clock import ET, SimClock, session_close, session_open
from zdte.config import EngineConfig
from zdte.engine import Engine


def _load(path: str | None) -> EngineConfig:
    if path and Path(path).exists():
        return EngineConfig.load(path)
    if path:
        print(f"config {path} not found; using built-in defaults", file=sys.stderr)
    return EngineConfig.default()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_run(args: argparse.Namespace) -> int:
    cfg = _load(args.config)
    if args.sim:
        cfg.feed = "sim"
    if cfg.mode == "alpaca" and not args.i_understand_live_trading and not cfg.alpaca.paper:
        print("Refusing to start a live Alpaca session without --i-understand-live-trading", file=sys.stderr)
        return 2

    clock = None
    if args.sim:
        day = args.day or date.today()
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        clock = SimClock(session_open(day), end=session_close(day) + timedelta(minutes=1),
                         real_seconds_per_step=1.0 / args.speed if args.speed else 0.0)

    engine = Engine(cfg, clock=clock)
    server = None
    if args.dashboard:
        from zdte.dashboard.server import start_dashboard
        server = start_dashboard(engine.snapshot, port=args.port or cfg.dashboard_port)
        print(f"dashboard: http://localhost:{server.port}/")

    def _stop(*_):
        print("stopping…", file=sys.stderr)
        engine.stop()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:
        engine.run()
    finally:
        if server:
            server.stop()
    snap = engine.snapshot()
    print(f"done: total P&L ${snap.get('total_pnl', 0):,.2f} over {snap.get('stats', {}).get('trades', 0)} trades")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    cfg = _load(args.config)
    if args.csv:
        cfg.feed, cfg.csv_path = "csv", args.csv
    elif cfg.feed == "yfinance":
        cfg.feed = "sim"
    start = date.fromisoformat(args.start) if args.start else None
    end = date.fromisoformat(args.end) if args.end else None
    result = run_backtest(cfg, start=start, end=end, days=args.days)
    print(result.report())
    if args.json:
        import json
        Path(args.json).write_text(json.dumps(result.summary(), indent=2, default=str))
        print(f"\nwrote {args.json}")
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    from zdte.dashboard.server import file_state_provider, start_dashboard
    cfg = _load(args.config)
    state = Path(cfg.state_dir) / "state.json"
    server = start_dashboard(file_state_provider(state), port=args.port or cfg.dashboard_port)
    print(f"dashboard: http://localhost:{server.port}/  (reading {state})")
    try:
        signal.pause()
    except (KeyboardInterrupt, AttributeError):
        pass
    finally:
        server.stop()
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Bake state/state.json into standalone dashboard pages."""
    import json
    from zdte.dashboard.export import export_snapshot
    cfg = _load(args.config)
    state_path = Path(args.state or Path(cfg.state_dir) / "state.json")
    if not state_path.exists():
        print(f"{state_path} not found; run the engine first", file=sys.stderr)
        return 2
    written = export_snapshot(json.loads(state_path.read_text()), args.out)
    for name, path in written.items():
        print(f"{name}: {path}")
    return 0


def cmd_ibkr_check(args: argparse.Namespace) -> int:
    from zdte.ibkr_check import main as check
    return check(args.host, args.port, args.client_id)


def cmd_fetch(args: argparse.Namespace) -> int:
    """Download recent 1-minute bars with yfinance into the CSV feed format."""
    try:
        import yfinance as yf
    except ImportError:
        print("pip install yfinance", file=sys.stderr)
        return 2
    from zdte.clock import to_et
    from zdte.data.candles import Candle
    from zdte.data.feeds import CSVFeed

    rows: list[tuple[str, Candle]] = []
    end = datetime.now(tz=ET)
    start = end - timedelta(days=args.days + 1)
    for sym in [s.strip().upper() for s in args.symbols.split(",") if s.strip()]:
        df = yf.download(sym, start=start, end=end, interval="1m", progress=False,
                         auto_adjust=False, prepost=False)
        if df is None or df.empty:
            print(f"{sym}: no data", file=sys.stderr)
            continue
        if hasattr(df.columns, "levels"):
            df.columns = [c[0] for c in df.columns]
        n = 0
        for ts, row in df.iterrows():
            ts = to_et(ts.to_pydatetime())
            rows.append((sym, Candle(ts, float(row["Open"]), float(row["High"]), float(row["Low"]),
                                     float(row["Close"]), float(row["Volume"]))))
            n += 1
        print(f"{sym}: {n} bars")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    CSVFeed.write(args.out, rows)
    print(f"wrote {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="zdte", description="Rule-based 0DTE options bots")
    p.add_argument("-c", "--config", default="config.toml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the bots (paper by default)")
    r.add_argument("--sim", action="store_true", help="simulated feed + simulated clock for one session")
    r.add_argument("--day", type=date.fromisoformat, help="session date for --sim (default: today)")
    r.add_argument("--speed", type=float, default=0.0,
                   help="with --sim: simulated minutes per real second (0 = as fast as possible)")
    r.add_argument("--dashboard", action="store_true", help="serve the dashboard from this process")
    r.add_argument("--port", type=int)
    r.add_argument("--i-understand-live-trading", action="store_true")
    r.set_defaults(func=cmd_run)

    b = sub.add_parser("backtest", help="replay sessions through the bots")
    b.add_argument("--csv", help="1-minute bars CSV (symbol,ts,open,high,low,close,volume)")
    b.add_argument("--days", type=int, default=5, help="sim sessions to run when no CSV is given")
    b.add_argument("--start")
    b.add_argument("--end")
    b.add_argument("--json", help="write the summary to this file")
    b.set_defaults(func=cmd_backtest)

    d = sub.add_parser("dashboard", help="serve the dashboard from state/state.json")
    d.add_argument("--port", type=int)
    d.set_defaults(func=cmd_dashboard)

    e = sub.add_parser("export", help="write standalone dashboard pages with the current state baked in")
    e.add_argument("--state", help="state.json to export (default: <state_dir>/state.json)")
    e.add_argument("--out", default="export", help="output directory")
    e.set_defaults(func=cmd_export)

    k = sub.add_parser("ibkr-check", help="verify IB Gateway connectivity, account, quotes and option chain")
    k.add_argument("--host", default="127.0.0.1")
    k.add_argument("--port", type=int, default=4002, help="4002 = paper gateway, 4001 = live gateway, 7497/7496 = TWS")
    k.add_argument("--client-id", type=int, default=7)
    k.set_defaults(func=cmd_ibkr_check)

    f = sub.add_parser("fetch", help="download 1-minute bars to CSV via yfinance")
    f.add_argument("--symbols", default="SPY,QQQ,IWM")
    f.add_argument("--days", type=int, default=5, help="calendar days back (Yahoo allows <= 7 for 1m)")
    f.add_argument("--out", default="data/bars.csv")
    f.set_defaults(func=cmd_fetch)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    return args.func(args)
