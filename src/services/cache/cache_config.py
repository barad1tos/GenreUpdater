"""Smart Cache Configuration with Event-Driven Invalidation.

This module provides intelligent cache configuration that understands the lifecycle
of media library tracks and optimizes caching strategies accordingly.

Key Features:
- Event-driven invalidation for media library tracks
- Content-type aware TTL management
- Integration with fingerprint-based change detection
- Lifecycle management for persistent vs temporary cache entries
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any


class CacheContentType(Enum):
    """Types of cached content with different lifecycle requirements."""

    TRACK_METADATA = "track_metadata"  # Media library tracks - persistent until removed
    SUCCESSFUL_API_METADATA = "successful_api_metadata"  # API successful responses - persistent
    FAILED_API_LOOKUP = "failed_api_lookup"  # Failed API calls - short retry TTL
    ALBUM_YEAR = "album_year"  # Album release years - semi-permanent
    NEGATIVE_RESULT = "negative_result"  # Failed lookups - long-term caching
    GENERIC = "generic"  # General purpose cache - medium TTL


class InvalidationStrategy(Enum):
    """Cache invalidation strategies."""

    TIME_BASED = "time_based"  # Standard TTL expiration
    EVENT_DRIVEN = "event_driven"  # Invalidate on specific events
    FINGERPRINT_BASED = "fingerprint_based"  # Invalidate when content fingerprint changes
    HYBRID = "hybrid"  # Combine time + event/fingerprint


@dataclass
class CachePolicy:
    """Configuration for a specific type of cached content."""

    content_type: CacheContentType
    ttl_seconds: int
    invalidation_strategy: InvalidationStrategy
    max_size_mb: int | None = None
    cleanup_threshold: float = 0.8  # Cleanup when cache reaches 80% of max_size
    description: str = ""


class SmartCacheConfig:
    """Smart cache configuration with content-aware policies."""

    # Time constants
    MINUTE = 60
    HOUR = 60 * MINUTE
    DAY = 24 * HOUR
    WEEK = 7 * DAY
    MONTH = 30 * DAY

    # Infinite TTL for persistent content (represented as very large number)
    INFINITE_TTL = 365 * DAY * 10  # 10 years - effectively infinite

    # Default TTL for negative results (failed lookups) - 30 days
    DEFAULT_NEGATIVE_RESULT_TTL = MONTH

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize smart cache configuration.

        Args:
            config: Optional application config dict to read cache settings from
        """
        self.logger = logging.getLogger(__name__)
        self._config = config or {}
        self._policies = self._create_default_policies()

    def _get_negative_result_ttl(self) -> int:
        """Get TTL for negative results from config or use default.

        Returns:
            TTL in seconds for caching failed lookups
        """
        caching_config = self._config.get("caching", {})
        value = caching_config.get("negative_result_ttl", self.DEFAULT_NEGATIVE_RESULT_TTL)
        try:
            return int(value)
        except (TypeError, ValueError):
            self.logger.warning(
                "Invalid negative_result_ttl %r, using default %d",
                value,
                self.DEFAULT_NEGATIVE_RESULT_TTL,
            )
            return self.DEFAULT_NEGATIVE_RESULT_TTL

    def _create_default_policies(self) -> dict[CacheContentType, CachePolicy]:
        """Create default cache policies for different content types."""
        return {
            CacheContentType.TRACK_METADATA: CachePolicy(
                content_type=CacheContentType.TRACK_METADATA,
                ttl_seconds=self.INFINITE_TTL,
                invalidation_strategy=InvalidationStrategy.EVENT_DRIVEN,
                max_size_mb=100,
                description="Media library tracks - persistent until track is removed or modified",
            ),
            CacheContentType.SUCCESSFUL_API_METADATA: CachePolicy(
                content_type=CacheContentType.SUCCESSFUL_API_METADATA,
                ttl_seconds=self.INFINITE_TTL,
                invalidation_strategy=InvalidationStrategy.EVENT_DRIVEN,
                max_size_mb=30,
                description="API metadata - persistent until track removed (immutable data)",
            ),
            CacheContentType.FAILED_API_LOOKUP: CachePolicy(
                content_type=CacheContentType.FAILED_API_LOOKUP,
                ttl_seconds=1 * self.HOUR,  # Retry failed lookups after 1 hour
                invalidation_strategy=InvalidationStrategy.TIME_BASED,
                max_size_mb=5,
                description="Failed API lookups - short TTL for retry opportunities",
            ),
            CacheContentType.ALBUM_YEAR: CachePolicy(
                content_type=CacheContentType.ALBUM_YEAR,
                ttl_seconds=self.MONTH,
                invalidation_strategy=InvalidationStrategy.HYBRID,
                max_size_mb=20,
                description="Album release years - semi-permanent with occasional refresh",
            ),
            CacheContentType.NEGATIVE_RESULT: CachePolicy(
                content_type=CacheContentType.NEGATIVE_RESULT,
                ttl_seconds=self._get_negative_result_ttl(),
                invalidation_strategy=InvalidationStrategy.TIME_BASED,
                max_size_mb=10,
                description="Failed lookups - long-term cache to avoid repeated failures",
            ),
            CacheContentType.GENERIC: CachePolicy(
                content_type=CacheContentType.GENERIC,
                ttl_seconds=5 * self.MINUTE,  # 300s - current L1 cache default
                invalidation_strategy=InvalidationStrategy.TIME_BASED,
                max_size_mb=30,
                description="General purpose cache - short TTL for safety",
            ),
        }

    def get_policy(self, content_type: CacheContentType) -> CachePolicy:
        """Get cache policy for specific content type.

        Args:
            content_type: Type of cached content

        Returns:
            Cache policy with TTL and invalidation strategy
        """
        return self._policies[content_type]

    def get_ttl(self, content_type: CacheContentType) -> int:
        """Get TTL in seconds for specific content type.

        Args:
            content_type: Type of cached content

        Returns:
            TTL in seconds
        """
        return self._policies[content_type].ttl_seconds

    def should_use_fingerprint_validation(self, content_type: CacheContentType) -> bool:
        """Check if content type should use fingerprint-based validation.

        Args:
            content_type: Type of cached content

        Returns:
            True if fingerprint validation should be used
        """
        policy = self._policies[content_type]
        return policy.invalidation_strategy in [InvalidationStrategy.FINGERPRINT_BASED, InvalidationStrategy.HYBRID]

    def should_use_event_invalidation(self, content_type: CacheContentType) -> bool:
        """Check if content type should use event-driven invalidation.

        Args:
            content_type: Type of cached content

        Returns:
            True if event-driven invalidation should be used
        """
        policy = self._policies[content_type]
        return policy.invalidation_strategy in [InvalidationStrategy.EVENT_DRIVEN, InvalidationStrategy.HYBRID]

    @staticmethod
    def is_persistent_cache(content_type: CacheContentType) -> bool:
        """Check if content type represents persistent cache data.

        Persistent cache should survive application restarts and
        only be invalidated by specific events.

        Args:
            content_type: Type of cached content

        Returns:
            True if cache should be persistent
        """
        return content_type in [CacheContentType.TRACK_METADATA, CacheContentType.SUCCESSFUL_API_METADATA, CacheContentType.ALBUM_YEAR]

    def get_cleanup_threshold(self, content_type: CacheContentType) -> float:
        """Get cleanup threshold for cache size management.

        Args:
            content_type: Type of cached content

        Returns:
            Threshold as ratio (0.0 - 1.0) when cleanup should trigger
        """
        return self._policies[content_type].cleanup_threshold

    def update_policy(self, content_type: CacheContentType, **kwargs: Any) -> None:
        """Update cache policy for specific content type.

        Args:
            content_type: Type of cached content
            **kwargs: Policy attributes to update
        """
        policy = self._policies[content_type]

        for key, value in kwargs.items():
            if hasattr(policy, key):
                setattr(policy, key, value)
                self.logger.info("Updated %s policy: %s=%s", content_type.value, key, value)
            else:
                msg = f"Unknown policy attribute: {key}"
                raise AttributeError(msg)

    def get_all_policies(self) -> dict[CacheContentType, CachePolicy]:
        """Get all configured cache policies.

        Returns:
            Dictionary mapping content types to their policies
        """
        return self._policies.copy()

    def log_configuration(self) -> None:
        """Log current cache configuration for debugging."""
        self.logger.info("=== Smart Cache Configuration ===")

        for content_type, policy in self._policies.items():
            ttl_str = self._format_ttl(policy.ttl_seconds)
            self.logger.info(
                "%s | TTL: %s | Strategy: %s | Max: %sMB",
                f"{content_type.value:20}",
                f"{ttl_str:12}",
                f"{policy.invalidation_strategy.value:15}",
                f"{policy.max_size_mb or 'N/A':3}",
            )

    def _format_ttl(self, seconds: int) -> str:
        """Format TTL seconds into human-readable string.

        Args:
            seconds: TTL in seconds

        Returns:
            Human-readable TTL string
        """
        if seconds < 0:
            return "expired"
        if seconds >= self.INFINITE_TTL:
            return "∞ (infinite)"
        if seconds >= self.DAY:
            return f"{seconds // self.DAY}d"
        if seconds >= self.HOUR:
            return f"{seconds // self.HOUR}h"
        if seconds >= self.MINUTE:
            return f"{seconds // self.MINUTE}m"
        return f"{seconds}s"


