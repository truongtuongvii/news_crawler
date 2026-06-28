# Workflow RSS Crawler

# Tài Liệu Workflow RSS Crawler

**Ngày:** 2026-04-06

**Mục đích:** Giải thích chi tiết workflow crawl tin tức tài chính từ RSS feeds

---

## Tổng Quan

`finhouse_rss` là thư viện Python tự động hoá toàn bộ quy trình thu thập tin tức tài chính từ RSS feeds. Workflow được thiết kế theo nguyên tắc **fail-safe**: mỗi bước đều có cơ chế dự phòng, đảm bảo dữ liệu vẫn được thu thập ngay cả khi một phần pipeline gặp sự cố.

Workflow gồm **8 bước tuần tự**, chia thành 3 giai đoạn lớn:

```
┌─────────────────────────────────────────────────────────────┐
│  GIAI ĐOẠN 1 — THU THẬP                                    │
│  Bước 1: Khởi tạo → Bước 2: Fetch RSS → Bước 3: Kiểm tra  │
├─────────────────────────────────────────────────────────────┤
│  GIAI ĐOẠN 2 — LỌC & TẢI                                   │
│  Bước 4: Parse entries → Bước 5: Dedup → Bước 6: Fetch HTML│
├─────────────────────────────────────────────────────────────┤
│  GIAI ĐOẠN 3 — XỬ LÝ                                       │
│  Bước 7: Extract content → Bước 8: Output                  │
└─────────────────────────────────────────────────────────────┘
```

---

## Bước 1 — Khởi Tạo

Khi `await crawler.crawl()` được gọi, hệ thống khởi tạo **3 thành phần** trước khi làm bất cứ điều gì khác:

```python
RateLimiter   ← kiểm soát tốc độ gửi request
ETagCache     ← nhớ trạng thái của từng RSS feed từ lần chạy trước
httpx.AsyncClient  ← HTTP client dùng chung, tái sử dụng TCP connection
```

### RateLimiter

Nếu gửi quá nhiều request cùng lúc, server của các trang báo sẽ block IP. `RateLimiter` dùng thuật toán **token bucket 2 tầng**:

- **Global bucket**: tối đa 5 request/giây cho toàn bộ hệ thống
- **Per-domain bucket**: tối đa 2 request/giây riêng cho mỗi domain

Ví dụ: dù đang crawl đồng thời CafeF và VnEconomy, mỗi trang chỉ nhận tối đa 2 request/giây, và tổng cộng không vượt quá 5 request/giây.

```
Request đến cafef.vn
      │
      ├── chờ global_bucket.acquire()   ← tối đa 5 req/s toàn cục
      └── chờ domain_bucket["cafef.vn"].acquire()  ← tối đa 2 req/s cho CafeF
```

### ETagCache

RSS feeds được crawl nhiều lần mỗi ngày nếu không có cơ chế kiểm tra, mỗi lần crawl đều phải tải lại toàn bộ XML của feed dù không có bài mới dẫn đến lãng phí băng thông.

`ETagCache` giúp chúng ta biết dữ liệu có thay đổi không và tránh load lại dữ liệu cũ.

```json
{
  "https://cafef.vn/rss.xml": {
    "etag": "\"abc123def456\"",
    "last_modified": "Mon, 06 Apr 2026 08:00:00 GMT"
  }
}
```

File JSON này được lưu trên disk và tồn tại giữa các lần chạy — nếu Windmill restart, cache vẫn còn.

---

## Bước 2 — Fetch RSS Feed (Conditional Request)

Với mỗi feed trong danh sách, hệ thống gửi HTTP GET request đến RSS URL. Tuy nhiên, request này **không phải request thông thường** — nó mang theo thông tin từ ETagCache để hỏi server: *"Feed này có thay đổi gì kể từ lần tôi tải lần trước không?"*

