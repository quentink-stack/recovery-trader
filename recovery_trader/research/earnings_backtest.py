"""Point-in-time validation for deterministic earnings briefs."""

from __future__ import annotations

from dataclasses import dataclass

from recovery_trader.domain.market import DailyBar
from recovery_trader.research.earnings import EarningsBrief


@dataclass(frozen=True)
class EarningsBriefTrade:
    availability_date: str
    entry_day: str
    exit_day: str
    entry_price: float
    exit_price: float
    return_pct: float
    conclusion: str


@dataclass(frozen=True)
class EarningsBriefBacktest:
    trades: tuple[EarningsBriefTrade, ...]

    @property
    def average_return(self) -> float:
        return sum(trade.return_pct for trade in self.trades) / len(self.trades) if self.trades else 0.0


def backtest_earnings_briefs(
    briefs: list[EarningsBrief] | tuple[EarningsBrief, ...],
    bars: list[DailyBar],
    *,
    hold_sessions: int = 10,
    required_conclusion: str = "Constructive comparable-period trend",
) -> EarningsBriefBacktest:
    """Enter at the next available session after a filing/release becomes public.

    Using a strictly later trading day is intentionally conservative: it avoids
    assuming whether an earnings filing was available before or after market
    close on its filing date.
    """
    if hold_sessions < 1:
        raise ValueError("hold_sessions must be positive.")
    ordered_bars = sorted(bars, key=lambda bar: bar.day)
    trades: list[EarningsBriefTrade] = []
    for brief in sorted(briefs, key=lambda item: item.availability_date):
        if brief.conclusion != required_conclusion:
            continue
        entry_index = next((index for index, bar in enumerate(ordered_bars) if bar.day > brief.availability_date), None)
        if entry_index is None:
            continue
        exit_index = min(entry_index + hold_sessions, len(ordered_bars) - 1)
        entry, exit_bar = ordered_bars[entry_index], ordered_bars[exit_index]
        trades.append(
            EarningsBriefTrade(
                brief.availability_date.isoformat(),
                entry.day.isoformat(),
                exit_bar.day.isoformat(),
                entry.open,
                exit_bar.close,
                (exit_bar.close / entry.open - 1) * 100,
                brief.conclusion,
            )
        )
    return EarningsBriefBacktest(tuple(trades))
