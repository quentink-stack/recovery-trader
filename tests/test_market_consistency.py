from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import pandas as pd

from recovery_trader.domain.market import DailyBar
from recovery_trader.research.consistency import net_return_pct, simulate_exit
from scripts.analyze_market_consistency import build_trades, prepare_cohort, run_analysis, summarize


def bar(index, opening=100, close=100, low=None, high=None):
    return DailyBar(date(2025, 1, 1) + timedelta(days=index), opening,
                    high if high is not None else max(opening, close) + 1,
                    low if low is not None else min(opening, close) - 1, close)


def fixture():
    signals = (5, 10, 40, 50, 80)
    rows = []
    for ticker in ("TEST", "SPY"):
        for i in range(120):
            price = 200 if ticker == "SPY" else 100
            close = 90 if ticker == "TEST" and i in signals else price
            b = bar(i, price, close)
            rows.append({"ticker": ticker, "day": b.day.isoformat(),
                         "open": b.open, "high": b.high, "low": b.low, "close": b.close})
    events = pd.DataFrame([{
        "ticker": "TEST", "sector": "Test sector", "signal_day": bar(i).day.isoformat(),
        "entry_day": bar(i + 1).day.isoformat(), "prior_close": 100, "entry_open": 100,
        "close_to_close_drop_pct": -10, "complete_30_sessions": True,
        "drop_volatility_multiple": 3,
    } for i in signals])
    return events, pd.DataFrame(rows)


class ExitTests(TestCase):
    def test_entry_counts_as_first_session(self):
        window = [bar(i, close=100 + i) for i in range(30)]
        result = simulate_exit(window, 150, "hold_20")
        self.assertEqual((result.index, result.price, result.timing), (19, 119, "close"))

    def test_recovery_uses_next_open_and_excludes_later_low(self):
        window = [bar(i) for i in range(30)]
        window[0] = bar(0, close=110)
        window[1] = bar(1, opening=95, low=40)
        result = simulate_exit(window, 110, "recovery_30")
        self.assertEqual((result.index, result.price, result.reason), (1, 95, "recovery_close"))
        self.assertAlmostEqual(result.adverse_excursion_pct, -5)

    def test_close_stop_is_not_a_guaranteed_ten_percent_loss(self):
        window = [bar(i) for i in range(30)]
        window[0] = bar(0, close=89)
        window[1] = bar(1, opening=80)
        result = simulate_exit(window, 110, "recovery_stop_10")
        self.assertEqual((result.index, result.price, result.reason), (1, 80, "close_stop"))

    def test_trailing_uses_observed_closes_not_intraday_high(self):
        window = [bar(i) for i in range(30)]
        window[0] = bar(0, high=200)
        self.assertEqual(simulate_exit(window, 110, "trailing_close_10").reason, "time_limit")
        window[0] = bar(0, close=120)
        window[1] = bar(1, close=107)
        window[2] = bar(2, opening=104)
        result = simulate_exit(window, 110, "trailing_close_10")
        self.assertEqual((result.index, result.price), (2, 104))

    def test_last_close_does_not_require_unavailable_next_open(self):
        window = [bar(i) for i in range(30)]
        window[-1] = bar(29, close=115)
        result = simulate_exit(window, 110, "recovery_30")
        self.assertEqual((result.index, result.timing, result.reason), (29, "close", "time_limit"))
        with self.assertRaises(ValueError):
            simulate_exit(window[:-1], 110, "hold_20")

    def test_costs_apply_on_both_sides(self):
        self.assertAlmostEqual(net_return_pct(100, 110, 10), (109.89 / 100.1 - 1) * 100)


class CohortTests(TestCase):
    def test_boundary_purge_and_common_thirty_session_exclusion(self):
        events, bars = fixture()
        cohort, spy, boundary, counts = prepare_cohort(events, bars)
        self.assertEqual(boundary, bar(50).day.isoformat())
        self.assertEqual(counts["purged_boundary"], 1)
        self.assertEqual(counts["overlapping"], 1)
        self.assertEqual([row.signal_day for row, _, _ in cohort],
                         [bar(i).day.isoformat() for i in (5, 50, 80)])
        trades = build_trades(cohort, spy)
        self.assertEqual(len(trades), 15)
        self.assertTrue(trades.groupby("policy").size().eq(3).all())

    def test_raw_price_mismatch_and_duplicate_bars_fail_loudly(self):
        events, bars = fixture()
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            prepare_cohort(events, pd.concat([bars, bars.iloc[:1]]))
        events.loc[0, "entry_open"] = 90
        with self.assertRaisesRegex(ValueError, "disagrees"):
            prepare_cohort(events, bars)

    def test_missing_benchmark_session_excludes_affected_event(self):
        events, bars = fixture()
        bars = bars[~(bars.ticker.eq("SPY") & bars.day.eq(bar(7).day.isoformat()))]
        cohort, _, _, counts = prepare_cohort(events, bars)
        self.assertEqual(counts["benchmark_gaps"], 1)
        self.assertNotIn(bar(5).day.isoformat(), [row.signal_day for row, _, _ in cohort])

    def test_matched_benchmark_uses_open_for_opening_exit(self):
        events, bars = fixture()
        cohort, spy, _, _ = prepare_cohort(events, bars)
        exit_day = cohort[0][1][1].day
        spy[exit_day] = DailyBar(exit_day, 220, 260, 219, 250)
        trades = build_trades(cohort[:1], spy)
        row = trades[trades.policy.eq("recovery_30")].iloc[0]
        self.assertEqual(row.spy_exit_price, 220)
        self.assertAlmostEqual(row.excess_return_0bps_pp, -10)

    def test_date_balancing_does_not_count_large_cluster_repeatedly(self):
        events, bars = fixture()
        cohort, spy, _, _ = prepare_cohort(events, bars)
        trades = build_trades(cohort, spy)
        sample = trades[trades.policy.eq("hold_30")].copy()
        sample["net_return_10bps_pct"] = [-5, 0, 20]
        repeated = pd.concat([sample, *[sample.iloc[2:] for _ in range(10)]])
        result = summarize(repeated, 10)
        self.assertEqual(result["median_net_return_pct"], 20)
        self.assertEqual(result["date_balanced_median_return_pct"], 0)

    def test_offline_export_does_not_overwrite_existing_directory(self):
        events, bars = fixture()
        with TemporaryDirectory() as temporary:
            source = Path(temporary)
            events.to_csv(source / "events.csv", index=False)
            bars.to_csv(source / "daily_bars.csv", index=False)
            (source / "metadata.json").write_text("{}", encoding="utf-8")
            output = source / "result"
            run_analysis(source, output)
            summary = pd.read_csv(output / "summary.csv")
            self.assertEqual(len(summary), 60)
            self.assertEqual(len(pd.read_csv(output / "trades.csv")), 15)
            with self.assertRaises(FileExistsError):
                run_analysis(source, output)
