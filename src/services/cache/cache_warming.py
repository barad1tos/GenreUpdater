"""Cache warming implementation for Music Genre Updater.

This module implements intelligent cache warming strategies with priority-based
warming, background tasks, and comprehensive progress tracking
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from collections.abc import Awaitable, Callable

from src.services.cache.cache_protocol import CacheProtocol


class WarmingPriority(Enum):
    """Cache warming priority levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class WarmingStatus(Enum):
    """Cache warming operation status."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _default_dependencies() -> list[str]:
    """Create the default dependencies list with proper typing."""
    return []


@dataclass
class WarmingItem:
    """Single cache warming item configuration."""

    key: str
    value_generator: Callable[[], Awaitable[str | dict[str, Any] | list[Any] | float | bool]]
    priority: WarmingPriority = WarmingPriority.MEDIUM
    ttl: int | None = None
    retry_count: int = 3
    timeout_seconds: float = 30.0
    dependencies: list[str] = field(default_factory=_default_dependencies)


@dataclass
class WarmingProgress:
    """Cache warming progress tracking."""

    total_items: int = 0
    completed_items: int = 0
    failed_items: int = 0
    cancelled_items: int = 0
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    current_item: str | None = None

    def get_completion_percentage(self) -> float:
        """Calculate completion percentage.

        Returns:
            Completion percentage (0.0-100.0)

        """
        if self.total_items == 0:
            return 100.0
        return (self.completed_items / self.total_items) * 100

    def get_duration_seconds(self) -> float:
        """Get warming duration in seconds.

        Returns:
            Duration in seconds (ongoing if not finished)

        """
        end = self.end_time or time.time()
        return end - self.start_time

    def is_finished(self) -> bool:
        """Check if the warming operation is finished.

        Returns:
            True if all items processed (completed/failed/canceled)

        """
        return (self.completed_items + self.failed_items + self.cancelled_items) >= self.total_items

    def to_dict(self) -> dict[str, Any]:
        """Convert progress to dictionary format.

        Returns:
            Dictionary containing warming progress data

        """
        return {
            "total_items": self.total_items,
            "completed_items": self.completed_items,
            "failed_items": self.failed_items,
            "cancelled_items": self.cancelled_items,
            "completion_percentage": self.get_completion_percentage(),
            "duration_seconds": self.get_duration_seconds(),
            "current_item": self.current_item,
            "is_finished": self.is_finished(),
        }


class WarmingStrategy(ABC):
    """Abstract base class for cache warming strategies."""

    @abstractmethod
    async def warm_items(
        self,
        cache: CacheProtocol[Any],
        items: list[WarmingItem],
        progress_callback: Callable[[WarmingProgress], None] | None = None,
    ) -> WarmingProgress:
        """Execute warming strategy for provided items.

        Args:
            cache: Cache backend to warm
            items: Items to warm
            progress_callback: Optional progress tracking callback

        Returns:
            Final warming progress

        """


class SequentialWarmingStrategy(WarmingStrategy):
    """Sequential cache warming strategy.

    Processes warming items one by one in priority order.
    """

    def __init__(self, logger: logging.Logger | None = None) -> None:
        """Initialize sequential warming strategy.

        Args:
            logger: Logger for warming events

        """
        self.logger = logger or logging.getLogger(__name__)

    async def warm_items(
        self,
        cache: CacheProtocol[Any],
        items: list[WarmingItem],
        progress_callback: Callable[[WarmingProgress], None] | None = None,
    ) -> WarmingProgress:
        """Execute sequential warming strategy.

        Args:
            cache: Cache backend to warm
            items: Items to warm in priority order
            progress_callback: Optional progress tracking callback

        Returns:
            Final warming progress

        """
        # Sort by priority (critical -> high -> medium -> low)
        priority_order = {
            WarmingPriority.CRITICAL: 0,
            WarmingPriority.HIGH: 1,
            WarmingPriority.MEDIUM: 2,
            WarmingPriority.LOW: 3,
        }
        sorted_items = sorted(items, key=lambda x: priority_order[x.priority])

        progress = WarmingProgress(total_items=len(sorted_items))

        for item in sorted_items:
            progress.current_item = item.key

            try:
                self.logger.debug("Warming cache key: %s (priority: %s)", item.key, item.priority.value)

                # Generate value with timeout
                value = await asyncio.wait_for(item.value_generator(), timeout=item.timeout_seconds)

                # Store in cache
                await cache.set(item.key, value, item.ttl)
                progress.completed_items += 1

                self.logger.info("Successfully warmed cache key: %s", item.key)

            except TimeoutError:
                progress.failed_items += 1
                self.logger.warning("Timeout warming cache key: %s", item.key)
            except (ValueError, TypeError, RuntimeError):
                progress.failed_items += 1
                self.logger.exception("Failed to warm cache key %s", item.key)

            # Report progress
            if progress_callback:
                progress_callback(progress)

        progress.end_time = time.time()
        progress.current_item = None
        return progress


class ParallelWarmingStrategy(WarmingStrategy):
    """Parallel cache warming strategy.

    Processes warming items concurrently with configurable limits.
    """

    def __init__(
        self,
        max_concurrency: int = 5,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize parallel warming strategy.

        Args:
            max_concurrency: Maximum concurrent warming operations
            logger: Logger for warming events

        """
        self.max_concurrency = max_concurrency
        self.logger = logger or logging.getLogger(__name__)

    async def warm_items(
        self,
        cache: CacheProtocol[Any],
        items: list[WarmingItem],
        progress_callback: Callable[[WarmingProgress], None] | None = None,
    ) -> WarmingProgress:
        """Execute parallel warming strategy.

        Args:
            cache: Cache backend to warm
            items: Items to warm concurrently
            progress_callback: Optional progress tracking callback

        Returns:
            Final warming progress

        """
        progress = WarmingProgress(total_items=len(items))
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def warm_single_item(item: WarmingItem) -> None:
            """Warm a single cache item with semaphore control."""
            async with semaphore:
                progress.current_item = item.key

                try:
                    self.logger.debug("Warming cache key: %s (priority: %s)", item.key, item.priority.value)

                    # Generate value with timeout
                    value = await asyncio.wait_for(item.value_generator(), timeout=item.timeout_seconds)

                    # Store in cache
                    await cache.set(item.key, value, item.ttl)
                    progress.completed_items += 1

                    self.logger.info("Successfully warmed cache key: %s", item.key)

                except TimeoutError:
                    progress.failed_items += 1
                    self.logger.warning("Timeout warming cache key: %s", item.key)
                except (ValueError, TypeError, RuntimeError):
                    progress.failed_items += 1
                    self.logger.exception("Failed to warm cache key %s", item.key)

                # Report progress
                if progress_callback:
                    progress_callback(progress)

        # Execute all warming tasks concurrently
        tasks = [warm_single_item(item) for item in items]
        await asyncio.gather(*tasks, return_exceptions=True)

        progress.end_time = time.time()
        progress.current_item = None
        return progress