```python
# Lấy conditional headers từ cache
cond_headers = etag_cache.get_headers("https://cafef.vn/rss.xml")
# → {"If-None-Match": "\"abc123\"", "If-Modified-Since": "Mon, 06 Apr 2026 08:00:00 GMT"}

# Gửi request kèm headers
response = await http_client.get("https://cafef.vn/rss.xml", headers=cond_headers)
```

### Conditional Request

Khi server nhận được `If-None-Match` hoặc `If-Modified-Since`, nó so sánh với trạng thái hiện tại của resource:

- Nếu **không thay đổi**: trả về `304 Not Modified` với body rỗng.
- Nếu **có thay đổi**: trả về `200 OK` với full XML.

### Concurrency

Các feeds được fetch **đồng thời** thay vì tuần tự, nhưng có giới hạn:

```python
semaphore = asyncio.Semaphore(max_concurrent_feeds)  # mặc định: 5

# Tất cả feeds chạy song song, nhưng tối đa 5 feeds cùng một lúc
await asyncio.gather(*[fetch_feed(f) for f in feeds])
```

### Retry Logic

Nếu request thất bại (timeout, lỗi mạng), hệ thống tự động thử lại với delay tăng dần:

```
Lần 1 thất bại → chờ 2 giây  → thử lại
Lần 2 thất bại → chờ 4 giây  → thử lại
Lần 3 thất bại → chờ 6 giây  → ghi lỗi, bỏ qua feed này
```

Lỗi HTTP cố định (403 Forbidden, 404 Not Found) không retry vì retry sẽ không giúp ích.

---

## Bước 3 — Kiểm Tra 304 Not Modified

Sau khi nhận response, hệ thống kiểm tra HTTP status code và rẽ nhánh:

```
Response status = 304?
        │
        ├── CÓ → feed không có gì mới
        │         cập nhật ETagCache với giá trị mới từ response headers
        │         return (articles=[], changed=False)
        │         ↓ RSSCrawler bỏ qua feed này hoàn toàn
        │
        └── KHÔNG (200 OK) → feed có bài mới hoặc đã thay đổi
                  cập nhật ETagCache với ETag/Last-Modified mới
                  tiếp tục parse XML
```

### Cập nhật ETagCache

Dù là 304 hay 200, ETagCache luôn được cập nhật với giá trị mới nhất từ response headers:

```python
etag_cache.update(
    url=feed.url,
    etag=response.headers.get("ETag", ""),
    last_modified=response.headers.get("Last-Modified", ""),
)
```

---

## Bước 4 — Parse Entries → RawArticle[]

`feedparser.parse()` chuyển RSS XML thành Python objects. Hệ thống đọc từng `<item>` (RSS) hoặc `<entry>` (Atom) và tạo `RawArticle`:

```
RSS XML entry:
  <item>
    <title>VN-Index tăng 15 điểm phiên chiều</title>
    <link>https://cafef.vn/bai-viet-123.chn</link>
    <description>Thị trường chứng khoán Việt Nam...</description>
    <author>Nguyễn Văn A</author>
    <pubDate>Mon, 06 Apr 2026 14:30:00 GMT</pubDate>
  </item>
          │
          ▼
RawArticle:
  url          = "https://cafef.vn/bai-viet-123.chn"
  title        = "VN-Index tăng 15 điểm phiên chiều"
  summary      = "Thị trường chứng khoán Việt Nam..."  ← chỉ ~100-300 ký tự
  author       = "Nguyễn Văn A"
  published_at = 1744000200000  ← đã convert sang milliseconds epoch
  raw_html     = ""             ← chưa có, sẽ fetch ở Bước 6
```

### Chuyển đổi timestamp

RSS dùng định dạng RFC 2822 (`Mon, 06 Apr 2026 14:30:00 GMT`). Hệ thống convert sang **milliseconds epoch** (Int64) để khớp với schema `NewsArticle.timing.published_at` trong MongoDB:

```
"Mon, 06 Apr 2026 14:30:00 GMT"  →  1744000200000
```

### Lọc bài theo thời gian

