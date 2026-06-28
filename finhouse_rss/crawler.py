import asyncio
import logging
from typing import Optional, Callable, Union

from .config import CrawlerConfig, FeedConfig
from .models import ExtractedArticle, CrawlResult, RawArticle
from .fetcher.rss import RSSFetcher
from .extractor.content import ContentExtractor
from .utils.rate_limiter import RateLimiter
from .utils.etag_cache import ETagCache

logger = logging.getLogger(__name__)

class RSSCrawler:
    """
    Main orchestrator for the news crawling workflow.
    Implements a robust fetching and extraction process with concurrency control.
    """

    def __init__(self, config: CrawlerConfig):
        self._config = config
        self._rate_limiter = RateLimiter(
            global_rps=config.global_rps,
            per_domain_rps=config.per_domain_rps
        )
        # Linh hoạt: lấy state_key từ config nếu có
        state_key = config.etag_cache_path or "rss_crawl_state"
        self._etag_cache = ETagCache(state_key=state_key)
        self._extractor = ContentExtractor(config)

    async def crawl(
        self, 
        is_seen: Optional[Callable[[str], Union[bool, asyncio.Future]]] = None,
        on_article: Optional[Callable[[ExtractedArticle], Union[None, asyncio.Future]]] = None
    ) -> CrawlResult:
        """Execute the full crawling workflow for all configured feeds."""
        feeds = self._config.normalize_feeds()
        result = CrawlResult(feeds_attempted=len(feeds))
        
        # Semaphore để giới hạn số lượng feed xử lý đồng thời
        sem = asyncio.Semaphore(self._config.max_concurrent_feeds)
        
        async with RSSFetcher(self._config, self._rate_limiter, self._etag_cache) as fetcher:
            tasks = [
                self._process_feed(fetcher, feed_conf, is_seen, on_article, result, sem)
                for feed_conf in feeds
            ]
            
            # Thực hiện song song tất cả các feed, an toàn hơn với Semaphore bên trong
            await asyncio.gather(*tasks, return_exceptions=True)

        result.finish()
        logger.info(result.summary())
        return result

    async def crawl_feed(
        self,
        feed_url: str,
        is_seen: Optional[Callable] = None,
        on_article: Optional[Callable] = None,
    ) -> CrawlResult:
        """Phương thức tiện ích để crawl một feed URL duy nhất."""
        original_feeds = self._config.feeds
        self._config.feeds = [feed_url]
        try:
            result = await self.crawl(is_seen=is_seen, on_article=on_article)
        finally:
            self._config.feeds = original_feeds
        return result

    async def _process_feed(
        self, 
        fetcher: RSSFetcher, 
        feed_conf: FeedConfig,
        is_seen: Optional[Callable],
        on_article: Optional[Callable],
        result: CrawlResult,
        sem: asyncio.Semaphore
    ):
        async with sem:
            try:
                # Bước 2 & 3: Fetch feed và xử lý 304 Not Modified
                articles, changed = await fetcher.fetch_feed(feed_conf)
                
                if not changed:
                    result.feeds_ok += 1
                    logger.debug(f"Feed unchanged (304): {feed_conf.url}")
                    return

                result.feeds_ok += 1
                result.articles_found += len(articles)

                # Bước 5: Deduplication (Hỗ trợ cả sync và async callback)
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

                # Bước 6: Tải HTML bài viết (nếu cấu hình cho phép)
                if self._config.fetch_full_content:
                    logger.info(f"Đang tải {len(to_fetch)} bài viết từ {feed_conf.url}")
                    to_fetch = await fetcher.fetch_articles_html_batch(to_fetch)
                    result.articles_fetched += sum(1 for a in to_fetch if a.raw_html)

                # Bước 7: Trích xuất nội dung
                extracted = self._extractor.extract_batch(to_fetch)
                
                # Bước 8: Callback & Lưu kết quả
                for article in extracted:
                    # Chỉ tính là thành công nếu trích xuất được body hoặc có flag thành công
                    if article.extraction_success or (article.body and len(article.body) > 0):
                        result.articles_extracted += 1
                        result.extracted_articles.append(article)
                        
                        if on_article:
                            try:
                                if asyncio.iscoroutinefunction(on_article):
                                    await on_article(article)
                                else:
                                    on_article(article)
                            except Exception as e:
                                logger.error(f"Lỗi trong callback on_article cho {article.url}: {e}")

            except Exception as e:
                result.feeds_failed += 1
                error_msg = f"Lỗi khi xử lý feed {feed_conf.url}: {str(e)}"
                result.errors.append({"feed": feed_conf.url, "error": str(e)})
                logger.error(error_msg, exc_info=True)
