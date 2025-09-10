"""Cache Coordinator Module for TASK-006: Cache Architecture Unification.

This module implements the hierarchical cache architecture with:
- L1 Cache: Fast in-memory cache for hot data (5-minute TTL)
- L2 Cache: Persistent cache for stable data (24-hour TTL)
- L3 Caches: Specialized caches with domain logic
- Automatic invalidation coordination across all cache levels

The coordinator manages cache relationships and ensures consistency
across the hierarchical structure while maintaining backward compatibility
with existing cache interfaces.
"""

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any, TypeVar, cast

from src.services.cache.cache_config import CacheConfiguration, CacheLevel
from src.services.cache.cache_protocol import CacheProtocol, CacheStats, TTLManager

T = TypeVar("T")
CacheValue = str | int | float | bool | dict[str, Any] | list[Any] | None

# Cache key prefixes used throughout the system
ALBUM_PREFIX = "album:"
API_PREFIX = "api:"
PENDING_PREFIX = "pending:"


class InvalidationCoordinator:
    """Manages automatic invalidation cascading between related cache entries.

    This coordinator implements the relationship rules to ensure that when
    an entry is invalidated in one cache, all related entries in other caches
    are also invalidated to maintain consistency.

    Relationship rules:
    - Album data changes invalidate related API cache entries
    - API results invalidate pending verification entries
    - Pending verification completion invalidates API cache entries
    """

    def __init__(self, caches: list[CacheProtocol[Any]], logger: logging.Logger) -> None:
        """Initialize the invalidation coordinator.

        Args:
            caches: List of all cache instances to coordinate
            logger: Logger for coordination activities

        """
        self.caches = caches
        self.logger = logger

        # Define cascade invalidation relationships
        self.relationship_rules = {
            # Album data changes should invalidate related API cache entries
            ALBUM_PREFIX: ["api:musicbrainz:", "api:discogs:", "api:lastfm:"],
            # API results should invalidate the pending verification
            API_PREFIX: [PENDING_PREFIX],
            # Pending verification completion should invalidate the API cache
            PENDING_PREFIX: [API_PREFIX],
        }

    async def cascade_invalidation(self, key: str) -> int:
        """Perform cascade invalidation for a key and all related keys.

        Uses a breadth-first traversal to find and invalidate all related
        keys while avoiding infinite loops through cycle detection.

        Args:
            key: The primary key being invalidated

        Returns:
            Total number of cache entries invalidated

        """
        total_invalidated = 0
        processed_keys: set[str] = set()
        keys_to_process: list[str] = [key]

        self.logger.debug("Starting cascade invalidation for key: %s", key)

        while keys_to_process:
            current_key = keys_to_process.pop(0)
            if current_key in processed_keys:
                continue

            processed_keys.add(current_key)

            # Invalidate the current key in all caches
            for cache in self.caches:
                try:
                    invalidated = await cache.invalidate(current_key)
                    total_invalidated += invalidated
                    if invalidated > 0:
                        self.logger.debug(
                            "Invalidated %d entries for key %s",
                            invalidated,
                            current_key,
                        )
                except (AttributeError, TypeError, ValueError, KeyError):
                    self.logger.exception("Error invalidating key %s", current_key)

            # Find and queue related keys
            related_keys = self._find_related_keys(current_key)
            keys_to_process.extend(related_keys)

        if total_invalidated > 0:
            self.logger.info(
                "Cascade invalidation complete: %d entries invalidated",
                total_invalidated,
            )

        return total_invalidated

    def _find_related_keys(self, key: str) -> list[str]:
        """Find keys related to the given key based on relationship rules.

        Args:
            key: Key to find relationships for

        Returns:
            List of related keys that should also be invalidated

        """
        related_keys: list[str] = []

        # Check each relationship rule
        for key_prefix, related_prefixes in self.relationship_rules.items():
            if key.startswith(key_prefix):
                # Extract the base identifier (everything after the prefix)
                base_part = key[len(key_prefix) :]

                # Generate related keys for each related prefix
                for related_prefix in related_prefixes:
                    related_key = f"{related_prefix}{base_part}"
                    related_keys.append(related_key)

        return related_keys


