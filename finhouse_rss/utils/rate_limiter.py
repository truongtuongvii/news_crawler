"""Token-bucket rate limiter — per-domain and global."""

import asyncio
import time
from collections import defaultdict


class TokenBucket:
    """Async token bucket for rate limiting.

    Refills at `rate` tokens/sec up to `capacity`.
    Callers suspend (not block) when the bucket is empty.
    """

    def __init__(self, rate: float, capacity: float = None):
        """
        Args:
            rate: Refill speed in tokens/sec (= sustained req/sec).
            capacity: Max stored tokens. Defaults to rate * 5 (5s burst).
        """
        self.rate = rate
        self.capacity = capacity if capacity is not None else rate * 5
        self._tokens = self.capacity
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()  # guards _tokens read/write

    async def acquire(self, tokens: float = 1.0):
        """Suspend until `tokens` are available, then consume them.

        Lock is released before sleeping so other coroutines
        can still make progress concurrently.
        """
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                wait = (tokens - self._tokens) / self.rate
            await asyncio.sleep(wait)  # sleep outside the lock

    def _refill(self):
        """Add tokens earned since the last refill. Must be called under lock."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last_refill = now


class RateLimiter:
    """Two-tier rate limiter: global throughput cap + per-domain cap.

    Both buckets are acquired concurrently, so total wait =
    max(global_wait, domain_wait) instead of their sum.
    """

    def __init__(self, global_rps: float = 5.0, per_domain_rps: float = 2.0):
        """
        Args:
            global_rps: Total req/sec across all domains. Default 5.0.
            per_domain_rps: Req/sec per individual domain. Default 2.0.
        """
        self._global = TokenBucket(rate=global_rps)
        self._domains: dict[str, TokenBucket] = defaultdict(
            lambda: TokenBucket(rate=per_domain_rps)
        )
        self._per_domain_rps = per_domain_rps

    async def acquire(self, domain: str):
        """Block until both the global and domain buckets grant a token.

        Args:
            domain: Target domain, e.g. "vnexpress.net".
        """
        await asyncio.gather(
            self._global.acquire(),
            self._domains[domain].acquire()
        )

    def set_domain_rate(self, domain: str, rps: float):
        """Override the rate limit for a specific domain.

        Replaces the existing bucket — any accumulated tokens are lost.

        Args:
            domain: Target domain to override.
            rps: New rate in req/sec.
        """
        self._domains[domain] = TokenBucket(rate=rps)