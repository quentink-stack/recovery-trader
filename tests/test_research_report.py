import json
from datetime import date, datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import Mock

from recovery_trader.domain.market import DailyBar
from recovery_trader.integrations.news import NewsArticle
from recovery_trader.research.context import build_research_context
from recovery_trader.research.report import build_prompt, category_evidence_coverage, evidence_coverage_score, generate_report, parse_report


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
        self.assertEqual(report.score, 80)
        self.assertEqual(report.risks, ("Potential risk",))

    def test_market_and_earnings_have_more_influence_than_sentiment(self) -> None:
        market_negative = self.report_payload("positive")
        market_negative["score_categories"]["market"]["rating"] = "negative"
        sentiment_negative = self.report_payload("positive")
        sentiment_negative["score_categories"]["sentiment"]["rating"] = "negative"

        market_report = parse_report(json.dumps(market_negative), "TEST")
        sentiment_report = parse_report(json.dumps(sentiment_negative), "TEST")

        self.assertEqual(market_report.score, 70)
        self.assertEqual(sentiment_report.score, 90)

    def test_generation_uses_prompt_and_parses_response(self) -> None:
        client = Mock()
        client.generate.return_value = json.dumps(self.report_payload())
        stages: list[str] = []

        report = generate_report(self.context, client, on_stage=stages.append)

        self.assertEqual(report.recovery_score, 50)
        self.assertEqual(report.evidence_coverage, 8)
        client.generate.assert_called_once()
        self.assertTrue(client.generate.call_args.kwargs["json_response"])
        self.assertEqual(stages, ["Generating report with local Qwen3", "Validating the structured report"])

    def test_evidence_coverage_uses_market_depth_and_news_source_breadth(self) -> None:
        bars = [
            DailyBar(date(2026, 7, 27) + timedelta(days=index), 100, 102, 99, 101)
            for index in range(20)
        ]
        articles = [
            NewsArticle(
                f"TEST article {index}",
                ("Publisher A", "Publisher B", "Publisher C")[index % 3],
                f"https://example.com/{index}",
                datetime(2026, 8, 20 + index, tzinfo=timezone.utc),
            )
            for index in range(5)
        ]
        context = build_research_context("TEST", bars, articles, as_of=date(2026, 8, 24))

        coverage = category_evidence_coverage(context)

        self.assertEqual(coverage["market"], 100)
        self.assertEqual(coverage["news"], 100)
        self.assertEqual(coverage["sentiment"], 50)
        self.assertEqual(coverage["earnings"], 0)
        self.assertEqual(evidence_coverage_score(coverage), 55)

    def test_no_sources_produce_zero_evidence_coverage(self) -> None:
        context = build_research_context("TEST", [], [], as_of=date(2026, 8, 24))

        coverage = category_evidence_coverage(context)

        self.assertTrue(all(value == 0 for value in coverage.values()))
        self.assertEqual(evidence_coverage_score(coverage), 0)

    def test_missing_category_is_recorded_as_an_explicit_uncertainty(self) -> None:
        payload = self.report_payload()
        payload["score_categories"].pop("macro")

        report = parse_report(json.dumps(payload), "TEST")

        self.assertEqual(report.assessments["macro"].rating, "neutral")
        self.assertEqual(report.assessments["macro"].evidence, "The local model did not provide an assessment for this category.")
        self.assertIn("The local model did not assess: Macro.", report.uncertainties)

    def test_missing_summary_and_optional_lists_are_recorded_as_uncertainties(self) -> None:
        payload = self.report_payload()
        payload.pop("summary")
        payload["catalysts"] = "not a list"
        payload.pop("uncertainties")

        report = parse_report(json.dumps(payload), "TEST")

        self.assertIn("did not provide a narrative summary", report.summary)
        self.assertEqual(report.catalysts, ())
        self.assertIn("The local model did not provide a narrative summary.", report.uncertainties)
        self.assertIn("The local model did not provide a valid catalysts list.", report.uncertainties)
        self.assertIn("The local model did not provide a valid uncertainties list.", report.uncertainties)

    def test_top_level_or_omitted_category_container_is_recovered(self) -> None:
        top_level_payload = self.report_payload()
        top_level_payload.update(top_level_payload.pop("score_categories"))

        top_level_report = parse_report(json.dumps(top_level_payload), "TEST")
        self.assertEqual(top_level_report.score, 50)

        no_categories_payload = self.report_payload()
        no_categories_payload.pop("score_categories")
        no_categories_report = parse_report(json.dumps(no_categories_payload), "TEST")

        self.assertTrue(all(item.rating == "neutral" for item in no_categories_report.assessments.values()))
        self.assertIn(
            "The local model did not assess: Market, Earnings, News, Macro, Regulation, Sentiment.",
            no_categories_report.uncertainties,
        )

    def test_extra_model_metadata_and_category_casing_are_tolerated(self) -> None:
        payload = self.report_payload()
        payload["score_categories"]["Market outlook"] = {"rating": "positive", "evidence": "Extra metadata."}
        payload["score_categories"]["MARKET"] = payload["score_categories"].pop("market")

        report = parse_report(json.dumps(payload), "TEST")

        self.assertEqual(report.score, 50)

    def test_common_model_rating_aliases_and_list_evidence_are_normalized(self) -> None:
        payload = self.report_payload()
        payload["score_categories"]["news"] = {"rating": "mixed", "evidence": ["Headline one", "Headline two"]}

        report = parse_report(json.dumps(payload), "TEST")

        self.assertEqual(report.assessments["news"].rating, "neutral")
        self.assertEqual(report.assessments["news"].evidence, "Headline one; Headline two")
