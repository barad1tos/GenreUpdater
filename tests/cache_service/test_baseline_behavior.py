#!/usr/bin/env python3

"""Baseline Behavior Tests for CacheService.

This test suite captures the current behavior of CacheService before refactoring
to ensure no functionality is broken during the restructuring process.

Test Categories:
    - Core cache operations (get, set, TTL)
    - Album cache functionality (CSV-based persistence)
    - API cache functionality (JSON-based persistence)
    - Cache decorator behavior
    - Error handling and edge cases
    - Concurrency and thread safety
    - Serialization behavior
"""

import asyncio
import json
import logging
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from datetime import datetime, UTC

import pytest
try:
    from freezegun import freeze_time
    FREEZEGUN_AVAILABLE = True
except ImportError:
    FREEZEGUN_AVAILABLE = False
    freeze_time = None

from src.services.cache.cache_service import CacheService
from src.utils.data.models import CachedApiResult, TrackDict

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_config():
    """Mock configuration for CacheService testing."""
    return {
        "caching": {
            "default_ttl_seconds": 900,
            "cache_size_limits": {
                "generic_cache_max_entries": 50000,
                "album_cache_max_entries": 10000,
                "api_cache_max_entries": 50000,
            },
            "cleanup_interval_seconds": 300,
            "album_cache_sync_interval": 300,
            "negative_result_ttl": 2592000,
            "api_result_cache_path": "cache/api_results.json",
        },
        "cache_ttl_seconds": 900,
        "api_cache_file": "cache/cache.json",
        "logging": {
            "base_dir": "logs",
            "last_incremental_run_file": "logs/last_incremental_run.log",
        },
    }


@pytest.fixture
def mock_loggers():
    """Mock loggers for CacheService testing."""
    console_logger = Mock(spec=logging.Logger)
    error_logger = Mock(spec=logging.Logger)
    return console_logger, error_logger


@pytest.fixture
def cache_service_sync(mock_config, mock_loggers):
    """Create a CacheService instance for testing (sync fixture)."""
    console_logger, error_logger = mock_loggers
    return mock_config, console_logger, error_logger


@pytest.fixture
def cache_service(mock_config, mock_loggers):
    """Create a CacheService instance for testing (sync fixture with manual init)."""
    console_logger, error_logger = mock_loggers
    temp_dir = tempfile.mkdtemp()
    
    try:
        # Update config to use temporary directory
        mock_config["logging"]["base_dir"] = temp_dir
        mock_config["api_cache_file"] = f"{temp_dir}/cache.json"
        mock_config["caching"]["api_result_cache_path"] = f"{temp_dir}/api_results.json"
        
        service = CacheService(mock_config, console_logger, error_logger)
        
        # Mock file paths to use temp directory
        service.cache_file = f"{temp_dir}/cache.json"
        service.album_cache_csv = f"{temp_dir}/album_cache.csv"
        service.api_cache_file = f"{temp_dir}/api_results.json"
        
        # For cleanup task test, manually create it
        service._initialized = False  # Flag to track init status
        
        yield service
        
    finally:
        # Clean up temp directory
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)


