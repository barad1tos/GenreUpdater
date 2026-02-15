"""AppleScript rate limiter module.

This module provides rate limiting for AppleScript execution
using a moving window approach with concurrency control.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import TypedDict


class RateLimiterStats(TypedDict):
    """Statistics from the AppleScript rate limiter."""

    total_requests: int
    total_wait_time: float
    avg_wait_time: float
    current_calls_in_window: int
    requests_per_window: int
    window_seconds: float


class AppleScriptRateLimiter:
    """Advanced rate limiter using a moving window approach."""

    def __init__(
        self,
        requests_per_window: int,
        window_seconds: float,
        max_concurrent: int = 3,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the rate limiter with configurable limits.

        Args:
            requests_per_window: Maximum requests allowed per time window.
            window_seconds: Duration of the sliding window in seconds.
            max_concurrent: Maximum concurrent requests (semaphore limit).
            logger: Optional logger instance for debug output.

        Raises:
            ValueError: If any numeric parameter is not positive.

        """
        if requests_per_window <= 0:
            msg = "requests_per_window must be a positive integer"
            raise ValueError(msg)
        if window_seconds <= 0:
            msg = "window_seconds must be a positive number"
            raise ValueError(msg)
        if max_concurrent <= 0:
            msg = "max_concurrent must be a positive integer"
            raise ValueError(msg)

        self.requests_per_window = requests_per_window
        self.window_seconds = window_seconds
        self.request_timestamps: deque[float] = deque()
        self.semaphore: asyncio.Semaphore | None = None
        self.max_concurrent = max_concurrent
        self.logger = logger or logging.getLogger(__name__)
        self.total_requests: int = 0
        self.total_wait_time: float = 0.0

    async def initialize(self) -> None:
        """Initialize the rate limiter for async operation.

        Creates the asyncio semaphore for concurrency control. Must be called
        before using acquire/release in an async context.

        """
        if self.semaphore is None:
            try:
                self.semaphore = asyncio.Semaphore(self.max_concurrent)
                self.logger.debug("RateLimiter initialized with max_concurrent: %s", self.max_concurrent)
                # Yield control to event loop to make this properly async
                await asyncio.sleep(0)
            except (ValueError, TypeError, RuntimeError, asyncio.InvalidStateError) as e:
                self.logger.exception("Error initializing RateLimiter semaphore: %s", e)
                raise

    async def acquire(self) -> float:
        """Acquire permission to make a request.

        Waits if necessary to respect rate limits, then acquires the semaphore.
        Records the request timestamp for sliding window calculation.

        Returns:
            Wait time in seconds (0.0 if no wait was needed).

        """
        if self.semaphore is None:
            msg = "RateLimiter not initialized"
            raise RuntimeError(msg)
        rate_limit_wait_time = await self._wait_if_needed()
        self.total_requests += 1
        self.total_wait_time += rate_limit_wait_time
        await self.semaphore.acquire()
        return rate_limit_wait_time

    def release(self) -> None:
        """Release the semaphore after request completion.

        Should be called after each request finishes to allow other
        concurrent requests to proceed.

        """
        if self.semaphore is None:
            return
        self.semaphore.release()

    async def _wait_if_needed(self) -> float:
        """Wait if rate limit would be exceeded.

        Checks if adding a new request would exceed requests_per_window.
        If so, waits until the oldest request falls outside the window.

        Returns:
            Actual wait time in seconds (0.0 if no wait was needed).

        """
        now = time.monotonic()
        while self.request_timestamps and now - self.request_timestamps[0] > self.window_seconds:
            self.request_timestamps.popleft()
        if len(self.request_timestamps) >= self.requests_per_window:
            oldest_timestamp = self.request_timestamps[0]
            wait_duration = (oldest_timestamp + self.window_seconds) - now
            if wait_duration > 0:
                self.logger.debug("Rate limit reached. Waiting %.3fs", wait_duration)
                await asyncio.sleep(wait_duration)
                return wait_duration + await self._wait_if_needed()
        self.request_timestamps.append(time.monotonic())
        return 0.0

    def get_stats(self) -> RateLimiterStats:
        """Get current rate limiter statistics.

        Returns:
            Dictionary containing:
            - "total_requests": Total requests processed
            - "total_wait_time": Cumulative wait time in seconds
            - "avg_wait_time": Average wait time per request
            - "current_calls_in_window": Current requests within sliding window
            - "requests_per_window": Configured request limit
            - "window_seconds": Window duration in seconds

        """
        now = time.monotonic()
        self.request_timestamps = deque(ts for ts in self.request_timestamps if now - ts <= self.window_seconds)
        return {
            "total_requests": self.total_requests,
            "total_wait_time": self.total_wait_time,
            "avg_wait_time": self.total_wait_time / max(1, self.total_requests),
            "current_calls_in_window": len(self.request_timestamps),
            "requests_per_window": self.requests_per_window,
            "window_seconds": self.window_seconds,
        }
