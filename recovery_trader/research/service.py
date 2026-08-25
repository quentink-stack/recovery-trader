"""Orchestration for collecting one ticker's research evidence."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Callable

from recovery_trader.integrations.alpaca import AlpacaMarketData
from recovery_trader.integrations.news import NewsClient
from recovery_trader.research.context import ResearchContext, build_research_context

ResearchStageCallback = Callable[[str], None]


def _report_stage(callback: ResearchStageCallback | None, stage: str) -> None:
    if callback is not None:
        callback(stage)


class ResearchService:
    def __init__(self, market_data: AlpacaMarketData, news_client: NewsClient) -> None:
        self.market_data = market_data
        self.news_client = news_client

    def collect(
        self,
        ticker: str,
        *,
        as_of: date | None = None,
        lookback_bars: int = 30,
        news_limit: int = 10,
        on_stage: ResearchStageCallback | None = None,
    ) -> ResearchContext:
        """Fetch market/news evidence and return one normalized research context."""
        research_date = as_of or date.today()
        _report_stage(on_stage, "Fetching adjusted price history from Alpaca")
        bars = self.market_data.daily_bars(ticker, research_date - timedelta(days=lookback_bars * 2), research_date)
        _report_stage(on_stage, "Fetching recent news")
        articles = self.news_client.recent_articles(ticker, limit=news_limit)
        _report_stage(on_stage, "Preparing evidence")
        return build_research_context(ticker, bars, articles, as_of=research_date, lookback_bars=lookback_bars)
