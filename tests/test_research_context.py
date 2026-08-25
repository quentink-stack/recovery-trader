from datetime import date, datetime, timezone
from unittest import TestCase

from recovery_trader.domain.market import DailyBar
from recovery_trader.integrations.news import NewsArticle
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
        self.assertAlmostEqual(context.market.return_pct, (108 / 102 - 1) * 100)  # type: ignore[union-attr]
        self.assertEqual(context.to_payload()["news"][0]["published_at"], "2026-08-24T12:00:00+00:00")

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
