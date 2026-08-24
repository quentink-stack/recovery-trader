from unittest import TestCase
from unittest.mock import patch

from recovery_trader.integrations.news import NewsClient, NewsError


class FakeResponse:
    def __init__(self, document: str) -> None:
        self.document = document.encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.document


RSS = """<?xml version="1.0"?><rss><channel>
<item><title>TEST reports quarterly results</title><link>https://example.com/results</link><pubDate>Mon, 24 Aug 2026 12:00:00 GMT</pubDate><source>Example News</source></item>
<item><title>Second story</title><link>https://example.com/second</link></item>
</channel></rss>"""


class NewsClientTests(TestCase):
    def test_recent_articles_parses_articles_and_limits_results(self) -> None:
        client = NewsClient(timeout=7)

        with patch("recovery_trader.integrations.news.urlopen", return_value=FakeResponse(RSS)) as mocked:
            articles = client.recent_articles(" test ", limit=1)

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].title, "TEST reports quarterly results")
        self.assertEqual(articles[0].publisher, "Example News")
        self.assertEqual(articles[0].url, "https://example.com/results")
        self.assertEqual(articles[0].published_at.year, 2026)  # type: ignore[union-attr]
        request = mocked.call_args.args[0]
        self.assertIn("q=TEST+stock", request.full_url)
        self.assertEqual(mocked.call_args.kwargs["timeout"], 7)

    def test_unreachable_feed_raises_news_error(self) -> None:
        client = NewsClient()

        with patch("recovery_trader.integrations.news.urlopen", side_effect=OSError("offline")):
            with self.assertRaises(NewsError):
                client.recent_articles("TEST")

    def test_empty_ticker_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            NewsClient().recent_articles(" ")
