"""Cache metrics implementation for Music Genre Updater.

This module implements comprehensive metrics collection for cache operations
including hit/miss rates, latency monitoring, and performance analytics.
"""

import logging
import time
from collections import deque, defaultdict
from dataclasses import dataclass, field
from enum import Enum
from statistics import mean, median
from typing import Any

from src.services.cache.cache_protocol import CacheProtocol


class MetricType(Enum):
    """Types of cache metrics."""

    HIT = "hit"
    MISS = "miss"
    SET = "set"
    INVALIDATE = "invalidate"
    CLEANUP = "cleanup"
    ERROR = "error"


@dataclass
class LatencyMetric:
    """Single latency measurement."""

    operation: MetricType
    duration_ms: float
    timestamp: float
    key: str | None = None
    success: bool = True


@dataclass
class CacheMetrics:
    """Comprehensive cache metrics tracking."""

    # Operation counters
    hits: int = 0
    misses: int = 0
    sets: int = 0
    invalidations: int = 0
    cleanups: int = 0
    errors: int = 0

    # Size tracking
    current_size: int = 0
    max_size_seen: int = 0

    # Latency tracking (circular buffer)
    latency_samples: deque[LatencyMetric] = field(default_factory=lambda: deque(maxlen=1000))

    # Operation frequency by hour (last 24 hours)
    hourly_operations: dict[int, dict[MetricType, int]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(int))
    )

    # Error tracking by type
    error_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def record_hit(self, key: str, duration_ms: float) -> None:
        """Record cache hit with latency.

        Args:
            key: Cache key that was hit
            duration_ms: Operation duration in milliseconds

        """
        self.hits += 1
        self._record_latency(MetricType.HIT, duration_ms, key)
        self._record_hourly_operation(MetricType.HIT)

    def record_miss(self, key: str, duration_ms: float) -> None:
        """Record cache miss with latency.

        Args:
            key: Cache key that was missed
            duration_ms: Operation duration in milliseconds

        """
        self.misses += 1
        self._record_latency(MetricType.MISS, duration_ms, key)
        self._record_hourly_operation(MetricType.MISS)

    def record_set(self, key: str, duration_ms: float, success: bool = True) -> None:
        """Record cache set operation.

        Args:
            key: Cache key that was set
            duration_ms: Operation duration in milliseconds
            success: Whether the operation succeeded

        """
        self.sets += 1
        if not success:
            self.errors += 1
        self._record_latency(MetricType.SET, duration_ms, key, success)
        self._record_hourly_operation(MetricType.SET)

    def record_invalidation(self, count: int, duration_ms: float, success: bool = True) -> None:
        """Record cache invalidation operation.

        Args:
            count: Number of keys invalidated
            duration_ms: Operation duration in milliseconds
            success: Whether the operation succeeded

        """
        self.invalidations += count
        if not success:
            self.errors += 1
        self._record_latency(MetricType.INVALIDATE, duration_ms, success=success)
        self._record_hourly_operation(MetricType.INVALIDATE)

    def record_cleanup(self, count: int, duration_ms: float, success: bool = True) -> None:
        """Record cache cleanup operation.

        Args:
            count: Number of entries cleaned
            duration_ms: Operation duration in milliseconds
            success: Whether the operation succeeded

        """
        self.cleanups += count
        if not success:
            self.errors += 1
        self._record_latency(MetricType.CLEANUP, duration_ms, success=success)
        self._record_hourly_operation(MetricType.CLEANUP)

    def record_error(self, error_type: str, operation: MetricType, duration_ms: float | None = None) -> None:
        """Record cache error.

        Args:
            error_type: Type/name of the error
            operation: Operation that failed
            duration_ms: Operation duration in milliseconds (if available)

        """
        self.errors += 1
        self.error_counts[error_type] += 1
        if duration_ms is not None:
            self._record_latency(operation, duration_ms, success=False)
        self._record_hourly_operation(MetricType.ERROR)

    def update_size(self, new_size: int) -> None:
        """Update the current cache size.

        Args:
            new_size: New cache size

        """
        self.current_size = new_size
        self.max_size_seen = max(self.max_size_seen, new_size)

    def get_hit_ratio(self) -> float:
        """Calculate cache hit ratio.

        Returns:
            A hit ratio as float between 0.0 and 1.0

        """
        total_requests = self.hits + self.misses
        return self.hits / total_requests if total_requests > 0 else 0.0

    def get_error_rate(self) -> float:
        """Calculate error rate.

        Returns:
            An error rate as float between 0.0 and 1.0

        """
        total_operations = self.hits + self.misses + self.sets + self.invalidations + self.cleanups
        return self.errors / total_operations if total_operations > 0 else 0.0

    def get_average_latency(self, operation: MetricType | None = None) -> float:
        """Calculate average latency for operations.

        Args:
            operation: Specific operation type (None for all operations)

        Returns:
            Average latency in milliseconds

        """
        relevant_samples = [
            sample.duration_ms for sample in self.latency_samples if operation is None or sample.operation == operation
        ]

        return mean(relevant_samples) if relevant_samples else 0.0

    def get_median_latency(self, operation: MetricType | None = None) -> float:
        """Calculate median latency for operations.

        Args:
            operation: Specific operation type (None for all operations)

        Returns:
            Median latency in milliseconds

        """
        relevant_samples = [
            sample.duration_ms for sample in self.latency_samples if operation is None or sample.operation == operation
        ]

        return median(relevant_samples) if relevant_samples else 0.0

    def get_p95_latency(self, operation: MetricType | None = None) -> float:
        """Calculate the 95th percentile latency for operations.

        Args:
            operation: Specific operation type (None for all operations)

        Returns:
            95th percentile latency in milliseconds

        """
        relevant_samples = [
            sample.duration_ms for sample in self.latency_samples if operation is None or sample.operation == operation
        ]

        if not relevant_samples:
            return 0.0

        sorted_samples = sorted(relevant_samples)
        index = int(0.95 * len(sorted_samples))
        return sorted_samples[min(index, len(sorted_samples) - 1)]

    def get_operations_per_hour(self, hours_back: int = 24) -> dict[MetricType, float]:
        """Get operations per hour for recent time period.

        Args:
            hours_back: How many hours back to analyze

        Returns:
            Dictionary with average operations per hour by type

        """
        current_hour = int(time.time() // 3600)
        totals: dict[MetricType, int] = defaultdict(int)

        for hour_offset in range(hours_back):
            hour = current_hour - hour_offset
            hour_data = self.hourly_operations.get(hour, {})
            for metric_type, count in hour_data.items():
                totals[metric_type] += count

        return {metric_type: count / hours_back for metric_type, count in totals.items()}

    def _record_latency(
        self, operation: MetricType, duration_ms: float, key: str | None = None, success: bool = True
    ) -> None:
        """Record latency measurement.

        Args:
            operation: Type of operation
            duration_ms: Duration in milliseconds
            key: Cache key (if applicable)
            success: Whether operation succeeded

        """
        metric = LatencyMetric(
            operation=operation,
            duration_ms=duration_ms,
            timestamp=time.time(),
            key=key,
            success=success,
        )
        self.latency_samples.append(metric)

    def _record_hourly_operation(self, operation: MetricType) -> None:
        """Record operation in hourly tracking.

        Args:
            operation: Type of operation to record

        """
        current_hour = int(time.time() // 3600)
        self.hourly_operations[current_hour][operation] += 1

    def to_dict(self) -> dict[str, Any]:
        """Convert metrics to dictionary format.

        Returns:
            Dictionary containing all cache metrics

        """
        return {
            # Basic counters
            "hits": self.hits,
            "misses": self.misses,
            "sets": self.sets,
            "invalidations": self.invalidations,
            "cleanups": self.cleanups,
            "errors": self.errors,
            # Derived metrics
            "hit_ratio": self.get_hit_ratio(),
            "error_rate": self.get_error_rate(),
            "total_requests": self.hits + self.misses,
            "total_operations": self.hits + self.misses + self.sets + self.invalidations + self.cleanups,
            # Size metrics
            "current_size": self.current_size,
            "max_size_seen": self.max_size_seen,
            # Latency metrics
            "average_latency_ms": self.get_average_latency(),
            "median_latency_ms": self.get_median_latency(),
            "p95_latency_ms": self.get_p95_latency(),
            # Per-operation latencies
            "latency_by_operation": {
                operation.value: {
                    "avg_ms": self.get_average_latency(operation),
                    "median_ms": self.get_median_latency(operation),
                    "p95_ms": self.get_p95_latency(operation),
                }
                for operation in MetricType
            },
            # Error breakdown
            "error_counts": dict(self.error_counts),
            # Operations per hour
            "operations_per_hour": {
                metric_type.value: rate for metric_type, rate in self.get_operations_per_hour().items()
            },
        }


class MetricsCollectingCacheWrapper:
    """Cache wrapper that collects comprehensive metrics.

    This wrapper transparently adds metrics collection to any CacheProtocol
    implementation, tracking performance and usage patterns.
    """

    def __init__(
        self,
        cache_backend: CacheProtocol[Any],
        logger: logging.Logger | None = None,
        metrics_enabled: bool = True,
    ) -> None:
        """Initialize metrics collecting cache wrapper.

        Args:
            cache_backend: Underlying cache implementation
            logger: Logger for metrics events
            metrics_enabled: Whether to collect metrics

        """
        self.cache_backend = cache_backend
        self.logger = logger or logging.getLogger(__name__)
        self.metrics_enabled = metrics_enabled
        self.metrics = CacheMetrics()

    async def get(
        self, key: str, default: str | dict[str, Any] | list[Any] | float | bool | None = None
    ) -> str | dict[str, Any] | list[Any] | int | float | bool | None:
        """Get cached value with metrics collection.

        Args:
            key: Cache key to retrieve
            default: Default value if key not found

        Returns:
            Cached value or default if not found

        """
        if not self.metrics_enabled:
            return await self.cache_backend.get(key, default)

        start_time = time.perf_counter()

        try:
            result = await self.cache_backend.get(key, default)
            duration_ms = (time.perf_counter() - start_time) * 1000

            if result is not None and result is not default:
                self.metrics.record_hit(key, duration_ms)
                self.logger.debug("Cache hit for key %s (%.2fms)", key, duration_ms)
            else:
                self.metrics.record_miss(key, duration_ms)
                self.logger.debug("Cache miss for key %s (%.2fms)", key, duration_ms)

        except (OSError, ValueError, KeyError, TypeError) as error:
            duration_ms = (time.perf_counter() - start_time) * 1000
            self.metrics.record_error(type(error).__name__, MetricType.HIT, duration_ms)
            self.logger.exception("Cache get failed for key %s", key)
            raise

        return result

    async def set(
        self, key: str, value: str | dict[str, Any] | list[Any] | float | bool, ttl: int | None = None
    ) -> None:
        """Set cached value with metrics collection.

        Args:
            key: Cache key to store
            value: Value to cache
            ttl: Time-to-live in seconds

        """
        if not self.metrics_enabled:
            await self.cache_backend.set(key, value, ttl)
            return

        start_time = time.perf_counter()

        try:
            await self.cache_backend.set(key, value, ttl)
            duration_ms = (time.perf_counter() - start_time) * 1000

            self.metrics.record_set(key, duration_ms)
            self.logger.debug("Cache set for key %s (%.2fms)", key, duration_ms)

        except (OSError, ValueError, KeyError, TypeError) as error:
            duration_ms = (time.perf_counter() - start_time) * 1000
            self.metrics.record_set(key, duration_ms, success=False)
            self.metrics.record_error(type(error).__name__, MetricType.SET, duration_ms)
            self.logger.exception("Cache set failed for key %s", key)
            raise

    async def invalidate(self, key: str | list[str]) -> int:
        """Invalidate cache keys with metrics collection.

        Args:
            key: Single key or list of keys to invalidate

        Returns:
            Count of keys successfully invalidated

        """
        if not self.metrics_enabled:
            return await self.cache_backend.invalidate(key)

        start_time = time.perf_counter()
        key_count = 1 if isinstance(key, str) else len(key)

        try:
            result = await self.cache_backend.invalidate(key)
            duration_ms = (time.perf_counter() - start_time) * 1000

            self.metrics.record_invalidation(result, duration_ms)
            self.logger.debug("Cache invalidated %d keys (requested %d) in %.2fms", result, key_count, duration_ms)

        except (OSError, ValueError, KeyError, TypeError) as error:
            duration_ms = (time.perf_counter() - start_time) * 1000
            self.metrics.record_invalidation(key_count, duration_ms, success=False)
            self.metrics.record_error(type(error).__name__, MetricType.INVALIDATE, duration_ms)
            self.logger.exception("Cache invalidation failed for keys %s", key)
            raise

        return result

    async def cleanup(self) -> int:
        """Clean cache with metrics collection.

        Returns:
            Count of entries cleaned

        """
        if not self.metrics_enabled:
            return await self.cache_backend.cleanup()

        start_time = time.perf_counter()

        try:
            result = await self.cache_backend.cleanup()
            duration_ms = (time.perf_counter() - start_time) * 1000

            self.metrics.record_cleanup(result, duration_ms)
            self.logger.info("Cache cleaned %d entries in %.2fms", result, duration_ms)

        except (OSError, ValueError, KeyError, TypeError) as error:
            duration_ms = (time.perf_counter() - start_time) * 1000
            self.metrics.record_cleanup(0, duration_ms, success=False)
            self.metrics.record_error(type(error).__name__, MetricType.CLEANUP, duration_ms)
            self.logger.exception("Cache cleanup failed")
            raise

        return result

    def get_stats(self) -> dict[str, Any]:
        """Get combined cache and metrics statistics.

        Returns:
            Combined statistics from cache backend and metrics

        """
        # Get base cache stats
        cache_stats = {}
        try:
            cache_stats = self.cache_backend.get_stats()

            # Update metrics with current cache size if available
            if "size" in cache_stats:
                self.metrics.update_size(cache_stats["size"])

        except (OSError, ValueError, KeyError, TypeError) as error:
            self.logger.warning("Failed to get cache backend stats: %s", error)

        # Combine with metrics
        metrics_stats = {"metrics": self.metrics.to_dict()}

        return {**cache_stats, **metrics_stats}

    def reset_metrics(self) -> None:
        """Reset all metrics."""
        self.metrics = CacheMetrics()
        self.logger.info("Cache metrics reset")

    def enable_metrics(self) -> None:
        """Enable metrics collection."""
        self.metrics_enabled = True
        self.logger.info("Cache metrics enabled")

    def disable_metrics(self) -> None:
        """Disable metrics collection."""
        self.metrics_enabled = False
        self.logger.info("Cache metrics disabled")


class MetricsCacheFactory:
    """Factory for creating metrics-enabled cache instances."""

    @classmethod
    def create_metrics_cache(
        cls,
        cache_backend: CacheProtocol[Any],
        logger: logging.Logger | None = None,
        metrics_enabled: bool = True,
    ) -> MetricsCollectingCacheWrapper:
        """Create cache with metrics collection.

        Args:
            cache_backend: Cache implementation to wrap
            logger: Logger for metrics events
            metrics_enabled: Whether to enable metrics collection initially

        Returns:
            MetricsCollectingCacheWrapper with comprehensive metrics

        """
        return MetricsCollectingCacheWrapper(
            cache_backend=cache_backend,
            logger=logger,
            metrics_enabled=metrics_enabled,
        )


# Export public interfaces
__all__ = [
    "CacheMetrics",
    "LatencyMetric",
    "MetricType",
    "MetricsCacheFactory",
    "MetricsCollectingCacheWrapper",
]