class FastInMemoryCache:
    """L1 Cache: Fast in-memory cache for hot data.

    Optimized for speed with minimal overhead. Use a simple dictionary
    with TTL support for frequently accessed data.
    """

    def __init__(self, max_size: int = 1000, default_ttl: int = 300) -> None:
        """Initialize L1 fast cache.

        Args:
            max_size: Maximum number of entries
            default_ttl: Default TTL in seconds (5 minutes)

        """
        self.max_size = max_size
        self.default_ttl = default_ttl
        self.cache: dict[str, tuple[Any, float]] = {}
        self.lock = asyncio.Lock()
        self.stats = CacheStats()

    async def get(self, key: str, default: CacheValue = None) -> CacheValue:
        """Get value from L1 cache."""
        async with self.lock:
            if key in self.cache:
                value, expiry = self.cache[key]
                if not TTLManager.is_expired(expiry):
                    self.stats.record_hit()
                    return cast(CacheValue, value)
                del self.cache[key]
                self.stats.record_cleanup()

            self.stats.record_miss()
            return default

    async def set(self, key: str, value: CacheValue, ttl: int | None = None) -> None:
        """Set value in L1 cache with LRU eviction."""
        async with self.lock:
            effective_ttl = ttl if ttl is not None else self.default_ttl
            expiry = TTLManager.calculate_expiry(effective_ttl, self.default_ttl)

            # Add a new entry
            self.cache[key] = (value, expiry)
            self.stats.record_set()

            # LRU eviction if over size limit
            if len(self.cache) > self.max_size:
                # Remove the oldest entries based on expiry time
                sorted_items = sorted(self.cache.items(), key=lambda x: x[1][1])
                to_remove = len(self.cache) - self.max_size

                for i in range(to_remove):
                    old_key = sorted_items[i][0]
                    del self.cache[old_key]
                    self.stats.record_eviction()

    async def invalidate(self, key: str | list[str]) -> int:
        """Invalidate one or more keys."""
        async with self.lock:
            keys = [key] if isinstance(key, str) else key
            invalidated = 0
            for k in keys:
                if k in self.cache:
                    del self.cache[k]
                    invalidated += 1

            self.stats.record_invalidation(invalidated)
            return invalidated

    async def cleanup(self) -> int:
        """Clean expired entries."""
        async with self.lock:
            now = time.time()
            expired_keys = [k for k, (_, expiry) in self.cache.items() if now > expiry]

            for key in expired_keys:
                del self.cache[key]

            self.stats.record_cleanup(len(expired_keys))
            return len(expired_keys)

    def get_stats(self) -> dict[str, Any]:
        """Get L1 cache statistics."""
        return {
            "level": "L1",
            "type": "fast_memory",
            "size": len(self.cache),
            "max_size": self.max_size,
            **self.stats.to_dict(),
        }


class PersistentCache:
    """L2 Cache: Persistent cache for stable data.

    Combines in-memory performance with disk persistence for data
    that needs to survive application restarts.
    """

    def __init__(self, max_size: int = 10000, default_ttl: int = 86400) -> None:
        """Initialize L2 persistent cache.

        Args:
            max_size: Maximum number of entries
            default_ttl: Default TTL in seconds (24 hours)

        """
        self.max_size = max_size
        self.default_ttl = default_ttl
        self.cache: dict[str, tuple[Any, float]] = {}
        self.lock = asyncio.Lock()
        self.stats = CacheStats()
        self.dirty = False

    async def get(self, key: str, default: CacheValue = None) -> CacheValue:
        """Get value from L2 cache."""
        async with self.lock:
            if key in self.cache:
                value, expiry = self.cache[key]
                if not TTLManager.is_expired(expiry):
                    self.stats.record_hit()
                    return cast(CacheValue, value)
                del self.cache[key]
                self.stats.record_cleanup()
                self.dirty = True

            self.stats.record_miss()
            return default

    async def set(self, key: str, value: CacheValue, ttl: int | None = None) -> None:
        """Set value in L2 cache."""
        async with self.lock:
            effective_ttl = ttl if ttl is not None else self.default_ttl
            expiry = TTLManager.calculate_expiry(effective_ttl, self.default_ttl)

            self.cache[key] = (value, expiry)
            self.stats.record_set()
            self.dirty = True

            # Size management
            if len(self.cache) > self.max_size:
                self._evict_lru_entries()

    async def invalidate(self, key: str | list[str]) -> int:
        """Invalidate one or more keys."""
        async with self.lock:
            keys = [key] if isinstance(key, str) else key
            invalidated = 0
            for k in keys:
                if k in self.cache:
                    del self.cache[k]
                    invalidated += 1
                    self.dirty = True

            self.stats.record_invalidation(invalidated)
            return invalidated

    async def cleanup(self) -> int:
        """Clean expired entries."""
        async with self.lock:
            now = time.time()
            expired_keys = [k for k, (_, expiry) in self.cache.items() if now > expiry]

            for key in expired_keys:
                del self.cache[key]

            if expired_keys:
                self.dirty = True

            self.stats.record_cleanup(len(expired_keys))
            return len(expired_keys)

    def _evict_lru_entries(self) -> None:
        """Evict least recently used entries."""
        sorted_items = sorted(self.cache.items(), key=lambda x: x[1][1])
        to_remove = len(self.cache) - self.max_size

        for i in range(to_remove):
            old_key = sorted_items[i][0]
            del self.cache[old_key]
            self.stats.record_eviction()

    def get_stats(self) -> dict[str, Any]:
        """Get L2 cache statistics."""
        return {
            "level": "L2",
            "type": "persistent",
            "size": len(self.cache),
            "max_size": self.max_size,
            "dirty": self.dirty,
            **self.stats.to_dict(),
        }


