from datetime import date, datetime

import pytest

from zdte.broker.paper import PaperBroker
from zdte.clock import ET
from zdte.config import PaperConfig
from zdte.options import OptionContract, black_scholes, select_strike, years_to_expiry


def test_occ_symbol():
    c = OptionContract("SPY", date(2025, 10, 4), 570.0, "C")
    assert c.occ_symbol == "SPY251004C00570000"
    p = OptionContract("IWM", date(2026, 1, 2), 225.5, "P")
    assert p.occ_symbol == "IWM260102P00225500"


def test_select_strike():
    assert select_strike(570.4, 1.0, 0, "C") == 570.0
    assert select_strike(570.6, 1.0, 0, "P") == 571.0
    assert select_strike(570.4, 1.0, 1, "C") == 571.0   # OTM call = up
    assert select_strike(570.4, 1.0, 1, "P") == 569.0   # OTM put = down
    assert select_strike(570.4, 0.5, 0, "C") == 570.5


def test_black_scholes_sanity():
    call = black_scholes(100, 100, 0.5, 0.2, "C")
    put = black_scholes(100, 100, 0.5, 0.2, "P")
    assert 5 < call < 7 and abs(call - put) < 1e-9       # ATM, r = 0 -> parity
    assert black_scholes(100, 90, 0.0, 0.2, "C") == 10.0  # expired -> intrinsic
    assert black_scholes(100, 90, 0.0, 0.2, "P") == 0.0
    assert black_scholes(100, 100, 0.5, 0.4, "C") > call  # more vol, more value


def test_years_to_expiry_decays_to_zero():
    exp = date(2026, 10, 2)
    t1 = years_to_expiry(datetime(2026, 10, 2, 10, 0, tzinfo=ET), exp)
    t2 = years_to_expiry(datetime(2026, 10, 2, 15, 0, tzinfo=ET), exp)
    assert t1 > t2 > 0
    assert years_to_expiry(datetime(2026, 10, 2, 16, 30, tzinfo=ET), exp) == 0.0


@pytest.fixture
def broker():
    return PaperBroker(PaperConfig(starting_cash=5000.0, commission_per_contract=0.65,
                                   spread_pct=0.04, min_spread=0.02), {"SPY": 0.2})


def test_paper_round_trip_pays_spread_and_commission(broker):
    now = datetime(2026, 10, 2, 10, 0, tzinfo=ET)
    c = OptionContract("SPY", now.date(), 570.0, "C")
    q = broker.quote(c, 570.0, now)
    assert q.ask > q.bid >= 0.01
    fill = broker.buy("bot", c, 2, 570.0, now)
    assert fill.price == q.ask and fill.qty == 2
    pos = broker.positions()[0]
    assert pos.owner == "bot" and pos.cost_basis == pytest.approx(q.ask * 200 + 1.30)
    assert broker.cash() == pytest.approx(5000 - pos.cost_basis)
    sell = broker.sell(pos, 570.0, now)
    assert sell.price == q.bid
    assert broker.positions() == []
    assert broker.cash() < 5000.0  # lost spread + commissions


def test_paper_call_gains_when_underlying_rises(broker):
    now = datetime(2026, 10, 2, 10, 0, tzinfo=ET)
    c = OptionContract("SPY", now.date(), 570.0, "C")
    broker.buy("bot", c, 1, 570.0, now)
    pos = broker.positions()[0]
    up = broker.quote(c, 574.0, now).bid
    assert pos.unrealized(up) > 0
    assert pos.pct_return(up) > 0


def test_paper_rejects_when_out_of_cash():
    b = PaperBroker(PaperConfig(starting_cash=10.0), {"SPY": 0.2})
    now = datetime(2026, 10, 2, 10, 0, tzinfo=ET)
    with pytest.raises(RuntimeError):
        b.buy("bot", OptionContract("SPY", now.date(), 570.0, "C"), 1, 570.0, now)
