"""News retrieval and bounded article-text extraction for local research."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree


GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"
ARTICLE_EXCERPT_LIMIT = 3
ARTICLE_EXCERPT_CHARS = 3_000
ARTICLE_MAX_BYTES = 1_000_000
ARTICLE_TIMEOUT_SECONDS = 12
GOOGLE_NEWS_METADATA_MAX_BYTES = 1_500_000
GOOGLE_NEWS_BATCH_EXECUTE_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
LANGUAGE_MENU_PATTERN = re.compile(
    r"English[\s\W]+繁体中文[\s\W]+ไทย[\s\W]+Tiếng\s+việt[\s\W]+简体中文[\s\W]+Español[\s\W]+"
    r"Português[\s\W]+Deutsch[\s\W]+한국어[\s\W]+日本語[\s\W]+Log\s+in[\s\W]+Start\s+for\s+free[\s\W]+"
    r"Search[\s\W]+Start\s+for\s+free",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class NewsArticle:
    title: str
    publisher: str
    url: str
    published_at: datetime | None
    excerpt: str | None = None


class NewsError(RuntimeError):
    """Raised when the news feed cannot be fetched or parsed."""


class _ArticleTextParser(HTMLParser):
    """Extract visible article/body text without executing page content."""

    _IGNORED_TAGS = {"script", "style", "noscript", "svg", "template", "iframe", "form", "nav", "footer", "aside", "dialog", "select", "option"}
    _IGNORED_ROLES = {"navigation", "banner", "contentinfo", "complementary", "search", "dialog"}
    _IGNORED_CONTAINER_HINTS = {"cookie", "consent", "language", "locale", "navbar", "navigation", "sidebar", "subscribe", "signup", "login"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_elements: list[str] = []
        self._article_depth = 0
        self._main_depth = 0
        self._body_depth = 0
        self._heading_depth = 0
        self._article_chunks: list[str] = []
        self._main_chunks: list[str] = []
        self._body_chunks: list[str] = []
        self._body_heading_start: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        if normalized in self._IGNORED_TAGS or self._is_ignored_container(attrs):
            self._ignored_elements.append(normalized)
        elif normalized == "article":
            self._article_depth += 1
        elif normalized == "main":
            self._main_depth += 1
        elif normalized == "h1":
            self._heading_depth += 1
        elif normalized == "body":
            self._body_depth += 1

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if self._ignored_elements and normalized == self._ignored_elements[-1]:
            self._ignored_elements.pop()
        elif normalized == "article" and self._article_depth:
            self._article_depth -= 1
        elif normalized == "main" and self._main_depth:
            self._main_depth -= 1
        elif normalized == "h1" and self._heading_depth:
            self._heading_depth -= 1
        elif normalized == "body" and self._body_depth:
            self._body_depth -= 1

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text or self._ignored_elements:
            return
        if self._article_depth:
            self._article_chunks.append(text)
        elif self._main_depth:
            self._main_chunks.append(text)
        elif self._body_depth:
            if self._heading_depth and self._body_heading_start is None:
                self._body_heading_start = len(self._body_chunks)
            self._body_chunks.append(text)

    def text(self) -> str:
        if self._article_chunks:
            return " ".join(self._article_chunks)
        if self._main_chunks:
            return " ".join(self._main_chunks)
        if self._body_heading_start is not None:
            return " ".join(self._body_chunks[self._body_heading_start :])
        return " ".join(self._body_chunks)

    def _is_ignored_container(self, attrs: list[tuple[str, str | None]]) -> bool:
        attributes = {name.lower(): (value or "") for name, value in attrs}
        if attributes.get("role", "").lower() in self._IGNORED_ROLES:
            return True
        if attributes.get("aria-hidden", "").lower() == "true":
            return True
        descriptor = f"{attributes.get('class', '')} {attributes.get('id', '')}".lower()
        return any(hint in descriptor for hint in self._IGNORED_CONTAINER_HINTS)


class _GoogleNewsMetadataParser(HTMLParser):
    """Read the signed URL-resolution parameters embedded in Google News HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.signature: str | None = None
        self.timestamp: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        signature = attributes.get("data-n-a-sg")
        timestamp = attributes.get("data-n-a-ts")
        if signature and timestamp:
            self.signature = signature
            self.timestamp = timestamp


