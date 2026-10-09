"""Comprehensive tests for ApiCacheService with Allure reporting."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, patch
import pytest

from core.models.track_models import CachedApiResult
from services.cache.api_cache import ApiCacheService
from services.cache.cache_config import CacheContentType, CacheEvent, CacheEventType
from services.cache.hash_service import UnifiedHashService
from tests.factories import create_test_app_config

if TYPE_CHECKING:
    from core.models.track_models import AppConfig


class TestApiCacheService:
    """Comprehensive tests for ApiCacheService."""

    @staticmethod
    def create_service(config: AppConfig | None = None) -> ApiCacheService:
        """Create an ApiCacheService instance for testing."""
        if config is None:
            config = create_test_app_config()
        mock_logger = MagicMock()
        return ApiCacheService(config, mock_logger)

    @staticmethod
    def create_cached_result(
        artist: str = "Test Artist", album: str = "Test Album", year: str | None = "2023", source: str = "spotify"
    ) -> CachedApiResult:
        """Create a test CachedApiResult."""
        return CachedApiResult(
            artist=artist,
            album=album,
            year=year,
            source=source,
            timestamp=datetime.now(UTC).timestamp(),
            metadata={},
            api_response={"year": year} if year else None,
        )

    @pytest.mark.asyncio
    async def test_initialization(self) -> None:
        """Test API cache service initialization."""
        service = TestApiCacheService.create_service()

        with patch.object(Path, "exists", return_value=False):
            await service.initialize()
        assert service.api_cache == {}
        assert service.event_manager is not None
        assert service.cache_config is not None

    @pytest.mark.asyncio
    async def test_get_set_cached_result(self) -> None:
        """Test storing and retrieving API results."""
        service = TestApiCacheService.create_service()
        await service.initialize()
        artist = "Pink Floyd"
        album = "Dark Side of the Moon"
        source = "musicbrainz"
        records = [{"year": "1973", "genres": ["Progressive Rock"]}]

        await service.set_cached_result(artist, album, source=source, records=records)
        result = await service.get_cached_result(artist, album, source)
        assert result is not None
        assert result.artist == artist.strip()
        assert result.album == album.strip()
        assert result.api_response == {"records": records}
        assert result.source == source.strip()
        missing = await service.get_cached_result("Unknown", "Album", "source")
        assert missing is None

    @pytest.mark.asyncio
    async def test_cache_expiration(self) -> None:
        """Test cache entry expiration."""
        service = TestApiCacheService.create_service()
        await service.initialize()
        # An empty answer expires
        await service.set_cached_result("Artist", "Album", source="source", records=[])
        # Get the key for our entry
        key = UnifiedHashService.hash_api_key("Artist", "Album", "source")

        # Set timestamp to past
        if key in service.api_cache:
            service.api_cache[key].timestamp = 0.0  # Very old timestamp
        result = await service.get_cached_result("Artist", "Album", "source")
        assert result is None
        assert key not in service.api_cache

    @pytest.mark.asyncio
    async def test_successful_results_eternal(self) -> None:
        """Found records never expire."""
        service = TestApiCacheService.create_service()
        await service.initialize()
        await service.set_cached_result("Beatles", "Abbey Road", source="spotify", records=[{"year": "1969"}])
        key = UnifiedHashService.hash_api_key("Beatles", "Abbey Road", "spotify")

        # Set timestamp to very old
        if key in service.api_cache:
            service.api_cache[key].timestamp = 0.0
        result = await service.get_cached_result("Beatles", "Abbey Road", "spotify")
        assert result is not None
        assert result.api_response == {"records": [{"year": "1969"}]}

    @pytest.mark.asyncio
    async def test_invalidate_for_album(self) -> None:
        """Test invalidating cache for specific album."""
        service = TestApiCacheService.create_service()
        await service.initialize()

        artist = "Led Zeppelin"
        album = "IV"
        await service.set_cached_result(artist, album, source="spotify", records=[{"year": "1971"}])
        await service.set_cached_result(artist, album, source="musicbrainz", records=[{"year": "1971"}])
        await service.set_cached_result(artist, album, source="discogs", records=[{"year": "1971"}])

        # Also add entry for different album
        await service.set_cached_result(artist, "Physical Graffiti", source="spotify", records=[{"year": "1975"}])
        await service.invalidate_for_album(artist, album)
        assert await service.get_cached_result(artist, album, "spotify") is None
        assert await service.get_cached_result(artist, album, "musicbrainz") is None
        assert await service.get_cached_result(artist, album, "discogs") is None
        result = await service.get_cached_result(artist, "Physical Graffiti", "spotify")
        assert result is not None
        assert result.api_response == {"records": [{"year": "1975"}]}

    @pytest.mark.asyncio
    async def test_invalidate_all(self) -> None:
        """Test clearing all cache entries."""
        service = TestApiCacheService.create_service()
        await service.initialize()
        await service.set_cached_result("Artist1", "Album1", source="source1", records=[{"year": "2020"}])
        await service.set_cached_result("Artist2", "Album2", source="source2", records=[{"year": "2021"}])
        await service.set_cached_result("Artist3", "Album3", source="source3", records=[{"year": "2022"}])
        await service.invalidate_all()
        assert len(service.api_cache) == 0
        assert await service.get_cached_result("Artist1", "Album1", "source1") is None

    @pytest.mark.asyncio
    async def test_cleanup_expired(self) -> None:
        """Test cleanup of expired entries."""
        service = TestApiCacheService.create_service()
        await service.initialize()
        # Add a successful entry that should persist indefinitely
        await service.set_cached_result("Artist1", "Album1", source="source1", records=[{"year": "2020"}])

        # Add failed entries that should expire
        await service.set_cached_result("Artist2", "Album2", source="source2", records=[])
        await service.set_cached_result("Artist3", "Album3", source="source3", records=[])
        key2 = UnifiedHashService.hash_api_key("Artist2", "Album2", "source2")
        key3 = UnifiedHashService.hash_api_key("Artist3", "Album3", "source3")

        if key2 in service.api_cache:
            service.api_cache[key2].timestamp = 0.0
        if key3 in service.api_cache:
            service.api_cache[key3].timestamp = 0.0
        removed_count = await service.cleanup_expired()
        assert removed_count == 2
        assert len(service.api_cache) == 1
        assert await service.get_cached_result("Artist1", "Album1", "source1") is not None

    @pytest.mark.asyncio
    async def test_save_to_disk(self) -> None:
        """Test saving cache to disk."""
        service = TestApiCacheService.create_service()
        await service.initialize()
        await service.set_cached_result("Queen", "A Night at the Opera", source="spotify", records=[{"year": "1975"}])
        await service.set_cached_result("Queen", "News of the World", source="musicbrainz", records=[{"year": "1977"}])

        mock_file = MagicMock()

        with (
            patch("pathlib.Path.open") as mock_open,
            patch("core.logger.ensure_directory"),
        ):
            mock_open.return_value = mock_file
            await service.save_to_disk()
        mock_open.assert_called_once_with("w", encoding="utf-8")
        mock_file.__enter__.assert_called_once()

    @pytest.mark.asyncio
    async def test_load_from_disk(self) -> None:
        """Test loading cache from disk."""
        # Create test data
        cache_data = {
            "key1": {
                "artist": "The Doors",
                "album": "L.A. Woman",
                "year": "1971",
                "source": "musicbrainz",
                "timestamp": datetime.now(UTC).timestamp(),
                "metadata": {},
                "api_response": {"year": "1971"},
            }
        }
        mock_file = MagicMock()
        mock_file.__enter__.return_value = mock_file
        mock_file.read.return_value = json.dumps(cache_data)

        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "open", return_value=mock_file),
            patch("json.load", return_value=cache_data),
        ):
            service = TestApiCacheService.create_service()
            await service.initialize()
        assert len(service.api_cache) == 1
        assert "key1" in service.api_cache
        result = service.api_cache["key1"]
        assert result.artist == "The Doors"
        assert result.album == "L.A. Woman"
        assert result.year == "1971"

    @pytest.mark.asyncio
    async def test_handle_track_removed_event(self) -> None:
        """Test handling track removed event."""
        service = TestApiCacheService.create_service()
        await service.initialize()

        artist = "Radiohead"
        album = "OK Computer"
        await service.set_cached_result(artist, album, source="spotify", records=[{"year": "1997"}])
        await service.set_cached_result(artist, album, source="musicbrainz", records=[{"year": "1997"}])
        event = CacheEvent(event_type=CacheEventType.TRACK_REMOVED, track_id="track123", metadata={"artist": artist, "album": album})

        service.event_manager.emit_event(event)

        # Wait for background task to complete
        await asyncio.sleep(0.1)
        # The entries should be removed
        assert await service.get_cached_result(artist, album, "spotify") is None
        assert await service.get_cached_result(artist, album, "musicbrainz") is None

    def test_handle_track_modified_event_without_metadata(self) -> None:
        """Test handling track modified event without metadata does nothing."""
        service = TestApiCacheService.create_service()
        event = CacheEvent(event_type=CacheEventType.TRACK_MODIFIED, track_id="track456")

        # Event without metadata should be ignored (no artist/album to invalidate)
        service.event_manager.emit_event(event)
        cast(MagicMock, service.logger.info).assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_track_modified_event_with_metadata(self) -> None:
        """Test handling track modified event invalidates cache for old artist/album."""
        service = TestApiCacheService.create_service()
        await service.initialize()

        # Pre-populate cache
        await service.set_cached_result("Old Artist", "Old Album", source="musicbrainz", records=[{"year": "2020"}])
        assert await service.get_cached_result("Old Artist", "Old Album", "musicbrainz") is not None

        # Emit track modified event with old artist/album in metadata
        event = CacheEvent(
            event_type=CacheEventType.TRACK_MODIFIED,
            track_id="track456",
            metadata={"artist": "Old Artist", "album": "Old Album"},
        )
        service.event_manager.emit_event(event)

        # Verify info log was called (f-string format)
        cast(MagicMock, service.logger.info).assert_called_with(
            "Track track456 identity changed, invalidating cache for: Old Artist - Old Album",
        )

        # Wait for async task to complete
        await asyncio.sleep(0.1)
        # Cache should be invalidated
        assert await service.get_cached_result("Old Artist", "Old Album", "musicbrainz") is None

    @pytest.mark.asyncio
    async def test_get_stats(self) -> None:
        """Test cache statistics."""
        service = TestApiCacheService.create_service()
        await service.initialize()
        # Found records
        await service.set_cached_result("Artist1", "Album1", source="source1", records=[{"year": "2020"}])
        await service.set_cached_result("Artist2", "Album2", source="source2", records=[{"year": "2021"}])

        # Empty answers
        await service.set_cached_result("Artist3", "Album3", source="source3", records=[])
        await service.set_cached_result("Artist4", "Album4", source="source4", records=[])
        stats = service.get_stats()
        assert stats["total_entries"] == 4
        assert stats["found"] == 2
        assert stats["not_found"] == 2
        assert "cache_file" in stats
        assert "cache_file_exists" in stats
        assert "successful_policy" in stats
        assert "not_found_policy" in stats
        assert stats["persistent"] is True

    @pytest.mark.asyncio
    async def test_edge_cases(self) -> None:
        """Test edge cases."""
        service = TestApiCacheService.create_service()
        await service.initialize()
        await service.set_cached_result("Artist", "Album", source="source", records=[])

        # Mock the timestamp to be recent so it's not expired
        key = UnifiedHashService.hash_api_key("Artist", "Album", "source")
        if key in service.api_cache:
            service.api_cache[key].timestamp = datetime.now(UTC).timestamp()

        result = await service.get_cached_result("Artist", "Album", "source")
        assert result is not None
        assert result.year is None
        await service.set_cached_result("Artist2", "Album2", source="source2", records=[{}])

        # Mock timestamp to be recent
        key2 = UnifiedHashService.hash_api_key("Artist2", "Album2", "source2")
        if key2 in service.api_cache:
            service.api_cache[key2].timestamp = datetime.now(UTC).timestamp()

        result = await service.get_cached_result("Artist2", "Album2", "source2")
        assert result is not None
        assert result.year is None
        # When stored with whitespace, it should be trimmed
        await service.set_cached_result("  Artist3  ", "  Album3  ", source="  source3  ", records=[{"year": "2023"}])
        # Should be able to retrieve with trimmed values
        result = await service.get_cached_result("  Artist3  ", "  Album3  ", "  source3  ")
        assert result is not None
        assert result.artist == "Artist3"  # Should be trimmed
        assert result.album == "Album3"  # Should be trimmed
        assert result.source == "source3"  # Should be trimmed

    def test_emit_track_removed(self) -> None:
        """Test emitting track removed event."""
        service = TestApiCacheService.create_service()
        mock_emit = MagicMock()
        object.__setattr__(service.event_manager, "emit_event", mock_emit)
        service.emit_track_removed("track789", "Pink Floyd", "The Wall")
        mock_emit.assert_called_once()
        event = mock_emit.call_args[0][0]
        assert event.event_type == CacheEventType.TRACK_REMOVED
        assert event.track_id == "track789"
        assert event.metadata["artist"] == "Pink Floyd"
        assert event.metadata["album"] == "The Wall"

    @pytest.mark.asyncio
    async def test_save_error_handling(self) -> None:
        """Test error handling during save."""
        service = TestApiCacheService.create_service()
        await service.initialize()
        await service.set_cached_result("Artist", "Album", source="source", records=[{"year": "2023"}])

        with (
            patch("pathlib.Path.open", side_effect=OSError("Disk full")),
            patch("core.logger.ensure_directory"),
            pytest.raises(OSError, match="Disk full"),
        ):
            await service.save_to_disk()
        cast(MagicMock, service.logger.exception).assert_called()

    @pytest.mark.asyncio
    async def test_load_error_handling(self) -> None:
        """Test error handling during load."""
        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "open", side_effect=OSError("File corrupted")),
        ):
            service = TestApiCacheService.create_service()
            await service.initialize()
        assert service.api_cache == {}
        cast(MagicMock, service.logger.exception).assert_called()

    @pytest.mark.asyncio
    async def test_load_skips_invalid_entry(self) -> None:
        """Should skip entries with missing required keys."""
        cache_data = {
            "valid_key": {
                "artist": "Artist",
                "album": "Album",
                "year": "2023",
                "source": "musicbrainz",
                "timestamp": 1700000000.0,
                "metadata": {},
            },
            "invalid_key": {
                "album": "Album Only",
                "year": "2023",
            },
        }
        mock_file = MagicMock()
        mock_file.__enter__.return_value = mock_file

        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "open", return_value=mock_file),
            patch("json.load", return_value=cache_data),
        ):
            service = TestApiCacheService.create_service()
            await service.initialize()

        assert len(service.api_cache) == 1
        assert "valid_key" in service.api_cache
        warning_calls = cast(MagicMock, service.logger.warning).call_args_list
        assert any("invalid_key" in str(call) for call in warning_calls)

    @pytest.mark.asyncio
    async def test_skip_save_when_empty(self) -> None:
        """Test skipping save when cache is empty."""
        service = TestApiCacheService.create_service()
        await service.initialize()
        assert len(service.api_cache) == 0

        with patch("pathlib.Path.open") as mock_open:
            await service.save_to_disk()
        mock_open.assert_not_called()
        cast(MagicMock, service.logger.debug).assert_any_call("API cache is empty, deleting cache file if exists")

    @pytest.mark.asyncio
    async def test_cleanup_on_init(self) -> None:
        """Test that cleanup_expired is called during initialization."""
        # Create cache data with expired entries
        cache_data = {
            "valid_key": {
                "artist": "Artist1",
                "album": "Album1",
                "year": None,
                "source": "spotify",
                "timestamp": 0.0,  # Found records never expire, however old
                "ttl": None,
                "metadata": {},
                "api_response": {"records": [{"year": "2023"}]},
            },
            "expired_key": {
                "artist": "Artist2",
                "album": "Album2",
                "year": None,
                "source": "musicbrainz",
                "timestamp": 0.0,  # An empty answer past its TTL
                "ttl": 60,
                "metadata": {},
                "api_response": {"records": []},
            },
        }

        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "open", MagicMock()),
            patch("json.load", return_value=cache_data),
        ):
            service = TestApiCacheService.create_service()
            await service.initialize()
        # Only non-expired entries should remain
        assert len(service.api_cache) == 1

    @pytest.mark.asyncio
    async def test_background_task_limit(self) -> None:
        """Test that background tasks are limited to max count."""
        service = TestApiCacheService.create_service()
        await service.initialize()
        # Create fake tasks to fill the limit
        for _ in range(service._max_background_tasks):
            fake_task = asyncio.create_task(asyncio.sleep(10))
            service._background_tasks.add(fake_task)

        assert len(service._background_tasks) == 100
        event = CacheEvent(
            event_type=CacheEventType.TRACK_REMOVED,
            track_id="test_track",
            metadata={"artist": "Test Artist", "album": "Test Album"},
        )
        initial_count = len(service._background_tasks)
        service._handle_track_removed(event)
        # Count should remain the same - new task was skipped
        assert len(service._background_tasks) == initial_count
        cast(MagicMock, service.logger.debug).assert_any_call(
            "Background task limit reached (%d), skipping invalidation for %s - %s",
            100,
            "Test Artist",
            "Test Album",
        )
        for task in list(service._background_tasks):
            task.cancel()
        await asyncio.gather(*service._background_tasks, return_exceptions=True)
        service._background_tasks.clear()


class TestOutcomeCaching:
    """Provider records are stored by outcome: found for good, an empty answer until the negative TTL runs out."""

    @pytest.mark.asyncio
    async def test_found_records_are_permanent_and_empty_answers_expire(self) -> None:
        """An empty answer is asked again after the negative TTL; found records are kept."""
        service = TestApiCacheService.create_service()
        records = [{"title": "Album", "year": "1999"}]
        await service.set_cached_result("artist", "album", source="discogs", records=records)
        await service.set_cached_result("artist", "other", source="discogs", records=[])
        negative_ttl = service.cache_config.get_policy(CacheContentType.NEGATIVE_RESULT).ttl_seconds
        for entry in service.api_cache.values():
            entry.timestamp -= negative_ttl + 1

        found = await service.get_cached_result("artist", "album", "discogs")

        assert found is not None
        assert found.api_response == {"records": records}
        assert found.ttl is None
        assert await service.get_cached_result("artist", "other", "discogs") is None

    @pytest.mark.asyncio
    async def test_fresh_empty_answer_is_served(self) -> None:
        """Within the negative TTL an empty answer is a cache hit, so the provider is not asked again."""
        service = TestApiCacheService.create_service()
        await service.set_cached_result("artist", "album", source="itunes", records=[])

        cached = await service.get_cached_result("artist", "album", "itunes")

        assert cached is not None
        assert cached.api_response == {"records": []}
        assert cached.ttl == service.cache_config.get_policy(CacheContentType.NEGATIVE_RESULT).ttl_seconds

    @pytest.mark.asyncio
    async def test_invalidation_covers_every_source_and_spelling(self) -> None:
        """Invalidating an album drops its records from all three providers, whatever the name spelling."""
        service = TestApiCacheService.create_service()
        for source in ("musicbrainz", "discogs", "itunes"):
            await service.set_cached_result("artist", "Album", source=source, records=[{"year": "2001"}])

        await service.invalidate_for_album("Artist ", "album")

        assert service.api_cache == {}
