from datetime import date, timedelta
from statistics import stdev
from unittest import TestCase

from recovery_trader.domain.market import DailyBar
from recovery_trader.research.market_features import build_market_feature_history, build_market_features


class MarketFeatureTests(TestCase):
    def _event_bars(self) -> list[DailyBar]:
        start = date(2026, 6, 1)
        baseline = [
            DailyBar(start + timedelta(days=index), 100 + index, 101 + index, 99 + index, 100 + index)
            for index in range(21)
        ]
        return baseline + [
            DailyBar(start + timedelta(days=21), 114, 115, 107, 108),
            DailyBar(start + timedelta(days=22), 109, 112, 108, 111),
        ]

    def test_builds_three_point_in_time_feature_groups(self) -> None:
        bars = self._event_bars()
        signal_day = bars[-2].day
        prior_day = bars[-3].day
        spy = [
            DailyBar(prior_day, 500, 501, 499, 500),
            DailyBar(signal_day, 497, 499, 494, 495),
        ]

        features = build_market_features(bars, spy, 5.0)

        self.assertIsNotNone(features)
        assert features is not None
        self.assertEqual(features.signal_day, signal_day.isoformat())
        self.assertEqual(features.entry_day, bars[-1].day.isoformat())
        self.assertAlmostEqual(features.close_to_close_drop_pct, -10.0)
        self.assertAlmostEqual(features.spy_return_pct, -1.0)
        self.assertAlmostEqual(features.excess_drop_vs_spy_pct, -9.0)
        self.assertAlmostEqual(features.overnight_gap_pct, -5.0)
        self.assertAlmostEqual(features.intraday_return_pct, (108 / 114 - 1) * 100)
        expected_returns = [(101 + index) / (100 + index) * 100 - 100 for index in range(20)]
        self.assertEqual(features.volatility_return_count, 20)
        self.assertAlmostEqual(features.prior_daily_volatility_pct, stdev(expected_returns))
        self.assertAlmostEqual(
            features.drop_volatility_multiple,
            abs(features.close_to_close_drop_pct) / stdev(expected_returns),
        )

    def test_requires_a_following_entry_session(self) -> None:
        bars = self._event_bars()[:-1]

        self.assertIsNone(build_market_features(bars, [], 5.0))

    def test_history_returns_every_actionable_event_in_order(self) -> None:
        bars = [
            DailyBar(date(2026, 8, 1), 100, 101, 99, 100),
            DailyBar(date(2026, 8, 2), 94, 95, 89, 90),
            DailyBar(date(2026, 8, 3), 92, 94, 91, 93),
            DailyBar(date(2026, 8, 4), 86, 87, 83, 84),
            DailyBar(date(2026, 8, 5), 85, 88, 84, 87),
        ]

        history = build_market_feature_history(bars, [], 5.0)

        self.assertEqual([event.signal_day for event in history], ["2026-08-02", "2026-08-04"])
        self.assertEqual([event.entry_day for event in history], ["2026-08-03", "2026-08-05"])

    def test_leaves_benchmark_and_volatility_blank_when_unavailable(self) -> None:
        bars = [
            DailyBar(date(2026, 8, 1), 100, 101, 99, 100),
            DailyBar(date(2026, 8, 2), 95, 96, 89, 90),
            DailyBar(date(2026, 8, 3), 91, 93, 90, 92),
        ]

        features = build_market_features(bars, [], 5.0)

        self.assertIsNotNone(features)
        assert features is not None
        self.assertIsNone(features.spy_return_pct)
        self.assertIsNone(features.excess_drop_vs_spy_pct)
        self.assertIsNone(features.prior_daily_volatility_pct)
        self.assertIsNone(features.drop_volatility_multiple)
        self.assertEqual(features.volatility_return_count, 0)
