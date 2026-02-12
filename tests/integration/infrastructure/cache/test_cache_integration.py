"""Integration tests for Cache Subsystem.

These tests verify the integration between:
- Album cache service (AlbumCacheService)
- API response cache service (ApiCacheService)
- Generic cache service (GenericCacheService)
- Fingerprint generation (FingerprintGenerator)
- Hash service (UnifiedHashService)

Tests use real cache operations but with mock configurations.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from services.cache.album_cache import AlbumCacheService
from services.cache.api_cache import ApiCacheService
from services.cache.generic_cache import GenericCacheService
from services.cache.fingerprint import FingerprintGenerator, FingerprintGenerationError
from services.cache.hash_service import UnifiedHashService
from tests.factories import create_test_app_config

if TYPE_CHECKING:
    from core.models.track_models import AppConfig


@pytest.fixture
def mock_config() -> AppConfig:
    """Create a mock configuration for cache tests."""
    return create_test_app_config(
        logs_base_dir="/tmp/test_cache",
        cache_ttl_seconds=3600,
        max_generic_entries=100,
    )


@pytest.fixture
def mock_logger() -> logging.Logger:
    """Create a mock logger for cache tests."""
    return MagicMock(spec=logging.Logger)


class TestAlbumCacheServiceIntegration:
    """Integration tests for album cache service operations."""

    @pytest.mark.asyncio
    async def test_album_cache_store_and_retrieve(
        self,
        mock_config: AppConfig,
        mock_logger: logging.Logger,
    ) -> None:
        """Test basic store and retrieve operations."""
        cache = AlbumCacheService(mock_config, mock_logger)

        test_cases = [
            ("The Beatles", "Abbey Road", "1969", 95),
            ("Pink Floyd", "The Dark Side of the Moon", "1973", 92),
            ("Led Zeppelin", "IV", "1971", 88),
        ]

        # Store all entries
        for artist, album, year, confidence in test_cases:
            await cache.store_album_year(
                artist=artist,
                album=album,
                year=year,
                confidence=confidence,
            )

        # Retrieve and verify
        for artist, album, year, _confidence in test_cases:
            result = await cache.get_album_year(artist=artist, album=album)
            assert result is not None, f"Cache miss for {artist} - {album}"
            assert result == year

    @pytest.mark.asyncio
    async def test_album_cache_entry_with_confidence(
        self,
        mock_config: AppConfig,
        mock_logger: logging.Logger,
    ) -> None:
        """Test that confidence scores are properly stored and retrieved."""
        cache = AlbumCacheService(mock_config, mock_logger)

        await cache.store_album_year(
            artist="Test Artist",
            album="Test Album",
            year="2020",
            confidence=85,
        )

        # Get full entry to check confidence
        entry = await cache.get_album_year_entry(artist="Test Artist", album="Test Album")
        assert entry is not None
        assert entry.year == "2020"
        assert entry.confidence == 85

    @pytest.mark.asyncio
    async def test_album_cache_invalidation(
        self,
        mock_config: AppConfig,
        mock_logger: logging.Logger,
    ) -> None:
        """Test cache invalidation for specific album."""
        cache = AlbumCacheService(mock_config, mock_logger)

        # Store entry
        await cache.store_album_year(
            artist="Artist",
            album="Album",
            year="2019",
            confidence=70,
        )

        # Verify stored
        result = await cache.get_album_year(artist="Artist", album="Album")
        assert result == "2019"

        # Invalidate
        await cache.invalidate_album(artist="Artist", album="Album")

        # Verify gone
        result = await cache.get_album_year(artist="Artist", album="Album")
        assert result is None

    @pytest.mark.asyncio
    async def test_album_cache_invalidate_all(
        self,
        mock_config: AppConfig,
        mock_logger: logging.Logger,
    ) -> None:
        """Test clearing all album cache entries."""
        cache = AlbumCacheService(mock_config, mock_logger)

        # Store multiple entries
        for i in range(5):
            await cache.store_album_year(
                artist=f"Artist {i}",
                album=f"Album {i}",
                year=str(2000 + i),
                confidence=80,
            )

        # Clear all
        await cache.invalidate_all()

        # Verify all cleared
        for i in range(5):
            result = await cache.get_album_year(artist=f"Artist {i}", album=f"Album {i}")
            assert result is None

    @pytest.mark.asyncio
    async def test_album_cache_concurrent_writes(
        self,
        mock_config: AppConfig,
        mock_logger: logging.Logger,
    ) -> None:
        """Test concurrent write operations don't corrupt cache."""
        cache = AlbumCacheService(mock_config, mock_logger)

        # Create concurrent write tasks
        async def write_entry(index: int) -> None:
            """Write a single cache entry for concurrency testing."""
            await cache.store_album_year(
                artist=f"Artist {index}",
                album=f"Album {index}",
                year=str(2000 + index),
                confidence=80 + index,
            )

        # Execute 20 concurrent writes
        await asyncio.gather(*[write_entry(i) for i in range(20)])

        # Verify all entries were stored correctly
        for i in range(20):
            result = await cache.get_album_year(
                artist=f"Artist {i}",
                album=f"Album {i}",
            )
            assert result is not None, f"Missing entry {i}"
            assert result == str(2000 + i)