Nếu `max_article_age_seconds` được cấu hình (ví dụ 6 giờ = 21600 giây), những bài cũ hơn sẽ bị loại bỏ **ngay tại bước này**, trước khi fetch HTML:

```python
if (now_ms - published_at_ms) > max_article_age_seconds * 1000:
    bỏ qua bài này  # tiết kiệm bandwidth, không cần fetch HTML
```

Lọc sớm ở đây rất quan trọng: một feed có thể chứa 50-100 entries nhưng chỉ có 5-10 bài trong 6 giờ gần nhất.

---

## Bước 5 — Dedup qua is_seen()

Trước khi fetch HTML của bài viết, hệ thống kiểm tra xem URL đó đã tồn tại trong database chưa. Nếu rồi, bỏ qua — không cần tải lại.

```python
for article in raw_articles:
    if is_seen(article.url):
        articles_skipped += 1
        continue  # không fetch HTML, không extract

    to_fetch.append(article)  # đưa vào danh sách cần xử lý
```

### Thiết kế callback

Thư viện **không tự quyết định** cách kiểm tra dedup, mà nhận một hàm callback từ người dùng. Điều này cho phép tích hợp với bất kỳ storage nào:

```python
# Dùng với MongoDB (async)
async def is_seen(url: str) -> bool:
    doc = await db.news_articles.find_one({"original_url": url}, {"_id": 1})
    return doc is not None

# Dùng với Redis (nhanh hơn, in-memory)
async def is_seen(url: str) -> bool:
    return await redis.exists(f"seen:{url}") > 0

# Dùng với Python set (cho testing)
seen_set = set()
def is_seen(url: str) -> bool:
    return url in seen_set
```

---

## Bước 6 — Fetch Article HTML

Với các bài chưa thấy, hệ thống tải HTML đầy đủ của từng trang bài viết. Đây là bước tốn thời gian nhất vì mỗi URL cần một HTTP request riêng.

Các bài được fetch **đồng thời** theo batch:

```python
semaphore = asyncio.Semaphore(max_concurrent_articles)  # mặc định: 10

async def fetch_one(article):
    async with semaphore:
        await rate_limiter.acquire(domain)  # tuân thủ rate limit
        article.raw_html = await http_client.get(article.url)
    return article

# Tất cả bài fetch song song, tối đa 10 bài cùng lúc
results = await asyncio.gather(*[fetch_one(a) for a in to_fetch])
```

### Tại sao cần fetch full HTML?

RSS feed chỉ có `<description>` khoảng 100-300 ký tự — đủ để preview nhưng không đủ cho LLM extraction. Bước LLM sau cần full body để:

- Phân tích chi tiết sentiment của toàn bài
- Extract tên công ty, mã cổ phiếu được đề cập
- Phát hiện các sự kiện quan trọng (phá sản, sáp nhập, kết quả kinh doanh...)

### Xử lý khi fetch thất bại

Nếu request thất bại sau tất cả lần retry, `raw_html` được set thành chuỗi rỗng. Bài **vẫn tiếp tục** được xử lý ở bước tiếp theo — `ContentExtractor` sẽ fallback về RSS summary thay vì bỏ bài hoàn toàn.

---

## Bước 7 — Content Extraction (3 Tầng Fallback)

`ContentExtractor` nhận `raw_html` và extract ra nội dung thuần túy — loại bỏ navigation, quảng cáo, sidebar, footer. Hệ thống thử **3 phương pháp** theo thứ tự ưu tiên, dừng lại khi kết quả đủ dài.

### Tầng 1: trafilatura (Primary)

trafilatura là thư viện Python dùng machine learning để phân biệt main content với "boilerplate" (nav, ads, sidebar). 

```python
result = trafilatura.extract(
    html,
    url=url,
    favor_precision=True,   # ưu tiên chính xác hơn là lấy nhiều
    output_format="python", # trả về dict thay vì plain text
)
# result = {"title": "...", "author": "...", "text": "full article body..."}
```

### Tầng 2: newspaper3k (Fallback)

