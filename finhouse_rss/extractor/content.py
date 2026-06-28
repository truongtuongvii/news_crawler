import logging
import re
from typing import Optional, List, Dict

from finhouse_rss.config import CrawlerConfig
from finhouse_rss.models import RawArticle, ExtractedArticle

logger = logging.getLogger(__name__)


class ContentExtractor:
    def __init__(self, config: CrawlerConfig):
        self._config = config
        self._trafilatura_available = self._check_trafilatura()
        self._newspaper_available = self._check_newspaper()

    def extract(self, raw: RawArticle) -> ExtractedArticle:
        base = self._build_base(raw)

        if not raw.raw_html:
            return self._fallback_summary(base, raw.summary)

        # 1. trafilatura
        if self._config.use_trafilatura and self._trafilatura_available:
            result = self._extract_trafilatura(raw.raw_html, raw.url)
            if self._is_valid_result(result):
                return self._apply_result(base, result, "trafilatura")

        # 2. newspaper
        if self._config.use_newspaper_fallback and self._newspaper_available:
            result = self._extract_newspaper(raw.raw_html, raw.url)
            if self._is_valid_result(result):
                return self._apply_result(base, result, "newspaper")

        # 3. fallback
        return self._fallback_summary(base, raw.summary)

    def extract_batch(self, articles: List[RawArticle]) -> List[ExtractedArticle]:
        return [self.extract(a) for a in articles]

    def _build_base(self, raw: RawArticle) -> ExtractedArticle:
        return ExtractedArticle(
            url=raw.url,
            feed_url=raw.feed_url,
            feed_name=raw.feed_name,
            credibility_score=raw.credibility_score,
            title=raw.title,
            summary=raw.summary,
            author=raw.author,
            published_at=raw.published_at,
            updated_at=raw.updated_at,
            crawled_at=raw.crawled_at,
            raw_html=raw.raw_html,
        )

    def _fallback_summary(self, base: ExtractedArticle, summary: str) -> ExtractedArticle:
        base.body = summary
        base.body_length = len(summary)
        base.extractor_used = "rss_summary"
        base.extraction_success = False   
        return base

    def _is_valid_result(self, result: Optional[Dict]) -> bool:
        if not result:
            return False
        body = result.get("body") or result.get("text") or ""
        return len(body.strip()) >= self._config.min_body_length

    def _extract_trafilatura(self, html: str, url: str) -> Optional[Dict]:
        try:
            import trafilatura

            result = trafilatura.extract(
                html,
                url=url,
                include_comments=False,
                include_tables=False,
                favor_precision=True,
                output_format="python",
            )

            if not result:
                return None

            if isinstance(result, dict):
                return {
                    "title": result.get("title", ""),
                    "author": result.get("author", ""),
                    "body": result.get("text", ""),
                }

            return {"title": "", "author": "", "body": str(result)}

        except Exception:
            return None

    def _extract_newspaper(self, html: str, url: str) -> Optional[Dict]:
        try:
            from newspaper import Article

            article = Article(url, language="vi")
            article.set_html(html)
            article.parse()

            return {
                "title": article.title or "",
                "author": ", ".join(article.authors) if article.authors else "",
                "body": article.text or "",
            }

        except Exception:
            return None

    def _apply_result(self, base: ExtractedArticle, result: Dict, extractor: str) -> ExtractedArticle:
        clean_body = _clean_text(result.get("body", ""))
        clean_title = _clean_text(result.get("title", ""))
        clean_author = _clean_text(result.get("author", ""))

        original_title = base.title

        base.body = clean_body
        base.body_length = len(clean_body)
        base.extractor_used = extractor
        base.extraction_success = True

        if clean_title:
            if original_title and original_title != clean_title:
                base.subheadline = original_title
            base.title = clean_title

        if clean_author:
            base.author = clean_author

        return base

    @staticmethod
    def _check_trafilatura() -> bool:
        try:
            import trafilatura
            return True
        except ImportError:
            return False

    @staticmethod
    def _check_newspaper() -> bool:
        try:
            import newspaper
            return True
        except ImportError:
            return False


def _clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()