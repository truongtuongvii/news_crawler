import pytest, asyncio
from unittest.mock import MagicMock, patch

from finhouse_rss.config import CrawlerConfig, FeedConfig
from finhouse_rss.models import RawArticle, ExtractedArticle, CrawlResult
from finhouse_rss.extractor.content import ContentExtractor


# ================= CONFIG =================

def test_config_normalize_feeds_strings():
    config = CrawlerConfig(feeds=["a", "b"])
    feeds = config.normalize_feeds()
    assert len(feeds) == 2


def test_config_normalize_feeds_mixed():
    config = CrawlerConfig(feeds=["a", FeedConfig(url="b", name="B")])
    feeds = config.normalize_feeds()
    assert feeds[1].name == "B"


def test_config_invalid_feed_raises():
    config = CrawlerConfig(feeds=[123])
    with pytest.raises(ValueError):
        config.normalize_feeds()


# ================= MODELS =================

def test_extracted_article_to_source_dict():
    a = ExtractedArticle(url="https://cafef.vn/x", feed_url="rss", feed_name="CafeF")
    src = a.to_source_dict()
    assert src["name"] == "CafeF"


def test_extracted_article_to_content_dict():
    a = ExtractedArticle(url="x", feed_url="rss", title="A", body="B")
    c = a.to_content_dict()
    assert c["headline"] == "A"


def test_crawl_result_summary():
    r = CrawlResult(feeds_attempted=1, feeds_ok=1, articles_extracted=2)
    r.finish()
    assert "extracted=2" in r.summary()


def test_crawl_result_duration():
    r = CrawlResult()
    r.finish()
    assert r.duration_seconds >= 0


# ================= EXTRACTOR =================

def test_extractor_no_html_uses_rss_summary():
    config = CrawlerConfig(feeds=[], min_body_length=10)
    extractor = ContentExtractor(config)

    raw = RawArticle(url="x", feed_url="rss", summary="short text", raw_html="")
    result = extractor.extract(raw)

    assert result.extractor_used == "rss_summary"


def test_extractor_uses_trafilatura_when_valid(monkeypatch):
    config = CrawlerConfig(feeds=[], use_trafilatura=True, min_body_length=10)
    extractor = ContentExtractor(config)
    extractor._trafilatura_available = True

    fake_body = "long content " * 10

    import trafilatura
    monkeypatch.setattr(trafilatura, "extract",
        lambda *a, **k: {"title": "New", "author": "A", "text": fake_body})

    raw = RawArticle(url="x", feed_url="rss", raw_html="<html>ok</html>")
    result = extractor.extract(raw)

    assert result.extractor_used == "trafilatura"
    assert result.extraction_success


# ================= CRAWLRESULT =================

def test_crawl_result_duration():
    r = CrawlResult()
    assert r.duration_seconds == 0.0
    r.finish()
    assert r.duration_seconds >= 0.0


# ================= FALLBACK =================

def test_extractor_newspaper_fallback(monkeypatch):
    config = CrawlerConfig(feeds=[], min_body_length=100)
    extractor = ContentExtractor(config)
    extractor._trafilatura_available = True
    extractor._newspaper_available = True

    import trafilatura
    monkeypatch.setattr(trafilatura, "extract",
        lambda *a, **k: {"text": "short"})

    long_body = "long body " * 50
    mock_article = MagicMock()
    mock_article.title = "Title"
    mock_article.authors = ["A"]
    mock_article.text = long_body

    with patch("newspaper.Article", return_value=mock_article):
        raw = RawArticle(url="x", feed_url="rss", raw_html="<html>ok</html>")
        result = extractor.extract(raw)

    assert result.extractor_used == "newspaper"


def test_extractor_fallback_when_all_fail():
    config = CrawlerConfig(feeds=[], min_body_length=100)
    extractor = ContentExtractor(config)

    raw = RawArticle(url="x", feed_url="rss", summary="fallback text", raw_html="<html></html>")
    result = extractor.extract(raw)

    assert result.extractor_used == "rss_summary"


# ================= TITLE + SUBHEADLINE =================

def test_extractor_title_subheadline_logic(monkeypatch):
    config = CrawlerConfig(feeds=[], min_body_length=10)
    extractor = ContentExtractor(config)
    extractor._trafilatura_available = True

    import trafilatura
    monkeypatch.setattr(trafilatura, "extract",
        lambda *a, **k: {"title": "New Title", "text": "long " * 10})

    raw = RawArticle(url="x", feed_url="rss", title="Old Title", raw_html="<html>ok</html>")
    result = extractor.extract(raw)

    assert result.title == "New Title"
    assert result.subheadline == "Old Title"


# ================= SHORT BODY =================

def test_extractor_short_body_not_success():
    config = CrawlerConfig(feeds=[], min_body_length=100)
    extractor = ContentExtractor(config)

    raw = RawArticle(url="x", feed_url="rss", summary="short", raw_html="")
    result = extractor.extract(raw)

    assert not result.extraction_success


# ================= TokenBucket =================

# @pytest.mark.asyncio
# async def test_token_bucket_basic():
#     bucket = TokenBucket(rate=100)
#     # Should acquire immediately when full
#     await bucket.acquire()
#     await bucket.acquire()


# @pytest.mark.asyncio
# async def test_token_bucket_rate_limit():
#     """Acquiring more tokens than capacity should take time."""
#     bucket = TokenBucket(rate=10, capacity=2)
#     # Drain tokens
#     await bucket.acquire()
#     await bucket.acquire()
#     # Next acquire should wait ~0.1s
#     start = asyncio.get_event_loop().time()
#     await bucket.acquire()
#     elapsed = asyncio.get_event_loop().time() - start
#     assert elapsed >= 0.05  # at least 50ms


# ================= ETagCache =================

# def test_etag_cache_empty():
#     cache = ETagCache(cache_path=None)
#     assert cache.get_headers("https://example.com") == {}


# def test_etag_cache_stores_and_retrieves():
#     cache = ETagCache(cache_path=None)
#     cache.update("https://example.com/rss", etag='"abc123"', last_modified="Wed, 01 Jan 2025 00:00:00 GMT")
#     headers = cache.get_headers("https://example.com/rss")
#     assert headers["If-None-Match"] == '"abc123"'
#     assert headers["If-Modified-Since"] == "Wed, 01 Jan 2025 00:00:00 GMT"


# def test_etag_cache_unknown_url():
#     cache = ETagCache(cache_path=None)
#     cache.update("https://a.com/rss", etag="abc", last_modified="")
#     assert cache.get_headers("https://b.com/rss") == {}


# def test_etag_cache_persists(tmp_path):
#     path = str(tmp_path / "etag_cache.json")
#     cache1 = ETagCache(cache_path=path)
#     cache1.update("https://x.com/rss", etag='"xyz"', last_modified="")
#     # Load in new instance
#     cache2 = ETagCache(cache_path=path)
#     assert cache2.get_headers("https://x.com/rss")["If-None-Match"] == '"xyz"'