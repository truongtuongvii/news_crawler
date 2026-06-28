import asyncio
import logging
import sys
import io

# Đảm bảo in được tiếng Việt trên console Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Đảm bảo có thể import finhouse_rss từ thư mục hiện tại
sys.path.append(".")

from finhouse_rss import RSSCrawler, CrawlerConfig, FeedConfig

# Cấu hình logging để thấy quá trình chạy
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

async def main():
    # 1. Cấu hình Crawler
    # Chúng ta sẽ thử nghiệm với 1 feed của VnExpress
    config = CrawlerConfig(
        feeds=[
            FeedConfig(
                url="https://vnexpress.net/rss/bat-dong-san.rss",
                name="VnExpress Bất Động Sản",
                max_articles=5  # Chỉ lấy 5 bài để test cho nhanh
            )
        ],
        max_concurrent_articles=2
    )

    # 2. Giả lập cơ chế check trùng lặp bằng set()
    seen_urls = set()
    
    async def is_seen_mock(url: str) -> bool:
        return url in seen_urls

    # 4. Khởi tạo và chạy Crawler
    crawler = RSSCrawler(config)
    all_articles = []

    async def on_article_mock(article):
        all_articles.append(article.to_dict())
        print(f"Extracted: {article.title}")

    print("--- STARTING TEST CRAWL ---")
    await crawler.crawl(
        is_seen=is_seen_mock,
        on_article=on_article_mock
    )
    
    # 5. Lưu kết quả ra file JSON
    import json
    with open("crawl_results.json", "w", encoding="utf-8") as f:
        json.dump(all_articles, f, ensure_ascii=False, indent=2)
    
    print(f"\n--- SUCCESS ---")
    print(f"Đã lưu {len(all_articles)} bài viết vào file crawl_results.json")

if __name__ == "__main__":
    asyncio.run(main())
