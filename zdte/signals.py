"""Signal evaluation: turn a session's candles into a Signal snapshot.

The three rules from the design brief, evaluated on every completed
1-minute candle:

  1. close crossed above / below the session VWAP
  2. close crossed above / below the 50 EMA
  3. close broke out above / below the opening range (first 15 minutes)

A *trigger* is the event (the cross or breakout happened on this candle).
A *bias* is the state (which side of each level the close currently sits).
Strategies combine triggers and biases differently; the chop filter is
shared.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Sequence

from zdte.config import SignalConfig
from zdte.data.candles import Candle
from zdte.indicators import atr, ema_series, opening_range, sign, vwap_series


class Direction(IntEnum):
    BEAR = -1
    NONE = 0
    BULL = 1

    @property
    def label(self) -> str:
        return {1: "bullish", -1: "bearish", 0: "none"}[int(self)]


@dataclass
class Signal:
    ts: datetime
    close: float
    vwap: float | None = None
    ema: float | None = None
    or_high: float | None = None
    or_low: float | None = None
    atr: float | None = None
    ready: bool = False
    choppy: bool = False
    chop_reason: str = ""
    # +1 / -1 / 0 per component (0 = inside the opening range / undefined)
    bias: dict[str, int] = field(default_factory=dict)
    # events on this candle, e.g. "vwap_cross_up", "orb_break_down"
    triggers: list[str] = field(default_factory=list)
    strength_bull: float = 0.0
    strength_bear: float = 0.0

    @property
    def trigger_direction(self) -> Direction:
        """Net direction of this candle's triggers (NONE if mixed or empty)."""
        ups = sum(1 for t in self.triggers if t.endswith("_up"))
        downs = sum(1 for t in self.triggers if t.endswith("_down"))
        if ups and not downs:
            return Direction.BULL
        if downs and not ups:
            return Direction.BEAR
        return Direction.NONE

    @property
    def consensus(self) -> Direction:
        """Direction all three biases agree on, else NONE."""
        vals = [self.bias.get(k, 0) for k in ("vwap", "ema", "orb")]
        if all(v == 1 for v in vals):
            return Direction.BULL
        if all(v == -1 for v in vals):
            return Direction.BEAR
        return Direction.NONE

    def to_dict(self) -> dict:
        return {
            "ts": self.ts.isoformat(),
            "close": self.close,
            "vwap": self.vwap,
            "ema": self.ema,
            "or_high": self.or_high,
            "or_low": self.or_low,
            "atr": self.atr,
            "ready": self.ready,
            "choppy": self.choppy,
            "chop_reason": self.chop_reason,
            "bias": dict(self.bias),
            "triggers": list(self.triggers),
            "strength_bull": round(self.strength_bull, 3),
            "strength_bear": round(self.strength_bear, 3),
        }


def _count_crosses(closes: Sequence[float], levels: Sequence[float]) -> int:
    n = 0
    prev = None
    for c, lv in zip(closes, levels):
        s = sign(c - lv)
        if prev is not None and s != 0 and prev != 0 and s != prev:
            n += 1
        if s != 0:
            prev = s
    return n


def evaluate(
    candles: Sequence[Candle], session_open: datetime, cfg: SignalConfig
) -> Signal:
    """Compute the Signal for the most recent completed candle."""
    if not candles:
        return Signal(ts=session_open, close=float("nan"))

    closes = [c.close for c in candles]
    vwaps = vwap_series(candles)
    emas = ema_series(closes, cfg.ema_period)
    orng = opening_range(candles, session_open, cfg.opening_range_minutes)
    cur = candles[-1]
    cur_atr = atr(candles, cfg.atr_period)

    sig = Signal(
        ts=cur.ts,
        close=cur.close,
        vwap=vwaps[-1],
        ema=emas[-1],
        or_high=orng[0] if orng else None,
        or_low=orng[1] if orng else None,
        atr=cur_atr,
    )

    # --- bias (state) -------------------------------------------------------
    sig.bias["vwap"] = sign(cur.close - vwaps[-1])
    sig.bias["ema"] = sign(cur.close - emas[-1])
    if orng:
        hi, lo = orng
        sig.bias["orb"] = 1 if cur.close > hi else -1 if cur.close < lo else 0
    else:
        sig.bias["orb"] = 0

    # --- triggers (events on this candle) -----------------------------------
    if len(candles) >= 2:
        prev = candles[-2]
        p_vwap, p_ema = vwaps[-2], emas[-2]
        if prev.close <= p_vwap < cur.close or prev.close < p_vwap <= cur.close:
            sig.triggers.append("vwap_cross_up")
        elif prev.close >= p_vwap > cur.close or prev.close > p_vwap >= cur.close:
            sig.triggers.append("vwap_cross_down")
        if prev.close <= p_ema < cur.close or prev.close < p_ema <= cur.close:
            sig.triggers.append("ema_cross_up")
        elif prev.close >= p_ema > cur.close or prev.close > p_ema >= cur.close:
            sig.triggers.append("ema_cross_down")
        if orng:
            hi, lo = orng
            if prev.close <= hi < cur.close:
                sig.triggers.append("orb_break_up")
            elif prev.close >= lo > cur.close:
                sig.triggers.append("orb_break_down")

    # --- chop filter --------------------------------------------------------
    window_c = closes[-cfg.chop_lookback :]
    window_v = vwaps[-cfg.chop_lookback :]
    crosses = _count_crosses(window_c, window_v)
    if crosses >= cfg.chop_max_vwap_crosses:
        sig.choppy = True
        sig.chop_reason = f"{crosses} VWAP crosses in last {len(window_c)} bars"
    elif cur_atr and abs(cur.close - vwaps[-1]) < cfg.chop_min_atr_distance * cur_atr:
        sig.choppy = True
        sig.chop_reason = "close hugging VWAP (< %.2f ATR)" % cfg.chop_min_atr_distance

    # --- readiness ----------------------------------------------------------
    sig.ready = len(candles) >= cfg.min_bars and orng is not None

    # --- scanner strength ---------------------------------------------------
    # Share of components agreeing with each side, plus a bump for a fresh
    # trigger, minus a penalty for chop. Purely descriptive; strategies do
    # not consume it.
    bull_votes = sum(1 for v in sig.bias.values() if v == 1)
    bear_votes = sum(1 for v in sig.bias.values() if v == -1)
    n = max(len(sig.bias), 1)
    trig = sig.trigger_direction
    bull = bull_votes / n * 0.75 + (0.25 if trig == Direction.BULL else 0.0)
    bear = bear_votes / n * 0.75 + (0.25 if trig == Direction.BEAR else 0.0)
    if sig.choppy:
        bull *= 0.5
        bear *= 0.5
    if not sig.ready:
        bull *= 0.5
        bear *= 0.5
    sig.strength_bull = min(bull, 1.0)
    sig.strength_bear = min(bear, 1.0)
    return sig
