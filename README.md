# News Crawler (Finhouse RSS Crawler)

A high-performance, asynchronous RSS news crawler designed to run as a **Windmill script** or a standalone Python script. It extracts full article content using multiple extractors and stores them in **Elasticsearch** for further processing.

## 🚀 Features

- **Asynchronous Crawling**: Uses `asyncio` and `httpx` for high-concurrency fetching.
- **Smart Extraction**: Multiple fallback extractors (`trafilatura`, `newspaper3k`, or RSS summary).
- **Deduplication**: Uses MD5 hashing of URLs to avoid duplicate entries in Elasticsearch.
- **Rate Limiting**: Per-domain and global rate limiting to avoid getting blocked.
- **Windmill Optimized**: Fully compatible with Windmill.dev platform with automatic dependency management.
- **Elasticsearch Integration**: Robust indexing with automatic index creation.

## 🛠️ Project Structure

```text
news-crawler/
├── windmill_scripts/
│   └── finhouse_rss_crawler.py  # Main crawler script (Windmill & Local)
├── scripts/
│   └── view_elastic_articles.py # Utility to check data in Elasticsearch
├── requirements.txt             # Python dependencies
├── .env.example                 # Environment variable template
└── README.md                    # This documentation
```

## 📋 Prerequisites

- **Python 3.11+**
- **Elasticsearch 8.x/9.x** (Local or Cloud/Codespaces)
- (Optional) **Windmill instance** for scheduled runs.

## ⚙️ Setup & Installation

### 1. Clone the repository
```bash
git clone <your-repo-url>
cd news-crawler
```

### 2. Install dependencies
It is recommended to use a virtual environment:
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Create a `.env` file from the example:
```bash
cp .env.example .env
```
Edit `.env` and set your Elasticsearch host:
```env
ELASTICSEARCH_HOST=http://localhost:9200
ELASTICSEARCH_INDEX=news_articles
```

## 🚀 Usage

### Running Locally
To run a test crawl from your terminal:
```bash
python windmill_scripts/finhouse_rss_crawler.py
```

### Running on Windmill
1. Create a new **Python Script** in Windmill.
2. Paste the contents of `windmill_scripts/finhouse_rss_crawler.py`.
3. Ensure the first line includes the requirements:
   ```python
   # wmill: pip aiohttp elasticsearch feedparser httpx beautifulsoup4 python-dotenv trafilatura newspaper3k
   ```
4. Configure the variables (feed_urls, elasticsearch_host, etc.) in the Windmill UI.
5. **Deploy** and **Schedule** as needed.

### Checking Data
Use the utility script to verify articles are being ingested:
```bash
python scripts/view_elastic_articles.py
```

## ⚠️ Common Issues & Troubleshooting

### 1. ValueError: You must have 'aiohttp' installed
This occurs because the `elasticsearch` library's async client requires `aiohttp`.
- **Solution**: Ensure `aiohttp` is in your `requirements.txt` or listed in the `# wmill: pip` line at the top of your Windmill script.

### 2. Connection Refused (Elasticsearch)
- **Local**: Check if Elasticsearch is running (`curl http://localhost:9200`).
- **Codespaces**: Ensure the port **9200** is set to **Public** in the Ports tab.

### 3. AuthenticationException (401)
Usually caused by a private GitHub Codespace tunnel.
- **Solution**: Change the Port Visibility to **Public** in GitHub Codespaces.

## 📝 License
MIT