class TestApiCacheServiceIntegration:
    """Integration tests for API response cache."""

    @pytest.mark.asyncio
    async def test_api_cache_store_and_retrieve(
        self,
        mock_config: AppConfig,
        mock_logger: logging.Logger,
    ) -> None:
        """Test basic API cache store and retrieve."""
        cache = ApiCacheService(mock_config, mock_logger)

        # Store API result
        await cache.set_cached_result(
            artist="Test Artist",
            album="Test Album",
            source="musicbrainz",
            success=True,
            data={"year": "2020", "score": 85},
            metadata={"query_time": 0.5},
        )

        # Retrieve and verify
        result = await cache.get_cached_result(
            artist="Test Artist",
            album="Test Album",
            source="musicbrainz",
        )
        assert result is not None
        assert result.year == "2020"
        assert result.source == "musicbrainz"

    @pytest.mark.asyncio
    async def test_api_cache_different_sources(
        self,
        mock_config: AppConfig,
        mock_logger: logging.Logger,
    ) -> None:
        """Test cache isolates different API sources."""
        cache = ApiCacheService(mock_config, mock_logger)

        # Store results from different sources for same artist/album
        await cache.set_cached_result(
            artist="Artist",
            album="Album",
            source="musicbrainz",
            success=True,
            data={"year": "2018"},
        )
        await cache.set_cached_result(
            artist="Artist",
            album="Album",
            source="discogs",
            success=True,
            data={"year": "2019"},
        )

        # Retrieve separately and verify isolation
        mb_result = await cache.get_cached_result(artist="Artist", album="Album", source="musicbrainz")
        discogs_result = await cache.get_cached_result(artist="Artist", album="Album", source="discogs")

        assert mb_result is not None
        assert discogs_result is not None
        assert mb_result.year == "2018"
        assert discogs_result.year == "2019"

    @pytest.mark.asyncio
    async def test_api_cache_failed_lookup(
        self,
        mock_config: AppConfig,
        mock_logger: logging.Logger,
    ) -> None:
        """Test caching failed API lookups."""
        cache = ApiCacheService(mock_config, mock_logger)

        # Store failed lookup (no year) - omit data parameter (defaults to None)
        await cache.set_cached_result(
            artist="Unknown Artist",
            album="Unknown Album",
            source="musicbrainz",
            success=False,
        )

        # Retrieve and verify
        result = await cache.get_cached_result(
            artist="Unknown Artist",
            album="Unknown Album",
            source="musicbrainz",
        )
        assert result is not None
        assert result.year is None

    @pytest.mark.asyncio
    async def test_api_cache_invalidate_for_album(
        self,
        mock_config: AppConfig,
        mock_logger: logging.Logger,
    ) -> None:
        """Test invalidating all API cache entries for an album."""
        cache = ApiCacheService(mock_config, mock_logger)

        # Store results from multiple sources
        for source in ["musicbrainz", "discogs", "lastfm"]:
            await cache.set_cached_result(
                artist="Artist",
                album="Album",
                source=source,
                success=True,
                data={"year": "2020"},
            )

        # Invalidate all entries for this album
        await cache.invalidate_for_album(artist="Artist", album="Album")

        # Verify all sources invalidated
        for source in ["musicbrainz", "discogs", "lastfm"]:
            result = await cache.get_cached_result(artist="Artist", album="Album", source=source)
            assert result is None


