# Setting up News Crawler on Windmill.dev

Follow these steps to deploy the crawler as a scheduled task on Windmill.

## 1. Create the Script
1. Log in to your Windmill instance.
2. Click **New** -> **Script**.
3. Choose **Python** as the language.
4. Set the path (e.g., `admin/news_crawler`).

## 2. Copy the Code
Copy the content of `windmill_scripts/finhouse_rss_crawler.py` and paste it into the editor.

## 3. Configuration
The script uses Windmill's parameters. You can set them in the UI:
- `feed_urls`: List of RSS URLs (e.g., `["https://vnexpress.net/rss/kinh-doanh.rss"]`)
- `elasticsearch_host`: Your ES URL (e.g., `http://localhost:9200/`)
- `elasticsearch_index`: Default is `news_articles`.

## 4. Environment Variables (Optional)
If you don't want to pass the ES host as a parameter, you can set it in Windmill's **Variables**:
1. Go to **Resources** -> **Variables**.
2. Add `ELASTICSEARCH_HOST`.
3. Add `ELASTICSEARCH_INDEX`.

## 5. Deployment & Scheduling
1. Click **Deploy** at the top right.
2. To automate, go to **Schedules** -> **New Schedule**.
3. Select your script and set a CRON expression (e.g., `0 */1 * * *` for every hour).

Make sure your ES open before running the script.