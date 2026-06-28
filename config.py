from dataclasses import dataclass, field
from typing import Optional, List, Union, Dict


@dataclass
class FeedConfig:
    url: str
    name: str = ""
    credibility_score: float = 0.7
    request_interval: float = 1.0
    headers: Dict[str, str] = field(default_factory=dict)
    timeout: Optional[float] = None
    max_articles: int = 0

    def __post_init__(self):
        if not self.url:
            raise ValueError("Feed URL cannot be empty")


@dataclass
class CrawlerConfig:
    feeds: List[Union[str, FeedConfig]] = field(default_factory=list)

    timeout: float = 30.0
    max_retries: int = 3
    retry_delay: float = 2.0
    user_agent: str = (
        "Mozilla/5.0 (compatible; FinhouseBot/1.0)"
    )

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

    def __post_init__(self):
        if self.global_rps <= 0:
            raise ValueError("global_rps must be > 0")
        if self.per_domain_rps <= 0:
            raise ValueError("per_domain_rps must be > 0")

    def normalize_feeds(self) -> List[FeedConfig]:
        result = []
        for f in self.feeds:
            if isinstance(f, str):
                result.append(FeedConfig(url=f))
            elif isinstance(f, FeedConfig):
                result.append(f)
            else:
                raise ValueError(f"Invalid feed entry: {f!r}")
        return result