class TestGenericCacheServiceIntegration:
    """Integration tests for generic cache service."""

    @pytest.mark.asyncio
    async def test_generic_cache_basic_operations(
        self,
        mock_config: AppConfig,
        mock_logger: logging.Logger,
    ) -> None:
        """Test basic get/set operations."""
        cache = GenericCacheService(mock_config, mock_logger)

        # CacheableKey must be str | int
        key = "test:123"
        value = {"data": "test_value", "timestamp": time.time()}

        # Store
        cache.set(key, value)

        # Retrieve
        result = cache.get(key)
        assert result is not None
        assert isinstance(result, dict)
        assert result["data"] == "test_value"

    @pytest.mark.asyncio
    async def test_generic_cache_invalidation(
        self,
        mock_config: AppConfig,
        mock_logger: logging.Logger,
    ) -> None:
        """Test explicit cache entry invalidation."""
        cache = GenericCacheService(mock_config, mock_logger)

        key = "test_key"
        cache.set(key, {"value": 123})

        # Verify stored
        assert cache.get(key) is not None

        # Invalidate
        removed = cache.invalidate(key)
        assert removed is True

        # Verify gone
        assert cache.get(key) is None

    @pytest.mark.asyncio
    async def test_generic_cache_invalidate_all(
        self,
        mock_config: AppConfig,
        mock_logger: logging.Logger,
    ) -> None:
        """Test clearing all cache entries."""
        cache = GenericCacheService(mock_config, mock_logger)

        # Store multiple entries
        for i in range(5):
            cache.set(f"key_{i}", {"index": i})

        # Clear all
        cache.invalidate_all()

        # Verify all cleared
        for i in range(5):
            assert cache.get(f"key_{i}") is None

    @pytest.mark.asyncio
    async def test_generic_cache_lru_eviction(
        self,
        mock_config: AppConfig,
        mock_logger: logging.Logger,
    ) -> None:
        """Test LRU eviction when cache reaches max size."""
        # Create cache with small max size
        config = mock_config.model_copy(update={"max_generic_entries": 3})

        cache = GenericCacheService(config, mock_logger)

        # Fill cache to capacity
        cache.set("key_1", "value_1")
        cache.set("key_2", "value_2")
        cache.set("key_3", "value_3")

        # Access key_1 to make it recently used
        cache.get("key_1")

        # Add new entry - should evict LRU (key_2)
        cache.set("key_4", "value_4")

        # key_1 should still exist (recently accessed)
        assert cache.get("key_1") is not None
        # key_2 should be evicted (LRU)
        assert cache.get("key_2") is None
        # key_3 and key_4 should exist
        assert cache.get("key_3") is not None
        assert cache.get("key_4") is not None

    @pytest.mark.asyncio
    async def test_generic_cache_ttl_expiration(
        self,
        mock_config: AppConfig,
        mock_logger: logging.Logger,
    ) -> None:
        """Test TTL-based expiration."""
        cache = GenericCacheService(mock_config, mock_logger)

        # Store with very short TTL
        cache.set("expiring_key", "value", ttl=1)

        # Should be available immediately
        assert cache.get("expiring_key") is not None

        # Wait for expiration
        await asyncio.sleep(1.1)

        # Should be expired now
        assert cache.get("expiring_key") is None


