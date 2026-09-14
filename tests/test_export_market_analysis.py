import argparse
import csv
import json
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from recovery_trader.domain.market import DailyBar
from scripts.export_market_analysis import run_export, validated_bars


class ExportMarketAnalysisTests(TestCase):
    def test_export_roundtrip_retains_unknown_outcomes_and_excludes_cutoff_day(self):
        class FakeClient:
            equities_feed = "iex"

            def daily_bars_for_symbols(self, symbols, start, end):
                return {symbol: [
                    DailyBar(date(2026, 8, 3), 100, 101, 99, 100),
                    DailyBar(date(2026, 8, 4), 92, 93, 89, 90),
                    DailyBar(date(2026, 8, 5), 91, 93, 90, 92),
                    DailyBar(date(2026, 8, 6), 92, 110, 91, 105),
                ] for symbol in symbols}

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            universe = root / "universe.csv"
            universe.write_text("ticker,company,sector\nTEST,Test Company,Technology\n", encoding="utf-8")
            args = argparse.Namespace(universe=universe, tickers=None, per_sector=None,
                                      as_of=date(2026, 8, 6), days=30, min_drop=5.0, output=root / "result")
            output = run_export(args, FakeClient())
            with (output / "events.csv").open(newline="", encoding="utf-8") as handle:
                events = list(csv.DictReader(handle))
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["entry_open"], "91")
            self.assertEqual(events[0]["return_5_sessions_pct"], "")
            self.assertEqual(events[0]["recovered_prior_close_30_sessions"], "")
            self.assertEqual(events[0]["complete_30_sessions"], "False")
            metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["complete_30_session_events"], 0)
            self.assertNotIn("2026-08-06", (output / "daily_bars.csv").read_text(encoding="utf-8"))

    def test_duplicate_sessions_are_rejected(self):
        bar = DailyBar(date(2026, 8, 3), 100, 101, 99, 100)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            validated_bars([bar, bar], date(2026, 8, 1), date(2026, 8, 6))