class NewsClient:
    def __init__(
        self,
        timeout: int = 30,
        *,
        article_timeout: int = ARTICLE_TIMEOUT_SECONDS,
        article_excerpt_limit: int = ARTICLE_EXCERPT_LIMIT,
        max_excerpt_chars: int = ARTICLE_EXCERPT_CHARS,
    ) -> None:
        self.timeout = timeout
        if article_timeout < 1 or article_excerpt_limit < 1 or max_excerpt_chars < 100:
            raise ValueError("Article extraction limits must be positive.")
        self.article_timeout = article_timeout
        self.article_excerpt_limit = article_excerpt_limit
        self.max_excerpt_chars = max_excerpt_chars

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

    def enrich_articles(self, articles: list[NewsArticle]) -> list[NewsArticle]:
        """Attach readable excerpts for a small, bounded set of public article links.

        A failed, blocked, paywalled, or non-HTML page remains headline-only so
        a single publisher cannot fail the complete ticker-research request.
        """
        selected = articles[: self.article_excerpt_limit]
        if not selected:
            return articles
        with ThreadPoolExecutor(max_workers=len(selected)) as executor:
            enriched = list(executor.map(self._enrich_article, selected))
        return enriched + articles[len(selected) :]

    def _enrich_article(self, article: NewsArticle) -> NewsArticle:
        source_url = self._resolve_google_news_url(article.url)
        if source_url is None:
            return article
        return replace(article, url=source_url, excerpt=self._article_excerpt(source_url, headline=article.title))

    def _article_excerpt(self, source_url: str, *, headline: str) -> str | None:
        parsed_url = urlparse(source_url)
        if parsed_url.scheme not in {"http", "https"}:
            return None
        request = Request(
            source_url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "recovery-trader/1.0 (local research tool; article excerpt)",
            },
        )
        try:
            with urlopen(request, timeout=self.article_timeout) as response:
                content_type = response.headers.get("Content-Type", "")
                if content_type and "html" not in content_type.lower():
                    return None
                document = response.read(ARTICLE_MAX_BYTES + 1)
        except (HTTPError, URLError, OSError, TimeoutError):
            return None
        if len(document) > ARTICLE_MAX_BYTES:
            return None
        try:
            parser = _ArticleTextParser()
            parser.feed(document.decode("utf-8", errors="replace"))
            return _truncate_text(parser.text(), self.max_excerpt_chars, headline=headline)
        except (ValueError, UnicodeError):
            return None

    def _resolve_google_news_url(self, source_url: str) -> str | None:
        """Resolve a Google News RSS wrapper to its publisher URL when needed.

        Google News places signed decoding parameters in the article wrapper
        page.  The resolver is deliberately best-effort because the endpoint
        is not a stable public API; failure leaves the original headline-only.
        """
        article_id = _google_news_article_id(source_url)
        if article_id is None:
            return source_url
        metadata = self._google_news_metadata(article_id)
        if metadata is None:
            return None
        signature, timestamp = metadata
        payload = [
            "Fbv4je",
            (
                '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
                f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{article_id}",{timestamp},"{signature}"]'
            ),
        ]
        request = Request(
            GOOGLE_NEWS_BATCH_EXECUTE_URL,
            data=urlencode({"f.req": json.dumps([[payload]])}).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "User-Agent": "recovery-trader/1.0 (local research tool; Google News resolver)",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.article_timeout) as response:
                response_text = response.read(100_000).decode("utf-8", errors="replace")
            resolved_url = _parse_google_news_resolution(response_text)
        except (HTTPError, URLError, OSError, TimeoutError, ValueError, json.JSONDecodeError):
            return None
        parsed_url = urlparse(resolved_url) if resolved_url else None
        return resolved_url if parsed_url and parsed_url.scheme in {"http", "https"} else None

    def _google_news_metadata(self, article_id: str) -> tuple[str, str] | None:
        for path in (f"https://news.google.com/articles/{article_id}", f"https://news.google.com/rss/articles/{article_id}"):
            request = Request(
                path,
                headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "User-Agent": "recovery-trader/1.0 (local research tool; Google News resolver)",
                },
            )
            try:
                with urlopen(request, timeout=self.article_timeout) as response:
                    document = response.read(GOOGLE_NEWS_METADATA_MAX_BYTES + 1)
            except (HTTPError, URLError, OSError, TimeoutError):
                continue
            if len(document) > GOOGLE_NEWS_METADATA_MAX_BYTES:
                continue
            parser = _GoogleNewsMetadataParser()
            parser.feed(document.decode("utf-8", errors="replace"))
            if parser.signature and parser.timestamp:
                return parser.signature, parser.timestamp
        return None

    @staticmethod
    def _parse_date(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None


def _truncate_text(text: str, limit: int, *, headline: str = "") -> str | None:
    """Keep a readable excerpt within the model's input budget."""
    normalized = _sanitize_article_text(text, headline=headline)
    if not normalized:
        return None
    if len(normalized) <= limit:
        return normalized
    cutoff = normalized[:limit]
    last_sentence = max(cutoff.rfind(". "), cutoff.rfind("! "), cutoff.rfind("? "))
    if last_sentence >= limit // 2:
        return cutoff[: last_sentence + 1]
    return f"{cutoff.rstrip()}…"


def _sanitize_article_text(text: str, *, headline: str = "") -> str:
    """Remove common site-chrome text that is not article evidence."""
    without_language_menu = LANGUAGE_MENU_PATTERN.sub(" ", text)
    normalized = " ".join(without_language_menu.split())
    normalized = re.sub(r"\bSkip\s+Navigation\b", " ", normalized, flags=re.IGNORECASE)
    normalized = " ".join(normalized.split())
    return _trim_to_headline(normalized, headline)


def _trim_to_headline(text: str, headline: str) -> str:
    """Discard leading site chrome when the RSS headline appears in page text."""
    headline_without_publisher = re.split(r"\s[-–—]\s", headline, maxsplit=1)[0].strip()
    if len(headline_without_publisher) < 12:
        return text
    normalized_text = _comparison_text(text)
    normalized_headline = _comparison_text(headline_without_publisher)
    position = normalized_text.find(normalized_headline)
    if position == -1 or position > 2_000:
        return text
    # _comparison_text preserves character positions for the punctuation variants
    # it replaces, so the position can safely slice the original normalized text.
    return text[position:]


def _comparison_text(value: str) -> str:
    """Normalize lightweight typography differences without changing offsets."""
    return value.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"})).casefold()


def _google_news_article_id(source_url: str) -> str | None:
    """Return the encoded ID from supported Google News article-link paths."""
    parsed_url = urlparse(source_url)
    if parsed_url.hostname != "news.google.com":
        return None
    parts = [part for part in parsed_url.path.split("/") if part]
    for index, part in enumerate(parts[:-1]):
        if part in {"articles", "read"}:
            return parts[index + 1]
    return None


def _parse_google_news_resolution(response_text: str) -> str | None:
    """Extract the resolved publisher URL from Google's batchexecute payload."""
    _, separator, payload_text = response_text.partition("\n\n")
    if not separator:
        return None
    payload = json.loads(payload_text)
    if not isinstance(payload, list):
        return None
    for item in payload:
        if not isinstance(item, list) or len(item) < 3 or item[1] != "Fbv4je" or not isinstance(item[2], str):
            continue
        decoded = json.loads(item[2])
        if isinstance(decoded, list) and len(decoded) > 1 and decoded[0] == "garturlres" and isinstance(decoded[1], str):
            return decoded[1]
    return None
