"""News retrieval through a public RSS search feed."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree


GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"


@dataclass(frozen=True)
class NewsArticle:
    title: str
    publisher: str
    url: str
    published_at: datetime | None


class NewsError(RuntimeError):
    """Raised when the news feed cannot be fetched or parsed."""


class NewsClient:
    def __init__(self, timeout: int = 30) -> None:
        self.timeout = timeout

    def recent_articles(self, ticker: str, limit: int = 10) -> list[NewsArticle]:
        """Return recent articles matching a ticker symbol from Google News RSS."""
        normalized_ticker = ticker.strip().upper()
        if not normalized_ticker:
            raise ValueError("Ticker cannot be empty.")
        if limit < 1:
            raise ValueError("Article limit must be positive.")

        query = urlencode({"q": f"{normalized_ticker} stock", "hl": "en-US", "gl": "US", "ceid": "US:en"})
        request = Request(
            f"{GOOGLE_NEWS_RSS_URL}?{query}",
            headers={"Accept": "application/rss+xml, application/xml", "User-Agent": "recovery-trader/1.0 (local research tool)"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                document = response.read()
            root = ElementTree.fromstring(document)
        except (HTTPError, URLError, OSError, TimeoutError, ElementTree.ParseError) as exc:
            raise NewsError(f"News request failed: {exc}") from exc

        articles: list[NewsArticle] = []
        for item in root.findall("./channel/item"):
            title = (item.findtext("title") or "").strip()
            url = (item.findtext("link") or "").strip()
            if not title or not url:
                continue
            source = item.find("source")
            publisher = (source.text if source is not None and source.text else "Google News").strip()
            published_at = self._parse_date(item.findtext("pubDate"))
            articles.append(NewsArticle(title, publisher, url, published_at))
            if len(articles) >= limit:
                break
        return articles

    @staticmethod
    def _parse_date(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
