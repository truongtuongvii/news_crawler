from dataclasses import dataclass, field
from typing import Optional, List
import time
import datetime
from urllib.parse import urlparse


# ================= Helpers =================

def extract_domain(url: str) -> str:
    return urlparse(url).netloc.replace("www.", "")


def get_market_session(ts_ms: Optional[int]) -> str:
    if not ts_ms:
        return "unknown"

    dt = datetime.datetime.utcfromtimestamp(ts_ms / 1000)
    hour_vn = (dt.hour + 7) % 24

    if (9 <= hour_vn < 11) or (13 <= hour_vn < 15):
        return "market_hours"
    if 7 <= hour_vn < 9:
        return "pre_market"
    if 15 <= hour_vn < 17:
        return "after_hours"
    return "closed"


# ================= RawArticle =================

@dataclass
class RawArticle:
    """Article từ RSS (chưa extract HTML)"""

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



# ================= ExtractedArticle =================

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

    def to_source_dict(self) -> dict:
        return {
            "name": self.feed_name or extract_domain(self.url),
            "domain": extract_domain(self.url),
            "credibility_score": self.credibility_score,
        }

    def to_content_dict(self) -> dict:
        return {
            "headline": self.title,
            "subheadline": self.subheadline,
            "summary": self.summary,
            "body": self.body,
            "author": self.author,
        }

    def to_dict(self) -> dict:
        return {
            "original_url": self.url,
            "source": self.to_source_dict(),
            "content": self.to_content_dict(),
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


# ================= CrawlResult =================

@dataclass
class CrawlResult:
    feeds_attempted: int = 0
    feeds_ok: int = 0
    feeds_failed: int = 0

    articles_found: int = 0
    articles_skipped: int = 0
    articles_fetched: int = 0
    articles_extracted: int = 0

    errors: List[str] = field(default_factory=list)
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