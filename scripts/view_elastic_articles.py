import asyncio
import json
import os
from elasticsearch import AsyncElasticsearch
from dotenv import load_dotenv

load_dotenv()

async def view_articles(host="http://localhost:9200", index="news_articles"):
    """
    Kết nối tới Elasticsearch và in ra tổng số bài viết cùng 5 bài mới nhất.
    """
    print(f"--- Đang kết nối tới Elasticsearch: {host} ---")
    
    es = AsyncElasticsearch(host)
    
    try:
        if not await es.indices.exists(index=index):
            print(f"Index '{index}' không tồn tại. Đang tiến hành tạo index mới...")
            await es.indices.create(index=index)
            print(f"Đã tạo index '{index}' thành công.")

        # Đếm tổng số bài viết
        count_res = await es.count(index=index)
        total_articles = count_res["count"]
        print(f"Tổng số bài viết trong index '{index}': {total_articles}")
        
        if total_articles > 0:
            print("\n--- 5 bài viết mới nhất ---")
            search_res = await es.search(
                index=index,
                size=5,
                sort=[{"created_at": "desc"}]
            )
            
            for i, hit in enumerate(search_res["hits"]["hits"], 1):
                article = hit["_source"]
                headline = article.get("content", {}).get("headline", "N/A")
                url = article.get("original_url", "N/A")
                source = article.get("source", {}).get("name", "N/A")
                
                print(f"{i}. {headline}")
                print(f"   Nguồn: {source}")
                print(f"   URL: {url}")
                print("-" * 30)
                
    except Exception as e:
        print(f"Lỗi khi kết nối Elasticsearch: {e}")
    finally:
        await es.close()

if __name__ == "__main__":
    es_host = os.getenv("ELASTICSEARCH_HOST", "http://localhost:9200")
    es_index = os.getenv("ELASTICSEARCH_INDEX", "news_articles")
    
    asyncio.run(view_articles(es_host, es_index))
