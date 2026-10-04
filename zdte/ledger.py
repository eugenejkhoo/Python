"""Trade ledger: records every round trip and derives P&L views.

Persists to a JSON-lines file so the engine can be restarted without
losing history, and so the dashboard / reports can read it independently.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path


@dataclass
class TradeRecord:
    bot: str
    symbol: str
    strategy: str
    contract: str
    option_type: str
    strike: float
    qty: int
    entry_ts: str
    entry_price: float
    exit_ts: str
    exit_price: float
    commission: float
    pnl: float
    entry_reason: str
    exit_reason: str
    hold_minutes: float

    @property
    def day(self) -> date:
        return datetime.fromisoformat(self.exit_ts).date()


class Ledger:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.trades: list[TradeRecord] = []
        if self.path and self.path.exists():
            self._load()

    def _load(self) -> None:
        with open(self.path) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    self.trades.append(TradeRecord(**json.loads(line)))

    def record(self, trade: TradeRecord) -> None:
        self.trades.append(trade)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a") as fh:
                fh.write(json.dumps(asdict(trade)) + "\n")

    # -- views -----------------------------------------------------------
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    def pnl_by_bot(self) -> dict[str, float]:
        out: dict[str, float] = defaultdict(float)
        for t in self.trades:
            out[t.bot] += t.pnl
        return dict(out)

    def pnl_by_day(self) -> dict[date, float]:
        out: dict[date, float] = defaultdict(float)
        for t in self.trades:
            out[t.day] += t.pnl
        return dict(sorted(out.items()))

    def day_pnl(self, day: date, bot: str | None = None) -> float:
        return sum(t.pnl for t in self.trades if t.day == day and (bot is None or t.bot == bot))

    def equity_curve(self) -> list[tuple[str, float]]:
        pts: list[tuple[str, float]] = []
        cum = 0.0
        for t in sorted(self.trades, key=lambda t: t.exit_ts):
            cum += t.pnl
            pts.append((t.exit_ts, round(cum, 2)))
        return pts

    def stats(self, bot: str | None = None) -> dict:
        ts = [t for t in self.trades if bot is None or t.bot == bot]
        wins = [t.pnl for t in ts if t.pnl > 0]
        losses = [t.pnl for t in ts if t.pnl <= 0]
        gross_win, gross_loss = sum(wins), -sum(losses)
        return {
            "trades": len(ts),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(ts), 3) if ts else 0.0,
            "pnl": round(sum(t.pnl for t in ts), 2),
            "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
            "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0.0,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else (float("inf") if wins else 0.0),
            "days": len({t.day for t in ts}),
        }

    def recent(self, n: int = 20) -> list[dict]:
        return [asdict(t) for t in sorted(self.trades, key=lambda t: t.exit_ts)[-n:]][::-1]
