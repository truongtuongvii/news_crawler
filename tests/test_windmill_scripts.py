import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import sys
import os
import time
from datetime import datetime, timezone

# Thêm thư mục windmill_scripts vào path để có thể import
WINDMILL_SCRIPTS_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'windmill_scripts'))
if WINDMILL_SCRIPTS_PATH not in sys.path:
    sys.path.insert(0, WINDMILL_SCRIPTS_PATH)

from finhouse_rss_crawler import (
    run_crawler, extract_domain, get_market_session, _parse_date, _strip_tags,
    TokenBucket, RateLimiter, ETagCache, ContentExtractor,
    RSSFetcher, RSSCrawler, CrawlerConfig, FeedConfig, RawArticle, ExtractedArticle
)

class TestCrawlerComprehensive(unittest.IsolatedAsyncioTestCase):
    """
    BỘ TEST TỔNG HỢP CHO RSS CRAWLER
    Bao phủ: Utilities, Rate Limiting, Caching, Extraction, Fetching và Workflow.
    """
    
    # ================= KIỂM THỬ TIỆN ÍCH (UTILITIES) =================
    
    def test_extract_domain(self):
        """Kiểm tra việc tách domain từ URL."""
        self.assertEqual(extract_domain("https://vnexpress.net/kinh-doanh.rss"), "vnexpress.net")
        self.assertEqual(extract_domain("http://www.thanhnien.vn/rss.xml"), "thanhnien.vn")
        self.assertEqual(extract_domain("https://cafef.vn/rss"), "cafef.vn")

    def test_get_market_session(self):
        """Kiểm tra việc xác định phiên giao dịch (giờ VN)."""
        # Giờ VN là UTC+7
        # 9:30 VN = 2:30 UTC -> market_hours (trong phiên)
        dt_market = datetime(2026, 4, 16, 2, 30, tzinfo=timezone.utc)
        ts_market = int(dt_market.timestamp() * 1000)
        self.assertEqual(get_market_session(ts_market), "market_hours")
        
        # 16:00 VN = 9:00 UTC -> after_hours (sau phiên)
        dt_after = datetime(2026, 4, 16, 9, 0, tzinfo=timezone.utc)
        ts_after = int(dt_after.timestamp() * 1000)
        self.assertEqual(get_market_session(ts_after), "after_hours")
        
        # 22:00 VN = 15:00 UTC -> closed (đóng cửa)
        dt_closed = datetime(2026, 4, 16, 15, 0, tzinfo=timezone.utc)
        ts_closed = int(dt_closed.timestamp() * 1000)
        self.assertEqual(get_market_session(ts_closed), "closed")

    def test_parse_date(self):
        """Kiểm tra việc phân tích định dạng ngày tháng."""
        # Kiểm tra định dạng chuỗi
        self.assertIsNotNone(_parse_date("Thu, 16 Apr 2026 10:00:00 +0700"))
        # Kiểm tra định dạng struct_time (đầu ra của feedparser)
        import calendar
        st = time.strptime("2026-04-16 10:00:00", "%Y-%m-%d %H:%M:%S")
        self.assertEqual(_parse_date(st), calendar.timegm(st) * 1000)
        # Kiểm tra giá trị None
        self.assertIsNone(_parse_date(None))

    def test_strip_tags(self):
        """Kiểm tra việc loại bỏ thẻ HTML và làm sạch khoảng trắng."""
        html = "<div>Xin chào <script>alert(1)</script> <b>Thế giới</b></div>"
        self.assertEqual(_strip_tags(html), "Xin chào Thế giới")

    # ================= KIỂM THỬ GIỚI HẠN TỐC ĐỘ (RATE LIMITER) =================

    async def test_token_bucket(self):
        """Kiểm tra thuật toán Token Bucket."""
        bucket = TokenBucket(rate=10, capacity=1)
        # Lần lấy đầu tiên phải tức thì
        start = time.monotonic()
        await bucket.acquire(1.0)
        self.assertLess(time.monotonic() - start, 0.1)
        
        # Lần lấy thứ hai phải đợi khoảng 0.1s (1 token / rate 10)
        start = time.monotonic()
        await bucket.acquire(1.0)
        self.assertGreaterEqual(time.monotonic() - start, 0.08)

    async def test_rate_limiter(self):
        """Kiểm tra bộ điều phối RateLimiter."""
        limiter = RateLimiter(global_rps=100, per_domain_rps=10)
        start = time.monotonic()
        # Lấy 5 token cho cùng một domain
        for _ in range(5):
            await limiter.acquire("test.com")
        # Với capacity mặc định = rate * 5, 5 token đầu tiên sẽ được lấy tức thì
        self.assertLess(time.monotonic() - start, 0.1)

    # ================= KIỂM THỬ CƠ CHẾ CACHING (ETAG CACHE) =================

    def test_etag_cache_logic(self):
        """Kiểm tra logic lưu trữ và lấy Header ETag/Last-Modified."""
        # Giả lập không có Windmill để tránh gọi API state thật
        with patch('finhouse_rss_crawler._WMILL_AVAILABLE', False):
            cache = ETagCache()
            cache.update("http://test.com", etag="v1", last_modified="Mon")
            
            headers = cache.get_headers("http://test.com")
            self.assertEqual(headers["If-None-Match"], "v1")
            self.assertEqual(headers["If-Modified-Since"], "Mon")
            
            self.assertEqual(cache.get_headers("http://other.com"), {})

    # ================= KIỂM THỬ CHIẾT XUẤT NỘI DUNG (EXTRACTOR) =================

    def test_content_extractor_fallbacks(self):
        """Kiểm tra cơ chế dự phòng khi chiết xuất nội dung thất bại."""
        config = CrawlerConfig(min_body_length=50)
        extractor = ContentExtractor(config)
        
        # Trường hợp 1: Không có HTML -> sử dụng tóm tắt từ RSS
        raw_no_html = RawArticle(url="http://t.com", feed_url="http://f.com", summary="Nội dung tóm tắt", raw_html="")
        ext1 = extractor.extract(raw_no_html)
        self.assertEqual(ext1.body, "Nội dung tóm tắt")
        self.assertEqual(ext1.extractor_used, "rss_summary")
        
        # Trường hợp 2: Có HTML nhưng quá ngắn hoặc extractor thất bại
        raw_html = RawArticle(url="http://t.com", feed_url="http://f.com", summary="Nội dung tóm tắt", raw_html="<html><body>Quá ngắn</body></html>")
        ext2 = extractor.extract(raw_html)
        self.assertEqual(ext2.body, "Nội dung tóm tắt") # Dự phòng sang summary vì body < 50 ký tự

    # ================= KIỂM THỬ BỘ TẢI (FETCHER) =================

    async def test_fetcher_retries(self):
        """Kiểm tra cơ chế thử lại (Retry) khi gặp lỗi mạng."""
        config = CrawlerConfig(max_retries=2, retry_delay=0.01)
        limiter = RateLimiter()
        cache = ETagCache()
        
        async with RSSFetcher(config, limiter, cache) as fetcher:
            with patch.object(fetcher._client, 'get', side_effect=Exception("Lỗi mạng giả lập")) as mock_get:
                with self.assertRaises(Exception):
                    await fetcher.fetch_feed(FeedConfig(url="http://fail.com"))
                # Phải gọi 2 lần (thử lại 1 lần)
                self.assertEqual(mock_get.call_count, 2)

    async def test_fetcher_parse_304(self):
        """Kiểm tra việc xử lý phản hồi 304 Not Modified."""
        config = CrawlerConfig()
        limiter = RateLimiter()
        cache = ETagCache()
        
        async with RSSFetcher(config, limiter, cache) as fetcher:
            mock_resp = MagicMock()
            mock_resp.status_code = 304
            with patch.object(fetcher._client, 'get', return_value=mock_resp):
                articles, changed = await fetcher.fetch_feed(FeedConfig(url="http://test.com"))
                self.assertEqual(articles, [])
                self.assertFalse(changed)

    # ================= KIỂM THỬ TOÀN BỘ QUY TRÌNH (WORKFLOW) =================

    async def test_crawler_full_workflow(self):
        """Kiểm tra luồng hoạt động đầy đủ của Crawler."""
        config = CrawlerConfig(feeds=["http://test.com/rss"])
        crawler = RSSCrawler(config)
        
        # Giả lập Fetcher
        mock_fetcher = AsyncMock()
        mock_article = RawArticle(
            url="http://test.com/1", 
            feed_url="http://test.com/rss", 
            title="Tiêu đề test",
            summary="Đây là tóm tắt hợp lệ để kiểm thử."
        )
        mock_fetcher.fetch_feed.return_value = ([mock_article], True)
        mock_fetcher.fetch_articles_html_batch.return_value = [mock_article]
        mock_fetcher.__aenter__.return_value = mock_fetcher
        
        # Theo dõi kết quả
        seen_urls = []
        ingested_articles = []
        
        async def is_seen(url):
            seen_urls.append(url)
            return False
            
        async def on_article(art):
            ingested_articles.append(art)
            
        with patch('finhouse_rss_crawler.RSSFetcher', return_value=mock_fetcher):
            result = await crawler.crawl(is_seen=is_seen, on_article=on_article)
            
        self.assertEqual(result.feeds_ok, 1)
        self.assertEqual(result.articles_found, 1)
        self.assertIn(mock_article.url, seen_urls)
        self.assertEqual(len(ingested_articles), 1)
        self.assertEqual(ingested_articles[0]['content']['headline'], "Tiêu đề test")

    async def test_run_crawler_entrypoint(self):
        """Kiểm tra điểm khởi chạy run_crawler với giả lập MongoDB."""
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
             patch("motor.motor_asyncio.AsyncIOMotorClient") as mock_mongo_client:
            
            # Giả lập phản hồi HTTP
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = '<?xml version="1.0"?><rss version="2.0"><channel><item><title>Bài viết</title><link>http://t.com</link></item></channel></rss>'
            mock_get.return_value = mock_response
            
            # Giả lập cấu trúc MongoDB
            mock_collection = mock_mongo_client.return_value.__getitem__.return_value.__getitem__.return_value
            mock_collection.find_one = AsyncMock(return_value=None)
            mock_collection.update_one = AsyncMock()

            result = await run_crawler(
                feed_urls=["http://test.com/rss"],
                fetch_full_content=False,
                global_rps=1.0, per_domain_rps=1.0, max_concurrent_feeds=1, max_concurrent_articles=1,
                min_body_length=10, max_article_age_days=7,
                mongodb_uri="mongodb://localhost:8121",
                mongodb_database="test_db"
            )

            self.assertIn("summary", result)
            self.assertTrue(mock_mongo_client.called)

if __name__ == "__main__":
    unittest.main()
