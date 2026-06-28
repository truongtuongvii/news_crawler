import aiohttp 
import asyncio
import calendar
import datetime
import logging
import os
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Optional, List, Union, Dict, Callable, Any
from urllib.parse import urlparse

from dotenv import load_dotenv

import feedparser
import httpx
from bs4 import BeautifulSoup
from elasticsearch import AsyncElasticsearch

try:
    import wmill
    _WMILL_AVAILABLE = True
except ImportError:
    _WMILL_AVAILABLE = False

# Tải các biến môi trường từ file .env nếu có
load_dotenv()

logger = logging.getLogger(__name__)

# ================= Configuration =================

@dataclass
class FeedConfig:
    url: str
    name: str = ""
    credibility_score: float = 0.7
    request_interval: float = 1.0
    headers: Dict[str, str] = field(default_factory=dict)
    timeout: Optional[float] = None
    max_articles: int = 0
    elasticsearch_index: str = "news_articles"

    def __post_init__(self):
        if not self.url:
            raise ValueError("Feed URL cannot be empty")


@dataclass
class CrawlerConfig:
    feeds: List[Union[str, FeedConfig]] = field(default_factory=list)
    timeout: float = 30.0
    max_retries: int = 3
    retry_delay: float = 2.0
    user_agent: str = "Mozilla/5.0 (compatible; FinhouseBot/1.0)"
    max_concurrent_feeds: int = 5
    max_concurrent_articles: int = 10
    global_rps: float = 5.0
    per_domain_rps: float = 2.0
    min_body_length: int = 200
    use_trafilatura: bool = True
    use_newspaper_fallback: bool = True
    fetch_full_content: bool = True
    etag_cache_path: Optional[str] = None
    max_article_age_seconds: int = 0

    def normalize_feeds(self) -> List[FeedConfig]:
        result = []
        for f in self.feeds:
            if isinstance(f, str):
                result.append(FeedConfig(url=f))
            elif isinstance(f, FeedConfig):
                result.append(f)
        return result

# ================= Models =================

def extract_domain(url: str) -> str:
    return urlparse(url).netloc.replace("www.", "")

def get_market_session(ts_ms: Optional[int]) -> str:
    if not ts_ms:
        return "unknown"
    dt = datetime.datetime.fromtimestamp(ts_ms / 1000, datetime.timezone.utc)
    hour_vn = (dt.hour + 7) % 24
    if (9 <= hour_vn < 11) or (13 <= hour_vn < 15):
        return "market_hours"
    if 7 <= hour_vn < 9:
        return "pre_market"
    if 15 <= hour_vn < 17:
        return "after_hours"
    return "closed"

@dataclass
class RawArticle:
    url: str
    feed_url: str
    feed_name: str = ""
    credibility_score: float = 0.7
    title: str = ""
    summary: str = ""
    author: str = ""
    published_at: Optional[int] = None
    updated_at: Optional[int] = None
    etag: str = ""
    last_modified: str = ""
    raw_html: str = ""
    crawled_at: int = field(default_factory=lambda: int(time.time() * 1000))

@dataclass
class ExtractedArticle:
    url: str
    feed_url: str
    feed_name: str = ""
    credibility_score: float = 0.7
    title: str = ""
    subheadline: str = ""
    summary: str = ""
    body: str = ""
    author: str = ""
    published_at: Optional[int] = None
    updated_at: Optional[int] = None
    crawled_at: int = field(default_factory=lambda: int(time.time() * 1000))
    extractor_used: str = ""
    extraction_success: bool = False
    body_length: int = 0
    raw_html: str = ""

    def __post_init__(self):
        if self.body:
            self.body_length = len(self.body)

    def to_dict(self) -> dict:
        return {
            "original_url": self.url,
            "source": {
                "name": self.feed_name or extract_domain(self.url),
                "domain": extract_domain(self.url),
                "credibility_score": self.credibility_score,
            },
            "content": {
                "headline": self.title,
                "subheadline": self.subheadline,
                "summary": self.summary,
                "body": self.body,
                "author": self.author,
            },
            "timing": {
                "published_at": self.published_at,
                "updated_at": self.updated_at,
                "market_session": get_market_session(self.published_at),
            },
            "metadata": {
                "process_status": "pending",
                "processed_at": None,
                "extractor_used": self.extractor_used,
                "extraction_success": self.extraction_success,
            },
            "created_at": self.crawled_at,
        }