class PriorityWarmingStrategy(WarmingStrategy):
    """Priority-based cache warming strategy.

    Processes critical and high priority items first, then medium and low
    priority items in parallel batches.
    """

    def __init__(
        self,
        critical_concurrency: int = 1,
        high_concurrency: int = 2,
        *,
        medium_concurrency: int = 3,
        low_concurrency: int = 5,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize priority warming strategy.

        Args:
            critical_concurrency: Concurrency for critical items
            high_concurrency: Concurrency for high priority items
            medium_concurrency: Concurrency for medium priority items
            low_concurrency: Concurrency for low priority items
            logger: Logger for warming events

        """
        self.concurrency_map = {
            WarmingPriority.CRITICAL: critical_concurrency,
            WarmingPriority.HIGH: high_concurrency,
            WarmingPriority.MEDIUM: medium_concurrency,
            WarmingPriority.LOW: low_concurrency,
        }
        self.logger = logger or logging.getLogger(__name__)

    async def warm_items(
        self,
        cache: CacheProtocol[Any],
        items: list[WarmingItem],
        progress_callback: Callable[[WarmingProgress], None] | None = None,
    ) -> WarmingProgress:
        """Execute priority-based warming strategy.

        Args:
            cache: Cache backend to warm
            items: Items to warm by priority
            progress_callback: Optional progress tracking callback

        Returns:
            Final warming progress

        """
        progress = WarmingProgress(total_items=len(items))

        # Group items by priority
        priority_groups: dict[WarmingPriority, list[WarmingItem]] = {priority: [] for priority in WarmingPriority}

        for item in items:
            priority_groups[item.priority].append(item)

        # Process each priority group in order
        for priority in [WarmingPriority.CRITICAL, WarmingPriority.HIGH, WarmingPriority.MEDIUM, WarmingPriority.LOW]:
            priority_items = priority_groups[priority]
            if not priority_items:
                continue

            concurrency = self.concurrency_map[priority]
            self.logger.info(
                "Processing %d %s priority items (concurrency: %d)", len(priority_items), priority.value, concurrency
            )

            # Use parallel strategy for this priority group
            parallel_strategy = ParallelWarmingStrategy(max_concurrency=concurrency, logger=self.logger)

            group_progress = await parallel_strategy.warm_items(cache, priority_items, progress_callback)

            # Update overall progress
            progress.completed_items += group_progress.completed_items
            progress.failed_items += group_progress.failed_items
            progress.cancelled_items += group_progress.cancelled_items

        progress.end_time = time.time()
        progress.current_item = None
        return progress


class CacheWarmer:
    """Main cache warming orchestrator.

    Coordinates cache warming operations with configurable strategies,
    progress tracking, and background task management.
    """

    def __init__(
        self,
        cache_backend: CacheProtocol[Any],
        strategy: WarmingStrategy | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize cache warmer.

        Args:
            cache_backend: Cache implementation to warm
            strategy: Warming strategy to use
            logger: Logger for warming events

        """
        self.cache_backend = cache_backend
        self.strategy = strategy or SequentialWarmingStrategy()
        self.logger = logger or logging.getLogger(__name__)
        self.current_progress: WarmingProgress | None = None
        self.background_task: asyncio.Task[WarmingProgress] | None = None

    async def warm_cache(
        self,
        items: list[WarmingItem],
        progress_callback: Callable[[WarmingProgress], None] | None = None,
    ) -> WarmingProgress:
        """Execute cache warming with provided items.

        Args:
            items: Items to warm
            progress_callback: Optional progress tracking callback

        Returns:
            Final warming progress

        """
        self.logger.info("Starting cache warming with %d items", len(items))

        try:
            progress = await self.strategy.warm_items(self.cache_backend, items, progress_callback)

            self.logger.info(
                "Cache warming completed: %d/%d items successful (%.1f%% success rate)",
                progress.completed_items,
                progress.total_items,
                (progress.completed_items / progress.total_items) * 100 if progress.total_items > 0 else 0,
            )

        except (ValueError, TypeError, RuntimeError):
            self.logger.exception("Cache warming failed")
            raise

        return progress

    def start_background_warming(
        self,
        items: list[WarmingItem],
        progress_callback: Callable[[WarmingProgress], None] | None = None,
    ) -> asyncio.Task[WarmingProgress]:
        """Start cache warming as a background task.

        Args:
            items: Items to warm
            progress_callback: Optional progress tracking callback

        Returns:
            Background task handle

        Raises:
            RuntimeError: If background warming is already in progress

        """
        if self.background_task and not self.background_task.done():
            msg = "Background warming already in progress"
            raise RuntimeError(msg)

        self.logger.info("Starting background cache warming with %d items", len(items))

        self.background_task = asyncio.create_task(self.warm_cache(items, progress_callback), name="cache_warming")

        return self.background_task

    def get_warming_progress(self) -> WarmingProgress | None:
        """Get current warming progress.

        Returns:
            Current progress or None if no warming in progress

        """
        return self.current_progress

    def is_warming_active(self) -> bool:
        """Check if background warming is active.

        Returns:
            True if background warming is in progress

        """
        return bool(self.background_task and not self.background_task.done())

    async def cancel_warming(self) -> bool:
        """Cancel background warming operation.

        Returns:
            True if successfully canceled, False if no warming was active

        """
        if not self.background_task or self.background_task.done():
            return False

        self.logger.info("Cancelling background cache warming")
        self.background_task.cancel()

        try:
            await self.background_task
        except asyncio.CancelledError:
            self.logger.info("Background cache warming cancelled successfully")
            raise  # Re-raise CancelledError to maintain asyncio protocol

        return True

    async def wait_for_completion(self) -> WarmingProgress:
        """Wait for background warming to complete.

        Returns:
            Final warming progress

        Raises:
            RuntimeError: If no background warming is active

        Note:
            Use asyncio.wait_for() or asyncio.timeout() context manager
            for timeout functionality:

            # Using asyncio.wait_for:
            result = await asyncio.wait_for(warmer.wait_for_completion(), timeout=30)

            # Using asyncio.timeout (Python 3.11+):
            async with asyncio.timeout(30):
                result = await warmer.wait_for_completion()

        """
        if not self.background_task:
            msg = "No background warming task active"
            raise RuntimeError(msg)

        return await self.background_task


class WarmingCacheFactory:
    """Factory for creating cache warming instances."""

    @classmethod
    def create_sequential_warmer(
        cls,
        cache_backend: CacheProtocol[Any],
        logger: logging.Logger | None = None,
    ) -> CacheWarmer:
        """Create cache warmer with sequential strategy.

        Args:
            cache_backend: Cache implementation to warm
            logger: Logger for warming events

        Returns:
            CacheWarmer configured with sequential strategy

        """
        strategy = SequentialWarmingStrategy(logger=logger)
        return CacheWarmer(
            cache_backend=cache_backend,
            strategy=strategy,
            logger=logger,
        )

    @classmethod
    def create_parallel_warmer(
        cls,
        cache_backend: CacheProtocol[Any],
        max_concurrency: int = 5,
        logger: logging.Logger | None = None,
    ) -> CacheWarmer:
        """Create cache warmer with parallel strategy.

        Args:
            cache_backend: Cache implementation to warm
            max_concurrency: Maximum concurrent warming operations
            logger: Logger for warming events

        Returns:
            CacheWarmer configured with parallel strategy

        """
        strategy = ParallelWarmingStrategy(max_concurrency=max_concurrency, logger=logger)
        return CacheWarmer(
            cache_backend=cache_backend,
            strategy=strategy,
            logger=logger,
        )

    @classmethod
    def create_priority_warmer(
        cls,
        cache_backend: CacheProtocol[Any],
        critical_concurrency: int = 1,
        high_concurrency: int = 2,
        *,
        medium_concurrency: int = 3,
        low_concurrency: int = 5,
        logger: logging.Logger | None = None,
    ) -> CacheWarmer:
        """Create cache warmer with priority-based strategy.

        Args:
            cache_backend: Cache implementation to warm
            critical_concurrency: Concurrency for critical items
            high_concurrency: Concurrency for high priority items
            medium_concurrency: Concurrency for medium priority items
            low_concurrency: Concurrency for low priority items
            logger: Logger for warming events

        Returns:
            CacheWarmer configured with priority-based strategy

        """
        strategy = PriorityWarmingStrategy(
            critical_concurrency=critical_concurrency,
            high_concurrency=high_concurrency,
            medium_concurrency=medium_concurrency,
            low_concurrency=low_concurrency,
            logger=logger,
        )
        return CacheWarmer(
            cache_backend=cache_backend,
            strategy=strategy,
            logger=logger,
        )


# Export public interfaces
__all__ = [
    "CacheWarmer",
    "ParallelWarmingStrategy",
    "PriorityWarmingStrategy",
    "SequentialWarmingStrategy",
    "WarmingCacheFactory",
    "WarmingItem",
    "WarmingPriority",
    "WarmingProgress",
    "WarmingStatus",
    "WarmingStrategy",
]