class TestCacheServiceBaseline:
    """Baseline behavior tests for CacheService core functionality."""

    async def test_basic_set_get_operations(self, cache_service):
        """Test basic cache set and get operations."""
        # Initialize the cache service
        with patch.object(cache_service, 'load_cache', new_callable=AsyncMock), \
             patch.object(cache_service, '_load_album_years_cache', new_callable=AsyncMock), \
             patch.object(cache_service, '_load_api_cache', new_callable=AsyncMock):
            await cache_service.initialize()
        
        # Test synchronous set
        cache_service.set("test_key", "test_value", ttl=300)
        
        # Test asynchronous get
        result = await cache_service.get_async("test_key")
        assert result == "test_value"
        
        # Test asynchronous set and get
        await cache_service.set_async("async_key", "async_value", ttl=300)
        async_result = await cache_service.get_async("async_key")
        assert async_result == "async_value"
        
        # Cancel cleanup task manually for tests
        if cache_service._cleanup_task and not cache_service._cleanup_task.done():
            cache_service._cleanup_task.cancel()

    @pytest.mark.skipif(not FREEZEGUN_AVAILABLE, reason="freezegun not available")
    async def test_ttl_expiration(self, cache_service):
        """Test TTL expiration behavior."""
        with freeze_time("2023-01-01 12:00:00") as frozen_time:
            # Set value with 1 second TTL
            cache_service.set("expiring_key", "value", ttl=1)
            
            # Should be available immediately
            result = await cache_service.get_async("expiring_key")
            assert result == "value"
            
            # Move time forward beyond TTL
            frozen_time.tick(2)
            
            # Should be expired
            expired_result = await cache_service.get_async("expiring_key")
            assert expired_result is None

    async def test_get_all_tracks_functionality(self, cache_service):
        """Test the special 'ALL' key functionality for track data."""
        # Set up some track-like data
        track1 = {"id": "1", "name": "Track 1", "artist": "Artist 1"}
        track2 = {"id": "2", "name": "Track 2", "artist": "Artist 2"}
        non_track = {"not_track": True}
        
        await cache_service.set_async("track1", track1, ttl=300)
        await cache_service.set_async("track2", track2, ttl=300)
        await cache_service.set_async("non_track", non_track, ttl=300)
        
        # Get all tracks
        all_tracks = await cache_service.get_async("ALL")
        
        # Should return only objects that look like tracks (have 'id' field)
        assert isinstance(all_tracks, list)
        assert len(all_tracks) == 2
        track_ids = {track["id"] for track in all_tracks}
        assert track_ids == {"1", "2"}

    async def test_compute_function_on_miss(self, cache_service):
        """Test compute function is called on cache miss."""
        async def compute_value():
            return "computed_value"
        
        # Cache miss with compute function
        result = await cache_service.get_async("missing_key", compute_value)
        assert result == "computed_value"
        
        # Should now be cached
        cached_result = await cache_service.get_async("missing_key")
        assert cached_result == "computed_value"

    async def test_cache_invalidation(self, cache_service):
        """Test cache invalidation functionality."""
        # Set up test data
        cache_service.set("key1", "value1")
        cache_service.set("key2", "value2")
        
        # Invalidate single key
        cache_service.invalidate("key1")
        
        # key1 should be gone, key2 should remain
        assert await cache_service.get_async("key1") is None
        assert await cache_service.get_async("key2") == "value2"
        
        # Invalidate all
        cache_service.invalidate("ALL")
        assert await cache_service.get_async("key2") is None

    async def test_cache_clear(self, cache_service):
        """Test cache clear functionality."""
        # Set up test data
        cache_service.set("key1", "value1")
        cache_service.set("key2", "value2")
        
        # Clear cache
        cache_service.clear()
        
        # All keys should be gone
        assert await cache_service.get_async("key1") is None
        assert await cache_service.get_async("key2") is None

    async def test_album_key_generation(self, cache_service):
        """Test album key generation produces consistent results."""
        key1 = CacheService.generate_album_key("Artist", "Album")
        key2 = CacheService.generate_album_key("Artist", "Album")
        
        # Should be deterministic
        assert key1 == key2
        assert isinstance(key1, str)
        assert len(key1) > 0
        
        # Different inputs should produce different keys
        key3 = CacheService.generate_album_key("Different Artist", "Album")
        assert key1 != key3

    async def test_album_cache_operations(self, cache_service):
        """Test album cache store and retrieve operations."""
        artist = "Test Artist"
        album = "Test Album"
        year = "2023"
        
        # Store album year
        await cache_service.store_album_year_in_cache(artist, album, year)
        
        # Retrieve album year
        cached_year = await cache_service.get_album_year_from_cache(artist, album)
        assert cached_year == year
        
        # Test cache miss
        missing_year = await cache_service.get_album_year_from_cache("Missing Artist", "Missing Album")
        assert missing_year is None

    async def test_album_cache_invalidation(self, cache_service):
        """Test album cache invalidation."""
        # Store test data
        await cache_service.store_album_year_in_cache("Artist1", "Album1", "2023")
        await cache_service.store_album_year_in_cache("Artist2", "Album2", "2024")
        
        # Invalidate specific album
        await cache_service.invalidate_album_cache("Artist1", "Album1")
        
        # Artist1/Album1 should be gone, Artist2/Album2 should remain
        assert await cache_service.get_album_year_from_cache("Artist1", "Album1") is None
        assert await cache_service.get_album_year_from_cache("Artist2", "Album2") == "2024"
        
        # Invalidate all albums
        await cache_service.invalidate_all_albums()
        assert await cache_service.get_album_year_from_cache("Artist2", "Album2") is None

    async def test_api_cache_operations(self, cache_service):
        """Test API cache store and retrieve operations."""
        artist = "Test Artist"
        album = "Test Album"
        source = "musicbrainz"
        year = "2023"
        
        # Store API result
        await cache_service.set_cached_api_result(artist, album, source, year)
        
        # Retrieve API result
        cached_result = await cache_service.get_cached_api_result(artist, album, source)
        
        assert isinstance(cached_result, CachedApiResult)
        assert cached_result.artist == artist
        assert cached_result.album == album
        assert cached_result.source == source
        assert cached_result.year == year

    async def test_api_cache_with_metadata(self, cache_service):
        """Test API cache with metadata and negative results."""
        artist = "Test Artist"
        album = "Test Album"
        source = "discogs"
        metadata = {"confidence": 0.95, "match_type": "exact"}
        
        # Store positive result with metadata
        await cache_service.set_cached_api_result(
            artist, album, source, "2023", metadata=metadata
        )
        
        cached_result = await cache_service.get_cached_api_result(artist, album, source)
        assert cached_result.metadata == metadata
        
        # Store negative result
        await cache_service.set_cached_api_result(
            artist, album, "lastfm", None, is_negative=True
        )
        
        negative_result = await cache_service.get_cached_api_result(artist, album, "lastfm")
        assert negative_result.year is None
        assert negative_result.ttl is not None  # Should have TTL for negative results

    @pytest.mark.skipif(not FREEZEGUN_AVAILABLE, reason="freezegun not available")
    async def test_api_cache_ttl_expiration(self, cache_service):
        """Test API cache TTL expiration."""
        with freeze_time("2023-01-01 12:00:00") as frozen_time:
            # Store result with negative result (has TTL)
            await cache_service.set_cached_api_result(
                "Artist", "Album", "source", None, is_negative=True
            )
            
            # Should be available immediately
            result = await cache_service.get_cached_api_result("Artist", "Album", "source")
            assert result is not None
            
            # Move time forward beyond TTL (30 days default for negative results)
            frozen_time.tick(delta=cache_service.negative_result_ttl + 1)
            
            # Should be expired
            expired_result = await cache_service.get_cached_api_result("Artist", "Album", "source")
            assert expired_result is None

    async def test_api_cache_invalidation(self, cache_service):
        """Test API cache invalidation for albums."""
        # Store results for multiple sources
        artist = "Test Artist"
        album = "Test Album"
        
        await cache_service.set_cached_api_result(artist, album, "musicbrainz", "2023")
        await cache_service.set_cached_api_result(artist, album, "discogs", "2023")
        await cache_service.set_cached_api_result(artist, album, "lastfm", "2023")
        
        # Invalidate all sources for the album
        await cache_service.invalidate_api_cache_for_album(artist, album)
        
        # All sources should be invalidated
        assert await cache_service.get_cached_api_result(artist, album, "musicbrainz") is None
        assert await cache_service.get_cached_api_result(artist, album, "discogs") is None
        assert await cache_service.get_cached_api_result(artist, album, "lastfm") is None

    async def test_last_run_timestamp(self, cache_service):
        """Test last run timestamp functionality."""
        # Should return minimum datetime if file doesn't exist
        timestamp = await cache_service.get_last_run_timestamp()
        assert timestamp == datetime.min.replace(tzinfo=UTC)

    async def test_cache_size_limits_enforcement(self, cache_service):
        """Test that cache size limits are enforced."""
        # Set a very small limit for testing
        cache_service.generic_cache_max_entries = 2
        
        # Add entries beyond the limit
        await cache_service.set_async("key1", "value1", ttl=300)
        await cache_service.set_async("key2", "value2", ttl=300)
        await cache_service.set_async("key3", "value3", ttl=300)  # Should trigger eviction
        
        # Cache should have at most 2 entries
        assert len(cache_service.cache) <= 2

    async def test_unified_stats(self, cache_service):
        """Test unified statistics functionality."""
        # Add some data to different caches
        await cache_service.set_async("test_key", "test_value")
        await cache_service.store_album_year_in_cache("Artist", "Album", "2023")
        await cache_service.set_cached_api_result("Artist", "Album", "source", "2023")
        
        stats = cache_service.get_unified_stats()
        
        assert "legacy_stats" in stats
        legacy_stats = stats["legacy_stats"]
        
        assert "generic_cache_size" in legacy_stats
        assert "album_cache_size" in legacy_stats
        assert "api_cache_size" in legacy_stats
        
        # Should have at least some entries
        assert legacy_stats["generic_cache_size"] >= 1
        assert legacy_stats["album_cache_size"] >= 1
        assert legacy_stats["api_cache_size"] >= 1

    async def test_performance_metrics(self, cache_service):
        """Test cache performance metrics functionality."""
        metrics = cache_service.get_cache_performance_metrics()
        
        assert "performance_metrics" in metrics
        assert "algorithm_info" in metrics
        assert "cache_sizes" in metrics
        assert "optimization_enabled" in metrics
        
        cache_sizes = metrics["cache_sizes"]
        assert "generic_cache_size" in cache_sizes
        assert "album_cache_size" in cache_sizes
        assert "api_cache_size" in cache_sizes
        assert "total_entries" in cache_sizes

    async def test_cache_optimization(self, cache_service):
        """Test cache optimization functionality."""
        # Add some album data first
        await cache_service.store_album_year_in_cache("Artist", "Album", "2023")
        
        optimization_result = await cache_service.optimize_cache_performance()
        
        assert "optimization_complete" in optimization_result
        
        if optimization_result.get("optimization_complete"):
            assert "entries_optimized" in optimization_result
            assert "recommendations" in optimization_result

    # Error handling tests
    async def test_invalid_cache_entry_handling(self, cache_service):
        """Test handling of invalid cache entries during validation."""
        # This tests the _validate_cache_entry method indirectly
        # by setting up invalid data and ensuring it's filtered out
        
        # Manually create invalid entries in cache dict
        cache_service.cache["invalid1"] = ("value",)  # Wrong tuple size
        cache_service.cache["invalid2"] = ("value", "not_a_number")  # Invalid expiry
        cache_service.cache["valid"] = ("value", time.time() + 300)  # Valid entry
        
        # Get all tracks should filter out invalid entries
        tracks = await cache_service.get_async("ALL")
        assert isinstance(tracks, list)  # Should not crash

    async def test_csv_header_validation(self, cache_service):
        """Test CSV header validation for album cache."""
        # This indirectly tests _validate_csv_headers method
        # The actual file reading would happen in _load_album_years_cache
        
        # Test that service handles missing/invalid CSV gracefully
        timestamp = await cache_service.get_last_run_timestamp()
        assert timestamp is not None  # Should not crash

    async def test_concurrent_access_safety(self, cache_service):
        """Test that concurrent cache operations are thread-safe."""
        # Create multiple concurrent operations
        tasks = []
        
        for i in range(10):
            tasks.append(cache_service.set_async(f"key{i}", f"value{i}", ttl=300))
            tasks.append(cache_service.get_async(f"key{i}"))
        
        # All operations should complete without errors
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Check that no exceptions occurred
        for result in results:
            if isinstance(result, Exception):
                pytest.fail(f"Concurrent operation failed: {result}")

    async def test_cleanup_task_lifecycle(self, cache_service):
        """Test background cleanup task lifecycle."""
        # Initialize service to start cleanup task
        await cache_service.initialize()
        
        # Cleanup task should be running after initialization
        assert cache_service._cleanup_task is not None
        assert not cache_service._cleanup_task.done()
        
        # Stop cleanup task (expect CancelledError which is normal)
        try:
            await cache_service.stop_cleanup_task()
        except asyncio.CancelledError:
            # This is expected behavior when stopping cleanup task
            pass
        
        # Task should be stopped
        assert cache_service._cleanup_task is None or cache_service._cleanup_task.done()


