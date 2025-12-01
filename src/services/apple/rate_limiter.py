"""AppleScript rate limiter module.

This module provides rate limiting for AppleScript execution
using a moving window approach with concurrency control.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any


class EnhancedRateLimiter:
    """Advanced rate limiter using a moving window approach."""

    def __init__(
        self,
        requests_per_window: int,
        window_size: float,
        max_concurrent: int = 3,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the rate limiter."""
        if requests_per_window <= 0:
            msg = "requests_per_window must be a positive integer"
            raise ValueError(msg)
        if window_size <= 0:
            msg = "window_size must be a positive number"
            raise ValueError(msg)
        if max_concurrent <= 0:
            msg = "max_concurrent must be a positive integer"
            raise ValueError(msg)

        self.requests_per_window = requests_per_window
        self.window_size = window_size
        self.request_timestamps: deque[float] = deque()
        self.semaphore: asyncio.Semaphore | None = None
        self.max_concurrent = max_concurrent
        self.logger = logger or logging.getLogger(__name__)
        self.total_requests: int = 0
        self.total_wait_time: float = 0.0

    async def initialize(self) -> None:
        """Initialize the rate limiter."""
        if self.semaphore is None:
            try:
                self.semaphore = asyncio.Semaphore(self.max_concurrent)
                self.logger.debug(f"RateLimiter initialized with max_concurrent: {self.max_concurrent}")
                # Yield control to event loop to make this properly async
                await asyncio.sleep(0)
            except (ValueError, TypeError, RuntimeError, asyncio.InvalidStateError) as e:
                self.logger.exception("Error initializing RateLimiter semaphore: %s", e)
                raise

    async def acquire(self) -> float:
        """Acquire permission to make a request, waiting if necessary due to rate limits or concurrency limits."""
        if self.semaphore is None:
            msg = "RateLimiter not initialized"
            raise RuntimeError(msg)
        rate_limit_wait_time = await self._wait_if_needed()
        self.total_requests += 1
        self.total_wait_time += rate_limit_wait_time
        await self.semaphore.acquire()
        return rate_limit_wait_time

    def release(self) -> None:
        """Release the semaphore, allowing another request to proceed."""
        if self.semaphore is None:
            return
        self.semaphore.release()

    async def _wait_if_needed(self) -> float:
        now = time.monotonic()
        while self.request_timestamps and now - self.request_timestamps[0] > self.window_size:
            self.request_timestamps.popleft()
        if len(self.request_timestamps) >= self.requests_per_window:
            oldest_timestamp = self.request_timestamps[0]
            wait_duration = (oldest_timestamp + self.window_size) - now
            if wait_duration > 0:
                self.logger.debug(f"Rate limit reached. Waiting {wait_duration:.3f}s")
                await asyncio.sleep(wait_duration)
                return wait_duration + await self._wait_if_needed()
        self.request_timestamps.append(time.monotonic())
        return 0.0

    def get_stats(self) -> dict[str, Any]:
        """Get statistics about rate limiter usage."""
        now = time.monotonic()
        self.request_timestamps = deque(ts for ts in self.request_timestamps if now - ts <= self.window_size)
        return {
            "total_requests": self.total_requests,
            "total_wait_time": self.total_wait_time,
            "avg_wait_time": self.total_wait_time / max(1, self.total_requests),
            "current_window_usage": len(self.request_timestamps),
            "max_requests_per_window": self.requests_per_window,
        }
