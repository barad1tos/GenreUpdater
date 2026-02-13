"""Unit tests for AppleScriptRateLimiter."""

from __future__ import annotations

import asyncio
import logging

import pytest

from services.apple.rate_limiter import AppleScriptRateLimiter


class TestAppleScriptRateLimiterInit:
    """Tests for AppleScriptRateLimiter initialization."""

    def test_valid_initialization(self) -> None:
        """Valid parameters should create a rate limiter."""
        limiter = AppleScriptRateLimiter(
            requests_per_window=10,
            window_seconds=1.0,
            max_concurrent=3,
        )
        assert limiter.requests_per_window == 10
        assert limiter.window_seconds == 1.0
        assert limiter.max_concurrent == 3
        assert limiter.semaphore is None  # Not initialized yet
        assert limiter.total_requests == 0
        assert limiter.total_wait_time == 0.0

    def test_custom_logger(self) -> None:
        """Should accept custom logger."""
        custom_logger = logging.getLogger("test_logger")
        limiter = AppleScriptRateLimiter(
            requests_per_window=10,
            window_seconds=1.0,
        )
        assert limiter.logger is custom_logger

    def test_invalid_requests_per_window_zero(self) -> None:
        """Zero requests_per_window should raise ValueError."""
        with pytest.raises(ValueError, match="requests_per_window must be a positive integer"):
            AppleScriptRateLimiter(requests_per_window=0, window_seconds=1.0)

    def test_invalid_requests_per_window_negative(self) -> None:
        """Negative requests_per_window should raise ValueError."""
        with pytest.raises(ValueError, match="requests_per_window must be a positive integer"):
            AppleScriptRateLimiter(requests_per_window=-5, window_seconds=1.0)

    def test_invalid_window_size_zero(self) -> None:
        """Zero window_size should raise ValueError."""
        with pytest.raises(ValueError, match="window_seconds must be a positive number"):
            AppleScriptRateLimiter(requests_per_window=10, window_seconds=0)

    def test_invalid_window_size_negative(self) -> None:
        """Negative window_size should raise ValueError."""
        with pytest.raises(ValueError, match="window_seconds must be a positive number"):
            AppleScriptRateLimiter(requests_per_window=10, window_seconds=-1.0)

    def test_invalid_max_concurrent_zero(self) -> None:
        """Zero max_concurrent should raise ValueError."""
        with pytest.raises(ValueError, match="max_concurrent must be a positive integer"):
            AppleScriptRateLimiter(requests_per_window=10, window_seconds=1.0, max_concurrent=0)

    def test_invalid_max_concurrent_negative(self) -> None:
        """Negative max_concurrent should raise ValueError."""
        with pytest.raises(ValueError, match="max_concurrent must be a positive integer"):
            AppleScriptRateLimiter(requests_per_window=10, window_seconds=1.0, max_concurrent=-1)


