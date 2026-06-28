"""
Tests for ETagCache (Windmill-compatible version).
Tất cả URL dùng feed thực của VnExpress — không gọi HTTP, toàn bộ là mock.

Chạy: pytest test_etag_cache.py -v -s
"""

import pytest
from unittest.mock import patch, MagicMock
from etag_cache import ETagCache
from vnexpress_feeds import VNEXPRESS_FEEDS, ALL_FINANCIAL_FEEDS, DEFAULT_FEED

# Shorthand tiện dùng trong test
URL_KINH_DOANH   = VNEXPRESS_FEEDS["kinh_doanh"]
URL_TAI_CHINH    = VNEXPRESS_FEEDS["tai_chinh"]
URL_CHUNG_KHOAN  = VNEXPRESS_FEEDS["chung_khoan"]
URL_BAT_DONG_SAN = VNEXPRESS_FEEDS["bat_dong_san"]
URL_QUOC_TE      = VNEXPRESS_FEEDS["quoc_te"]
URL_VI_MO        = VNEXPRESS_FEEDS["vi_mo"]


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

def make_cache(initial_state: dict = None, state_key: str = "etag_cache") -> ETagCache:
    state_key_data = initial_state if initial_state is not None else {}
    windmill_state = {state_key: state_key_data}
    mock_wmill = MagicMock()
    mock_wmill.get_state.return_value = windmill_state
    with patch.dict("sys.modules", {"wmill": mock_wmill}):
        import importlib
        import etag_cache as module
        importlib.reload(module)
        cache = module.ETagCache(state_key=state_key)
        cache._mock_wmill = mock_wmill
    return cache


# ---------------------------------------------------------------------------
# 1. Khởi tạo & load state
# ---------------------------------------------------------------------------

class TestInit:
    def test_load_existing_state(self):
        print(f"\n  [input]  URL: {URL_KINH_DOANH}")
        initial = {URL_KINH_DOANH: {"etag": '"abc123"', "last_modified": "Wed, 01 Jan 2025 00:00:00 GMT"}}
        cache = make_cache(initial)
        print(f"  [result] cache size: {len(cache)}, URL in cache: {URL_KINH_DOANH in cache}")
        assert URL_KINH_DOANH in cache
        assert len(cache) == 1

    def test_empty_state_on_first_run(self):
        print(f"\n  [input]  state: {{}}")
        cache = make_cache({})
        print(f"  [result] cache size: {len(cache)}")
        assert len(cache) == 0

    def test_handles_none_state_from_windmill(self):
        print(f"\n  [input]  get_state() trả về None")
        mock_wmill = MagicMock()
        mock_wmill.get_state.return_value = None
        with patch.dict("sys.modules", {"wmill": mock_wmill}):
            import importlib
            import etag_cache as module
            importlib.reload(module)
            cache = module.ETagCache()
        print(f"  [result] cache size: {len(cache)}")
        assert len(cache) == 0

    def test_handles_get_state_exception(self):
        print(f"\n  [input]  get_state() ném Exception('state not found')")
        mock_wmill = MagicMock()
        mock_wmill.get_state.side_effect = Exception("state not found")
        with patch.dict("sys.modules", {"wmill": mock_wmill}):
            import importlib
            import etag_cache as module
            importlib.reload(module)
            cache = module.ETagCache()
        print(f"  [result] cache size: {len(cache)} (khởi tạo rỗng, không crash)")
        assert len(cache) == 0

    def test_custom_state_key(self):
        print(f"\n  [input]  state_key='vnexpress_etags', URL: {URL_TAI_CHINH}")
        initial = {"vnexpress_etags": {URL_TAI_CHINH: {"etag": '"xyz"'}}}
        mock_wmill = MagicMock()
        mock_wmill.get_state.return_value = initial
        with patch.dict("sys.modules", {"wmill": mock_wmill}):
            import importlib
            import etag_cache as module
            importlib.reload(module)
            cache = module.ETagCache(state_key="vnexpress_etags")
        print(f"  [result] URL in cache: {URL_TAI_CHINH in cache}")
        assert URL_TAI_CHINH in cache


# ---------------------------------------------------------------------------
# 2. get_headers()
# ---------------------------------------------------------------------------