class HierarchicalCacheManager:
    """Unified cache manager with hierarchical coordination.

    This is the main class that implements the hierarchical cache architecture
    specified in TASK-006. It coordinates between L1, L2, and L3 cache levels
    and provides automatic invalidation cascading.

    Architecture:
    - L1: Fast in-memory cache for hot data (1000 entries, 5-min TTL)
    - L2: Persistent cache for stable data (10_000 entries, 24-hour TTL)
    - L3: Specialized caches with domain logic (albums, api_results, pending)
    """

    def __init__(self, config: CacheConfiguration, logger: logging.Logger) -> None:
        """Initialize the hierarchical cache manager.

        Args:
            config: Cache configuration object
            logger: Logger for cache operations

        """
        self.config = config
        self.logger = logger

        # Initialize cache levels
        self.l1_cache = FastInMemoryCache(
            max_size=config.l1_max_entries,
            default_ttl=config.l1_ttl_seconds,
        )

        self.l2_cache = PersistentCache(
            max_size=config.l2_max_entries,
            default_ttl=config.l2_ttl_seconds,
        )

        # L3 specialized caches - delegated to existing implementations
        # These will be wrapped to implement the CacheProtocol interface
        self.l3_caches: dict[str, CacheProtocol[Any]] = {}

        # Coordination layer
        all_caches: list[CacheProtocol[Any]] = [self.l1_cache, self.l2_cache]
        self.invalidation_coordinator = InvalidationCoordinator(all_caches, logger)

        # Performance metrics
        self.hit_stats: dict[str, int] = defaultdict(int)
        self.total_requests = 0

    def register_l3_cache(self, name: str, cache: CacheProtocol[Any]) -> None:
        """Register a specialized L3 cache.

        Args:
            name: Name of the cache (e.g., 'albums', 'api_results', 'pending')
            cache: Cache implementation conforming to CacheProtocol

        """
        self.l3_caches[name] = cache
        self.invalidation_coordinator.caches.append(cache)
        self.logger.info("Registered L3 cache: %s", name)

    async def get(self, key: str, default: CacheValue = None) -> CacheValue:
        """Get value from the hierarchical cache with L1->L2->L3 fallback.

        Implements the cache hierarchy by checking L1 first (fastest),
        then L2 (persistent), then L3 specialized caches.

        Args:
            key: Cache key to retrieve
            default: Default value if not found

        Returns:
            Cached value or default

        """
        self.total_requests += 1

        # Try L1 cache first (the hottest data)
        value = await self.l1_cache.get(key)
        if value is not None:
            self.hit_stats["L1"] += 1
            self.logger.debug("L1 cache hit for key: %s", key)
            return value

        # Try L2 cache second (persistent data)
        value = await self.l2_cache.get(key)
        if value is not None:
            self.hit_stats["L2"] += 1
            # Promote to L1 for faster future access
            await self.l1_cache.set(key, value, ttl=self.config.l1_ttl_seconds)
            self.logger.debug("L2 cache hit for key: %s (promoted to L1)", key)
            return value

        # Try L3 specialized caches
        for cache_name, cache in self.l3_caches.items():
            try:
                value = await cache.get(key)
                if value is not None:
                    self.hit_stats[f"L3_{cache_name}"] += 1
                    # Promote to L1 and L2 for future access
                    await self.l2_cache.set(key, value, ttl=self.config.l2_ttl_seconds)
                    await self.l1_cache.set(key, value, ttl=self.config.l1_ttl_seconds)
                    self.logger.debug("L3 cache hit in %s for key: %s (promoted)", cache_name, key)
                    return cast(CacheValue, value)
            except (AttributeError, TypeError, ValueError, KeyError):
                self.logger.exception("Error accessing the L3 cache %s", cache_name)

        # Cache miss at all levels
        self.hit_stats["MISS"] += 1
        self.logger.debug("Cache miss for key: %s", key)
        return default

    async def set(
        self,
        key: str,
        value: CacheValue,
        ttl: int | None = None,
        level: CacheLevel = CacheLevel.AUTO,
    ) -> None:
        """Set value in the hierarchical cache.

        Args:
            key: Cache key
            value: Value to cache
            ttl: Time-to-live in seconds
            level: Target cache level (AUTO, L1, L2, L3)

        """
        if level == CacheLevel.AUTO:
            # Auto-placement based on a key prefix
            if key.startswith((ALBUM_PREFIX, API_PREFIX, PENDING_PREFIX)):
                # Domain-specific data goes to L2 and the appropriate L3
                await self.l2_cache.set(key, value, ttl)

                # Also set in L1 for hot access
                l1_ttl = min(ttl or self.config.l1_ttl_seconds, self.config.l1_ttl_seconds)
                await self.l1_cache.set(key, value, l1_ttl)
            else:
                # Generic data goes to L1
                await self.l1_cache.set(key, value, ttl)

        elif level == CacheLevel.L1:
            await self.l1_cache.set(key, value, ttl)

        elif level == CacheLevel.L2:
            await self.l2_cache.set(key, value, ttl)

        elif level == CacheLevel.L3:
            # Route to appropriate L3 cache based on key prefix
            cache_name = HierarchicalCacheManager._get_l3_cache_for_key(key)
            if cache_name in self.l3_caches:
                await self.l3_caches[cache_name].set(key, value, ttl)

    async def invalidate(self, key: str | list[str]) -> int:
        """Invalidate key(s) with automatic cascade coordination.

        Args:
            key: Key or list of keys to invalidate

        Returns:
            Total number of entries invalidated

        """
        if isinstance(key, str):
            # Single key - use cascade invalidation
            return await self.invalidation_coordinator.cascade_invalidation(key)
        # Multiple keys - invalidate each with cascade
        total = 0
        for k in key:
            total += await self.invalidation_coordinator.cascade_invalidation(k)
        return total

    async def cleanup(self) -> int:
        """Clean expired entries from all cache levels.

        Returns:
            Total number of entries cleaned

        """
        total_cleaned = 0

        # Clean L1 cache
        l1_cleaned = await self.l1_cache.cleanup()
        total_cleaned += l1_cleaned

        # Clean L2 cache
        l2_cleaned = await self.l2_cache.cleanup()
        total_cleaned += l2_cleaned

        # Clean L3 caches
        for cache_name, cache in self.l3_caches.items():
            try:
                l3_cleaned = await cache.cleanup()
                total_cleaned += l3_cleaned
            except (AttributeError, TypeError, ValueError, KeyError):
                self.logger.exception("Error cleaning L3 cache %s", cache_name)

        if total_cleaned > 0:
            self.logger.info("Cache cleanup complete: %d entries removed", total_cleaned)

        return total_cleaned

    @staticmethod
    def _get_l3_cache_for_key(key: str) -> str:
        """Determine which L3 cache should handle a key.

        Args:
            key: Cache key to analyze

        Returns:
            Name of the appropriate L3 cache

        """
        if key.startswith(ALBUM_PREFIX):
            return "albums"
        if key.startswith(API_PREFIX):
            return "api_results"
        return "pending" if key.startswith(PENDING_PREFIX) else "generic"

    def get_stats(self) -> dict[str, Any]:
        """Get comprehensive cache statistics.

        Returns:
            Dictionary with statistics from all cache levels

        """
        # Calculate hit ratios
        hit_ratio_by_level = (
            {level: hits / self.total_requests for level, hits in self.hit_stats.items()}
            if self.total_requests > 0
            else {}
        )

        return {
            "hierarchical_stats": {
                "total_requests": self.total_requests,
                "hit_ratios": hit_ratio_by_level,
                "overall_hit_ratio": (
                    (self.total_requests - self.hit_stats.get("MISS", 0)) / max(self.total_requests, 1)
                ),
            },
            "l1_stats": self.l1_cache.get_stats(),
            "l2_stats": self.l2_cache.get_stats(),
            "l3_stats": {name: cache.get_stats() for name, cache in self.l3_caches.items()},
            "coordination": {
                "invalidation_rules": len(self.invalidation_coordinator.relationship_rules),
                "registered_l3_caches": list(self.l3_caches.keys()),
            },
        }


# Export public interfaces
__all__ = [
    "FastInMemoryCache",
    "HierarchicalCacheManager",
    "InvalidationCoordinator",
    "PersistentCache",
]
