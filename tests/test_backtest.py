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

    def test_breakout_confirmation_strategy_requires_higher_close(self) -> None:
        strategy = Strategy(
            name="Breakout confirmation",
            description="Wait for a higher close after the dip.",
            hold_days=2,
            breakout_confirmation=True,
        )

        result = run_strategy(self.make_bars(), 5.0, strategy)

        self.assertEqual(result.trades, [])

    def test_enters_at_the_third_day_open_after_a_confirmed_close_to_close_drop(self) -> None:
        strategy = Strategy(name="Hold", description="", hold_days=1)

        result = run_strategy(self.make_bars(), 5.0, strategy)

        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].entry_day, "2024-01-03")
        self.assertEqual(result.trades[0].entry_price, 88.0)

    def test_trailing_stop_exits_after_a_high_water_mark_decline(self) -> None:
        bars = [
            DailyBar(date(2024, 1, 1), 100.0, 101.0, 99.0, 100.0),
            DailyBar(date(2024, 1, 2), 100.0, 100.0, 90.0, 90.0),
            DailyBar(date(2024, 1, 3), 90.0, 100.0, 89.0, 99.0),
            DailyBar(date(2024, 1, 4), 99.0, 101.0, 90.0, 91.0),
        ]
        strategy = Strategy(
            name="Trailing stop",
            description="Trail 10% below the high-water mark.",
            hold_days=3,
            trailing_stop_pct=10.0,
        )

        result = run_strategy(bars, 5.0, strategy)

        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].exit_reason, "trailing stop")
        self.assertEqual(result.trades[0].exit_price, 90.0)
