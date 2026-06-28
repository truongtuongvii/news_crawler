"""
ETag / Last-Modified cache for conditional HTTP requests.
Windmill-compatible: uses wmill.get_state / wmill.set_state for persistence.
Falls back to in-memory dict nếu chạy ngoài môi trường Windmill.
"""

from typing import Optional

try:
    import wmill
    _WMILL_AVAILABLE = True
except ImportError:
    _WMILL_AVAILABLE = False


class ETagCache:
    """
    Persist ETag và Last-Modified values per URL.

    Khi chạy trên Windmill:
      - Dùng wmill.get_state() / wmill.set_state() để lưu trữ persistent
        giữa các lần trigger của cùng một script/flow path.
      - State key mặc định là "etag_cache", có thể tuỳ chỉnh qua `state_key`.

    Khi chạy ngoài Windmill (local dev / test):
      - Tự động fallback về in-memory dict (không persistent).
    """

    def __init__(self, state_key: str = "etag_cache"):
        self._state_key = state_key
        self._data: dict[str, dict] = {}
        self._use_wmill = _WMILL_AVAILABLE
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_headers(self, url: str) -> dict:
        """Trả về conditional request headers cho URL (có thể rỗng)."""
        entry = self._data.get(url, {})
        headers: dict[str, str] = {}
        if entry.get("etag"):
            headers["If-None-Match"] = entry["etag"]
        if entry.get("last_modified"):
            headers["If-Modified-Since"] = entry["last_modified"]
        return headers

    def update(self, url: str, etag: str = "", last_modified: str = "") -> None:
        """Cập nhật ETag / Last-Modified cho URL và lưu vào state."""
        entry = self._data.get(url, {})
        if etag:
            entry["etag"] = etag
        if last_modified:
            entry["last_modified"] = last_modified
        self._data[url] = entry
        self._save()

    def delete(self, url: str) -> None:
        """Xoá cache entry của một URL cụ thể."""
        if url in self._data:
            del self._data[url]
            self._save()

    def clear(self) -> None:
        """Xoá toàn bộ cache."""
        self._data = {}
        self._save()

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, url: str) -> bool:
        return url in self._data

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._use_wmill:
            return
        try:
            state = wmill.get_state()
            # State là dict toàn bộ script; lấy đúng key của cache này
            if isinstance(state, dict):
                self._data = state.get(self._state_key) or {}
        except Exception:
            # Lần đầu chạy hoặc state chưa tồn tại → bắt đầu với dict rỗng
            self._data = {}

    def _save(self) -> None:
        if not self._use_wmill:
            return
        try:
            # Merge vào state hiện tại để không ghi đè các key khác
            state = {}
            try:
                existing = wmill.get_state()
                if isinstance(existing, dict):
                    state = existing
            except Exception:
                pass
            state[self._state_key] = self._data
            wmill.set_state(state)
        except Exception:
            pass