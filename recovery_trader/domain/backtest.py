"""No-look-ahead price rules for sharp-dip recovery research."""

from __future__ import annotations

from dataclasses import dataclass

from recovery_trader.domain.market import DailyBar


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
    entry_confirmation_pct: float | None = None
    breakout_confirmation: bool = False
    trailing_stop_pct: float | None = None


STRATEGIES = (
    Strategy("Hold 15 days", "Enter next open after a dip; exit after 15 sessions.", 15),
    Strategy("Bracket 10/12", "Enter next open; 10% stop, 12% target, or 15-session exit.", 15, 10, 12),
    Strategy("Bounce confirmed 10/12", "Require a 2% rebound after entry before trading; then use a 10% stop / 12% target or 15-session exit.", 15, 10, 12, 2.0),
    Strategy("Fast bounce 8/10", "Require a 1.5% rebound after entry and exit on an 8-session hold, 8% stop, or 10% target.", 8, 8, 10, 1.5),
    Strategy("Breakout confirmation 10/12", "Wait for the first recovery day to close above the dip day before entering; then use a 10% stop / 12% target or 15-session exit.", 15, 10, 12, None, True),
    Strategy("Trailing stop 10%", "Enter next open; exit 10% below the highest previously observed price, or after 15 sessions.", 15, trailing_stop_pct=10.0),
)


def run_dip_recovery_proxy(bars: list[DailyBar], min_dip_pct: float, hold_days: int = 15, stop_loss_pct: float = 10, take_profit_pct: float = 12) -> BacktestResult:
    return run_strategy(bars, min_dip_pct, Strategy("Bracket", "", hold_days, stop_loss_pct, take_profit_pct))


def run_strategy(bars: list[DailyBar], min_dip_pct: float, strategy: Strategy) -> BacktestResult:
    trades: list[Trade] = []
    index = 0
    while index < len(bars) - 2:
        signal, next_day = bars[index], bars[index + 1]
        if (next_day.close / signal.close - 1) * 100 > -min_dip_pct:
            index += 1
            continue
        entry_index = index + 2
        if strategy.breakout_confirmation:
            confirmation_bar = bars[entry_index]
            if confirmation_bar.close <= signal.close:
                index = entry_index
                continue
            entry_index += 1
        if entry_index >= len(bars):
            break
        entry = bars[entry_index]
        if strategy.entry_confirmation_pct is not None:
            confirmation_index = entry_index + 1
            if confirmation_index >= len(bars):
                break
            if bars[confirmation_index].close <= entry.open * (1 + strategy.entry_confirmation_pct / 100):
                index = confirmation_index
                continue
        exit_index = min(entry_index + strategy.hold_days, len(bars) - 1)
        exit_bar, exit_price, reason = bars[exit_index], bars[exit_index].close, "time exit"
        if strategy.trailing_stop_pct is not None:
            high_water_mark = entry.open
            for cursor in range(entry_index, exit_index + 1):
                bar = bars[cursor]
                trailing_stop = high_water_mark * (1 - strategy.trailing_stop_pct / 100)
                if bar.low <= trailing_stop:
                    exit_bar, exit_price, reason = bar, trailing_stop, "trailing stop"
                    break
                high_water_mark = max(high_water_mark, bar.high)
        elif strategy.stop_loss_pct is not None and strategy.take_profit_pct is not None:
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