newspaper3k dùng heuristic-based approach: tìm thẻ HTML có class/id liên quan đến content (`<article>`, `<div class="content">`, v.v.). Rộng hơn trafilatura nhưng đôi khi bắt thêm nội dung không liên quan.

```python
article = Article(url, language="vi")
article.set_html(html)      # không request lại, dùng HTML đã có
article.parse()
# article.text = full body
```

### Tầng 3: RSS Summary (Last Resort)

Khi cả hai extractor đều thất bại bài vẫn được output với đầy đủ metadata (title, URL, timestamp...) chỉ thiếu full body. Ở bước LLM sau, hệ thống có thể nhận biết qua `extraction_success=False` để xử lý khác hoặc re-crawl sau.

```python
body = raw_article.summary   # đoạn mô tả ngắn từ RSS feed
extraction_success = False   # đánh dấu để biết quality thấp
extractor_used = "rss_summary"
```

### Kết quả sau extraction

Mỗi `RawArticle` trở thành `ExtractedArticle` với đầy đủ thông tin:

```python
ExtractedArticle(
    title        = "VN-Index tăng 15 điểm trong phiên giao dịch chiều nay",
    subheadline  = "",
    summary      = "Thị trường chứng khoán Việt Nam...",   # từ RSS
    body         = "Kết thúc phiên giao dịch chiều 6/4, VN-Index đóng cửa ở mức...",  # full text
    author       = "Nguyễn Văn A",
    extractor_used    = "trafilatura",
    extraction_success = True,
    body_length       = 2847,   # số ký tự
)
```

---

## Bước 8 — Callback & Output

Mỗi bài extract xong được xử lý **ngay lập tức** qua hai kênh song song:

```python
# Kênh 1: callback on_article (nếu được truyền vào)
if on_article:
    await on_article(extracted_article)   # insert DB, gọi API, v.v.

# Kênh 2: tích luỹ vào CrawlResult
result.extracted_articles.append(extracted_article)
result.articles_extracted += 1
```

### Tại sao callback thay vì trả về toàn bộ kết quả?

`on_article` được gọi **ngay sau mỗi bài**, không đợi cả batch hoàn thành. Lợi ích:

- **Tiết kiệm memory**: không phải giữ tất cả bài trong RAM
- **Real-time**: bài được insert vào MongoDB ngay khi ready, không phải chờ toàn bộ crawl xong
- **Fault-tolerant**: nếu process bị kill giữa chừng, những bài đã callback vẫn được lưu

```python
# Ví dụ: insert vào MongoDB ngay khi mỗi bài xong
async def on_article(article: ExtractedArticle):
    await db.news_articles.insert_one(article.to_dict())

result = await crawler.crawl(on_article=on_article)
```

### Output Schema

`ExtractedArticle.to_dict()` trả ra dict khớp trực tiếp với `NewsArticle` schema trong MongoDB, với `process_status: "pending"`sẵn sàng cho Windmill step tiếp theo (LLM extraction):

```python
{
    "original_url": "https://cafef.vn/bai-viet-123.chn",
    "source": {
        "name": "CafeF",
        "domain": "cafef.vn",
        "credibility_score": 0.82
    },
    "content": {
        "headline": "VN-Index tăng 15 điểm phiên chiều",
        "subheadline": "",
        "summary": "Thị trường chứng khoán...",
        "body": "Kết thúc phiên giao dịch chiều 6/4...",  # full text
        "author": "Nguyễn Văn A"
    },
    "timing": {
        "published_at": 1744000200000,
        "updated_at": null,
        "market_session": "market_hours"   # tính theo giờ HOSE (UTC+7)
    },
    "metadata": {
        "process_status": "pending",       # ← LLM step sẽ update thành "completed"
        "processed_at": null,
        "extractor_used": "trafilatura",
        "extraction_success": true
    },
    "created_at": 1744000250000
}
```

### Tổng kết crawl

Sau khi tất cả feeds hoàn thành, `CrawlResult` cung cấp số liệu tổng hợp:

