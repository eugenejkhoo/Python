import json
from datetime import date, timedelta

from zdte.backtest import run_backtest
from zdte.clock import SimClock, session_close, session_open
from zdte.config import EngineConfig
from zdte.data.feeds import CSVFeed, SimulatedFeed
from zdte.engine import Engine, next_session_open
from zdte.ledger import Ledger


def small_cfg(**over):
    raw = {
        "symbols": [{"symbol": "SPY", "implied_vol": 0.18, "sim_start_price": 570.0},
                    {"symbol": "QQQ", "implied_vol": 0.22, "sim_start_price": 490.0}],
        "strategies": [{"name": "og", "risk": {"profit_lock": 200.0}},
                       {"name": "trending", "risk": {"contracts": 2}}],
        "signals": {"chop_max_vwap_crosses": 4},
    }
    raw.update(over)
    return EngineConfig.from_dict(raw)


def test_full_simulated_session_trades_and_flattens(tmp_path):
    day = date(2026, 10, 2)
    cfg = small_cfg(state_dir=str(tmp_path / "state"))
    clock = SimClock(session_open(day), end=session_close(day) + timedelta(minutes=1))
    eng = Engine(cfg, clock=clock)
    eng.run()

    assert eng.ticks == 390
    assert len(eng.bots) == 4
    assert all(b.position is None for b in eng.bots)          # nothing held past the close
    assert len(eng.ledger.trades) > 0
    for t in eng.ledger.trades:
        assert t.hold_minutes <= cfg.strategies[0].risk.auto_exit_minutes + 1 or t.strategy == "trending"
        assert t.exit_ts > t.entry_ts

    state = json.loads((tmp_path / "state" / "state.json").read_text())
    assert state["total_pnl"] == round(sum(t.pnl for t in eng.ledger.trades), 2)
    assert {b["name"] for b in state["leaderboard"]} == {b.name for b in eng.bots}
    assert state["leaderboard"] == sorted(state["leaderboard"], key=lambda b: (b["total_pnl"], b["day_pnl"]), reverse=True)
    assert [s["symbol"] for s in state["scanner"]] == ["SPY", "QQQ"]
    assert set(state["charts"]) == {"SPY", "QQQ"}
    ch = state["charts"]["SPY"]
    assert len(ch["close"]) == len(ch["vwap"]) == len(ch["ema"]) == len(ch["ts"]) == 120
    assert (tmp_path / "state" / "trades.jsonl").exists()

    # the ledger reloads from disk
    again = Ledger(tmp_path / "state" / "trades.jsonl")
    assert len(again.trades) == len(eng.ledger.trades)


def test_simulated_feed_is_deterministic_and_complete():
    f = SimulatedFeed({"SPY": 570.0}, seed=3)
    g = SimulatedFeed({"SPY": 570.0}, seed=3)
    day = date(2026, 10, 2)
    full = f.session_candles("SPY", session_close(day))
    assert len(full) == 390
    assert full == g.session_candles("SPY", session_close(day))
    assert len(f.session_candles("SPY", session_open(day) + timedelta(minutes=5))) == 5
    assert all(c.low <= min(c.open, c.close) <= max(c.open, c.close) <= c.high for c in full)


def test_csv_feed_roundtrip_and_backtest(tmp_path):
    sim = SimulatedFeed({"SPY": 570.0}, seed=11)
    days = [date(2026, 9, 28), date(2026, 9, 29)]
    rows = [("SPY", c) for d in days for c in sim.session_candles("SPY", session_close(d))]
    path = tmp_path / "bars.csv"
    CSVFeed.write(path, rows)
    feed = CSVFeed(path)
    assert feed.symbols == ["SPY"] and feed.trading_days() == days

    cfg = small_cfg(symbols=[{"symbol": "SPY", "sim_start_price": 570.0}], feed="csv", csv_path=str(path))
    result = run_backtest(cfg, feed=feed)
    assert result.days == days
    s = result.summary()
    assert s["days"] == 2 and set(s["by_bot"]) == {"SPY-og", "SPY-trending"}
    assert "Backtest: 2 session(s)" in result.report()


def test_backtest_sim_default_days():
    cfg = small_cfg(symbols=[{"symbol": "SPY", "sim_start_price": 570.0}],
                    strategies=[{"name": "og"}])
    result = run_backtest(cfg, days=2, end=date(2026, 10, 2))
    assert result.days == [date(2026, 10, 1), date(2026, 10, 2)]


def test_next_session_open_skips_weekend_and_holiday():
    from zdte.clock import ET
    from datetime import datetime
    fri_close = datetime(2026, 10, 2, 16, 5, tzinfo=ET)
    assert next_session_open(fri_close).date() == date(2026, 10, 5)
    pre = datetime(2026, 10, 2, 8, 0, tzinfo=ET)
    assert next_session_open(pre) == session_open(date(2026, 10, 2))
    before_labor_day = datetime(2026, 9, 5, 12, 0, tzinfo=ET)   # Saturday; Mon 7th is a holiday
    assert next_session_open(before_labor_day).date() == date(2026, 9, 8)


def test_config_validation():
    import pytest
    with pytest.raises(ValueError):
        EngineConfig.from_dict({"symbols": [{"symbol": "SPY"}], "strategies": [{"name": "nope"}]})
    with pytest.raises(ValueError):
        EngineConfig.from_dict({"feed": "csv", "symbols": [{"symbol": "SPY"}], "strategies": [{"name": "og"}]})
    with pytest.raises(ValueError):
        EngineConfig.from_dict({"symbols": [{"symbol": "SPY"}], "strategies": [{"name": "og"}], "bogus": 1})
    cfg = EngineConfig.default()
    assert [s.symbol for s in cfg.symbols] == ["SPY", "QQQ", "IWM"]
