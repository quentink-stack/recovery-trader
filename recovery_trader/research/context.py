"""Combine market data and news into an Ollama-ready research context."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

from recovery_trader.domain.market import DailyBar
from recovery_trader.integrations.news import NewsArticle
from recovery_trader.integrations.sec_edgar import EarningsFacts, EarningsRelease


# Keep this aligned with the deterministic category weights in report.py.  The
# SEC preview does not yet change the report score, but it can show how much of
# the earnings category is supportable by the available, timely evidence.
EARNINGS_RECOVERY_WEIGHT = 25


@dataclass(frozen=True)
class MarketSummary:
    latest_day: str
    latest_close: float
    lookback_start: str
    lookback_close: float
    return_pct: float
    high: float
    low: float
    bar_count: int


@dataclass(frozen=True)
class EarningsEvidence:
    """SEC data collected for preview only; it is not yet sent to Qwen."""

    cik: str | None = None
    release: EarningsRelease | None = None
    facts: EarningsFacts | None = None
    error: str | None = None
    public_release_date: date | None = None
    days_since_release: int | None = None
    event_freshness: int | None = None
    estimated_next_earnings_date: date | None = None
    days_until_next_expected_earnings: int | None = None
    raw_data_coverage: int = 0
    confidence: int = 0
    available_recovery_weight: float = 0.0


@dataclass(frozen=True)
class ResearchContext:
    ticker: str
    as_of: str
    market: MarketSummary | None
    news: tuple[NewsArticle, ...]
    earnings: EarningsEvidence | None = None

    def to_payload(self) -> dict:
        """Return JSON-serializable evidence for an Ollama prompt."""
        return {
            "ticker": self.ticker,
            "as_of": self.as_of,
            "market": asdict(self.market) if self.market else None,
            "news": [
                {
                    "title": article.title,
                    "publisher": article.publisher,
                    "url": article.url,
                    "published_at": article.published_at.isoformat() if article.published_at else None,
                }
                for article in self.news
            ],
        }


def build_research_context(
    ticker: str,
    bars: list[DailyBar],
    articles: list[NewsArticle],
    *,
    as_of: date | None = None,
    lookback_bars: int = 30,
    earnings: EarningsEvidence | None = None,
) -> ResearchContext:
    """Build a compact context from recent bars and already-fetched news."""
    normalized_ticker = ticker.strip().upper()
    if not normalized_ticker:
        raise ValueError("Ticker cannot be empty.")
    if lookback_bars < 1:
        raise ValueError("Lookback bars must be positive.")

    ordered_bars = sorted(bars, key=lambda bar: bar.day)
    recent_bars = ordered_bars[-lookback_bars:]
    market = None
    if recent_bars:
        first_bar, latest_bar = recent_bars[0], recent_bars[-1]
        market = MarketSummary(
            latest_day=latest_bar.day.isoformat(),
            latest_close=latest_bar.close,
            lookback_start=first_bar.day.isoformat(),
            lookback_close=first_bar.close,
            return_pct=(latest_bar.close / first_bar.close - 1) * 100,
            high=max(bar.high for bar in recent_bars),
            low=min(bar.low for bar in recent_bars),
            bar_count=len(recent_bars),
        )

    return ResearchContext(normalized_ticker, (as_of or date.today()).isoformat(), market, tuple(articles), earnings)