class TestFingerprintGeneratorIntegration:
    """Integration tests for fingerprint generation."""

    @pytest.fixture
    def generator(self) -> FingerprintGenerator:
        """Create a FingerprintGenerator instance for testing."""
        return FingerprintGenerator()

    def test_fingerprint_consistency(self, generator: FingerprintGenerator) -> None:
        """Test that same input produces same fingerprint."""
        track_data = {
            "persistent_id": "ABC123DEF456",
            "location": "/Users/test/Music/song.mp3",
            "file_size": 5242880,
            "duration": 240.5,
            "date_modified": "2024-01-01 10:00:00",
            "date_added": "2024-01-01 09:00:00",
        }

        fp1 = generator.generate_track_fingerprint(track_data)
        fp2 = generator.generate_track_fingerprint(track_data)

        assert fp1 == fp2, "Same track should produce same fingerprint"

    def test_fingerprint_changes_with_content(self, generator: FingerprintGenerator) -> None:
        """Test that fingerprint changes when track content changes."""
        track1 = {
            "persistent_id": "ABC123DEF456",
            "location": "/Users/test/Music/song.mp3",
            "file_size": 5242880,
            "duration": 240.5,
        }

        track2 = {
            "persistent_id": "ABC123DEF456",
            "location": "/Users/test/Music/song.mp3",
            "file_size": 6000000,  # Changed file size
            "duration": 240.5,
        }

        fp1 = generator.generate_track_fingerprint(track1)
        fp2 = generator.generate_track_fingerprint(track2)

        assert fp1 != fp2, "Different content should produce different fingerprint"

    def test_fingerprint_requires_persistent_id(self, generator: FingerprintGenerator) -> None:
        """Test that fingerprint requires persistent_id."""
        track_data = {
            "location": "/Users/test/Music/song.mp3",
            # Missing persistent_id
        }

        with pytest.raises(FingerprintGenerationError):
            generator.generate_track_fingerprint(track_data)

    def test_fingerprint_requires_location(self, generator: FingerprintGenerator) -> None:
        """Test that fingerprint requires location."""
        track_data = {
            "persistent_id": "ABC123DEF456",
            # Missing location
        }

        with pytest.raises(FingerprintGenerationError):
            generator.generate_track_fingerprint(track_data)

    def test_fingerprint_validates_empty_persistent_id(self, generator: FingerprintGenerator) -> None:
        """Test that empty persistent_id is rejected."""

        track_data = {
            "persistent_id": "",  # Empty
            "location": "/Users/test/Music/song.mp3",
        }

        with pytest.raises(FingerprintGenerationError):
            generator.generate_track_fingerprint(track_data)

    def test_fingerprint_format_is_valid_sha256(self, generator: FingerprintGenerator) -> None:
        """Test that fingerprint is a valid SHA-256 hex string."""
        track_data = {
            "persistent_id": "ABC123",
            "location": "/path/to/file.mp3",
        }

        fingerprint = generator.generate_track_fingerprint(track_data)

        # SHA-256 produces 64 hex characters
        assert len(fingerprint) == 64
        assert FingerprintGenerator.validate_fingerprint(fingerprint)


