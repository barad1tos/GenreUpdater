"""Generic Cache Service - In-memory cache for general-purpose caching.

This module provides a generic in-memory cache service with TTL support
and automatic cleanup for temporary caching needs.

Key Features:
- In-memory storage for fast access
- TTL-based expiration with automatic cleanup
- Content-aware policies via SmartCacheConfig
- Thread-safe operations
- Comprehensive statistics and monitoring
"""

import asyncio
import contextlib
import logging
import time
from typing import Any, Self, TypeGuard

from src.infrastructure.cache.cache_config import CacheContentType, SmartCacheConfig
from src.infrastructure.cache.hash_service import UnifiedHashService
from src.shared.data.protocols import CacheableKey, CacheableValue


def is_generic_cache_entry(value: object) -> TypeGuard[tuple[CacheableValue, float]]:
    """Type guard to check if value is a valid generic cache entry.

    Args:
        value: Value to check

    Returns:
        True if value is a (data, timestamp) tuple
    """
    if not isinstance(value, tuple) or len(value) != 2:
        return False

    # Check timestamp (second element) is numeric
    if not isinstance(value[1], (int, float)):
        return False

    # Check first element is a cacheable type (str, int, float, bool, dict, list, or None)
    return isinstance(value[0], (str, int, float, bool, dict, list, type(None)))