class TestGetHeaders:
    def test_returns_empty_for_unknown_url(self):
        print(f"\n  [input]  URL chưa có trong cache: {URL_KINH_DOANH}")
        cache = make_cache()
        headers = cache.get_headers(URL_KINH_DOANH)
        print(f"  [result] headers: {headers}")
        assert headers == {}

    def test_returns_if_none_match_when_etag_present(self):
        etag = '"etag-vne-001"'
        print(f"\n  [input]  URL: {URL_CHUNG_KHOAN}, etag: {etag}")
        cache = make_cache({URL_CHUNG_KHOAN: {"etag": etag}})
        headers = cache.get_headers(URL_CHUNG_KHOAN)
        print(f"  [result] headers: {headers}")
        assert headers == {"If-None-Match": etag}

    def test_returns_if_modified_since_when_last_modified_present(self):
        lm = "Fri, 11 Apr 2026 07:00:00 GMT"
        print(f"\n  [input]  URL: {URL_TAI_CHINH}, last_modified: {lm}")
        cache = make_cache({URL_TAI_CHINH: {"last_modified": lm}})
        headers = cache.get_headers(URL_TAI_CHINH)
        print(f"  [result] headers: {headers}")
        assert headers == {"If-Modified-Since": lm}

    def test_returns_both_headers_when_both_present(self):
        etag = '"etag-bds"'
        lm = "Mon, 01 Jan 2024 00:00:00 GMT"
        print(f"\n  [input]  URL: {URL_BAT_DONG_SAN}, etag: {etag}, last_modified: {lm}")
        cache = make_cache({URL_BAT_DONG_SAN: {"etag": etag, "last_modified": lm}})
        headers = cache.get_headers(URL_BAT_DONG_SAN)
        print(f"  [result] headers: {headers}")
        assert "If-None-Match" in headers
        assert "If-Modified-Since" in headers

    def test_ignores_empty_string_etag(self):
        print(f"\n  [input]  URL: {URL_QUOC_TE}, etag: '', last_modified: ''")
        cache = make_cache({URL_QUOC_TE: {"etag": "", "last_modified": ""}})
        headers = cache.get_headers(URL_QUOC_TE)
        print(f"  [result] headers: {headers} (phải rỗng)")
        assert headers == {}


# ---------------------------------------------------------------------------
# 3. update()
# ---------------------------------------------------------------------------

class TestUpdate:
    def test_update_etag_only(self):
        etag = '"etag-ck-new"'
        print(f"\n  [input]  URL: {URL_CHUNG_KHOAN}, etag: {etag}")
        cache = make_cache()
        cache.update(URL_CHUNG_KHOAN, etag=etag)
        result = cache.get_headers(URL_CHUNG_KHOAN)["If-None-Match"]
        print(f"  [result] If-None-Match: {result}")
        assert result == etag

    def test_update_last_modified_only(self):
        lm = "Sat, 11 Apr 2026 00:00:00 GMT"
        print(f"\n  [input]  URL: {URL_TAI_CHINH}, last_modified: {lm}")
        cache = make_cache()
        cache.update(URL_TAI_CHINH, last_modified=lm)
        result = cache.get_headers(URL_TAI_CHINH)["If-Modified-Since"]
        print(f"  [result] If-Modified-Since: {result}")
        assert result == lm

    def test_update_both(self):
        etag, lm = '"etag-bds"', "Fri, 01 Jan 2021 00:00:00 GMT"
        print(f"\n  [input]  URL: {URL_BAT_DONG_SAN}, etag: {etag}, last_modified: {lm}")
        cache = make_cache()
        cache.update(URL_BAT_DONG_SAN, etag=etag, last_modified=lm)
        headers = cache.get_headers(URL_BAT_DONG_SAN)
        print(f"  [result] headers: {headers}")
        assert "If-None-Match" in headers
        assert "If-Modified-Since" in headers

    def test_update_overwrites_existing_etag(self):
        print(f"\n  [input]  URL: {URL_KINH_DOANH}, etag cũ: '\"etag-old\"' → etag mới: '\"etag-new\"'")
        cache = make_cache({URL_KINH_DOANH: {"etag": '"etag-old"'}})
        cache.update(URL_KINH_DOANH, etag='"etag-new"')
        result = cache.get_headers(URL_KINH_DOANH)["If-None-Match"]
        print(f"  [result] If-None-Match: {result}")
        assert result == '"etag-new"'

    def test_update_preserves_existing_last_modified_when_only_etag_given(self):
        lm = "Mon, 01 Jan 2024 00:00:00 GMT"
        print(f"\n  [input]  URL: {URL_VI_MO}, chỉ update etag mới, last_modified giữ nguyên: {lm}")
        cache = make_cache({URL_VI_MO: {"etag": '"etag-old"', "last_modified": lm}})
        cache.update(URL_VI_MO, etag='"etag-new"')
        headers = cache.get_headers(URL_VI_MO)
        print(f"  [result] headers: {headers}")
        assert headers["If-None-Match"] == '"etag-new"'
        assert "If-Modified-Since" in headers

    def test_update_calls_set_state(self):
        print(f"\n  [input]  URL: {URL_CHUNG_KHOAN}, update etag → kiểm tra set_state() được gọi")
        cache = make_cache()
        cache.update(URL_CHUNG_KHOAN, etag='"etag-ck"')
        called = cache._mock_wmill.set_state.called
        print(f"  [result] set_state called: {called}")
        assert called

    def test_update_multiple_feeds(self):
        print(f"\n  [input]  update 2 feed: tai_chinh + chung_khoan")
        cache = make_cache()
        cache.update(URL_TAI_CHINH, etag='"e-tc"')
        cache.update(URL_CHUNG_KHOAN, etag='"e-ck"')
        print(f"  [result] cache size: {len(cache)}, feeds: {[URL_TAI_CHINH in cache, URL_CHUNG_KHOAN in cache]}")
        assert len(cache) == 2
        assert URL_TAI_CHINH in cache
        assert URL_CHUNG_KHOAN in cache


