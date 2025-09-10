"""Cache eviction policies implementation for Music Genre Updater.

This module implements various cache eviction policies including LRU (Least Recently Used)
and priority-based eviction with comprehensive metrics collection
"""

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

from src.services.cache.cache_protocol import CacheProtocol

# Type variables for eviction operations
EvictionKey = TypeVar("EvictionKey", bound=str)
EvictionValue = TypeVar("EvictionValue")


class EvictionReason(Enum):
    """Reasons for cache entry eviction."""

    CAPACITY_EXCEEDED = "capacity_exceeded"
    TTL_EXPIRED = "ttl_expired"
    PRIORITY_EVICTION = "priority_eviction"
    MANUAL_EVICTION = "manual_eviction"
    POLICY_TRIGGERED = "policy_triggered"


def _default_evictions_by_reason() -> dict[EvictionReason, int]:
    """Create default evictions_by_reason dict with proper typing."""
    return {}


def _default_evicted_entry_ages() -> list[float]:
    """Create default evicted_entry_ages list with proper typing."""
    return []


def _default_evicted_entry_access_counts() -> list[int]:
    """Create default evicted_entry_access_counts list with proper typing."""
    return []


@dataclass
class CacheEntry[EvictionValue]:
    """Cache entry with metadata for eviction policies."""

    key: str
    value: EvictionValue
    access_time: float = field(default_factory=time.time)
    creation_time: float = field(default_factory=time.time)
    access_count: int = 0
    priority: int = 0  # Higher priority = less likely to be evicted
    ttl: float | None = None

    def touch(self) -> None:
        """Update access time and increment access count."""
        self.access_time = time.time()
        self.access_count += 1

    def is_expired(self) -> bool:
        """Check if entry is expired based on TTL."""
        if self.ttl is None:
            return False
        return (time.time() - self.creation_time) > self.ttl

    def age_seconds(self) -> float:
        """Get age of entry in seconds."""
        return time.time() - self.creation_time

    def last_access_seconds(self) -> float:
        """Get seconds since last access."""
        return time.time() - self.access_time


@dataclass
class EvictionMetrics:
    """Metrics tracking for eviction operations."""

    total_evictions: int = 0
    evictions_by_reason: dict[EvictionReason, int] = field(default_factory=_default_evictions_by_reason)
    evicted_entry_ages: list[float] = field(default_factory=_default_evicted_entry_ages)
    evicted_entry_access_counts: list[int] = field(default_factory=_default_evicted_entry_access_counts)
    eviction_duration_ms: float = 0.0

    def record_eviction(
        self,
        reason: EvictionReason,
        entry: CacheEntry[Any],
        duration_ms: float,
    ) -> None:
        """Record eviction operation metrics.

        Args:
            reason: Reason for eviction
            entry: Cache entry that was evicted
            duration_ms: Eviction operation duration in milliseconds

        """
        self.total_evictions += 1
        self.evictions_by_reason[reason] = self.evictions_by_reason.get(reason, 0) + 1
        self.evicted_entry_ages.append(entry.age_seconds())
        self.evicted_entry_access_counts.append(entry.access_count)
        self.eviction_duration_ms += duration_ms

    def get_average_evicted_age(self) -> float:
        """Calculate the average age of evicted entries.

        Returns:
            Average age in seconds of evicted entries

        """
        if not self.evicted_entry_ages:
            return 0.0
        return sum(self.evicted_entry_ages) / len(self.evicted_entry_ages)

    def get_average_evicted_access_count(self) -> float:
        """Calculate the average access count of evicted entries.

        Returns:
            Average access count of evicted entries

        """
        if not self.evicted_entry_access_counts:
            return 0.0
        return sum(self.evicted_entry_access_counts) / len(self.evicted_entry_access_counts)

    def to_dict(self) -> dict[str, Any]:
        """Convert metrics to dictionary format.

        Returns:
            Dictionary containing all eviction metrics

        """
        return {
            "total_evictions": self.total_evictions,
            "evictions_by_reason": {reason.value: count for reason, count in self.evictions_by_reason.items()},
            "eviction_duration_ms": self.eviction_duration_ms,
            "average_evicted_age_seconds": self.get_average_evicted_age(),
            "average_evicted_access_count": self.get_average_evicted_access_count(),
        }


