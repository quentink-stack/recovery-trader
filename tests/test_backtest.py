from datetime import date
from unittest import TestCase

from backtest import DailyBar, Strategy, run_strategy


class BacktestStrategyTests(TestCase):
    def make_bars(self) -> list[DailyBar]:
        return [
            DailyBar(date(2024, 1, 1), 100.0, 101.0, 99.0, 100.0),
            DailyBar(date(2024, 1, 2), 100.0, 100.0, 90.0, 90.0),
            DailyBar(date(2024, 1, 3), 88.0, 92.0, 86.0, 89.0),
            DailyBar(date(2024, 1, 4), 89.0, 90.0, 88.0, 88.5),
            DailyBar(date(2024, 1, 5), 90.0, 92.0, 89.0, 91.0),
        ]

    def test_bounce_confirmation_strategy_skips_weak_recovery(self) -> None:
        strategy = Strategy(
            name="Bounce confirmed",
            description="Require a meaningful rebound before entry.",
            hold_days=2,
            entry_confirmation_pct=2.0,
        )

        result = run_strategy(self.make_bars(), 5.0, strategy)

        self.assertEqual(result.trades, [])