# ---------------------------------------------------------------------------
# 4. delete()
# ---------------------------------------------------------------------------

class TestDelete:
    def test_delete_existing_url(self):
        print(f"\n  [input]  xoá URL: {URL_BAT_DONG_SAN}")
        cache = make_cache({URL_BAT_DONG_SAN: {"etag": '"etag-bds"'}})
        cache.delete(URL_BAT_DONG_SAN)
        print(f"  [result] URL in cache sau delete: {URL_BAT_DONG_SAN in cache}")
        assert URL_BAT_DONG_SAN not in cache

    def test_delete_nonexistent_url_does_not_raise(self):
        print(f"\n  [input]  xoá URL chưa có trong cache: {URL_QUOC_TE}")
        cache = make_cache()
        cache.delete(URL_QUOC_TE)
        print(f"  [result] không raise exception, cache size: {len(cache)}")

    def test_delete_calls_set_state(self):
        print(f"\n  [input]  xoá URL: {URL_TAI_CHINH} → kiểm tra set_state() được gọi")
        cache = make_cache({URL_TAI_CHINH: {"etag": '"etag-tc"'}})
        cache._mock_wmill.set_state.reset_mock()
        cache.delete(URL_TAI_CHINH)
        called = cache._mock_wmill.set_state.call_count
        print(f"  [result] set_state call count: {called}")
        assert called == 1

    def test_delete_one_feed_keeps_others(self):
        print(f"\n  [input]  xoá tai_chinh, giữ chung_khoan + bat_dong_san")
        cache = make_cache({
            URL_TAI_CHINH:    {"etag": '"e-tc"'},
            URL_CHUNG_KHOAN:  {"etag": '"e-ck"'},
            URL_BAT_DONG_SAN: {"etag": '"e-bds"'},
        })
        cache.delete(URL_TAI_CHINH)
        print(f"  [result] tai_chinh in cache: {URL_TAI_CHINH in cache}")
        print(f"  [result] chung_khoan in cache: {URL_CHUNG_KHOAN in cache}")
        print(f"  [result] bat_dong_san in cache: {URL_BAT_DONG_SAN in cache}")
        assert URL_TAI_CHINH not in cache
        assert URL_CHUNG_KHOAN in cache
        assert URL_BAT_DONG_SAN in cache


# ---------------------------------------------------------------------------
# 5. clear()
# ---------------------------------------------------------------------------

class TestClear:
    def test_clear_removes_all_feeds(self):
        print(f"\n  [input]  cache có 2 feed: tai_chinh + chung_khoan")
        cache = make_cache({URL_TAI_CHINH: {"etag": '"e-tc"'}, URL_CHUNG_KHOAN: {"etag": '"e-ck"'}})
        cache.clear()
        print(f"  [result] cache size sau clear: {len(cache)}")
        assert len(cache) == 0

    def test_clear_calls_set_state(self):
        print(f"\n  [input]  clear() → kiểm tra set_state() được gọi đúng 1 lần")
        cache = make_cache({URL_KINH_DOANH: {"etag": '"e-kd"'}})
        cache._mock_wmill.set_state.reset_mock()
        cache.clear()
        called = cache._mock_wmill.set_state.call_count
        print(f"  [result] set_state call count: {called}")
        assert called == 1

    def test_clear_on_empty_cache(self):
        print(f"\n  [input]  clear() trên cache rỗng")
        cache = make_cache()
        cache.clear()
        print(f"  [result] cache size: {len(cache)}, không raise exception")
        assert len(cache) == 0


