"""
VnExpress RSS feed URLs cho các chuyên mục tài chính.
Dùng làm config trung tâm cho thư viện crawl tin tức tài chính.
"""

# -----------------------------------------------------------------------
# VnExpress RSS Feed URLs - Chuyên mục Kinh doanh / Tài chính
# Cấu trúc: https://vnexpress.net/rss/<chuyên-mục>.rss
# -----------------------------------------------------------------------

VNEXPRESS_FEEDS: dict[str, str] = {
    # Tổng hợp kinh doanh
    "kinh_doanh":       "https://vnexpress.net/rss/kinh-doanh.rss",

    # Các chuyên mục tài chính cụ thể
    "tai_chinh":        "https://vnexpress.net/rss/kinh-doanh/tai-chinh.rss",
    "chung_khoan":      "https://vnexpress.net/rss/kinh-doanh/chung-khoan.rss",
    "bat_dong_san":     "https://vnexpress.net/rss/kinh-doanh/bat-dong-san.rss",
    "quoc_te":          "https://vnexpress.net/rss/kinh-doanh/quoc-te.rss",
    "doanh_nghiep":     "https://vnexpress.net/rss/kinh-doanh/doanh-nghiep.rss",
    "vi_mo":            "https://vnexpress.net/rss/kinh-doanh/vi-mo.rss",
    "hang_hoa":         "https://vnexpress.net/rss/kinh-doanh/hang-hoa.rss",
    "e_commerce":       "https://vnexpress.net/rss/kinh-doanh/thuong-mai-dien-tu.rss",
}

# Feed mặc định khi không chỉ định cụ thể
DEFAULT_FEED = VNEXPRESS_FEEDS["kinh_doanh"]

# Tất cả feed tài chính (dùng để crawl toàn bộ)
ALL_FINANCIAL_FEEDS: list[str] = list(VNEXPRESS_FEEDS.values())


# -----------------------------------------------------------------------
# Sử dụng với ETagCache
# -----------------------------------------------------------------------
# from etag_cache import ETagCache
#
# cache = ETagCache(state_key="vnexpress_etags")
#
# for name, url in VNEXPRESS_FEEDS.items():
#     headers = cache.get_headers(url)
#     response = httpx.get(url, headers=headers)
#
#     if response.status_code == 200:
#         cache.update(
#             url,
#             etag=response.headers.get("ETag", ""),
#             last_modified=response.headers.get("Last-Modified", ""),
#         )
#         # xử lý feed...
#     elif response.status_code == 304:
#         pass  # không có tin mới