"""Tests for TokenBucket and RateLimiter.

Uses a FakeClock + patched asyncio.sleep so tests run instantly
and deterministically — no real wall-clock waiting.
"""

import pytest
import asyncio
from unittest.mock import patch

from finhouse_rss.utils.rate_limiter import RateLimiter, TokenBucket


# ---------------------------------------------------------------------------
# Fake clock & patch helpers
# ---------------------------------------------------------------------------

class FakeClock:
    """Monotonic clock that advances only when told to."""

    def __init__(self):
        self._time = 0.0

    def now(self) -> float:
        return self._time

    def advance(self, dt: float):
        self._time += dt


def make_patches(clock: FakeClock):
    """Return (time.monotonic patch, asyncio.sleep patch) for a given clock."""

    async def fake_sleep(dt: float):
        clock.advance(dt)

    return (
        patch("finhouse_rss.utils.rate_limiter.time.monotonic", clock.now),
        patch("finhouse_rss.utils.rate_limiter.asyncio.sleep", fake_sleep),
    )


# ---------------------------------------------------------------------------
# TokenBucket
# ---------------------------------------------------------------------------

class TestTokenBucket:

    @pytest.mark.asyncio
    async def test_acquire_immediate_when_full(self):
        """Bucket starts full — first acquire should not wait."""
        clock = FakeClock()
        p1, p2 = make_patches(clock)

        with p1, p2:
            bucket = TokenBucket(rate=10, capacity=10)
            start = clock.now()
            await bucket.acquire()
            assert clock.now() - start == 0

    @pytest.mark.asyncio
    async def test_acquire_waits_when_empty(self):
        """After exhausting the bucket, next acquire waits exactly 1/rate seconds."""
        clock = FakeClock()
        p1, p2 = make_patches(clock)

        with p1, p2:
            bucket = TokenBucket(rate=1, capacity=1)
            await bucket.acquire()          # drains bucket

            start = clock.now()
            await bucket.acquire()          # must wait 1s to refill
            assert clock.now() - start == pytest.approx(1.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_acquire_multiple_tokens(self):
        """Acquiring N tokens at once waits proportionally."""
        clock = FakeClock()
        p1, p2 = make_patches(clock)

        with p1, p2:
            bucket = TokenBucket(rate=2, capacity=2)
            await bucket.acquire(2)         # drains bucket

            start = clock.now()
            await bucket.acquire(2)         # needs 2 tokens @ 2/s → wait 1s
            assert clock.now() - start == pytest.approx(1.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_partial_refill_sufficient(self):
        """If enough tokens have refilled, acquire should not wait."""
        clock = FakeClock()
        p1, p2 = make_patches(clock)

        with p1, p2:
            bucket = TokenBucket(rate=2, capacity=2)
            await bucket.acquire(2)         # drains bucket

            clock.advance(0.5)              # refills 1 token

            start = clock.now()
            await bucket.acquire(1)         # 1 token available → no wait
            assert clock.now() - start == 0

    @pytest.mark.asyncio
    async def test_tokens_never_exceed_capacity(self):
        """Tokens must be capped at capacity even after a long idle period."""
        clock = FakeClock()
        p1, p2 = make_patches(clock)

        with p1, p2:
            bucket = TokenBucket(rate=1, capacity=3)
            clock.advance(100)              # would produce 100 tokens without cap
            bucket._refill()
            assert bucket._tokens == pytest.approx(3.0)

    @pytest.mark.asyncio
    async def test_default_capacity_is_rate_times_five(self):
        """Default capacity = rate * 5 (allows 5s of burst)."""
        bucket = TokenBucket(rate=4)
        assert bucket.capacity == pytest.approx(20.0)

    @pytest.mark.asyncio
    async def test_explicit_capacity_zero_is_respected(self):
        """capacity=0 must not fall back to rate * 5."""
        bucket = TokenBucket(rate=4, capacity=0)
        assert bucket.capacity == 0

    @pytest.mark.asyncio
    async def test_concurrent_acquires_all_succeed(self):
        """Multiple concurrent acquires should all complete without deadlock."""
        clock = FakeClock()
        p1, p2 = make_patches(clock)

        with p1, p2:
            bucket = TokenBucket(rate=2, capacity=2)

            results = await asyncio.gather(*[bucket.acquire() for _ in range(4)])
            assert results == [None, None, None, None]

    @pytest.mark.asyncio
    async def test_tokens_decremented_correctly(self):
        """Token count must reflect each acquire."""
        clock = FakeClock()
        p1, p2 = make_patches(clock)

        with p1, p2:
            bucket = TokenBucket(rate=10, capacity=5)
            await bucket.acquire(2)
            assert bucket._tokens == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------

class TestRateLimiter:

    @pytest.mark.asyncio
    async def test_acquire_passes_when_both_buckets_have_tokens(self):
        """No wait when neither global nor domain bucket is exhausted."""
        clock = FakeClock()
        p1, p2 = make_patches(clock)

        with p1, p2:
            limiter = RateLimiter(global_rps=10, per_domain_rps=10)
            start = clock.now()
            await limiter.acquire("a.com")
            assert clock.now() - start == 0

    @pytest.mark.asyncio
    async def test_global_bucket_throttles_different_domains(self):
        """Global limit applies even when each request targets a different domain."""
        clock = FakeClock()
        p1, p2 = make_patches(clock)

        with p1, p2:
            # capacity = 1*5 = 5 by default; pin it to 1 for predictability
            limiter = RateLimiter(global_rps=1, per_domain_rps=10)
            limiter._global.capacity = 1
            limiter._global._tokens = 1

            await limiter.acquire("a.com")  # uses the 1 available global token

            start = clock.now()
            await limiter.acquire("b.com")  # different domain, but global is empty
            assert clock.now() - start == pytest.approx(1.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_domain_bucket_throttles_same_domain(self):
        """Per-domain limit fires when the same domain is hit repeatedly."""
        clock = FakeClock()
        p1, p2 = make_patches(clock)

        with p1, p2:
            limiter = RateLimiter(global_rps=10, per_domain_rps=1)
            # Pin domain capacity to 1 token
            limiter._domains["a.com"].capacity = 1  # pre-create bucket
            limiter._domains["a.com"]._tokens = 1

            await limiter.acquire("a.com")  # consumes domain token

            start = clock.now()
            await limiter.acquire("a.com")  # domain empty → wait 1s
            assert clock.now() - start == pytest.approx(1.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_different_domains_are_isolated(self):
        """Exhausting one domain's bucket must not affect another domain."""
        clock = FakeClock()
        p1, p2 = make_patches(clock)

        with p1, p2:
            limiter = RateLimiter(global_rps=10, per_domain_rps=1)
            limiter._domains["a.com"].capacity = 1
            limiter._domains["a.com"]._tokens = 1

            await limiter.acquire("a.com")  # drain a.com

            start = clock.now()
            await limiter.acquire("b.com")  # b.com bucket is untouched
            assert clock.now() - start == 0

    @pytest.mark.asyncio
    async def test_set_domain_rate_overrides_default(self):
        """set_domain_rate should allow higher (or lower) rps for a domain."""
        clock = FakeClock()
        p1, p2 = make_patches(clock)

        with p1, p2:
            limiter = RateLimiter(global_rps=10, per_domain_rps=1)
            limiter.set_domain_rate("fast.com", 100)

            # capacity = 100*5 = 500 → no waiting for 2 quick acquires
            start = clock.now()
            await limiter.acquire("fast.com")
            await limiter.acquire("fast.com")
            assert clock.now() - start == 0

    @pytest.mark.asyncio
    async def test_set_domain_rate_resets_accumulated_tokens(self):
        """Replacing a bucket via set_domain_rate starts fresh at full capacity."""
        clock = FakeClock()
        p1, p2 = make_patches(clock)

        with p1, p2:
            limiter = RateLimiter(global_rps=10, per_domain_rps=10)
            # Drain old bucket
            limiter._domains["a.com"]._tokens = 0

            limiter.set_domain_rate("a.com", 2)  # new bucket starts full (capacity=10)
            assert limiter._domains["a.com"]._tokens == pytest.approx(10.0)

    @pytest.mark.asyncio
    async def test_concurrent_acquire_wait_is_max_not_sum(self):
        """Because gather is used, total wait = max(global, domain), not their sum."""
        clock = FakeClock()
        p1, p2 = make_patches(clock)

        with p1, p2:
            limiter = RateLimiter(global_rps=1, per_domain_rps=2)
            # Force both buckets empty
            limiter._global.capacity = 1
            limiter._global._tokens = 0
            limiter._domains["a.com"].capacity = 1
            limiter._domains["a.com"]._tokens = 0

            start = clock.now()
            await limiter.acquire("a.com")
            elapsed = clock.now() - start

            # global needs 1s, domain needs 0.5s → should wait ~1s (the max)
            assert elapsed == pytest.approx(1.0, abs=0.05)

    @pytest.mark.asyncio
    async def test_multiple_domains_created_automatically(self):
        """Accessing a new domain should auto-create its bucket via defaultdict."""
        clock = FakeClock()
        p1, p2 = make_patches(clock)

        with p1, p2:
            limiter = RateLimiter(global_rps=10, per_domain_rps=2)
            for domain in ["x.com", "y.com", "z.com"]:
                await limiter.acquire(domain)
            assert len(limiter._domains) == 3