# Global configuration instance
cache_config = SmartCacheConfig()


class CacheEventType(Enum):
    """Types of cache invalidation events."""

    TRACK_ADDED = "track_added"
    TRACK_REMOVED = "track_removed"
    TRACK_MODIFIED = "track_modified"
    FINGERPRINT_CHANGED = "fingerprint_changed"
    LIBRARY_SYNC = "library_sync"
    MANUAL_INVALIDATION = "manual_invalidation"


@dataclass
class CacheEvent:
    """Cache event for event-driven invalidation."""

    event_type: CacheEventType
    track_id: str | None = None
    fingerprint: str | None = None
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        """Validate event data."""
        if self.event_type in [CacheEventType.TRACK_ADDED, CacheEventType.TRACK_REMOVED, CacheEventType.TRACK_MODIFIED] and not self.track_id:
            msg = f"track_id required for {self.event_type.value}"
            raise ValueError(msg)
        if self.event_type == CacheEventType.FINGERPRINT_CHANGED and not self.fingerprint:
            msg = "fingerprint required for fingerprint_changed event"
            raise ValueError(msg)


class EventDrivenCacheManager:
    """Manages event-driven cache invalidation."""

    def __init__(self, config: SmartCacheConfig) -> None:
        """Initialize event-driven cache manager.

        Args:
            config: Smart cache configuration instance
        """
        self.config = config
        self.logger = logging.getLogger(__name__)
        self._event_handlers: dict[CacheEventType, list[Callable[[CacheEvent], None]]] = {}

    def register_event_handler(self, event_type: CacheEventType, handler: Callable[[CacheEvent], None]) -> None:
        """Register event handler for specific event type.

        Args:
            event_type: Type of cache event
            handler: Callable to handle the event
        """
        if event_type not in self._event_handlers:
            self._event_handlers[event_type] = []

        self._event_handlers[event_type].append(handler)
        self.logger.debug("Registered handler for %s", event_type.value)

    def emit_event(self, event: CacheEvent) -> None:
        """Emit cache event to trigger invalidation.

        Args:
            event: Cache event to emit
        """
        self.logger.info("Cache event: %s %s", event.event_type.value, event.track_id or "")

        handlers = self._event_handlers.get(event.event_type, [])
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                # Catch all exceptions to ensure remaining handlers still execute
                self.logger.exception("Event handler %r failed while processing event %r", handler, event)

    def should_invalidate_for_event(self, content_type: CacheContentType, event: CacheEvent) -> bool:
        """Check if cache should be invalidated for given event.

        Args:
            content_type: Type of cached content
            event: Cache event that occurred

        Returns:
            True if cache should be invalidated
        """
        # Manual invalidation affects all content types that support events
        if event.event_type == CacheEventType.MANUAL_INVALIDATION:
            return self.config.should_use_event_invalidation(content_type)

        if not self.config.should_use_event_invalidation(content_type):
            return False

        # Track metadata cache should invalidate for track events
        if content_type == CacheContentType.TRACK_METADATA:
            return event.event_type in [CacheEventType.TRACK_REMOVED, CacheEventType.TRACK_MODIFIED, CacheEventType.FINGERPRINT_CHANGED]

        # API metadata cache should invalidate when tracks are removed/modified
        if content_type == CacheContentType.SUCCESSFUL_API_METADATA:
            return event.event_type in [
                CacheEventType.TRACK_REMOVED,  # Track removed → invalidate API data
                CacheEventType.TRACK_MODIFIED,  # Track metadata changed → may need refresh
            ]

        # Library sync may affect album years
        return content_type == CacheContentType.ALBUM_YEAR and event.event_type == CacheEventType.LIBRARY_SYNC