@dataclass
class CrawlResult:
    feeds_attempted: int = 0
    feeds_ok: int = 0
    feeds_failed: int = 0
    articles_found: int = 0
    articles_skipped: int = 0
    articles_fetched: int = 0
    articles_extracted: int = 0
    errors: List[dict] = field(default_factory=list)
    extracted_articles: List[ExtractedArticle] = field(default_factory=list)
    started_at: int = field(default_factory=lambda: int(time.time() * 1000))
    finished_at: Optional[int] = None

    def finish(self):
        self.finished_at = int(time.time() * 1000)

    @property
    def duration_seconds(self) -> float:
        if self.finished_at:
            return (self.finished_at - self.started_at) / 1000
        return 0.0

    def summary(self) -> str:
        return (
            f"Crawl done in {self.duration_seconds:.1f}s | "
            f"feeds {self.feeds_ok}/{self.feeds_attempted} ok | "
            f"articles found={self.articles_found} "
            f"skipped={self.articles_skipped} "
            f"extracted={self.articles_extracted}"
        )

# ================= Utilities =================

class TokenBucket:
    def __init__(self, rate: float, capacity: float = None):
        self.rate = rate
        self.capacity = capacity if capacity is not None else rate * 5
        self._tokens = self.capacity
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0):
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                wait = (tokens - self._tokens) / self.rate
            await asyncio.sleep(wait)

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last_refill = now

class RateLimiter:
    def __init__(self, global_rps: float = 5.0, per_domain_rps: float = 2.0):
        self._global = TokenBucket(rate=global_rps)
        self._domains: Dict[str, TokenBucket] = defaultdict(
            lambda: TokenBucket(rate=per_domain_rps)
        )

    async def acquire(self, domain: str):
        await asyncio.gather(
            self._global.acquire(),
            self._domains[domain].acquire()
        )

class ETagCache:
    def __init__(self, state_key: str = "etag_cache"):
        self._state_key = state_key
        self._data: dict[str, dict] = {}
        self._use_wmill = _WMILL_AVAILABLE
        self._load()

    def get_headers(self, url: str) -> Dict[str, str]:
        entry = self._data.get(url, {})
        headers: Dict[str, str] = {}
        if entry.get("etag"):
            headers["If-None-Match"] = entry["etag"]
        if entry.get("last_modified"):
            headers["If-Modified-Since"] = entry["last_modified"]
        return headers

    def update(self, url: str, etag: str = "", last_modified: str = "") -> None:
        entry = self._data.get(url, {})
        if etag: entry["etag"] = etag
        if last_modified: entry["last_modified"] = last_modified
        self._data[url] = entry
        self._save()

    def _load(self) -> None:
        if not self._use_wmill: return
        try:
            state = wmill.get_state()
            if isinstance(state, dict):
                self._data = state.get(self._state_key) or {}
        except Exception:
            self._data = {}

    def _save(self) -> None:
        if not self._use_wmill: return
        try:
            state = {}
            try:
                existing = wmill.get_state()
                if isinstance(existing, dict): state = existing
            except Exception: pass
            state[self._state_key] = self._data
            wmill.set_state(state)
        except Exception: pass

# ================= Fetcher & Extractor =================

def _parse_date(date_input) -> Optional[int]:
    if not date_input: return None
    if hasattr(date_input, "tm_year"):
        try: return int(calendar.timegm(date_input) * 1000)
        except Exception: return None
    if isinstance(date_input, str):
        try: return int(parsedate_to_datetime(date_input).timestamp() * 1000)
        except Exception: return None
    return None