```
Crawl done in 12.4s | feeds 8/8 ok | found=156 skipped=89 extracted=67
```

| Chỉ số | Ý nghĩa |
| --- | --- |
| `feeds_attempted` | Tổng số feed được crawl |
| `feeds_ok` | Số feed fetch thành công (kể cả 304) |
| `articles_found` | Tổng entries từ RSS |
| `articles_skipped` | Bị dedup hoặc lọc theo thời gian |
| `articles_extracted` | Bài được extract thành công, sẵn sàng cho LLM |

---

## Workflow

```
                    ┌──────────────────────────────────────┐
                    │         RSSCrawler.crawl()           │
                    │                                      │
                    │  Khởi tạo: RateLimiter               │
                    │           ETagCache                  │
                    │           httpx.AsyncClient          │
                    └──────────────────┬───────────────────┘
                                       │
                    ┌──────────────────▼───────────────────┐
                    │  [Bước 2] Fetch RSS (Conditional)    │
                    │  If-None-Match / If-Modified-Since   │
                    │  RateLimiter.acquire(domain)         │
                    └──────────────────┬───────────────────┘
                                       │
                         ┌─────────────▼─────────────┐
                         │  [Bước 3] 304 Not Modified?│
                         └──────┬──────────┬──────────┘
                           CÓ   │          │  KHÔNG
                                ▼          ▼
                           bỏ qua    parse XML
                           feed      (feedparser)
                                          │
                    ┌─────────────────────▼───────────────────┐
                    │  [Bước 4] Parse → RawArticle[]          │
                    │  - Convert timestamp → ms epoch         │
                    │  - Lọc bài quá cũ (max_article_age)     │
                    └──────────────────┬──────────────────────┘
                                       │
                    ┌──────────────────▼──────────────────────┐
                    │  [Bước 5] Dedup: is_seen(url)?          │
                    │  callback → MongoDB / Redis / Set       │
                    └──────┬───────────────────┬──────────────┘
                      ĐÃ CÓ│                   │CHƯA CÓ
                           ▼                   ▼
                      skip (+1)         to_fetch list
                                               │
                    ┌──────────────────────────▼──────────────┐
                    │  [Bước 6] Fetch Article HTML            │
                    │  async batch, Semaphore(10)             │
                    │  RateLimiter per domain                 │
                    │  retry × 3 nếu thất bại                │
                    └──────────────────┬──────────────────────┘
                                       │
                    ┌──────────────────▼──────────────────────┐
                    │  [Bước 7] ContentExtractor              │
                    │                                         │
                    │  raw_html → trafilatura                 │
                    │               │ thất bại               │
                    │               ▼                         │
                    │           newspaper3k                   │
                    │               │ thất bại               │
                    │               ▼                         │
                    │           RSS summary                   │
                    │           (extraction_success=False)    │
                    └──────────────────┬──────────────────────┘
                                       │
                    ┌──────────────────▼──────────────────────┐
                    │  [Bước 8] Output                        │
                    │  on_article(ExtractedArticle)           │
                    │    → insert MongoDB ngay lập tức        │
                    │  result.extracted_articles.append()     │
                    │  process_status: "pending"              │
                    │    → sẵn sàng cho LLM extraction step  │
                    └─────────────────────────────────────────┘
```
Cấu trúc pakage
finhouse\\_rss/
├── \\_\\_init\\_\\_.py
├── config.py          # CrawlerConfig, FeedConfig
├── models.py          # RawArticle, ExtractedArticle, CrawlResult
├── crawler.py         # RSSCrawler — orchestrator chính
├── fetcher/
│   └── rss.py         # RSS parser + async HTML fetcher
├── extractor/
│   └── content.py     # trafilatura + newspaper3k fallback
└── utils/
    ├── rate\\_limiter.py # Token bucket rate limiter
    └── etag\\_cache.py   # ETag / Last-Modified cache

windmill\\_scripts/
└── finhouse\\_rss\\_crawl.py   # Self-contained single-file cho Windmill shared script
---

##