class GenericCacheService:
    """Generic in-memory cache service with TTL support and automatic cleanup."""

    def __init__(self, config: dict[str, Any], logger: logging.Logger | None = None) -> None:
        """Initialize generic cache service.

        Args:
            config: Cache configuration dictionary
            logger: Optional logger instance
        """
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        self.cache_config = SmartCacheConfig()

        # Generic cache: {hash_key: (value, timestamp)}
        self.cache: dict[str, tuple[CacheableValue, float]] = {}

        # Cleanup task reference
        self._cleanup_task: asyncio.Task[None] | None = None

        # Default TTL from configuration
        self.default_ttl = self.cache_config.get_ttl(CacheContentType.GENERIC)

    async def initialize(self) -> None:
        """Initialize generic cache service and start cleanup task."""
        self.logger.info("Initializing GenericCacheService...")

        # Start periodic cleanup task
        await self._start_cleanup_task()

        self.logger.info("GenericCacheService initialized with %ds default TTL", self.default_ttl)

    async def _start_cleanup_task(self) -> None:
        """Start periodic cleanup task for expired entries."""
        cleanup_interval = self.config.get("cleanup_interval", 300)  # 5 minutes default

        async def cleanup_loop() -> None:
            """Periodic cleanup loop."""
            while True:
                try:
                    await asyncio.sleep(cleanup_interval)
                    cleaned = self.cleanup_expired()
                    if cleaned > 0:
                        self.logger.debug("Periodic cleanup removed %d expired entries", cleaned)

                    # Enforce size limits to prevent unbounded growth
                    removed = self.enforce_size_limits()
                    if removed > 0:
                        self.logger.debug("Periodic size enforcement removed %d oldest entries", removed)
                except asyncio.CancelledError:
                    self.logger.debug("Cleanup task cancelled")
                    raise
                except Exception as e:
                    self.logger.exception("Error in cleanup task: %s", e)

        self._cleanup_task = asyncio.create_task(cleanup_loop())
        self.logger.debug("Started cleanup task with %ds interval", cleanup_interval)

    def get(self, key_data: CacheableKey) -> CacheableValue | None:
        """Get value from generic cache.

        Args:
            key_data: Cache key data

        Returns:
            Cached value if found and valid, None otherwise
        """
        key = UnifiedHashService.hash_generic_key(key_data)

        if key not in self.cache:
            self.logger.debug("Generic cache miss: %s", key[:16])
            return None

        value, timestamp = self.cache[key]

        # Check if expired
        if GenericCacheService._is_expired(timestamp):
            self.logger.debug("Generic cache expired: %s", key[:16])
            del self.cache[key]
            return None

        self.logger.debug("Generic cache hit: %s", key[:16])
        return value

    def set(self, key_data: CacheableKey, value: CacheableValue, ttl: int | None = None) -> None:
        """Store value in generic cache.

        Args:
            key_data: Cache key data
            value: Value to cache
            ttl: Time to live in seconds (uses default if not specified)
        """
        key = UnifiedHashService.hash_generic_key(key_data)

        # Use provided TTL or default
        actual_ttl = ttl if ttl is not None else self.default_ttl
        timestamp = time.time() + actual_ttl

        self.cache[key] = (value, timestamp)

        self.logger.debug("Stored in generic cache: %s (TTL: %ds)", key[:16], actual_ttl)

    def invalidate(self, key_data: CacheableKey) -> bool:
        """Invalidate specific cache entry.

        Args:
            key_data: Cache key data to invalidate

        Returns:
            True if entry was found and removed, False otherwise
        """
        key = UnifiedHashService.hash_generic_key(key_data)

        if key in self.cache:
            del self.cache[key]
            self.logger.debug("Invalidated generic cache entry: %s", key[:16])
            return True

        return False

    def invalidate_all(self) -> None:
        """Clear all generic cache entries."""
        count = len(self.cache)
        self.cache.clear()
        self.logger.info("Cleared all generic cache entries (%d items)", count)

    def cleanup_expired(self) -> int:
        """Remove expired entries from cache.

        Returns:
            Number of entries removed
        """
        current_time = time.time()
        expired_keys = [key for key, (_, timestamp) in self.cache.items() if timestamp <= current_time]

        # Remove expired entries
        for key in expired_keys:
            del self.cache[key]

        if expired_keys:
            self.logger.debug("Cleaned up %d expired generic cache entries", len(expired_keys))

        return len(expired_keys)

    @staticmethod
    def _is_expired(timestamp: float) -> bool:
        """Check if timestamp indicates expiration.

        Args:
            timestamp: Expiration timestamp

        Returns:
            True if expired, False otherwise
        """
        return timestamp <= time.time()

    def get_all_entries(self) -> list[tuple[str, Any, float]]:
        """Get all cache entries for debugging/inspection.

        Returns:
            List of (key, value, timestamp) tuples
        """
        return [(key[:16], value, timestamp) for key, (value, timestamp) in self.cache.items()]

    def enforce_size_limits(self) -> int:
        """Enforce cache size limits by removing oldest entries.

        Returns:
            Number of entries removed
        """
        max_entries = self.config.get("max_generic_entries", 10000)

        if len(self.cache) <= max_entries:
            return 0

        # Sort by timestamp (oldest first) and remove excess
        sorted_items = sorted(self.cache.items(), key=lambda x: x[1][1])
        entries_to_remove = len(self.cache) - max_entries

        removed_count = 0
        for key, _ in sorted_items[:entries_to_remove]:
            del self.cache[key]
            removed_count += 1

        if removed_count > 0:
            self.logger.info("Enforced size limit: removed %d oldest entries", removed_count)

        return removed_count

    async def stop_cleanup_task(self) -> None:
        """Stop the periodic cleanup task."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
            self.logger.debug("Stopped cleanup task")

    async def __aenter__(self) -> Self:
        """Async context manager entry."""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: object) -> None:
        """Async context manager exit - ensures cleanup task is stopped."""
        await self.stop_cleanup_task()

    def get_stats(self) -> dict[str, Any]:
        """Get generic cache statistics.

        Returns:
            Dictionary containing cache statistics
        """
        current_time = time.time()
        valid_entries = 0
        expired_entries = 0

        for _, timestamp in self.cache.values():
            if timestamp > current_time:
                valid_entries += 1
            else:
                expired_entries += 1

        policy = self.cache_config.get_policy(CacheContentType.GENERIC)

        return {
            "total_entries": len(self.cache),
            "valid_entries": valid_entries,
            "expired_entries": expired_entries,
            "content_type": CacheContentType.GENERIC.value,
            "default_ttl": self.default_ttl,
            "ttl_policy": policy.ttl_seconds,
            "invalidation_strategy": policy.invalidation_strategy.value,
            "max_entries": self.config.get("max_generic_entries", 10000),
            "cleanup_running": self._cleanup_task is not None and not self._cleanup_task.done(),
        }