def _strip_tags(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ").strip()
    return re.sub(r"\s+", " ", text)

class RSSFetcher:
    def __init__(self, config: CrawlerConfig, rate_limiter: RateLimiter, etag_cache: ETagCache):
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
        if self._client: await self._client.aclose()

    async def fetch_feed(self, feed: FeedConfig) -> tuple[list[RawArticle], bool]:
        domain = urlparse(feed.url).netloc
        await self._limiter.acquire(domain)
        cond_headers = self._etag_cache.get_headers(feed.url)
        for attempt in range(self._config.max_retries):
            try:
                resp = await self._client.get(feed.url, headers=cond_headers)
                if resp.status_code == 304: return [], False
                resp.raise_for_status()
                etag = resp.headers.get("ETag")
                last_modified = resp.headers.get("Last-Modified")
                if etag or last_modified:
                    self._etag_cache.update(feed.url, etag=etag or "", last_modified=last_modified or "")
                articles = self._parse_feed(resp.text, feed)
                return articles, True
            except Exception as e:
                if attempt < self._config.max_retries - 1:
                    await asyncio.sleep(self._config.retry_delay * (attempt + 1))
                else:
                    raise e
        return [], True

    async def fetch_article_html(self, article: RawArticle) -> str:
        domain = urlparse(article.url).netloc
        await self._limiter.acquire(domain)
        for attempt in range(self._config.max_retries):
            try:
                resp = await self._client.get(article.url)
                resp.raise_for_status()
                return resp.text
            except Exception:
                if attempt < self._config.max_retries - 1:
                    await asyncio.sleep(self._config.retry_delay * (attempt + 1))
        return ""

    async def fetch_articles_html_batch(self, articles: list[RawArticle]) -> list[RawArticle]:
        sem = asyncio.Semaphore(self._config.max_concurrent_articles)
        async def _fetch(article: RawArticle) -> RawArticle:
            async with sem:
                article.raw_html = await self.fetch_article_html(article)
            return article
        return await asyncio.gather(*[_fetch(a) for a in articles])

    def _parse_feed(self, feed_text: str, feed: FeedConfig) -> list[RawArticle]:
        parsed = feedparser.parse(feed_text)
        articles = []
        entries = parsed.entries
        if feed.max_articles > 0: entries = entries[: feed.max_articles]
        now_ms = int(time.time() * 1000)
        max_age_ms = self._config.max_article_age_seconds * 1000
        for entry in entries:
            url = entry.get("link", "").strip()
            if not url: continue
            pub_ms = _parse_date(entry.get("published_parsed") or entry.get("updated_parsed"))
            if max_age_ms > 0 and pub_ms and (now_ms - pub_ms > max_age_ms): continue
            article = RawArticle(
                url=url,
                feed_url=feed.url,
                feed_name=feed.name or parsed.feed.get("title", ""),
                credibility_score=feed.credibility_score,
                title=entry.get("title", "").strip(),
                summary=_strip_tags(entry.get("summary", "") or entry.get("description", ""))[:500],
                published_at=pub_ms,
            )
            articles.append(article)
        return articles

class ContentExtractor:
    def __init__(self, config: CrawlerConfig):
        self._config = config

    def extract(self, raw: RawArticle) -> ExtractedArticle:
        base = ExtractedArticle(
            url=raw.url, feed_url=raw.feed_url, feed_name=raw.feed_name,
            credibility_score=raw.credibility_score, title=raw.title,
            summary=raw.summary, published_at=raw.published_at,
            crawled_at=raw.crawled_at, raw_html=raw.raw_html
        )
        if not raw.raw_html:
            base.body = raw.summary
            base.extractor_used = "rss_summary"
            return base

        # Trafilatura attempt
        try:
            import trafilatura
            trafilatura_result = trafilatura.extract(raw.raw_html, url=raw.url, output_format="python")
            if trafilatura_result and len(trafilatura_result.get("text", "")) >= self._config.min_body_length:
                base.body = trafilatura_result["text"]
                base.extractor_used = "trafilatura"
                base.extraction_success = True
                return base
        except ImportError: pass

        # Newspaper3k attempt
        try:
            from newspaper import Article
            art = Article(raw.url, language="vi")
            art.set_html(raw.raw_html)
            art.parse()
            if len(art.text) >= self._config.min_body_length:
                base.body = art.text
                base.extractor_used = "newspaper"
                base.extraction_success = True
                return base
        except ImportError: pass

        base.body = raw.summary
        base.extractor_used = "rss_summary"
        return base

# ================= Orchestrator =================

class RSSCrawler:
    def __init__(self, config: CrawlerConfig):
        self._config = config
        self._rate_limiter = RateLimiter(config.global_rps, config.per_domain_rps)
        self._etag_cache = ETagCache(config.etag_cache_path or "rss_crawl_state")
        self._extractor = ContentExtractor(config)

    async def crawl(
        self,
        is_seen: Optional[Callable[[str], Union[bool, asyncio.Future]]] = None,
        on_article: Optional[Callable[[Dict[str, Any]], Union[None, asyncio.Future]]] = None
    ) -> CrawlResult:
        feeds = self._config.normalize_feeds()
        result = CrawlResult(feeds_attempted=len(feeds))
        sem = asyncio.Semaphore(self._config.max_concurrent_feeds)
        async with RSSFetcher(self._config, self._rate_limiter, self._etag_cache) as fetcher:
            tasks = [self._process_feed(fetcher, f, is_seen, on_article, result, sem) for f in feeds]
            await asyncio.gather(*tasks, return_exceptions=True)
        result.finish()
        return result

    async def _process_feed(
        self,
        fetcher: RSSFetcher,
        feed: FeedConfig,
        is_seen: Optional[Callable],
        on_article: Optional[Callable],
        result: CrawlResult,
        sem: asyncio.Semaphore
    ):
        async with sem:
            try:
                articles, changed = await fetcher.fetch_feed(feed)
                if not changed:
                    result.feeds_ok += 1
                    return
                result.feeds_ok += 1
                result.articles_found += len(articles)
                
                # Bước 5: Deduplication
                to_fetch: list[RawArticle] = []
                for article in articles:
                    seen = False
                    if is_seen:
                        try:
                            if asyncio.iscoroutinefunction(is_seen):
                                seen = await is_seen(article.url)
                            else:
                                seen = is_seen(article.url)
                        except Exception as e:
                            logger.warning(f"Lỗi khi kiểm tra is_seen cho {article.url}: {e}")
                            seen = False
                    
                    if seen:
                        result.articles_skipped += 1
                        continue
                    to_fetch.append(article)

                if not to_fetch:
                    return

                # Bước 6: Fetch Full Content
                if self._config.fetch_full_content:
                    to_fetch = await fetcher.fetch_articles_html_batch(to_fetch)
                    result.articles_fetched += sum(1 for a in to_fetch if a.raw_html)
                
                # Bước 7 & 8: Extraction & Output
                extracted = [self._extractor.extract(a) for a in to_fetch]
                for art in extracted:
                    if art.extraction_success or art.body:
                        result.articles_extracted += 1
                        result.extracted_articles.append(art)
                        
                        if on_article:
                            try:
                                art_dict = art.to_dict()
                                if asyncio.iscoroutinefunction(on_article):
                                    await on_article(art_dict)
                                else:
                                    on_article(art_dict)
                            except Exception as e:
                                logger.error(f"Lỗi trong callback on_article cho {art.url}: {e}")

            except Exception as e:
                result.feeds_failed += 1
                result.errors.append({"feed": feed.url, "error": str(e)})

# ================= Windmill Main Entrypoint =================

def main(
    feed_urls: List[str] = [
        "https://vnexpress.net/rss/kinh-doanh.rss",
    ],
    fetch_full_content: bool = True,
    global_rps: float = 5.0,
    per_domain_rps: float = 2.0,
    max_concurrent_feeds: int = 5,
    max_concurrent_articles: int = 10,
    min_body_length: int = 200,
    max_article_age_days: int = 7,
    elasticsearch_host: Optional[str] = None,
    elasticsearch_index: Optional[str] = None
):
    """
    Kéo dữ liệu từ các RSS feeds, trích xuất nội dung bài viết và lưu vào database hoặc trả về kết quả.
    
    :param feed_urls: Danh sách các URL RSS feed cần crawl.
    :param fetch_full_content: Có tải toàn bộ nội dung HTML hay chỉ lấy summary từ RSS.
    :param global_rps: Giới hạn số lượng request mỗi giây trên toàn hệ thống.
    :param per_domain_rps: Giới hạn số lượng request mỗi giây cho mỗi tên miền.
    :param max_concurrent_feeds: Số lượng feed xử lý đồng thời.
    :param max_concurrent_articles: Số lượng bài báo tải HTML đồng thời.
    :param min_body_length: Độ dài tối thiểu của nội dung bài viết để coi là trích xuất thành công.
    :param max_article_age_days: Chỉ lấy các bài báo được xuất bản trong vòng N ngày qua.
    :param elasticsearch_host: Host kết nối Elasticsearch.
    :param elasticsearch_index: Tên index trong Elasticsearch.
    """
    return asyncio.run(run_crawler(
        feed_urls=feed_urls,
        fetch_full_content=fetch_full_content,
        global_rps=global_rps,
        per_domain_rps=per_domain_rps,
        max_concurrent_feeds=max_concurrent_feeds,
        max_concurrent_articles=max_concurrent_articles,
        min_body_length=min_body_length,
        max_article_age_days=max_article_age_days,
        elasticsearch_host=elasticsearch_host,
        elasticsearch_index=elasticsearch_index
    ))

async def run_crawler(
    feed_urls: List[str],
    fetch_full_content: bool,
    global_rps: float,
    per_domain_rps: float,
    max_concurrent_feeds: int,
    max_concurrent_articles: int,
    min_body_length: int,
    max_article_age_days: int,
    elasticsearch_host: Optional[str] = None,
    elasticsearch_index: Optional[str] = None
) -> Dict[str, Any]:
    # Ưu tiên lấy từ tham số, sau đó đến Biến môi trường, cuối cùng là giá trị mặc định
    elasticsearch_host = elasticsearch_host or os.getenv("ELASTICSEARCH_HOST") or "http://localhost:9200"
    elasticsearch_index = elasticsearch_index or os.getenv("ELASTICSEARCH_INDEX") or "news_articles"

    config = CrawlerConfig(
        feeds=feed_urls,
        fetch_full_content=fetch_full_content,
        global_rps=global_rps,
        per_domain_rps=per_domain_rps,
        max_concurrent_feeds=max_concurrent_feeds,
        max_concurrent_articles=max_concurrent_articles,
        min_body_length=min_body_length,
        max_article_age_seconds=max_article_age_days * 86400
    )
    
    # Setup Elasticsearch
    es = AsyncElasticsearch(elasticsearch_host)
    
    # Đảm bảo index tồn tại
    if not await es.indices.exists(index=elasticsearch_index):
        await es.indices.create(index=elasticsearch_index)

    async def is_seen(url: str) -> bool:
        try:
            res = await es.search(
                index=elasticsearch_index,
                query={"term": {"original_url.keyword": url}},
                size=0
            )
            return res["hits"]["total"]["value"] > 0
        except Exception as e:
            logger.warning(f"Lỗi khi kiểm tra is_seen trong ES cho {url}: {e}")
            return False

    async def on_article(article_dict: Dict[str, Any]):
        # Sử dụng index với ID dựa trên URL để tránh trùng lặp
        # ID của document có thể là hash của URL
        import hashlib
        doc_id = hashlib.md5(article_dict["original_url"].encode()).hexdigest()
        
        await es.index(
            index=elasticsearch_index,
            id=doc_id,
            document=article_dict,
            refresh=True
        )
        print(f"   [+] Ingested (ES): {article_dict.get('content', {}).get('headline', 'N/A')[:70]}...")

    crawler = RSSCrawler(config)
    result = await crawler.crawl(is_seen=is_seen, on_article=on_article)
    
    # Đóng connection
    await es.close()

    return {
        "summary": result.summary(),
        "articles": [a.to_dict() for a in result.extracted_articles],
        "errors": result.errors
    }

if __name__ == "__main__":
    # Sửa lỗi hiển thị tiếng Việt trên Console Windows nếu chạy local
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    # Để chạy test nhanh trên local
    print("Running local test...")
    res = main(feed_urls=["https://vnexpress.net/rss/the-gioi.rss"], max_article_age_days=1)
    print(res["summary"])
    if res["articles"]:
        print(f"Sample: {res['articles'][0]['content']['headline']}")

