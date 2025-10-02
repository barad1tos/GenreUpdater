"""Tests for Smart Cache Configuration."""

import pytest
from src.infrastructure.cache.cache_config import (
    CacheContentType,
    CacheEvent,
    CacheEventType,
    CachePolicy,
    EventDrivenCacheManager,
    InvalidationStrategy,
    SmartCacheConfig,
)


class TestSmartCacheConfig:
    """Test cases for SmartCacheConfig."""

    def setup_method(self) -> None:
        """Set up test instance."""
        self.config = SmartCacheConfig()

    def test_default_policies_creation(self) -> None:
        """Test that default policies are created correctly."""
        policies = self.config.get_all_policies()

        # Check all content types have policies
        expected_types = [
            CacheContentType.TRACK_METADATA,
            CacheContentType.SUCCESSFUL_API_METADATA,
            CacheContentType.FAILED_API_LOOKUP,
            CacheContentType.ALBUM_YEAR,
            CacheContentType.NEGATIVE_RESULT,
            CacheContentType.GENERIC,
        ]

        assert len(policies) == len(expected_types)
        for content_type in expected_types:
            assert content_type in policies
            assert isinstance(policies[content_type], CachePolicy)

    def test_track_metadata_policy(self) -> None:
        """Test track metadata policy configuration."""
        policy = self.config.get_policy(CacheContentType.TRACK_METADATA)

        assert policy.ttl_seconds == self.config.INFINITE_TTL
        assert policy.invalidation_strategy == InvalidationStrategy.EVENT_DRIVEN
        assert policy.max_size_mb == 100

    def test_successful_api_metadata_policy(self) -> None:
        """Test successful API metadata policy configuration."""
        policy = self.config.get_policy(CacheContentType.SUCCESSFUL_API_METADATA)

        assert policy.ttl_seconds == self.config.INFINITE_TTL
        assert policy.invalidation_strategy == InvalidationStrategy.EVENT_DRIVEN
        assert policy.max_size_mb == 30

    def test_failed_api_lookup_policy(self) -> None:
        """Test failed API lookup policy configuration."""
        policy = self.config.get_policy(CacheContentType.FAILED_API_LOOKUP)

        assert policy.ttl_seconds == 1 * 60 * 60  # 1 hour
        assert policy.invalidation_strategy == InvalidationStrategy.TIME_BASED
        assert policy.max_size_mb == 5

    def test_negative_result_policy(self) -> None:
        """Test negative result policy matches current system."""
        policy = self.config.get_policy(CacheContentType.NEGATIVE_RESULT)

        # Should match current 2592000s (30 days)
        assert policy.ttl_seconds == 30 * 24 * 60 * 60
        assert policy.invalidation_strategy == InvalidationStrategy.TIME_BASED

    def test_get_ttl(self) -> None:
        """Test TTL getter method."""
        # Infinite TTL for tracks
        track_ttl = self.config.get_ttl(CacheContentType.TRACK_METADATA)
        assert track_ttl == self.config.INFINITE_TTL

        # Infinite TTL for successful API metadata
        api_ttl = self.config.get_ttl(CacheContentType.SUCCESSFUL_API_METADATA)
        assert api_ttl == self.config.INFINITE_TTL

        # Short TTL for failed API lookups
        failed_ttl = self.config.get_ttl(CacheContentType.FAILED_API_LOOKUP)
        assert failed_ttl == 1 * 60 * 60  # 1 hour

    def test_should_use_fingerprint_validation(self) -> None:
        """Test fingerprint validation detection."""
        # Track metadata uses event-driven (no fingerprint by default)
        assert not self.config.should_use_fingerprint_validation(CacheContentType.TRACK_METADATA)

        # Album year uses hybrid (includes fingerprint)
        assert self.config.should_use_fingerprint_validation(CacheContentType.ALBUM_YEAR)

        # Successful API metadata uses event-driven (no fingerprint)
        assert not self.config.should_use_fingerprint_validation(CacheContentType.SUCCESSFUL_API_METADATA)

        # Failed API lookup uses time-based (no fingerprint)
        assert not self.config.should_use_fingerprint_validation(CacheContentType.FAILED_API_LOOKUP)

    def test_should_use_event_invalidation(self) -> None:
        """Test event invalidation detection."""
        # Track metadata uses event-driven
        assert self.config.should_use_event_invalidation(CacheContentType.TRACK_METADATA)

        # Album year uses hybrid (includes events)
        assert self.config.should_use_event_invalidation(CacheContentType.ALBUM_YEAR)

        # Successful API metadata uses event-driven
        assert self.config.should_use_event_invalidation(CacheContentType.SUCCESSFUL_API_METADATA)

        # Failed API lookup uses time-based (no events)
        assert not self.config.should_use_event_invalidation(CacheContentType.FAILED_API_LOOKUP)

    def test_is_persistent_cache(self) -> None:
        """Test persistent cache detection."""
        # Track metadata should be persistent
        assert self.config.is_persistent_cache(CacheContentType.TRACK_METADATA)

        # Album years should be persistent
        assert self.config.is_persistent_cache(CacheContentType.ALBUM_YEAR)

        # Successful API metadata should be persistent
        assert self.config.is_persistent_cache(CacheContentType.SUCCESSFUL_API_METADATA)

        # Failed API lookups should not be persistent
        assert not self.config.is_persistent_cache(CacheContentType.FAILED_API_LOOKUP)

        # Generic cache should not be persistent
        assert not self.config.is_persistent_cache(CacheContentType.GENERIC)

    def test_update_policy(self) -> None:
        """Test policy updating."""
        original_ttl = self.config.get_ttl(CacheContentType.GENERIC)

        # Update TTL
        new_ttl = 600  # 10 minutes
        self.config.update_policy(CacheContentType.GENERIC, ttl_seconds=new_ttl)

        # Check update took effect
        updated_ttl = self.config.get_ttl(CacheContentType.GENERIC)
        assert updated_ttl == new_ttl
        assert updated_ttl != original_ttl

    def test_ttl_formatting(self) -> None:
        """Test TTL formatting for human readability."""
        # Note: _format_ttl is a private method, but we test it to ensure
        # the formatting logic works correctly for logging purposes
        # Testing private method is acceptable in unit tests
        assert "∞" in self.config._format_ttl(self.config.INFINITE_TTL)  # noqa: SLF001
        assert self.config._format_ttl(24 * 60 * 60) == "1d"  # noqa: SLF001
        assert self.config._format_ttl(60 * 60) == "1h"  # noqa: SLF001
        assert self.config._format_ttl(60) == "1m"  # noqa: SLF001
        assert self.config._format_ttl(30) == "30s"  # noqa: SLF001

    def test_cleanup_threshold(self) -> None:
        """Test cleanup threshold getter."""
        threshold = self.config.get_cleanup_threshold(CacheContentType.TRACK_METADATA)
        assert 0.0 <= threshold <= 1.0
        assert threshold == 0.8  # Default value