# ---------------------------------------------------------------------------
# 6. __len__ và __contains__
# ---------------------------------------------------------------------------

class TestDunderMethods:
    def test_len_empty(self):
        print(f"\n  [input]  cache rỗng")
        cache = make_cache()
        print(f"  [result] len(cache): {len(cache)}")
        assert len(cache) == 0

    def test_len_with_entries(self):
        print(f"\n  [input]  cache có 2 feed: tai_chinh + chung_khoan")
        cache = make_cache({URL_TAI_CHINH: {"etag": '"e-tc"'}, URL_CHUNG_KHOAN: {"etag": '"e-ck"'}})
        print(f"  [result] len(cache): {len(cache)}")
        assert len(cache) == 2

    def test_contains_true(self):
        print(f"\n  [input]  kiểm tra URL có trong cache: {URL_BAT_DONG_SAN}")
        cache = make_cache({URL_BAT_DONG_SAN: {"etag": '"e-bds"'}})
        print(f"  [result] URL in cache: {URL_BAT_DONG_SAN in cache}")
        assert URL_BAT_DONG_SAN in cache

    def test_contains_false(self):
        print(f"\n  [input]  kiểm tra URL không có trong cache: {URL_CHUNG_KHOAN}")
        cache = make_cache()
        print(f"  [result] URL in cache: {URL_CHUNG_KHOAN in cache}")
        assert URL_CHUNG_KHOAN not in cache


# ---------------------------------------------------------------------------
# 7. Windmill state namespace isolation
# ---------------------------------------------------------------------------

class TestStateNamespace:
    def test_does_not_overwrite_other_state_keys(self):
        print(f"\n  [input]  state có 2 key: 'etag_cache' và 'other_key'")
        mock_wmill = MagicMock()
        mock_wmill.get_state.return_value = {
            "etag_cache": {},
            "other_key": {"important": True},
        }
        with patch.dict("sys.modules", {"wmill": mock_wmill}):
            import importlib
            import etag_cache as module
            importlib.reload(module)
            cache = module.ETagCache()
        cache.update(URL_KINH_DOANH, etag='"etag-kd"')
        saved_state = mock_wmill.set_state.call_args[0][0]
        print(f"  [result] 'other_key' còn trong saved_state: {'other_key' in saved_state}")
        print(f"  [result] saved_state['other_key']: {saved_state.get('other_key')}")
        assert "other_key" in saved_state
        assert saved_state["other_key"] == {"important": True}

    def test_two_caches_with_different_keys_are_isolated(self):
        print(f"\n  [input]  2 cache với key khác nhau: 'cache_tai_chinh' và 'cache_chung_khoan'")
        mock_wmill = MagicMock()
        mock_wmill.get_state.return_value = {
            "cache_tai_chinh":   {URL_TAI_CHINH:   {"etag": '"e-tc"'}},
            "cache_chung_khoan": {URL_CHUNG_KHOAN: {"etag": '"e-ck"'}},
        }
        with patch.dict("sys.modules", {"wmill": mock_wmill}):
            import importlib
            import etag_cache as module
            importlib.reload(module)
            cache_tc = module.ETagCache(state_key="cache_tai_chinh")
            cache_ck = module.ETagCache(state_key="cache_chung_khoan")
        print(f"  [result] tai_chinh in cache_tc: {URL_TAI_CHINH in cache_tc}")
        print(f"  [result] tai_chinh in cache_ck: {URL_TAI_CHINH in cache_ck} (phải False)")
        print(f"  [result] chung_khoan in cache_ck: {URL_CHUNG_KHOAN in cache_ck}")
        print(f"  [result] chung_khoan in cache_tc: {URL_CHUNG_KHOAN in cache_tc} (phải False)")
        assert URL_TAI_CHINH in cache_tc
        assert URL_TAI_CHINH not in cache_ck
        assert URL_CHUNG_KHOAN in cache_ck
        assert URL_CHUNG_KHOAN not in cache_tc

    def test_vnexpress_state_key_does_not_affect_other_sources(self):
        print(f"\n  [input]  state_key='vnexpress_etags', có thêm 'other_source_etags'")
        mock_wmill = MagicMock()
        mock_wmill.get_state.return_value = {
            "vnexpress_etags": {URL_QUOC_TE: {"etag": '"vne"'}},
            "other_source_etags": {"https://other-source.com/rss": {"etag": '"other"'}},
        }
        with patch.dict("sys.modules", {"wmill": mock_wmill}):
            import importlib
            import etag_cache as module
            importlib.reload(module)
            cache = module.ETagCache(state_key="vnexpress_etags")
        print(f"  [result] quoc_te in cache: {URL_QUOC_TE in cache}")
        print(f"  [result] other-source in cache: {'https://other-source.com/rss' in cache} (phải False)")
        assert URL_QUOC_TE in cache
        assert "https://other-source.com/rss" not in cache
        cache.update(URL_QUOC_TE, etag='"vne-updated"')
        saved = mock_wmill.set_state.call_args[0][0]
        print(f"  [result] 'other_source_etags' còn trong saved state: {'other_source_etags' in saved}")
        assert "other_source_etags" in saved