class TestAppleScriptRateLimiterAsync:
    """Async tests for AppleScriptRateLimiter."""

    @pytest.mark.asyncio
    async def test_initialize_creates_semaphore(self) -> None:
        """Initialize should create the semaphore."""
        limiter = AppleScriptRateLimiter(
            requests_per_window=10,
            window_seconds=1.0,
        )
        assert limiter.semaphore is None
        await limiter.initialize()
        assert limiter.semaphore is not None
        assert isinstance(limiter.semaphore, asyncio.Semaphore)

    @pytest.mark.asyncio
    async def test_initialize_idempotent(self) -> None:
        """Multiple initialize calls should be safe."""
        limiter = AppleScriptRateLimiter(
            requests_per_window=10,
            window_seconds=1.0,
        )
        await limiter.initialize()
        first_semaphore = limiter.semaphore
        await limiter.initialize()
        # Should still be the same semaphore
        assert limiter.semaphore is first_semaphore

    @pytest.mark.asyncio
    async def test_acquire_without_initialize_raises(self) -> None:
        """Acquire without initialize should raise RuntimeError."""
        limiter = AppleScriptRateLimiter(
            requests_per_window=10,
            window_seconds=1.0,
        )
        with pytest.raises(RuntimeError, match="RateLimiter not initialized"):
            await limiter.acquire()

    @pytest.mark.asyncio
    async def test_acquire_increments_total_requests(self) -> None:
        """Acquire should increment total_requests counter."""
        limiter = AppleScriptRateLimiter(
            requests_per_window=10,
            window_seconds=1.0,
        )
        await limiter.initialize()
        assert limiter.total_requests == 0

        await limiter.acquire()
        limiter.release()
        assert limiter.total_requests == 1

        await limiter.acquire()
        limiter.release()
        assert limiter.total_requests == 2

    @pytest.mark.asyncio
    async def test_release_without_initialize_is_safe(self) -> None:
        """Release without initialize should not raise."""
        limiter = AppleScriptRateLimiter(
            requests_per_window=10,
            window_seconds=1.0,
        )
        # Should not raise
        limiter.release()

    @pytest.mark.asyncio
    async def test_acquire_release_flow(self) -> None:
        """Normal acquire/release flow should work."""
        limiter = AppleScriptRateLimiter(
            requests_per_window=100,
            window_seconds=1.0,
            max_concurrent=2,
        )
        await limiter.initialize()

        # First acquire should succeed immediately
        wait_time = await limiter.acquire()
        assert wait_time == 0.0

        # Release
        limiter.release()

    @pytest.mark.asyncio
    async def test_rate_limiting_kicks_in(self) -> None:
        """Rate limiting should wait when window is full."""
        # Very small window with 2 requests allowed
        limiter = AppleScriptRateLimiter(
            requests_per_window=2,
            window_seconds=0.5,  # 500ms window
            max_concurrent=10,
        )
        await limiter.initialize()

        # First two requests should be immediate
        wait1 = await limiter.acquire()
        limiter.release()
        wait2 = await limiter.acquire()
        limiter.release()

        assert wait1 == 0.0
        assert wait2 == 0.0

        # Third request should wait (rate limit hit)
        wait3 = await limiter.acquire()
        limiter.release()

        # Should have waited some time
        assert wait3 > 0 or limiter.total_wait_time > 0

    @pytest.mark.asyncio
    async def test_concurrency_limiting(self) -> None:
        """Concurrency limiting should work."""
        limiter = AppleScriptRateLimiter(
            requests_per_window=100,  # High rate limit
            window_seconds=10.0,
            max_concurrent=2,  # Only 2 concurrent
        )
        await limiter.initialize()

        # Acquire two slots
        await limiter.acquire()
        await limiter.acquire()

        # Third acquire should block (we use timeout to test)
        async def try_acquire_with_timeout() -> bool:
            """Attempt to acquire with a short timeout for testing."""
            try:
                await asyncio.wait_for(limiter.acquire(), timeout=0.1)
                return True
            except TimeoutError:
                return False

        # This should timeout because we have 2 concurrent and haven't released
        result = await try_acquire_with_timeout()
        assert result is False

        # Release one
        limiter.release()

        # Now acquire should succeed
        result = await try_acquire_with_timeout()
        assert result is True

        # Clean up
        limiter.release()
        limiter.release()


class TestAppleScriptRateLimiterStats:
    """Tests for get_stats method."""

    @pytest.mark.asyncio
    async def test_get_stats_initial(self) -> None:
        """Initial stats should be zero."""
        limiter = AppleScriptRateLimiter(
            requests_per_window=10,
            window_seconds=1.0,
        )
        stats = limiter.get_stats()

        assert stats["total_requests"] == 0
        assert stats["total_wait_time"] == 0.0
        assert stats["avg_wait_time"] == 0.0
        assert stats["current_calls_in_window"] == 0
        assert stats["requests_per_window"] == 10

    @pytest.mark.asyncio
    async def test_get_stats_after_requests(self) -> None:
        """Stats should reflect request activity."""
        limiter = AppleScriptRateLimiter(
            requests_per_window=10,
            window_seconds=1.0,
        )
        await limiter.initialize()

        # Make some requests
        await limiter.acquire()
        limiter.release()
        await limiter.acquire()
        limiter.release()
        await limiter.acquire()
        limiter.release()

        stats = limiter.get_stats()
        assert stats["total_requests"] == 3
        assert stats["current_calls_in_window"] == 3

    @pytest.mark.asyncio
    async def test_get_stats_cleans_old_timestamps(self) -> None:
        """get_stats should clean expired timestamps."""
        limiter = AppleScriptRateLimiter(
            requests_per_window=10,
            window_seconds=0.1,  # 100ms window
        )
        await limiter.initialize()

        # Make a request
        await limiter.acquire()
        limiter.release()

        # Wait for window to expire
        await asyncio.sleep(0.15)

        # Stats should show 0 current usage (expired)
        stats = limiter.get_stats()
        assert stats["total_requests"] == 1
        assert stats["current_calls_in_window"] == 0