class EvictionPolicy[EvictionKey: str, EvictionValue](ABC):
    """Abstract base class for cache eviction policies.

    Defines the interface that all eviction policies must implement
    for consistent behavior across different eviction strategies.
    """

    def __init__(
        self,
        max_capacity: int = 1000,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize eviction policy.

        Args:
            max_capacity: Maximum number of entries to maintain
            logger: Logger for eviction events

        """
        self.max_capacity = max_capacity
        self.logger = logger or logging.getLogger(__name__)
        self.metrics = EvictionMetrics()

    @abstractmethod
    def should_evict(self, entries: dict[EvictionKey, CacheEntry[EvictionValue]]) -> bool:
        """Determine if eviction should be triggered.

        Args:
            entries: Current cache entries

        Returns:
            True if eviction should be performed

        """

    @abstractmethod
    def select_eviction_candidates(
        self,
        entries: dict[EvictionKey, CacheEntry[EvictionValue]],
        count: int = 1,
    ) -> list[EvictionKey]:
        """Select entries for eviction.

        Args:
            entries: Current cache entries
            count: Number of entries to select for eviction

        Returns:
            List of keys to be evicted

        """

    @staticmethod
    def evict_expired_entries(
        entries: dict[EvictionKey, CacheEntry[EvictionValue]],
    ) -> list[EvictionKey]:
        """Find and mark expired entries for eviction.

        Args:
            entries: Current cache entries

        Returns:
            List of expired entry keys

        """
        return [key for key, entry in entries.items() if entry.is_expired()]

    def get_metrics(self) -> dict[str, Any]:
        """Get eviction policy metrics.

        Returns:
            Dictionary containing eviction statistics

        """
        return self.metrics.to_dict()

    def reset_metrics(self) -> None:
        """Reset eviction metrics."""
        self.metrics = EvictionMetrics()
        self.logger.info("Eviction metrics reset for policy %s", self.__class__.__name__)


class LRUEvictionPolicy(EvictionPolicy[EvictionKey, EvictionValue]):
    """Least Recently Used (LRU) eviction policy.

    Evicts the least recently accessed entries when capacity is exceeded.
    This is one of the most common and effective eviction policies.
    """

    def should_evict(self, entries: dict[EvictionKey, CacheEntry[EvictionValue]]) -> bool:
        """Determine if eviction should be triggered based on capacity.

        Args:
            entries: Current cache entries

        Returns:
            True if current size exceeds maximum capacity

        """
        return len(entries) > self.max_capacity

    def select_eviction_candidates(
        self,
        entries: dict[EvictionKey, CacheEntry[EvictionValue]],
        count: int = 1,
    ) -> list[EvictionKey]:
        """Select least recently used entries for eviction.

        Args:
            entries: Current cache entries
            count: Number of entries to select for eviction

        Returns:
            List of LRU entry keys sorted by access time (oldest first)

        """
        if not entries:
            return []

        # Sort by access time (oldest first)
        sorted_entries = sorted(
            entries.items(),
            key=lambda item: item[1].access_time,
        )

        # Select the oldest entries up to the requested count
        selected_count = min(count, len(sorted_entries))
        candidates = [key for key, _ in sorted_entries[:selected_count]]

        self.logger.debug(
            "Selected %d LRU candidates from %d entries",
            len(candidates),
            len(entries),
        )

        return candidates


class PriorityEvictionPolicy(EvictionPolicy[EvictionKey, EvictionValue]):
    """Priority-based eviction policy.

    Evicts entries based on priority levels, with lower priority entries
    being evicted first. Among entries of the same priority, use LRU.
    """

    def __init__(
        self,
        max_capacity: int = 1000,
        default_priority: int = 0,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize priority eviction policy.

        Args:
            max_capacity: Maximum number of entries to maintain
            default_priority: Default priority for new entries
            logger: Logger for eviction events

        """
        super().__init__(max_capacity, logger)
        self.default_priority = default_priority

    def should_evict(self, entries: dict[EvictionKey, CacheEntry[EvictionValue]]) -> bool:
        """Determine if eviction should be triggered based on capacity.

        Args:
            entries: Current cache entries

        Returns:
            True if current size exceeds maximum capacity

        """
        return len(entries) > self.max_capacity

    def select_eviction_candidates(
        self,
        entries: dict[EvictionKey, CacheEntry[EvictionValue]],
        count: int = 1,
    ) -> list[EvictionKey]:
        """Select entries for eviction based on priority and LRU.

        Args:
            entries: Current cache entries
            count: Number of entries to select for eviction

        Returns:
            List of entry keys sorted by priority (lowest first), then by access time

        """
        if not entries:
            return []

        # Sort by priority (lowest first), then by access time (oldest first)
        sorted_entries = sorted(
            entries.items(),
            key=lambda item: (item[1].priority, item[1].access_time),
        )

        # Select entries up to the requested count
        selected_count = min(count, len(sorted_entries))
        candidates = [key for key, _ in sorted_entries[:selected_count]]

        self.logger.debug(
            "Selected %d priority-based candidates from %d entries",
            len(candidates),
            len(entries),
        )

        return candidates


class EvictingCacheWrapper[EvictionValue]:
    """Cache wrapper that adds eviction capabilities to any CacheProtocol implementation.

    This wrapper maintains cache entries with metadata and applies eviction policies
    to keep the cache within configured limits while tracking detailed metrics.
    """

    def __init__(
        self,
        cache_backend: CacheProtocol[EvictionValue],
        eviction_policy: EvictionPolicy[str, EvictionValue],
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize evicting cache wrapper.

        Args:
            cache_backend: Underlying cache implementation
            eviction_policy: Eviction policy to apply
            logger: Logger for cache events

        """
        self.cache_backend = cache_backend
        self.eviction_policy = eviction_policy
        self.logger = logger or logging.getLogger(__name__)

        # Maintain entries with metadata separately from backend
        self._entries: dict[str, CacheEntry[EvictionValue]] = {}

    async def get(
        self,
        key: str,
        default: EvictionValue | None = None,
    ) -> EvictionValue | None:
        """Get cached value and update access metadata.

        Args:
            key: Cache key to retrieve
            default: Default value if key not found

        Returns:
            Cached value or default if not found

        """
        # Check if we have metadata for this entry
        if key in self._entries:
            entry = self._entries[key]
            entry.touch()  # Update access time and count

            # Check if entry is expired
            if entry.is_expired():
                await self._evict_entry(key, EvictionReason.TTL_EXPIRED)
                return default

        # Get from backend
        try:
            return await self.cache_backend.get(key, default)
        except (OSError, ValueError, KeyError):
            self.logger.exception("Failed to get value for key %s", key)
            return default

    async def set(
        self,
        key: str,
        value: EvictionValue,
        ttl: int | None = None,
        priority: int = 0,
    ) -> None:
        """Set cached value with eviction metadata.

        Args:
            key: Cache key to store
            value: Value to cache
            ttl: Time-to-live in seconds
            priority: Priority for eviction (higher = less likely to be evicted)

        """
        # Create or update entry metadata
        self._entries[key] = CacheEntry(
            key=key,
            value=value,
            ttl=float(ttl) if ttl is not None else None,
            priority=priority,
        )

        # Store in backend
        try:
            await self.cache_backend.set(key, value, ttl)
        except (OSError, ValueError, KeyError):
            # Remove from our metadata if backend storage failed
            self._entries.pop(key, None)
            self.logger.exception("Failed to set value for key %s", key)
            raise

        # Check if eviction is needed after adding new entry
        await self._check_and_evict()

    async def invalidate(self, key: str | list[str]) -> int:
        """Invalidate cache keys and remove metadata.

        Args:
            key: Single key or list of keys to invalidate

        Returns:
            Count of keys successfully invalidated

        """
        keys_to_remove = [key] if isinstance(key, str) else key

        # Remove from our metadata
        for k in keys_to_remove:
            if k in self._entries:
                entry = self._entries.pop(k)
                self.eviction_policy.metrics.record_eviction(
                    EvictionReason.MANUAL_EVICTION,
                    entry,
                    0.0,
                )

        # Invalidate in backend
        try:
            return await self.cache_backend.invalidate(key)
        except (OSError, ValueError, KeyError):
            self.logger.exception("Failed to invalidate keys")
            return 0

    async def cleanup(self) -> int:
        """Clean cache and remove metadata.

        Returns:
            Count of entries cleaned

        """
        # Count entries before cleanup
        entries_before = len(self._entries)

        try:
            # Clean backend
            result = await self.cache_backend.cleanup()

            # Clear our metadata
            self._entries.clear()

        except (OSError, ValueError, KeyError):
            self.logger.exception("Failed to cleanup cache")
            return 0

        self.logger.info("Cleaned %d entries from evicting cache", entries_before)
        return result

    async def _check_and_evict(self) -> None:
        """Check eviction conditions and perform eviction if necessary."""
        start_time = time.perf_counter()

        try:
            # First, evict expired entries
            expired_keys = self.eviction_policy.evict_expired_entries(self._entries)
            for key in expired_keys:
                await self._evict_entry(key, EvictionReason.TTL_EXPIRED)

            # Then check if policy-based eviction is needed
            if self.eviction_policy.should_evict(self._entries):
                # Calculate how many entries to evict
                excess_count = len(self._entries) - self.eviction_policy.max_capacity
                if excess_count > 0:
                    # Select candidates for eviction
                    candidates = self.eviction_policy.select_eviction_candidates(
                        self._entries,
                        excess_count,
                    )

                    # Evict selected candidates
                    for key in candidates:
                        await self._evict_entry(key, EvictionReason.CAPACITY_EXCEEDED)

            duration_ms = (time.perf_counter() - start_time) * 1000
            if expired_keys or len(self._entries) > self.eviction_policy.max_capacity:
                self.logger.debug(
                    "Eviction check completed in %.2fms, evicted %d expired entries",
                    duration_ms,
                    len(expired_keys),
                )

        except (OSError, ValueError, KeyError):
            self.logger.exception("Error during eviction check")

    async def _evict_entry(self, key: str, reason: EvictionReason) -> None:
        """Evict a specific entry from cache.

        Args:
            key: Key of entry to evict
            reason: Reason for eviction

        """
        start_time = time.perf_counter()

        try:
            # Get entry metadata
            entry = self._entries.get(key)
            if not entry:
                return

            # Remove from backend
            await self.cache_backend.invalidate(key)

            # Remove from our metadata
            self._entries.pop(key, None)

            # Record metrics
            duration_ms = (time.perf_counter() - start_time) * 1000
            self.eviction_policy.metrics.record_eviction(reason, entry, duration_ms)

            self.logger.debug(
                "Evicted entry %s (reason: %s, age: %.1fs, access_count: %d)",
                key,
                reason.value,
                entry.age_seconds(),
                entry.access_count,
            )

        except (OSError, ValueError, KeyError):
            self.logger.exception("Failed to evict entry %s", key)

    def get_stats(self) -> dict[str, Any]:
        """Get combined cache and eviction statistics.

        Returns:
            Combined statistics from cache backend and eviction policy

        """
        cache_stats = {}
        try:
            cache_stats = self.cache_backend.get_stats()
        except (OSError, ValueError, KeyError):
            self.logger.warning("Failed to get cache backend stats")

        eviction_stats: dict[str, Any] = {
            "eviction": self.eviction_policy.get_metrics(),
            "entry_count": len(self._entries),
            "max_capacity": self.eviction_policy.max_capacity,
        }

        return {**cache_stats, **eviction_stats}

    def get_entry_details(self) -> list[dict[str, Any]]:
        """Get detailed information about all cached entries.

        Returns:
            List of entry details for debugging and monitoring

        """
        return [
            {
                "key": entry.key,
                "age_seconds": entry.age_seconds(),
                "last_access_seconds": entry.last_access_seconds(),
                "access_count": entry.access_count,
                "priority": entry.priority,
                "is_expired": entry.is_expired(),
            }
            for entry in self._entries.values()
        ]


class EvictionCacheFactory:
    """Factory for creating eviction-enabled cache instances."""

    @classmethod
    def create_lru_cache(
        cls,
        cache_backend: CacheProtocol[Any],
        max_capacity: int = 1000,
        logger: logging.Logger | None = None,
    ) -> EvictingCacheWrapper[Any]:
        """Create cache with LRU eviction policy.

        Args:
            cache_backend: Cache implementation to wrap
            max_capacity: Maximum number of entries to maintain
            logger: Logger for cache events

        Returns:
            EvictingCacheWrapper with LRU eviction policy

        """
        eviction_policy: LRUEvictionPolicy[str, Any] = LRUEvictionPolicy(
            max_capacity=max_capacity,
            logger=logger,
        )

        return EvictingCacheWrapper(
            cache_backend=cache_backend,
            eviction_policy=eviction_policy,
            logger=logger,
        )

    @classmethod
    def create_priority_cache(
        cls,
        cache_backend: CacheProtocol[Any],
        max_capacity: int = 1000,
        default_priority: int = 0,
        logger: logging.Logger | None = None,
    ) -> EvictingCacheWrapper[Any]:
        """Create cache with priority-based eviction policy.

        Args:
            cache_backend: Cache implementation to wrap
            max_capacity: Maximum number of entries to maintain
            default_priority: Default priority for new entries
            logger: Logger for cache events

        Returns:
            EvictingCacheWrapper with priority-based eviction policy

        """
        eviction_policy: PriorityEvictionPolicy[str, Any] = PriorityEvictionPolicy(
            max_capacity=max_capacity,
            default_priority=default_priority,
            logger=logger,
        )

        return EvictingCacheWrapper(
            cache_backend=cache_backend,
            eviction_policy=eviction_policy,
            logger=logger,
        )


# Export public interfaces
__all__ = [
    "CacheEntry",
    "EvictingCacheWrapper",
    "EvictionCacheFactory",
    "EvictionMetrics",
    "EvictionPolicy",
    "EvictionReason",
    "LRUEvictionPolicy",
    "PriorityEvictionPolicy",
]