# ---------------------------------------------------------------------------
# 8. Fallback khi wmill không có
# ---------------------------------------------------------------------------

class TestLocalFallback:
    def test_in_memory_when_wmill_not_available(self):
        print(f"\n  [input]  wmill=None (môi trường local), URL: {URL_KINH_DOANH}")
        with patch.dict("sys.modules", {"wmill": None}):
            import importlib
            import etag_cache as module
            importlib.reload(module)
            cache = module.ETagCache()
        cache.update(URL_KINH_DOANH, etag='"local-etag"')
        result = cache.get_headers(URL_KINH_DOANH).get("If-None-Match")
        print(f"  [result] If-None-Match: {result}")
        assert URL_KINH_DOANH in cache
        assert result == '"local-etag"'

    def test_no_persistence_between_instances_without_wmill(self):
        print(f"\n  [input]  2 instance riêng biệt khi wmill=None")
        with patch.dict("sys.modules", {"wmill": None}):
            import importlib
            import etag_cache as module
            importlib.reload(module)
            c1 = module.ETagCache()
            c1.update(URL_CHUNG_KHOAN, etag='"etag-ck"')
            c2 = module.ETagCache()
        print(f"  [result] URL in c1: {URL_CHUNG_KHOAN in c1}")
        print(f"  [result] URL in c2: {URL_CHUNG_KHOAN in c2} (phải False, không share state)")
        assert URL_CHUNG_KHOAN not in c2


# ---------------------------------------------------------------------------
# 9. Resiliency
# ---------------------------------------------------------------------------

class TestResiliency:
    def test_set_state_exception_does_not_crash(self):
        print(f"\n  [input]  set_state() ném Exception('network error')")
        mock_wmill = MagicMock()
        mock_wmill.get_state.return_value = {"etag_cache": {}}
        mock_wmill.set_state.side_effect = Exception("network error")
        with patch.dict("sys.modules", {"wmill": mock_wmill}):
            import importlib
            import etag_cache as module
            importlib.reload(module)
            cache = module.ETagCache()
        cache.update(URL_TAI_CHINH, etag='"etag-tc"')
        print(f"  [result] không crash, URL in cache (in-memory): {URL_TAI_CHINH in cache}")
        assert URL_TAI_CHINH in cache


# ---------------------------------------------------------------------------
# 10. VnExpress feeds — cấu trúc config & vòng đời crawl
# ---------------------------------------------------------------------------

