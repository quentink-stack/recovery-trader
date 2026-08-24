"""Orchestration for collecting one ticker's research evidence."""

from __future__ import annotations

from datetime import date, timedelta

from recovery_trader.integrations.alpaca import AlpacaMarketData
from recovery_trader.integrations.news import NewsClient
from recovery_trader.research.context import ResearchContext, build_research_context


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
    ) -> ResearchContext:
        """Fetch market/news evidence and return one normalized research context."""
        research_date = as_of or date.today()
        bars = self.market_data.daily_bars(ticker, research_date - timedelta(days=lookback_bars * 2), research_date)
        articles = self.news_client.recent_articles(ticker, limit=news_limit)
        return build_research_context(ticker, bars, articles, as_of=research_date, lookback_bars=lookback_bars)
