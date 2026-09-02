from datetime import date, datetime, timezone
from unittest import TestCase

from recovery_trader.domain.market import DailyBar
from recovery_trader.integrations.news import NewsArticle
from recovery_trader.integrations.sec_edgar import EarningsFacts, EarningsRelease, SecFiling
from recovery_trader.research.context import build_research_context
from recovery_trader.research.service import ResearchService


class ResearchContextTests(TestCase):
    def test_builds_market_summary_and_serializable_news_payload(self) -> None:
        bars = [
            DailyBar(date(2026, 8, 21), 100, 105, 98, 102),
            DailyBar(date(2026, 8, 20), 95, 101, 94, 100),
            DailyBar(date(2026, 8, 24), 103, 110, 101, 108),
        ]
        article = NewsArticle("TEST earnings", "Example News", "https://example.com/test", datetime(2026, 8, 24, 12, tzinfo=timezone.utc))

        context = build_research_context(" test ", bars, [article], as_of=date(2026, 8, 24), lookback_bars=2)

        self.assertEqual(context.ticker, "TEST")
        self.assertEqual(context.market.latest_day, "2026-08-24")  # type: ignore[union-attr]
        self.assertEqual(context.market.lookback_start, "2026-08-21")  # type: ignore[union-attr]
        self.assertEqual(context.market.bar_count, 2)  # type: ignore[union-attr]
        self.assertAlmostEqual(context.market.return_pct, (108 / 102 - 1) * 100)  # type: ignore[union-attr]
        self.assertEqual(context.to_payload()["news"][0]["published_at"], "2026-08-24T12:00:00+00:00")
        self.assertNotIn("earnings", context.to_payload())

    def test_empty_bars_leave_market_context_empty(self) -> None:
        context = build_research_context("TEST", [], [])

        self.assertIsNone(context.market)
        self.assertEqual(context.to_payload()["market"], None)

    def test_service_collects_market_and_news_evidence(self) -> None:
        class FakeMarketData:
            def daily_bars(self, ticker: str, start: date, end: date) -> list[DailyBar]:
                self.request = ticker, start, end
                return [DailyBar(date(2026, 8, 24), 100, 101, 99, 100)]

        class FakeNewsClient:
            def recent_articles(self, ticker: str, limit: int) -> list[NewsArticle]:
                self.request = ticker, limit
                return []

        market_data, news_client = FakeMarketData(), FakeNewsClient()
        context = ResearchService(market_data, news_client).collect("TEST", as_of=date(2026, 8, 24), lookback_bars=10, news_limit=4)  # type: ignore[arg-type]

        self.assertEqual(context.ticker, "TEST")
        self.assertEqual(market_data.request[0], "TEST")
        self.assertEqual(news_client.request, ("TEST", 4))

    def test_service_reports_evidence_collection_stages(self) -> None:
        class FakeMarketData:
            def daily_bars(self, ticker: str, start: date, end: date) -> list[DailyBar]:
                return [DailyBar(date(2026, 8, 24), 100, 101, 99, 100)]

        class FakeNewsClient:
            def recent_articles(self, ticker: str, limit: int) -> list[NewsArticle]:
                return []

        stages: list[str] = []
        ResearchService(FakeMarketData(), FakeNewsClient()).collect(  # type: ignore[arg-type]
            "TEST",
            as_of=date(2026, 8, 24),
            on_stage=stages.append,
        )

        self.assertEqual(
            stages,
            [
                "Fetching adjusted price history from Alpaca",
                "Fetching recent news",
                "Preparing evidence",
            ],
        )

    def test_service_collects_sec_preview_without_adding_it_to_qwen_payload(self) -> None:
        class FakeMarketData:
            def daily_bars(self, ticker: str, start: date, end: date) -> list[DailyBar]:
                return []

        class FakeNewsClient:
            def recent_articles(self, ticker: str, limit: int) -> list[NewsArticle]:
                return []

        class FakeSecClient:
            def cik_for_ticker(self, ticker: str) -> str:
                return "0000320193"

            def filing_history(self, cik: str) -> tuple[SecFiling, ...]:
                return (SecFiling(cik, "0000320193-26-000010", "8-K", date(2026, 8, 1), None, "filing.htm", "Results", ("2.02",)),)

            def latest_earnings_facts(self, cik: str) -> EarningsFacts:
                return EarningsFacts(cik, "10-Q", date(2026, 7, 30), date(2026, 4, 1), date(2026, 6, 30), 2026, "Q2", 95.0, 20.0, 1.25, 1.24, 30.0, "0000320193-26-000010")

            def latest_earnings_release(self, cik: str, filings: tuple[SecFiling, ...]) -> EarningsRelease:
                return EarningsRelease(filings[0], "earnings.htm", "https://www.sec.gov/earnings.htm")

        context = ResearchService(FakeMarketData(), FakeNewsClient(), FakeSecClient()).collect("TEST", as_of=date(2026, 8, 24))  # type: ignore[arg-type]

        self.assertEqual(context.earnings.cik, "0000320193")  # type: ignore[union-attr]
        self.assertEqual(context.earnings.facts.revenue, 95.0)  # type: ignore[union-attr]
        self.assertEqual(context.earnings.release.exhibit_name, "earnings.htm")  # type: ignore[union-attr]
        self.assertEqual(context.earnings.days_since_release, 23)  # type: ignore[union-attr]
        self.assertEqual(context.earnings.event_freshness, 65)  # type: ignore[union-attr]
        self.assertEqual(context.earnings.confidence, 65)  # type: ignore[union-attr]
        self.assertEqual(context.earnings.available_recovery_weight, 16.25)  # type: ignore[union-attr]
        self.assertIsNone(context.earnings.estimated_next_earnings_date)  # type: ignore[union-attr]
        self.assertNotIn("earnings", context.to_payload())

    def test_sec_timing_estimates_next_earnings_from_recent_release_cadence(self) -> None:
        class FakeMarketData:
            def daily_bars(self, ticker: str, start: date, end: date) -> list[DailyBar]:
                return []

        class FakeNewsClient:
            def recent_articles(self, ticker: str, limit: int) -> list[NewsArticle]:
                return []

        releases = (
            SecFiling("0000320193", "0001-26-3", "8-K", date(2026, 8, 1), None, "new.htm", "Results", ("2.02",)),
            SecFiling("0000320193", "0001-26-2", "8-K", date(2026, 5, 2), None, "middle.htm", "Results", ("2.02",)),
            SecFiling("0000320193", "0001-26-1", "8-K", date(2026, 1, 31), None, "old.htm", "Results", ("2.02",)),
        )

        class FakeSecClient:
            def cik_for_ticker(self, ticker: str) -> str:
                return "0000320193"

            def filing_history(self, cik: str) -> tuple[SecFiling, ...]:
                return releases

            def latest_earnings_facts(self, cik: str) -> EarningsFacts:
                return EarningsFacts(cik, "10-Q", date(2026, 7, 30), date(2026, 4, 1), date(2026, 6, 30), 2026, "Q2", 95.0, 20.0, 1.25, 1.24, 30.0, "0001-26-3")

            def latest_earnings_release(self, cik: str, filings: tuple[SecFiling, ...]) -> EarningsRelease:
                return EarningsRelease(filings[0], "earnings.htm", "https://www.sec.gov/earnings.htm")

        context = ResearchService(FakeMarketData(), FakeNewsClient(), FakeSecClient()).collect("TEST", as_of=date(2026, 8, 24))  # type: ignore[arg-type]

        assert context.earnings is not None
        self.assertEqual(context.earnings.estimated_next_earnings_date, date(2026, 10, 31))
        self.assertEqual(context.earnings.days_until_next_expected_earnings, 68)
        self.assertEqual(context.earnings.raw_data_coverage, 100)
        self.assertEqual(context.earnings.confidence, 65)
