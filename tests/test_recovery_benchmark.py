import json
import unittest
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from recovery_trader.integrations.news import NewsArticle
from recovery_trader.research.context import ResearchContext
from recovery_trader.research.report import CategoryAssessment, ResearchReport
from scripts import recovery_benchmark as bench
from scripts.recovery_trials import ROOT, open_store


class BenchmarkTests(unittest.TestCase):
    def test_ranking_and_confidence(self):
        rows = [(70, 90, True), (60, 20, False), (50, 60, False)]
        self.assertEqual(bench.metrics(rows), dict(recovery_auc=1, directional_accuracy=.5,
                         directional_calls=2, coverage_correctness_auc=1))
        self.assertEqual(bench.auc([(50, True), (50, False)]), .5)
        self.assertIsNone(bench.auc([(50, False)]))

    def test_frozen_suite_replay(self):
        article = NewsArticle("headline", "publisher", "url", datetime(2026, 1, 1), "excerpt")
        ctx = ResearchContext("ABC", "2026-01-02", None, (article,))
        report = ResearchReport("ABC", "summary", 70, 90, {"news": CategoryAssessment("positive", "text")},
                                {}, (), (), ())
        snapshot = json.dumps(dict(context=asdict(ctx), report=asdict(report)), default=str)
        with TemporaryDirectory() as directory:
            db = open_store(Path(directory) / "trials")
            try:
                def add(ticker):
                    db.execute("INSERT INTO trials VALUES (?, '', '', '', ?, ?)",
                               (ticker, snapshot, '{"recovered": true}'))
                add("ABC")
                bench.freeze(db, "fixed")
                add("XYZ")
                with self.assertRaises(ValueError):
                    bench.freeze(db, "fixed")
                with patch.object(bench, "generate_report", return_value=report) as generate:
                    result = bench.replay(db, "fixed", "prompt", False, ROOT)
                    self.assertEqual(generate.call_args.args[0], ctx)
                    self.assertEqual(result["cases"], 1)
                    bench.replay(db, "fixed", "weights", True, ROOT)
                    generate.assert_called_once()
                self.assertEqual(bench.compare(db, "fixed", "prompt", "weights")["cases"], 1)
                with self.assertRaises(ValueError):
                    bench.replay(db, "fixed", "weights", True, ROOT)
            finally:
                db.close()
