from unittest import TestCase
from unittest.mock import patch

from recovery_trader.integrations.news import NewsArticle, NewsClient, NewsError


class FakeResponse:
    def __init__(self, document: str, content_type: str = "application/rss+xml") -> None:
        self.document = document.encode("utf-8")
        self.headers = {"Content-Type": content_type}

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int | None = None) -> bytes:
        return self.document if size is None else self.document[:size]


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

    def test_default_article_excerpt_limit_is_three(self) -> None:
        self.assertEqual(NewsClient().article_excerpt_limit, 3)

    def test_enrich_articles_extracts_visible_text_for_qwen(self) -> None:
        article = NewsArticle("TEST results", "Example News", "https://example.com/results", None)
        html = """<html><body><nav>Navigation</nav><article><h1>Results</h1><p>Revenue grew 12 percent year over year.</p><script>ignore me</script></article></body></html>"""
        client = NewsClient(article_timeout=7, article_excerpt_limit=1, max_excerpt_chars=500)

        with patch("recovery_trader.integrations.news.urlopen", return_value=FakeResponse(html, "text/html")) as mocked:
            enriched = client.enrich_articles([article])

        self.assertEqual(enriched[0].excerpt, "Results Revenue grew 12 percent year over year.")
        request = mocked.call_args.args[0]
        self.assertEqual(request.full_url, article.url)
        self.assertEqual(mocked.call_args.kwargs["timeout"], 7)

    def test_enrich_articles_removes_navigation_and_language_menu_boilerplate(self) -> None:
        article = NewsArticle("Revenue grew 12 percent year over year", "Example News", "https://example.com/results", None)
        html = """<html><body>
        <div role="navigation">Navigation Search</div>
        English  繁体中文 ไทย Tiếng việt 简体中文 Español Português Deutsch 한국어 日本語 Log in Start for free  Search Start for free
        <p>Skip Navigation Markets Business Revenue grew 12 percent year over year. Full article evidence.</p>
        </body></html>"""
        client = NewsClient(article_timeout=7, article_excerpt_limit=1, max_excerpt_chars=500)

        with patch("recovery_trader.integrations.news.urlopen", return_value=FakeResponse(html, "text/html")):
            enriched = client.enrich_articles([article])

        self.assertEqual(enriched[0].excerpt, "Revenue grew 12 percent year over year. Full article evidence.")

    def test_enrich_articles_uses_page_headline_when_article_and_main_are_absent(self) -> None:
        article = NewsArticle("Revenue grows", "Example News", "https://example.com/results", None)
        html = """<html><body>
        <p>Promoted stories and subscription offers.</p>
        <h1>Revenue grows</h1><p>Revenue grew 12 percent year over year.</p>
        </body></html>"""
        client = NewsClient(article_timeout=7, article_excerpt_limit=1, max_excerpt_chars=500)

        with patch("recovery_trader.integrations.news.urlopen", return_value=FakeResponse(html, "text/html")):
            enriched = client.enrich_articles([article])

        self.assertEqual(enriched[0].excerpt, "Revenue grows Revenue grew 12 percent year over year.")

    def test_enrich_articles_leaves_non_html_pages_headline_only(self) -> None:
        article = NewsArticle("TEST transcript", "Example News", "https://example.com/transcript.pdf", None)
        client = NewsClient(article_excerpt_limit=1)

        with patch("recovery_trader.integrations.news.urlopen", return_value=FakeResponse("pdf", "application/pdf")):
            enriched = client.enrich_articles([article])

        self.assertIsNone(enriched[0].excerpt)

    def test_enrich_articles_resolves_google_news_url_before_reading_article(self) -> None:
        article = NewsArticle("TEST results", "Example News", "https://news.google.com/rss/articles/encoded-id?oc=5", None)
        metadata = '<html><body><div data-n-a-sg="signature" data-n-a-ts="12345"></div></body></html>'
        batch_response = ")]}'\n\n[[\"wrb.fr\",\"Fbv4je\",\"[\\\"garturlres\\\",\\\"https://example.com/results\\\",1]\"]]"
        publisher_html = "<html><body><article><p>Resolved article content.</p></article></body></html>"
        client = NewsClient(article_timeout=7, article_excerpt_limit=1, max_excerpt_chars=500)

        with patch(
            "recovery_trader.integrations.news.urlopen",
            side_effect=[
                FakeResponse(metadata, "text/html"),
                FakeResponse(batch_response, "application/json"),
                FakeResponse(publisher_html, "text/html"),
            ],
        ) as mocked:
            enriched = client.enrich_articles([article])

        self.assertEqual(enriched[0].url, "https://example.com/results")
        self.assertEqual(enriched[0].excerpt, "Resolved article content.")
        self.assertEqual(mocked.call_count, 3)
