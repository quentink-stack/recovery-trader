from datetime import date, timedelta
from unittest import TestCase

from recovery_trader.domain.market import DailyBar
from recovery_trader.domain.screener import WatchlistItem
from recovery_trader.research.market_feature_lab import (
    build_market_event_outcomes,
    build_market_feature_observations,
    chronological_evaluation_periods,
    select_sector_sample,
)


class MarketFeatureLabTests(TestCase):
    def test_chronological_split_keeps_same_day_events_together(self) -> None:
        days = [
            date(2025, 1, 1),
            date(2025, 2, 1),
            date(2025, 3, 1),
            date(2025, 3, 1),
            date(2025, 4, 1),
        ]

        periods = chronological_evaluation_periods(days, [True, True, True, True, False])

        self.assertEqual(periods[0], "Discovery (older 70%)")
        self.assertEqual(periods[1], "Discovery (older 70%)")
        self.assertEqual(periods[2], "Holdout (recent 30%)")
        self.assertEqual(periods[2], periods[3])
        self.assertEqual(periods[4], "Incomplete 30-session outcome")

    def test_sector_sample_is_bounded_and_repeatable(self) -> None:
        constituents = [
            WatchlistItem(f"T{index}", f"Tech {index}", "Technology") for index in range(8)
        ] + [
            WatchlistItem(f"E{index}", f"Energy {index}", "Energy") for index in range(8)
        ]

        first = select_sector_sample(constituents, {"Technology", "Energy"}, 3)
        second = select_sector_sample(constituents, {"Technology", "Energy"}, 3)

        self.assertEqual(first, second)
        self.assertEqual(len(first), 6)
        self.assertEqual({item.sector for item in first}, {"Technology", "Energy"})

    def test_observations_include_all_events_in_requested_window(self) -> None:
        item = WatchlistItem("TEST", "Test Co.", "Industrials")
        bars = [
            DailyBar(date(2026, 7, 1), 100, 101, 99, 100),
            DailyBar(date(2026, 7, 2), 94, 95, 89, 90),
            DailyBar(date(2026, 7, 3), 91, 93, 90, 92),
            DailyBar(date(2026, 8, 1), 92, 94, 91, 93),
            DailyBar(date(2026, 8, 2), 86, 88, 83, 84),
            DailyBar(date(2026, 8, 3), 85, 87, 84, 86),
        ]
        spy = [
            DailyBar(date(2026, 8, 1), 500, 501, 499, 500),
            DailyBar(date(2026, 8, 2), 496, 498, 494, 495),
        ]

        observations = build_market_feature_observations(
            (item,), {"TEST": bars}, spy, 5.0, date(2026, 8, 1)
        )

        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].signal_day, date(2026, 8, 2))
        self.assertAlmostEqual(observations[0].close_to_close_drop_pct, (84 / 93 - 1) * 100)
        self.assertAlmostEqual(observations[0].excess_drop_vs_spy_pct, (84 / 93 - 1) * 100 + 1)
        self.assertFalse(observations[0].complete_30_sessions)
        self.assertIsNone(observations[0].return_5_sessions_pct)

    def test_outcomes_use_entry_open_and_trading_session_horizons(self) -> None:
        entry_day = date(2026, 6, 1)
        bars = [
            DailyBar(
                entry_day + timedelta(days=index),
                92 + index,
                93 + index,
                90 + index,
                92 + index,
            )
            for index in range(30)
        ]

        outcomes = build_market_event_outcomes(bars, entry_day, 92, 100)

        self.assertAlmostEqual(outcomes.return_5_sessions_pct, (96 / 92 - 1) * 100)
        self.assertAlmostEqual(outcomes.return_10_sessions_pct, (101 / 92 - 1) * 100)
        self.assertAlmostEqual(outcomes.return_20_sessions_pct, (111 / 92 - 1) * 100)
        self.assertAlmostEqual(outcomes.return_30_sessions_pct, (121 / 92 - 1) * 100)
        self.assertTrue(outcomes.recovered_prior_close_30_sessions)
        self.assertEqual(outcomes.sessions_to_recovery, 9)
        self.assertAlmostEqual(outcomes.max_drawdown_30_sessions_pct, (90 / 92 - 1) * 100)
        self.assertAlmostEqual(outcomes.max_favorable_30_sessions_pct, (122 / 92 - 1) * 100)
        self.assertTrue(outcomes.complete_30_sessions)

    def test_incomplete_window_does_not_turn_unknown_recovery_into_failure(self) -> None:
        entry_day = date(2026, 8, 1)
        bars = [
            DailyBar(entry_day + timedelta(days=index), 90, 92, 89, 91)
            for index in range(10)
        ]

        outcomes = build_market_event_outcomes(bars, entry_day, 90, 100)

        self.assertIsNotNone(outcomes.return_5_sessions_pct)
        self.assertIsNotNone(outcomes.return_10_sessions_pct)
        self.assertIsNone(outcomes.return_20_sessions_pct)
        self.assertIsNone(outcomes.return_30_sessions_pct)
        self.assertIsNone(outcomes.recovered_prior_close_30_sessions)
        self.assertIsNone(outcomes.max_drawdown_30_sessions_pct)
        self.assertFalse(outcomes.complete_30_sessions)
