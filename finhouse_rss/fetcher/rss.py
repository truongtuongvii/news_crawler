"""RSS feed fetcher — parses feeds and fetches article HTML."""

import asyncio
import calendar
import logging
import re
import time
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import feedparser
import httpx

from ..config import CrawlerConfig, FeedConfig
from ..models import RawArticle
from ..utils.etag_cache import ETagCache
from ..utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


def _parse_date(date_input) -> Optional[int]:
    """
    Convert a date value to milliseconds epoch (Int64).

    Accepts:
      - feedparser struct_time (e.g. entry.published_parsed / updated_parsed)
      - RFC 2822 string (e.g. "Mon, 14 Apr 2025 07:00:00 +0000")
    Returns:
      int milliseconds since epoch, or None if parsing fails.
    """
    if not date_input:
        return None

    # struct_time from feedparser — use calendar.timegm for UTC-safe conversion
    if hasattr(date_input, "tm_year"):
        try:
            return int(calendar.timegm(date_input) * 1000)
        except Exception:
            return None

    # RFC 2822 string
    if isinstance(date_input, str):
        try:
            return int(parsedate_to_datetime(date_input).timestamp() * 1000)
        except Exception:
            return None

    return None


class RSSFetcher:
    """
    Fetches and parses RSS feeds, then fetches full article HTML.

    Usage:
        async with RSSFetcher(config, rate_limiter, etag_cache) as fetcher:
            articles = await fetcher.fetch_feed(feed_config)
    """

    def __init__(
        self,
        config: CrawlerConfig,
        rate_limiter: RateLimiter,
        etag_cache: ETagCache,
    ):
        self._config = config
        self._limiter = rate_limiter
        self._etag_cache = etag_cache
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            timeout=self._config.timeout,
            headers={"User-Agent": self._config.user_agent},
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *_):
        if self._client:
            await self._client.aclose()

    async def fetch_feed(self, feed: FeedConfig) -> tuple[list[RawArticle], bool]:
        """
        Fetch one RSS feed and return (articles, changed).

        Returns:
            articles: list of RawArticle (empty if 304 Not Modified)
            changed: False if server returned 304 (feed unchanged)
        """
        domain = urlparse(feed.url).netloc
        await self._limiter.acquire(domain)

        cond_headers = self._etag_cache.get_headers(feed.url)

        for attempt in range(self._config.max_retries):
            try:
                resp = await self._client.get(feed.url, headers=cond_headers)

                # Feed unchanged
                if resp.status_code == 304:
                    logger.debug("304 Not Modified: %s", feed.url)
                    return [], False

                resp.raise_for_status()

                # Only update ETag cache when headers are actually present,
                # to avoid overwriting existing values with empty strings.
                etag = resp.headers.get("ETag")
                last_modified = resp.headers.get("Last-Modified")
                if etag or last_modified:
                    self._etag_cache.update(
                        feed.url,
                        etag=etag or "",
                        last_modified=last_modified or "",
                    )

                articles = self._parse_feed(resp.text, feed)
                return articles, True

            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401, 403, 404):
                    # Permanent errors: don't retry
                    logger.warning(
                        "HTTP %s (permanent) for feed %s",
                        e.response.status_code,
                        feed.url,
                    )
                    break
                logger.warning(
                    "HTTP %s (temporary) for feed %s",
                    e.response.status_code,
                    feed.url,
                )
                if attempt < self._config.max_retries - 1:
                    await asyncio.sleep(self._config.retry_delay * (attempt + 1))

            except (httpx.ConnectError, httpx.TimeoutException) as e:
                logger.warning(
                    "Attempt %d/%d failed for %s: %s",
                    attempt + 1,
                    self._config.max_retries,
                    feed.url,
                    e,
                )
                if attempt < self._config.max_retries - 1:
                    await asyncio.sleep(self._config.retry_delay * (attempt + 1))

        return [], True

    async def fetch_article_html(self, article: RawArticle) -> str:
        """Fetch raw HTML of an article URL. Returns empty string on failure."""
        domain = urlparse(article.url).netloc
        await self._limiter.acquire(domain)

        for attempt in range(self._config.max_retries):
            try:
                resp = await self._client.get(article.url)
                resp.raise_for_status()
                return resp.text
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401, 403, 404):
                    # Permanent errors: don't retry
                    logger.debug(
                        "HTTP %s (permanent) fetching %s",
                        e.response.status_code,
                        article.url,
                    )
                    break

                logger.warning(
                    "HTTP %s (temporary) fetching %s",
                    e.response.status_code,
                    article.url,
                )
                if attempt < self._config.max_retries - 1:
                    await asyncio.sleep(self._config.retry_delay * (attempt + 1))

            except (httpx.ConnectError, httpx.TimeoutException) as e:
                logger.warning(
                    "Article fetch attempt %d/%d failed for %s: %s",
                    attempt + 1,
                    self._config.max_retries,
                    article.url,
                    e,
                )

                if attempt < self._config.max_retries - 1:
                    await asyncio.sleep(self._config.retry_delay * (attempt + 1))

        return ""

    async def fetch_articles_html_batch(
        self, articles: list[RawArticle]
    ) -> list[RawArticle]:
        """Fetch HTML for a list of articles concurrently (respects max_concurrent_articles)."""
        sem = asyncio.Semaphore(self._config.max_concurrent_articles)

        async def _fetch(article: RawArticle) -> RawArticle:
            async with sem:
                article.raw_html = await self.fetch_article_html(article)
            return article

        return await asyncio.gather(*[_fetch(a) for a in articles])

    def _parse_feed(self, feed_text: str, feed: FeedConfig) -> list[RawArticle]:
        """Parse feedparser output into RawArticle list."""
        parsed = feedparser.parse(feed_text)
        articles = []

        entries = parsed.entries
        if feed.max_articles > 0:
            entries = entries[: feed.max_articles]

        now_ms = int(time.time() * 1000)
        max_age_ms = self._config.max_article_age_seconds * 1000

        for entry in entries:
            url = entry.get("link", "").strip()
            if not url:
                continue


            published_ms = _parse_date(
                entry.get("published_parsed") or entry.get("updated_parsed")
            )
            updated_ms = _parse_date(entry.get("updated_parsed"))

            if max_age_ms > 0 and published_ms is not None:
                if now_ms - published_ms > max_age_ms:
                    logger.debug("Skipping old article: %s", url)
                    continue

            # Extract summary text (strip basic HTML tags)
            summary_raw = (
                entry.get("summary", "")
                or entry.get("description", "")
            )
            summary = _strip_tags(summary_raw)

            article = RawArticle(
                url=url,
                feed_url=feed.url,
                feed_name=feed.name or parsed.feed.get("title", ""),
                credibility_score=feed.credibility_score,
                title=entry.get("title", "").strip(),
                summary=summary[:500],
                author=_extract_author(entry),
                published_at=published_ms,
                updated_at=updated_ms,
            )
            articles.append(article)

        return articles


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _strip_tags(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(separator=" ").strip()


def _extract_author(entry: dict) -> str:
    """Best-effort author extraction from feedparser entry."""
    if entry.get("author"):
        return entry["author"]
    authors = entry.get("authors", [])
    if authors:
        return authors[0].get("name", "")
    return ""