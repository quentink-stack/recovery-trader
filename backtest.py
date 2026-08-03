"""No-look-ahead underlying-price proxy backtest for sharp-dip recovery research."""

from __future__ import annotations

from dataclasses import dataclass
from alpaca import DailyBar


@dataclass(frozen=True)
class Trade:
    entry_day: str
    exit_day: str
    entry_price: float
    exit_price: float
    return_pct: float
    exit_reason: str


@dataclass(frozen=True)
class BacktestResult:
    trades: list[Trade]

    @property
    def win_rate(self) -> float:
        return sum(trade.return_pct > 0 for trade in self.trades) / len(self.trades) * 100 if self.trades else 0

    @property
    def average_return(self) -> float:
        return sum(trade.return_pct for trade in self.trades) / len(self.trades) if self.trades else 0


@dataclass(frozen=True)
class Strategy:
    name: str
    description: str
    hold_days: int
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None


STRATEGIES = (
    Strategy("Hold 15 days", "Enter next open after a dip; exit after 15 sessions.", 15),
    Strategy("Bracket 10/12", "Enter next open; 10% stop, 12% target, or 15-session exit.", 15, 10, 12),
)


def run_dip_recovery_proxy(bars: list[DailyBar], min_dip_pct: float, hold_days: int = 15, stop_loss_pct: float = 10, take_profit_pct: float = 12) -> BacktestResult:
    """Buy the next day open after a qualifying close-to-close dip; this tests underlying return, not option P&L."""
    return run_strategy(bars, min_dip_pct, Strategy("Bracket", "", hold_days, stop_loss_pct, take_profit_pct))


def run_strategy(bars: list[DailyBar], min_dip_pct: float, strategy: Strategy) -> BacktestResult:
    trades: list[Trade] = []
    index = 1
    while index < len(bars) - 1:
        prior, signal = bars[index - 1], bars[index]
        if (signal.close / prior.close - 1) * 100 > -min_dip_pct:
            index += 1
            continue
        entry_index = index + 1
        if entry_index >= len(bars):
            break
        entry = bars[entry_index]
        exit_index = min(entry_index + strategy.hold_days, len(bars) - 1)
        exit_bar, exit_price, reason = bars[exit_index], bars[exit_index].close, "time exit"
        if strategy.stop_loss_pct is not None and strategy.take_profit_pct is not None:
            stop, target = entry.open * (1 - strategy.stop_loss_pct / 100), entry.open * (1 + strategy.take_profit_pct / 100)
            for cursor in range(entry_index, exit_index + 1):
                bar = bars[cursor]
                if bar.low <= stop:
                    exit_bar, exit_price, reason = bar, stop, "stop loss"
                    break
                if bar.high >= target:
                    exit_bar, exit_price, reason = bar, target, "take profit"
                    break
        trades.append(Trade(entry.day.isoformat(), exit_bar.day.isoformat(), entry.open, exit_price, (exit_price / entry.open - 1) * 100, reason))
        index = max(index + 1, exit_index)
    return BacktestResult(trades)
