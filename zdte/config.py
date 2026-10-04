"""Configuration schema and loader (TOML)."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any


def _build(cls, raw: dict[str, Any]):
    """Instantiate a dataclass from a dict, ignoring unknown keys."""
    allowed = {f.name for f in fields(cls)}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"{cls.__name__}: unknown keys {sorted(unknown)}")
    return cls(**raw)


@dataclass
class SignalConfig:
    ema_period: int = 50
    opening_range_minutes: int = 15
    min_bars: int = 20                 # bars needed before any signal is trusted
    chop_lookback: int = 20            # window (bars) for the chop filter
    chop_max_vwap_crosses: int = 3     # >= this many VWAP crosses in window = chop
    chop_min_atr_distance: float = 0.25  # close must sit this many ATRs from VWAP
    atr_period: int = 14


@dataclass
class RiskConfig:
    contracts: int = 1                  # contracts per trade
    profit_lock: float = 500.0          # $ realized/day -> bot stops entering
    daily_max_loss: float = -500.0      # $ realized/day -> bot stops entering
    take_profit_pct: float = 0.50       # +50% on premium -> exit
    stop_loss_pct: float = 0.35         # -35% on premium -> exit
    auto_exit_minutes: int = 30         # hard time stop per position
    flatten_minutes_before_close: int = 10
    no_entry_minutes_before_close: int = 45
    max_trades_per_day: int = 6
    cooldown_minutes: int = 3           # wait after an exit before re-entering


@dataclass
class StrategyConfig:
    name: str                           # "og" | "trending"
    enabled: bool = True
    strike_offset: int = 0              # 0 = ATM; +1 = one strike OTM, -1 = one ITM
    risk: RiskConfig = field(default_factory=RiskConfig)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "StrategyConfig":
        raw = dict(raw)
        risk = raw.pop("risk", {})
        return cls(**raw, risk=_build(RiskConfig, risk))


@dataclass
class SymbolConfig:
    symbol: str
    strike_increment: float = 1.0
    implied_vol: float = 0.20           # annualised IV used by the paper broker
    sim_start_price: float = 500.0      # simulated feed only


@dataclass
class PaperConfig:
    starting_cash: float = 25_000.0
    commission_per_contract: float = 0.65
    spread_pct: float = 0.04            # bid/ask width as a fraction of mid
    min_spread: float = 0.02
    risk_free_rate: float = 0.045


@dataclass
class AlpacaConfig:
    paper: bool = True
    key_env: str = "ALPACA_API_KEY"
    secret_env: str = "ALPACA_API_SECRET"


@dataclass
class EngineConfig:
    mode: str = "paper"                 # "paper" | "alpaca"
    feed: str = "sim"                   # "sim" | "yfinance" | "csv"
    csv_path: str | None = None
    state_dir: str = "state"
    dashboard_port: int = 8080
    sim_seed: int = 7
    symbols: list[SymbolConfig] = field(default_factory=list)
    strategies: list[StrategyConfig] = field(default_factory=list)
    signals: SignalConfig = field(default_factory=SignalConfig)
    paper: PaperConfig = field(default_factory=PaperConfig)
    alpaca: AlpacaConfig = field(default_factory=AlpacaConfig)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "EngineConfig":
        raw = dict(raw)
        symbols = [_build(SymbolConfig, s) for s in raw.pop("symbols", [])]
        strategies = [StrategyConfig.from_dict(s) for s in raw.pop("strategies", [])]
        signals = _build(SignalConfig, raw.pop("signals", {}))
        paper = _build(PaperConfig, raw.pop("paper", {}))
        alpaca = _build(AlpacaConfig, raw.pop("alpaca", {}))
        cfg = _build(cls, raw)
        cfg.symbols, cfg.strategies = symbols, strategies
        cfg.signals, cfg.paper, cfg.alpaca = signals, paper, alpaca
        cfg.validate()
        return cfg

    @classmethod
    def load(cls, path: str | Path) -> "EngineConfig":
        with open(path, "rb") as fh:
            return cls.from_dict(tomllib.load(fh))

    @classmethod
    def default(cls) -> "EngineConfig":
        return cls.from_dict(
            {
                "symbols": [
                    {"symbol": "SPY", "implied_vol": 0.16, "sim_start_price": 570.0},
                    {"symbol": "QQQ", "implied_vol": 0.21, "sim_start_price": 490.0},
                    {"symbol": "IWM", "implied_vol": 0.24, "sim_start_price": 225.0},
                ],
                "strategies": [{"name": "og"}, {"name": "trending"}],
            }
        )

    def validate(self) -> None:
        if self.mode not in {"paper", "alpaca"}:
            raise ValueError(f"mode must be 'paper' or 'alpaca', got {self.mode!r}")
        if self.feed not in {"sim", "yfinance", "csv"}:
            raise ValueError(f"feed must be 'sim', 'yfinance' or 'csv', got {self.feed!r}")
        if self.feed == "csv" and not self.csv_path:
            raise ValueError("feed = 'csv' requires csv_path")
        if not self.symbols:
            raise ValueError("at least one symbol is required")
        if not self.strategies:
            raise ValueError("at least one strategy is required")
        for s in self.strategies:
            if s.name not in {"og", "trending"}:
                raise ValueError(f"unknown strategy {s.name!r}")
            if s.risk.contracts < 1:
                raise ValueError("risk.contracts must be >= 1")
            if s.risk.daily_max_loss > 0:
                raise ValueError("risk.daily_max_loss must be <= 0")