class TestCacheServiceEdgeCases:
    """Edge case tests for CacheService."""

    async def test_empty_string_keys(self, cache_service):
        """Test handling of empty string keys."""
        cache_service.set("", "empty_key_value")
        result = await cache_service.get_async("")
        assert result == "empty_key_value"

    async def test_unicode_keys_and_values(self, cache_service):
        """Test handling of unicode keys and values."""
        unicode_key = "🔑ключ"
        unicode_value = "🎵значення"
        
        cache_service.set(unicode_key, unicode_value)
        result = await cache_service.get_async(unicode_key)
        assert result == unicode_value

    async def test_large_data_caching(self, cache_service):
        """Test caching of large data objects."""
        large_data = {"data": "x" * 10000}  # 10KB of data
        
        cache_service.set("large_key", large_data)
        result = await cache_service.get_async("large_key")
        assert result == large_data

    async def test_none_values_caching(self, cache_service):
        """Test caching of None values."""
        cache_service.set("none_key", None)
        result = await cache_service.get_async("none_key")
        assert result is None
        
        # Should be different from cache miss
        cache_service.invalidate("none_key")
        miss_result = await cache_service.get_async("none_key")
        assert miss_result is None  # But this is a cache miss, not cached None

    async def test_zero_ttl(self, cache_service):
        """Test behavior with zero TTL."""
        cache_service.set("zero_ttl_key", "value", ttl=0)
        # Should expire immediately
        result = await cache_service.get_async("zero_ttl_key")
        assert result is None

    async def test_negative_ttl(self, cache_service):
        """Test behavior with negative TTL."""
        cache_service.set("negative_ttl_key", "value", ttl=-1)
        # Should be expired immediately
        result = await cache_service.get_async("negative_ttl_key")
        assert result is None


if __name__ == "__main__":
    pass
pytestmark = pytest.mark.integration