class TestCacheEvent:
    """Test cases for CacheEvent."""

    def test_track_event_creation(self) -> None:
        """Test creation of track-related events."""
        event = CacheEvent(event_type=CacheEventType.TRACK_ADDED, track_id="track123")

        assert event.event_type == CacheEventType.TRACK_ADDED
        assert event.track_id == "track123"

    def test_track_event_requires_track_id(self) -> None:
        """Test that track events require track_id."""
        with pytest.raises(ValueError, match="track_id required"):
            CacheEvent(event_type=CacheEventType.TRACK_REMOVED)

    def test_fingerprint_event_creation(self) -> None:
        """Test creation of fingerprint change events."""
        event = CacheEvent(event_type=CacheEventType.FINGERPRINT_CHANGED, fingerprint="abc123def456")

        assert event.event_type == CacheEventType.FINGERPRINT_CHANGED
        assert event.fingerprint == "abc123def456"

    def test_fingerprint_event_requires_fingerprint(self) -> None:
        """Test that fingerprint events require fingerprint."""
        with pytest.raises(ValueError, match="fingerprint required"):
            CacheEvent(event_type=CacheEventType.FINGERPRINT_CHANGED)

    def test_library_sync_event(self) -> None:
        """Test library sync event creation."""
        event = CacheEvent(event_type=CacheEventType.LIBRARY_SYNC)

        assert event.event_type == CacheEventType.LIBRARY_SYNC
        assert event.track_id is None
        assert event.fingerprint is None


