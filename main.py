from finhouse_rss.models import RawArticle
from finhouse_rss.config import CrawlerConfig
from finhouse_rss.extractor.content import ContentExtractor

config = CrawlerConfig(feeds=[])
extractor = ContentExtractor(config)

raw = RawArticle(
    url="https://example.com",
    feed_url="https://example.com/rss",
    summary="Demo summary",
    raw_html="<html><body><h1>Title</h1><p>This is a test article content.</p></body></html>"
)

result = extractor.extract(raw)

print("Title:", result.title)
print("Body:", result.body)
print("Extractor:", result.extractor_used)