from datetime import timedelta

from tests.conftest import make_candles
from zdte.clock import session_open
from zdte.indicators import atr, ema_last, ema_series, opening_range, vwap_series


def test_vwap_is_volume_weighted(day):
    c = make_candles([100.0, 110.0], day=day, spread=0.0)
    # typical prices: bar1 = (100+100+100)/3=100, bar2 = (110+100+110)/3 = 106.67
    c = [c[0], c[1].__class__(c[1].ts, 110.0, 110.0, 110.0, 110.0, 3000.0)]
    v = vwap_series(c)
    assert v[0] == 100.0
    assert abs(v[1] - (100 * 1000 + 110 * 3000) / 4000) < 1e-9


def test_vwap_zero_volume_falls_back():
    c = make_candles([100.0, 101.0], volume=0.0)
    assert vwap_series(c) == [100.0, 100.0]


def test_ema_matches_pandas_style_recursion():
    vals = [1.0, 2.0, 3.0, 4.0]
    out = ema_series(vals, 3)
    k = 0.5
    expected = [1.0]
    for v in vals[1:]:
        expected.append((v - expected[-1]) * k + expected[-1])
    assert out == expected
    assert ema_last(vals, 3) == expected[-1]


def test_opening_range_waits_for_full_window(day):
    c = make_candles([100, 101, 99, 102, 100], day=day, spread=0.0)
    assert opening_range(c, session_open(day), minutes=15) is None
    c = make_candles([100 + (i % 3) for i in range(15)], day=day, spread=0.0)
    hi, lo = opening_range(c, session_open(day), minutes=15)
    assert hi == 102.0 and lo == 100.0
    # bars after the window do not change it
    c += make_candles([150.0], day=day, start_minute=15)
    assert opening_range(c, session_open(day), minutes=15) == (102.0, 100.0)


def test_opening_range_ignores_premarket(day):
    c = make_candles([100 + i for i in range(15)], day=day, spread=0.0)
    early = c[0].__class__(session_open(day) - timedelta(minutes=5), 50, 50, 50, 50, 1)
    assert opening_range([early] + c, session_open(day)) == (114.0, 100.0)


def test_atr_positive_and_bounded():
    c = make_candles([100, 101, 100, 102, 101], spread=0.1)
    a = atr(c, 14)
    assert a is not None and 0 < a < 3
    assert atr([], 14) is None
