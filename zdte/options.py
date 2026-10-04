"""Option contract model, strike selection and Black-Scholes pricing.

Pricing is used only by the paper broker to mark 0DTE contracts to a
plausible value as the underlying moves and time decays.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime

from zdte.clock import session_close, to_et


@dataclass(frozen=True, slots=True)
class OptionContract:
    underlying: str
    expiry: date
    strike: float
    option_type: str  # "C" or "P"

    @property
    def occ_symbol(self) -> str:
        """OCC-style symbol, e.g. ``SPY251004C00570000``."""
        strike_part = f"{int(round(self.strike * 1000)):08d}"
        return f"{self.underlying}{self.expiry:%y%m%d}{self.option_type}{strike_part}"

    @property
    def is_call(self) -> bool:
        return self.option_type == "C"

    def intrinsic(self, spot: float) -> float:
        if self.is_call:
            return max(spot - self.strike, 0.0)
        return max(self.strike - spot, 0.0)

    def to_dict(self) -> dict:
        return {
            "symbol": self.occ_symbol,
            "underlying": self.underlying,
            "expiry": self.expiry.isoformat(),
            "strike": self.strike,
            "type": "call" if self.is_call else "put",
        }


def select_strike(spot: float, increment: float, offset: int, option_type: str) -> float:
    """At-the-money strike, nudged ``offset`` increments out of the money."""
    base = round(spot / increment) * increment
    step = increment * offset * (1 if option_type == "C" else -1)
    return round(base + step, 4)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def years_to_expiry(now: datetime, expiry: date) -> float:
    """Fraction of a year until the 16:00 ET close on ``expiry`` (>= 0)."""
    now = to_et(now)
    seconds = (session_close(expiry) - now).total_seconds()
    return max(seconds, 0.0) / (365.0 * 24 * 3600)


def black_scholes(
    spot: float, strike: float, t_years: float, iv: float, option_type: str, r: float = 0.0
) -> float:
    """Black-Scholes price for a European option, no dividends."""
    if t_years <= 0 or iv <= 0 or spot <= 0:
        return max(spot - strike, 0.0) if option_type == "C" else max(strike - spot, 0.0)
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t_years) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    disc = math.exp(-r * t_years)
    if option_type == "C":
        return spot * _norm_cdf(d1) - strike * disc * _norm_cdf(d2)
    return strike * disc * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
