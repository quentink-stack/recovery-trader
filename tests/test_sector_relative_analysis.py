from datetime import date, timedelta
from types import SimpleNamespace
from unittest import TestCase

import pandas as pd

from recovery_trader.domain.market import DailyBar
from scripts.analyze_sector_relative import assign_groups, build_sector_events, summarize_groups
from scripts.analyze_sector_relative_matched import match_events, summarize_pairs


class SectorRelativeAnalysisTests(TestCase):
    def test_builds_matched_sector_returns_and_freezes_older_quartiles(self) -> None:
        start = date(2026, 1, 1)
        stock_bars = pd.DataFrame(
            {"ticker": "TEST", "day": (start + timedelta(days=index)).isoformat()}
            for index in range(40)
        )
        sector_bars = [
            DailyBar(start + timedelta(days=index), 100, 101, 99, 100 + index)
            for index in range(40)
        ]
        cohort = []
        for index, excess in enumerate((-10.0, -8.0, -6.0, -4.0, -2.0, 0.0, 2.0, 4.0)):
            signal_index = index + 1
            signal_day = (start + timedelta(days=signal_index)).isoformat()
            sector_return = (sector_bars[signal_index].close / sector_bars[signal_index - 1].close - 1) * 100
            event = SimpleNamespace(
                ticker="TEST",
                sector="Information Technology",
                signal_day=signal_day,
                entry_day=(start + timedelta(days=signal_index + 1)).isoformat(),
                close_to_close_drop_pct=sector_return + excess,
                recovered_prior_close_30_sessions="True",
                max_drawdown_30_sessions_pct=-5.0,
            )
            window = [
                DailyBar(start + timedelta(days=signal_index + 1 + day), 90, 95, 85, 91 + day)
                for day in range(30)
            ]
            cohort.append((event, window, "older" if index < 4 else "recent"))

        events, gaps = build_sector_events(cohort, stock_bars, {"XLK": sector_bars})
        grouped, cutpoints = assign_groups(events)
        summary = summarize_groups(grouped)

        self.assertEqual(gaps, 0)
        self.assertEqual(len(events), 8)
        for actual, expected in zip(cutpoints, (-8.5, -7.0, -5.5)):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(set(grouped.quartile.dropna().astype(str)), {
            "Q1 most negative", "Q2", "Q3", "Q4 least negative / positive"
        })
        self.assertEqual(set(summary.horizon_sessions), {5, 10, 20, 30})

    def test_matches_without_outcomes_and_reports_paired_differences(self) -> None:
        rows = []
        for index, (excess, drop, recovery) in enumerate((
            (-9.0, -8.0, False),
            (-5.0, -7.8, True),
            (-10.0, -6.0, False),
            (-4.0, -5.9, True),
        )):
            row = {
                "ticker": f"T{index}",
                "sector": "Information Technology",
                "signal_day": (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
                "period": "older",
                "stock_drop_pct": drop,
                "excess_drop_vs_sector_pct": excess,
                "recovered_prior_close_30_sessions": recovery,
            }
            for horizon in (5, 10, 20, 30):
                row[f"return_{horizon}_sessions_pct"] = float(index + horizon)
            rows.append(row)

        pairs, counts = match_events(pd.DataFrame(rows))
        summary = summarize_pairs(pairs)

        self.assertEqual(counts["matched_pairs"], 2)
        self.assertTrue((pairs.absolute_drop_difference_pp <= 1.0).all())
        self.assertEqual(set(summary.horizon_sessions), {5, 10, 20, 30})
