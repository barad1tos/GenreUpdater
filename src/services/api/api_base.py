"""Base classes and utilities for external API services.

This module provides common functionality for all API providers including:
- Rate limiting with moving window approach
- Common type definitions
- Base scoring and normalization methods
- Shared utilities for API interactions
"""

import asyncio
import logging
import re
import time
from datetime import UTC
from datetime import datetime as dt
from typing import Any, TypedDict


class ScoredRelease(TypedDict):
    """Type definition for a scored release with metadata and scoring details."""

    title: str
    year: str | None
    score: float
    artist: str | None
    album_type: str | None
    country: str | None
    status: str | None
    format: str | None
    label: str | None
    catalog_number: str | None
    barcode: str | None
    disambiguation: str | None
    source: str


class EnhancedRateLimiter:
    """Advanced rate limiter using a moving window approach for API calls.

    This rate limiter tracks API calls within a sliding time window to ensure
    compliance with API rate limits. It uses a moving window algorithm that's
    more accurate than simple token bucket approaches.

    Attributes:
        requests_per_window: Maximum number of requests allowed in the time window
        window_seconds: Size of the time window in seconds
        call_times: List of timestamps for recent API calls
        lock: Asyncio lock for thread-safe operations

    """

    def __init__(self, requests_per_window: int, window_seconds: float) -> None:
        """Initialize the rate limiter.

        Args:
            requests_per_window: Maximum requests allowed in the time window
            window_seconds: Duration of the time window in seconds

        Raises:
            ValueError: If parameters are not positive numbers

        """
        if requests_per_window <= 0:
            msg = "requests_per_window must be a positive integer"
            raise ValueError(msg)
        if window_seconds <= 0:
            msg = "window_seconds must be a positive number"
            raise ValueError(msg)

        self.requests_per_window = requests_per_window
        self.window_seconds = window_seconds
        self.call_times: list[float] = []
        self.lock = asyncio.Lock()
        self.total_requests = 0  # Track total requests made
        self.total_wait_time = 0.0  # Track cumulative wait time

    async def acquire(self) -> float:
        """Acquire permission to make an API call, waiting if necessary.

        Returns:
            float: The amount of time (in seconds) that was spent waiting

        """
        async with self.lock:
            wait_time = await self._wait_if_needed()
            self.call_times.append(time.monotonic())
            self.total_requests += 1
            self.total_wait_time += wait_time
            return wait_time

    def release(self) -> None:
        """Release method for compatibility (no-op for this implementation)."""

    async def _wait_if_needed(self) -> float:
        """Wait if necessary to comply with rate limits.

        Returns:
            float: Time waited in seconds

        """
        now = time.monotonic()

        # Remove calls outside the current window
        cutoff = now - self.window_seconds
        self.call_times = [t for t in self.call_times if t > cutoff]

        # Check if we need to wait
        if len(self.call_times) >= self.requests_per_window:
            # Calculate wait time: oldest call and window - now
            oldest_call = self.call_times[0]
            wait_until = oldest_call + self.window_seconds
            wait_time = max(0.0, wait_until - now)

            if wait_time > 0:
                # Add a small buffer to avoid edge cases
                wait_time += 0.01
                await asyncio.sleep(wait_time)

                # Clean up again after waiting
                now = time.monotonic()
                cutoff = now - self.window_seconds
                self.call_times = [t for t in self.call_times if t > cutoff]

            return wait_time

        return 0.0

    def get_stats(self) -> dict[str, Any]:
        """Get current rate limiter statistics.

        Returns:
            Dictionary containing current stats and configuration

        """
        now = time.monotonic()
        cutoff = now - self.window_seconds
        current_calls = [t for t in self.call_times if t > cutoff]

        return {
            "requests_per_window": self.requests_per_window,
            "window_seconds": self.window_seconds,
            "current_calls_in_window": len(current_calls),
            "available_capacity": max(0, self.requests_per_window - len(current_calls)),
            "window_utilization": (len(current_calls) / self.requests_per_window if self.requests_per_window > 0 else 0),
            "total_requests": self.total_requests,
            "avg_wait_time": self.total_wait_time / max(1, self.total_requests),
        }


class BaseApiClient:
    """Base class for API client implementations.

    Provides common functionality for all API clients including
    - Name normalization
    - Year validation
    - Common scoring logic
    """

    def __init__(self, console_logger: logging.Logger, error_logger: logging.Logger) -> None:
        """Initialize base API client.

        Args:
            console_logger: Logger for console output
            error_logger: Logger for error messages

        """
        self.console_logger = console_logger
        self.error_logger = error_logger
        self.compilation_pattern = re.compile(
            r"\b(compilation|greatest\s+hits|best\s+of|collection|anthology)\b",
            re.IGNORECASE,
        )

    @staticmethod
    def _normalize_name(name: str) -> str:
        """Normalize an artist or album name for case-insensitive matching.

        Uses casefold() for proper Unicode handling (e.g., German ß → ss,
        Turkish İ → i). Removes punctuation but preserves non-ASCII letters
        for international artist names (Björk, 東京事変, Молчат Дома).

        Args:
            name: The name to normalize

        Returns:
            Normalized name string suitable for fuzzy matching

        """
        if not name:
            return ""

        # Use casefold() for Unicode-aware case-insensitive comparison
        # (handles ß → ss, İ → i, etc. better than lower())
        normalized = name.casefold()

        # Replace '&' with 'and' for consistency
        normalized = normalized.replace("&", "and")

        # Remove punctuation but keep Unicode word chars (\w includes non-ASCII letters)
        normalized = re.sub(r"[^\w\s]", "", normalized)

        # Normalize whitespace (multiple spaces to single space)
        normalized = re.sub(r"\s+", " ", normalized)

        return normalized.strip()

    @staticmethod
    def _is_valid_year(year_str: str | None) -> bool:
        """Check if a year string represents a valid release year.

        Uses system datetime validation instead of magic numbers.

        Args:
            year_str: String representation of a year

        Returns:
            True if the year is valid, False otherwise

        """
        if not year_str or not year_str.strip():
            return False

        try:
            year = int(year_str.strip())
            # Let the system decide if the year is valid
            dt(year, 1, 1, tzinfo=UTC)
            return year >= 1900  # Only reasonable constraint - before gramophones
        except (ValueError, TypeError, OverflowError, OSError):
            return False

    @staticmethod
    def _extract_year_from_date(date_str: str | None) -> str | None:
        """Extract year from various date formats.

        Args:
            date_str: Date string in various formats

        Returns:
            Extracted year or None if invalid

        """
        if not date_str:
            return None

        # Handle ISO date formats (YYYY-MM-DD or YYYY)
        if match := re.match(r"^(\d{4})", date_str):
            year = match[1]
            if BaseApiClient._is_valid_year(year):
                return year

        return None