class TestUnifiedHashServiceIntegration:
    """Integration tests for unified hash service."""

    @staticmethod
    def _assert_hash_consistency(hash_func: Any, *args: Any) -> str:
        """Assert that hash function produces consistent results for same input.

        Args:
            hash_func: The hash function to test.
            *args: Arguments to pass to the hash function.

        Returns:
            The computed hash value for further assertions.
        """
        key1 = hash_func(*args)
        key2 = hash_func(*args)
        assert key1 == key2, "Hash should be consistent for same input"
        return key1

    def test_hash_album_key_consistency(self) -> None:
        """Test hash consistency for album keys."""
        self._assert_hash_consistency(UnifiedHashService.hash_album_key, "artist", "album")

    def test_hash_album_key_uniqueness(self) -> None:
        """Test hash uniqueness for different inputs."""
        keys = [
            UnifiedHashService.hash_album_key("artist1", "album1"),
            UnifiedHashService.hash_album_key("artist1", "album2"),
            UnifiedHashService.hash_album_key("artist2", "album1"),
        ]

        # All keys should be unique
        assert len(keys) == len(set(keys))

    def test_hash_api_key_includes_source(self) -> None:
        """Test that API hash includes source in key."""
        key1 = UnifiedHashService.hash_api_key("artist", "album", "musicbrainz")
        key2 = UnifiedHashService.hash_api_key("artist", "album", "discogs")

        # Same artist/album but different source = different keys
        assert key1 != key2

    def test_hash_generic_key_handles_dict(self) -> None:
        """Test generic hash handles dictionary input."""
        data = {"type": "test", "id": 123, "nested": {"key": "value"}}
        self._assert_hash_consistency(UnifiedHashService.hash_generic_key, data)

    def test_hash_generic_key_handles_string(self) -> None:
        """Test generic hash handles string input."""
        self._assert_hash_consistency(UnifiedHashService.hash_generic_key, "simple_string")

    def test_hash_handles_unicode(self) -> None:
        """Test hash service handles unicode characters."""
        # Japanese artist name
        key1 = UnifiedHashService.hash_album_key("宇多田ヒカル", "First Love")
        # Cyrillic artist name
        key2 = UnifiedHashService.hash_album_key("Сплин", "Гранатовый альбом")
        # Korean artist name
        key3 = UnifiedHashService.hash_album_key("방탄소년단", "Map of the Soul")

        # All should produce valid hashes
        assert key1 is not None
        assert len(key1) == 64
        assert key2 is not None
        assert len(key2) == 64
        assert key3 is not None
        assert len(key3) == 64

        # All should be unique
        assert len({key1, key2, key3}) == 3

    def test_hash_pending_key(self) -> None:
        """Test pending verification key generation."""
        # Consistency: same input = same hash
        key1 = self._assert_hash_consistency(UnifiedHashService.hash_pending_key, "track_123")
        key3 = UnifiedHashService.hash_pending_key("track_456")

        # Uniqueness: different input = different hash
        assert key1 != key3


class TestCacheIntegrationScenarios:
    """Integration tests for cross-cache scenarios."""

    @pytest.mark.asyncio
    async def test_album_and_api_cache_consistency(
        self,
        mock_config: AppConfig,
        mock_logger: logging.Logger,
    ) -> None:
        """Test that album cache and API cache can be used together."""
        album_cache = AlbumCacheService(mock_config, mock_logger)
        api_cache = ApiCacheService(mock_config, mock_logger)

        artist = "Test Artist"
        album = "Test Album"
        year = "2020"

        # Store in API cache (simulating API response)
        await api_cache.set_cached_result(
            artist=artist,
            album=album,
            source="musicbrainz",
            success=True,
            data={"year": year},
        )

        # Store in album cache (simulating resolved year)
        await album_cache.store_album_year(
            artist=artist,
            album=album,
            year=year,
            confidence=90,
        )

        # Both should return consistent data
        api_result = await api_cache.get_cached_result(artist=artist, album=album, source="musicbrainz")
        album_result = await album_cache.get_album_year(artist=artist, album=album)

        assert api_result is not None
        assert album_result is not None
        assert api_result.year == album_result

    @pytest.mark.asyncio
    async def test_cache_stats_available(
        self,
        mock_config: AppConfig,
        mock_logger: logging.Logger,
    ) -> None:
        """Test that all cache services provide stats."""
        album_cache = AlbumCacheService(mock_config, mock_logger)
        api_cache = ApiCacheService(mock_config, mock_logger)
        generic_cache = GenericCacheService(mock_config, mock_logger)

        # Add some data
        await album_cache.store_album_year("Artist", "Album", "2020", 80)
        await api_cache.set_cached_result("Artist", "Album", "musicbrainz", True, {"year": "2020"})
        generic_cache.set("key", "value")

        # Get stats
        album_stats = album_cache.get_stats()
        api_stats = api_cache.get_stats()
        generic_stats = generic_cache.get_stats()

        # Verify stats structure
        assert "total_albums" in album_stats
        assert album_stats["total_albums"] == 1

        assert "total_entries" in api_stats
        assert api_stats["total_entries"] == 1

        assert "total_entries" in generic_stats
        assert generic_stats["total_entries"] == 1
