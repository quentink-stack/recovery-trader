import json
from datetime import date
from unittest import TestCase
from unittest.mock import Mock

from recovery_trader.domain.market import DailyBar
from recovery_trader.integrations.news import NewsArticle
from recovery_trader.research.context import build_research_context
from recovery_trader.research.report import build_prompt, generate_report, parse_report


class ResearchReportTests(TestCase):
    def setUp(self) -> None:
        self.context = build_research_context(
            "TEST",
            [DailyBar(date(2026, 8, 21), 100, 105, 98, 102), DailyBar(date(2026, 8, 24), 103, 110, 101, 108)],
            [NewsArticle("TEST earnings", "Example News", "https://example.com", None)],
            as_of=date(2026, 8, 24),
        )

    def report_payload(self, rating: str = "neutral") -> dict:
        return {
            "summary": "Evidence is mixed.",
            "score_categories": {category: {"rating": rating, "evidence": f"Evidence for {category}."} for category in ("market", "earnings", "news", "macro", "regulation", "sentiment")},
            "catalysts": ["Potential catalyst"],
            "risks": ["Potential risk"],
            "uncertainties": ["Missing data"],
        }

    def test_prompt_contains_context_and_json_instructions(self) -> None:
        prompt = build_prompt(self.context)

        self.assertIn('"ticker": "TEST"', prompt)
        self.assertIn("Return JSON only", prompt)
        self.assertIn("TEST earnings", prompt)

    def test_report_is_validated_and_score_is_calculated(self) -> None:
        payload = self.report_payload("positive")
        payload["score_categories"]["news"]["rating"] = "negative"

        report = parse_report(json.dumps(payload), "test")

        self.assertEqual(report.ticker, "TEST")
        self.assertEqual(report.score, 83)
        self.assertEqual(report.risks, ("Potential risk",))

    def test_generation_uses_prompt_and_parses_response(self) -> None:
        client = Mock()
        client.generate.return_value = json.dumps(self.report_payload())

        report = generate_report(self.context, client)

        self.assertEqual(report.score, 50)
        client.generate.assert_called_once()
        self.assertTrue(client.generate.call_args.kwargs["json_response"])

    def test_invalid_category_is_rejected(self) -> None:
        payload = self.report_payload()
        payload["score_categories"].pop("macro")

        with self.assertRaises(ValueError):
            parse_report(json.dumps(payload), "TEST")

    def test_extra_model_metadata_and_category_casing_are_tolerated(self) -> None:
        payload = self.report_payload()
        payload["score_categories"]["Market outlook"] = {"rating": "positive", "evidence": "Extra metadata."}
        payload["score_categories"]["MARKET"] = payload["score_categories"].pop("market")

        report = parse_report(json.dumps(payload), "TEST")

        self.assertEqual(report.score, 50)