class TestEventDrivenCacheManager:
    """Test cases for EventDrivenCacheManager."""

    def setup_method(self) -> None:
        """Set up test instances."""
        self.config = SmartCacheConfig()
        self.manager = EventDrivenCacheManager(self.config)

    def test_event_handler_registration(self) -> None:
        """Test event handler registration."""
        handler_calls = []

        def test_handler(evt: CacheEvent) -> None:
            handler_calls.append(evt)

        # Register handler
        self.manager.register_event_handler(CacheEventType.TRACK_ADDED, test_handler)

        # Emit event
        event = CacheEvent(event_type=CacheEventType.TRACK_ADDED, track_id="track123")
        self.manager.emit_event(event)

        # Check handler was called
        assert len(handler_calls) == 1
        assert handler_calls[0] == event

    def test_multiple_handlers(self) -> None:
        """Test multiple handlers for same event."""
        call_count = 0

        def handler1(_event: CacheEvent) -> None:
            """First test handler that increments counter."""
            nonlocal call_count
            call_count += 1

        def handler2(_event: CacheEvent) -> None:
            """Second test handler that increments counter."""
            nonlocal call_count
            call_count += 1

        # Register multiple handlers
        self.manager.register_event_handler(CacheEventType.TRACK_REMOVED, handler1)
        self.manager.register_event_handler(CacheEventType.TRACK_REMOVED, handler2)

        # Emit event
        event = CacheEvent(event_type=CacheEventType.TRACK_REMOVED, track_id="track456")
        self.manager.emit_event(event)

        # Both handlers should be called
        assert call_count == 2

    def test_should_invalidate_track_metadata(self) -> None:
        """Test invalidation logic for track metadata."""
        # Track removed should invalidate track metadata
        event = CacheEvent(event_type=CacheEventType.TRACK_REMOVED, track_id="track123")
        assert self.manager.should_invalidate_for_event(CacheContentType.TRACK_METADATA, event)

        # Track modified should invalidate track metadata
        event = CacheEvent(event_type=CacheEventType.TRACK_MODIFIED, track_id="track123")
        assert self.manager.should_invalidate_for_event(CacheContentType.TRACK_METADATA, event)

        # Track added should NOT invalidate track metadata (cache miss is fine)
        event = CacheEvent(event_type=CacheEventType.TRACK_ADDED, track_id="track123")
        assert not self.manager.should_invalidate_for_event(CacheContentType.TRACK_METADATA, event)

    def test_should_not_invalidate_time_based_content(self) -> None:
        """Test that time-based content ignores events."""
        event = CacheEvent(event_type=CacheEventType.TRACK_REMOVED, track_id="track123")

        # Failed API lookups use time-based invalidation, should ignore events
        assert not self.manager.should_invalidate_for_event(CacheContentType.FAILED_API_LOOKUP, event)

    def test_manual_invalidation_affects_all(self) -> None:
        """Test that manual invalidation affects all content types."""
        event = CacheEvent(event_type=CacheEventType.MANUAL_INVALIDATION)

        # Should affect all content types
        for content_type in CacheContentType:
            # Only if they support event invalidation
            expected = self.config.should_use_event_invalidation(content_type)
            result = self.manager.should_invalidate_for_event(content_type, event)
            assert result == expected
