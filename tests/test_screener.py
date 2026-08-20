from datetime import date
from unittest import TestCase

from alpaca import DailyBar
from screener import WatchlistItem, latest_large_drop


class DropResearchTests(TestCase):
    def test_returns_first_close_on_or_after_calendar_checkpoints(self) -> None:
        bars = [
            DailyBar(date(2024, 1, 1), 100, 101, 99, 100),
            DailyBar(date(2024, 1, 2), 100, 100, 90, 90),
            DailyBar(date(2024, 1, 9), 92, 95, 91, 94),
            DailyBar(date(2024, 2, 1), 96, 98, 95, 97),
        ]

        result = latest_large_drop(WatchlistItem("TEST", "Test Co."), bars, 5.0)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.signal_close, 90)
        self.assertEqual(result.one_week_close, 94)
        self.assertEqual(result.thirty_day_close, 97)

    def test_leaves_unavailable_checkpoints_blank(self) -> None:
        bars = [
            DailyBar(date(2024, 1, 1), 100, 101, 99, 100),
            DailyBar(date(2024, 1, 2), 100, 100, 90, 90),
        ]

        result = latest_large_drop(WatchlistItem("TEST", "Test Co."), bars, 5.0)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIsNone(result.one_week_close)
        self.assertIsNone(result.thirty_day_close)
