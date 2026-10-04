from tests.conftest import chop, make_candles, trend
from zdte.clock import session_open
from zdte.config import SignalConfig
from zdte.signals import Direction, evaluate


def cfg(**kw):
    base = dict(ema_period=5, opening_range_minutes=3, min_bars=5, chop_lookback=10,
                chop_max_vwap_crosses=3, chop_min_atr_distance=0.0, atr_period=5)
    base.update(kw)
    return SignalConfig(**base)


def test_empty_candles_not_ready(day):
    sig = evaluate([], session_open(day), cfg())
    assert not sig.ready and sig.triggers == []


def test_uptrend_bullish_consensus_and_orb_break(day):
    c = make_candles(trend(12, step=0.2), day=day, spread=0.01)
    sig = evaluate(c, session_open(day), cfg())
    assert sig.ready
    assert sig.bias == {"vwap": 1, "ema": 1, "orb": 1}
    assert sig.consensus == Direction.BULL
    assert sig.strength_bull > sig.strength_bear


def test_orb_break_trigger_fires_once(day):
    # 3-minute opening range 100..102, then a bar inside, then a break above.
    closes = [100.0, 101.0, 102.0, 101.5, 103.0, 103.5]
    c = make_candles(closes, day=day, spread=0.0)
    sig = evaluate(c[:5], session_open(day), cfg())
    assert "orb_break_up" in sig.triggers
    sig2 = evaluate(c, session_open(day), cfg())
    assert "orb_break_up" not in sig2.triggers
    assert sig2.bias["orb"] == 1


def test_vwap_and_ema_cross_down(day):
    closes = [100.0] * 6 + [90.0]
    c = make_candles(closes, day=day, spread=0.0)
    sig = evaluate(c, session_open(day), cfg())
    assert "vwap_cross_down" in sig.triggers
    assert "ema_cross_down" in sig.triggers
    assert sig.trigger_direction == Direction.BEAR


def test_mixed_triggers_have_no_direction(day):
    from zdte.signals import Signal
    s = Signal(ts=session_open(day), close=1.0, triggers=["vwap_cross_up", "ema_cross_down"])
    assert s.trigger_direction == Direction.NONE


def test_chop_detected_by_vwap_crosses(day):
    c = make_candles(chop(30), day=day, spread=0.0)
    sig = evaluate(c, session_open(day), cfg())
    assert sig.choppy
    assert "VWAP crosses" in sig.chop_reason
    assert sig.strength_bull <= 0.5 and sig.strength_bear <= 0.5


def test_chop_detected_by_hugging_vwap(day):
    c = make_candles(trend(12, step=0.0001), day=day, spread=0.5)
    sig = evaluate(c, session_open(day), cfg(chop_max_vwap_crosses=99, chop_min_atr_distance=0.25))
    assert sig.choppy and "hugging" in sig.chop_reason


def test_not_ready_before_min_bars(day):
    c = make_candles(trend(4), day=day)
    assert not evaluate(c, session_open(day), cfg(min_bars=5)).ready


def test_signal_to_dict_roundtrip_fields(day):
    c = make_candles(trend(12), day=day)
    d = evaluate(c, session_open(day), cfg()).to_dict()
    assert {"ts", "close", "vwap", "ema", "or_high", "or_low", "bias", "triggers",
            "strength_bull", "strength_bear", "choppy", "ready"} <= set(d)