class TestVnExpressFeeds:

    def test_all_feed_urls_start_with_vnexpress(self):
        print(f"\n  [input]  kiểm tra tất cả URL bắt đầu bằng https://vnexpress.net/")
        for name, url in VNEXPRESS_FEEDS.items():
            print(f"    {name}: {url}")
            assert url.startswith("https://vnexpress.net/"), f"Feed '{name}' URL không hợp lệ: {url}"
        print(f"  [result] tất cả {len(VNEXPRESS_FEEDS)} URL hợp lệ")

    def test_all_feed_urls_end_with_rss(self):
        print(f"\n  [input]  kiểm tra tất cả URL kết thúc bằng .rss")
        for name, url in VNEXPRESS_FEEDS.items():
            assert url.endswith(".rss"), f"Feed '{name}' không kết thúc .rss: {url}"
        print(f"  [result] tất cả {len(VNEXPRESS_FEEDS)} URL kết thúc .rss")

    def test_no_duplicate_urls(self):
        urls = list(VNEXPRESS_FEEDS.values())
        print(f"\n  [input]  {len(urls)} URL, kiểm tra không trùng lặp")
        print(f"  [result] unique URLs: {len(set(urls))}")
        assert len(urls) == len(set(urls)), "Có URL bị trùng trong VNEXPRESS_FEEDS"

    def test_all_financial_feeds_matches_vnexpress_feeds_values(self):
        print(f"\n  [input]  ALL_FINANCIAL_FEEDS ({len(ALL_FINANCIAL_FEEDS)}) phải khớp VNEXPRESS_FEEDS.values()")
        diff = set(ALL_FINANCIAL_FEEDS).symmetric_difference(set(VNEXPRESS_FEEDS.values()))
        print(f"  [result] diff: {diff if diff else 'không có (khớp hoàn toàn)'}")
        assert set(ALL_FINANCIAL_FEEDS) == set(VNEXPRESS_FEEDS.values())

    def test_default_feed_is_in_feed_list(self):
        print(f"\n  [input]  DEFAULT_FEED: {DEFAULT_FEED}")
        print(f"  [result] in ALL_FINANCIAL_FEEDS: {DEFAULT_FEED in ALL_FINANCIAL_FEEDS}")
        assert DEFAULT_FEED in ALL_FINANCIAL_FEEDS

    def test_required_financial_categories_present(self):
        required = {"kinh_doanh", "tai_chinh", "chung_khoan", "bat_dong_san"}
        present = required.intersection(VNEXPRESS_FEEDS.keys())
        missing = required - set(VNEXPRESS_FEEDS.keys())
        print(f"\n  [input]  required: {required}")
        print(f"  [result] present: {present}")
        print(f"  [result] missing: {missing if missing else 'không có'}")
        assert required.issubset(VNEXPRESS_FEEDS.keys())

    def test_200_response_updates_etag_in_cache(self):
        etag = '"etag-from-server"'
        lm = "Sat, 11 Apr 2026 00:00:00 GMT"
        print(f"\n  [input]  simulate HTTP 200, URL: {URL_KINH_DOANH}")
        print(f"           etag: {etag}, last_modified: {lm}")
        cache = make_cache()
        cache.update(URL_KINH_DOANH, etag=etag, last_modified=lm)
        headers = cache.get_headers(URL_KINH_DOANH)
        print(f"  [result] headers: {headers}")
        assert headers["If-None-Match"] == etag
        assert headers["If-Modified-Since"] == lm

    def test_304_response_preserves_existing_etag(self):
        original = '"etag-original"'
        print(f"\n  [input]  simulate HTTP 304, URL: {URL_CHUNG_KHOAN}, etag hiện tại: {original}")
        cache = make_cache({URL_CHUNG_KHOAN: {"etag": original}})
        result = cache.get_headers(URL_CHUNG_KHOAN)["If-None-Match"]
        print(f"  [result] If-None-Match (không đổi): {result}")
        assert result == original

    def test_crawl_all_feeds_stores_all_etags(self):
        print(f"\n  [input]  crawl toàn bộ {len(VNEXPRESS_FEEDS)} feed VnExpress")
        cache = make_cache()
        for i, (name, url) in enumerate(VNEXPRESS_FEEDS.items()):
            cache.update(url, etag=f'"etag-{name}-{i}"')
            print(f"    [{i}] {name}: etag saved")
        print(f"  [result] cache size: {len(cache)} / {len(VNEXPRESS_FEEDS)}")
        assert len(cache) == len(VNEXPRESS_FEEDS)
        for name, url in VNEXPRESS_FEEDS.items():
            assert url in cache
            assert f'"etag-{name}-' in cache.get_headers(url)["If-None-Match"]

    def test_update_one_feed_does_not_affect_others(self):
        print(f"\n  [input]  update chung_khoan etag, kiểm tra bat_dong_san không đổi")
        cache = make_cache({
            URL_CHUNG_KHOAN:  {"etag": '"e-ck-old"'},
            URL_BAT_DONG_SAN: {"etag": '"e-bds-old"'},
        })
        cache.update(URL_CHUNG_KHOAN, etag='"e-ck-new"')
        ck  = cache.get_headers(URL_CHUNG_KHOAN)["If-None-Match"]
        bds = cache.get_headers(URL_BAT_DONG_SAN)["If-None-Match"]
        print(f"  [result] chung_khoan etag: {ck}")
        print(f"  [result] bat_dong_san etag: {bds} (phải giữ nguyên)")
        assert ck  == '"e-ck-new"'
        assert bds == '"e-bds-old"'