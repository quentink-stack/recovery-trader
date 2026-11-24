import json
import unittest
from datetime import date, timedelta

from recovery_trader.domain.market import DailyBar
from scripts.recovery_trials import active_drop, label, open_store, summary
from pathlib import Path
from tempfile import TemporaryDirectory


def bar(day, price):
    return DailyBar(date(2026, 1, 1) + timedelta(days=day), price, price, price, price)


class RecoveryTrialsTests(unittest.TestCase):
    def test_drop_must_still_be_unresolved(self):
        bars = [bar(0, 100), bar(1, 95)]
        self.assertEqual(active_drop(bars), tuple(bars))
        with self.assertRaises(ValueError):
            active_drop(bars + [bar(2, 100)])

    def test_only_future_completed_bars_on_same_adjustment_basis(self):
        snapshot = {"prior_day": "2026-01-01", "target": 100}
        bars = [bar(0, 50), bar(1, 51)] + [bar(i, 49) for i in range(2, 32)]
        self.assertIsNone(label(snapshot, bars[1].day, bars, bars[-1].day))
        tomorrow = bars[-1].day + timedelta(days=1)
        self.assertFalse(label(snapshot, bars[1].day, bars, tomorrow)["recovered"])
        bars[-1] = bar(31, 50)
        self.assertEqual(label(snapshot, bars[1].day, bars, tomorrow)["first_recovery"], bars[-1].day)

    def test_joint_rates_exclude_pending(self):
        with TemporaryDirectory() as directory:
            db = open_store(Path(directory) / "test.db")
            try:
                snapshot = json.dumps({"report": {"recovery_score": 100, "evidence_coverage": 39}})
                for i, outcome in enumerate((None, '{"recovered": true}', '{"recovered": false}')):
                    db.execute("INSERT INTO trials VALUES (?, '', '', '', ?, ?)", (str(i), snapshot, outcome))
                result = summary(db)
                self.assertEqual(result["pending"], 1)
                self.assertEqual(result["groups"]["score 80-100, coverage 20-39"],
                                 {"n": 2, "recovered": 1, "recovery_rate": 0.5})
            finally:
                db.close()
