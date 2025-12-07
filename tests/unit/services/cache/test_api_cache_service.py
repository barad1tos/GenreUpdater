"""Comprehensive tests for ApiCacheService with Allure reporting."""

from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import allure
import pytest

from core.models.track_models import CachedApiResult
from services.cache.api_cache import ApiCacheService
from services.cache.cache_config import CacheEvent, CacheEventType
from services.cache.hash_service import UnifiedHashService


@allure.epic("Music Genre Updater")
@allure.feature("Cache Infrastructure")
class TestApiCacheService:
    """Comprehensive tests for ApiCacheService."""

    @staticmethod
    def create_service(config: dict[str, Any] | None = None) -> ApiCacheService:
        """Create an ApiCacheService instance for testing."""
        temp_path = Path(tempfile.mkdtemp(prefix="api-cache-service-test-"))
        log_directory = temp_path / "logs"
        log_directory.mkdir(parents=True, exist_ok=True)

        default_config = {
            "api_cache_file": str(temp_path / "test_cache.json"),
            "log_directory": str(log_directory),
        }
        test_config = {**default_config, **(config or {})}
        mock_logger = MagicMock()
        return ApiCacheService(test_config, mock_logger)

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

    @allure.story("Initialization")
    @allure.title("Should initialize API cache service")
    @allure.description("Test initialization with file loading")
    @pytest.mark.asyncio
    async def test_initialization(self) -> None:
        """Test API cache service initialization."""
        service = TestApiCacheService.create_service()

        with allure.step("Initialize service with no existing cache file"), patch.object(Path, "exists", return_value=False):
            await service.initialize()

        with allure.step("Verify initialization"):
            assert service.api_cache == {}
            assert service.event_manager is not None
            assert service.cache_config is not None

    @allure.story("Basic Operations")
    @allure.title("Should store and retrieve API results")
    @allure.description("Test basic get/set operations for API cache")
    @pytest.mark.asyncio
    async def test_get_set_cached_result(self) -> None:
        """Test storing and retrieving API results."""
        service = TestApiCacheService.create_service()
        await service.initialize()

        with allure.step("Store successful API result"):
            artist = "Pink Floyd"
            album = "Dark Side of the Moon"
            source = "musicbrainz"
            data = {"year": "1973", "genres": ["Progressive Rock"]}

            await service.set_cached_result(artist, album, source, True, data)

        with allure.step("Retrieve cached result"):
            result = await service.get_cached_result(artist, album, source)
            assert result is not None
            assert result.artist == artist.strip()
            assert result.album == album.strip()
            assert result.year == "1973"
            assert result.source == source.strip()

        with allure.step("Verify cache miss for non-existent entry"):
            missing = await service.get_cached_result("Unknown", "Album", "source")
            assert missing is None

    @allure.story("Expiration")
    @allure.title("Should handle expired cache entries")
    @allure.description("Test TTL expiration for failed API lookups")
    @pytest.mark.asyncio
    async def test_cache_expiration(self) -> None:
        """Test cache entry expiration."""
        service = TestApiCacheService.create_service()
        await service.initialize()

        with allure.step("Store failed API result (should have TTL)"):
            # Failed result (no year) should expire
            await service.set_cached_result("Artist", "Album", "source", False)

        with allure.step("Mock expired timestamp"):
            # Get the key for our entry
            key = UnifiedHashService.hash_api_key("Artist", "Album", "source")

            # Set timestamp to past
            if key in service.api_cache:
                service.api_cache[key].timestamp = 0.0  # Very old timestamp

        with allure.step("Verify expired entry is removed"):
            result = await service.get_cached_result("Artist", "Album", "source")
            assert result is None
            assert key not in service.api_cache

    @allure.story("Expiration")
    @allure.title("Should treat successful results as eternal")
    @allure.description("Test that successful API results never expire")
    @pytest.mark.asyncio
    async def test_successful_results_eternal(self) -> None:
        """Test that successful API results with year never expire."""
        service = TestApiCacheService.create_service()
        await service.initialize()

        with allure.step("Store successful API result with year"):
            await service.set_cached_result("Beatles", "Abbey Road", "spotify", True, {"year": "1969"})

        with allure.step("Mock very old timestamp"):
            key = UnifiedHashService.hash_api_key("Beatles", "Abbey Road", "spotify")

            # Set timestamp to very old
            if key in service.api_cache:
                service.api_cache[key].timestamp = 0.0

        with allure.step("Verify result is still valid (eternal)"):
            result = await service.get_cached_result("Beatles", "Abbey Road", "spotify")
            assert result is not None
            assert result.year == "1969"

    @allure.story("Invalidation")
    @allure.title("Should invalidate cache for specific album")
    @allure.description("Test invalidating all cache entries for an album")
    @pytest.mark.asyncio
    async def test_invalidate_for_album(self) -> None:
        """Test invalidating cache for specific album."""
        service = TestApiCacheService.create_service()
        await service.initialize()

        artist = "Led Zeppelin"
        album = "IV"

        with allure.step("Store multiple entries for same album"):
            await service.set_cached_result(artist, album, "spotify", True, {"year": "1971"})
            await service.set_cached_result(artist, album, "lastfm", True, {"year": "1971"})
            await service.set_cached_result(artist, album, "discogs", True, {"year": "1971"})

            # Also add entry for different album
            await service.set_cached_result(artist, "Physical Graffiti", "spotify", True, {"year": "1975"})

        with allure.step("Invalidate specific album"):
            await service.invalidate_for_album(artist, album)

        with allure.step("Verify album entries removed"):
            assert await service.get_cached_result(artist, album, "spotify") is None
            assert await service.get_cached_result(artist, album, "lastfm") is None
            assert await service.get_cached_result(artist, album, "discogs") is None

        with allure.step("Verify other album unaffected"):
            result = await service.get_cached_result(artist, "Physical Graffiti", "spotify")
            assert result is not None
            assert result.year == "1975"

    @allure.story("Invalidation")
    @allure.title("Should clear all cache entries")
    @allure.description("Test clearing entire API cache")
    @pytest.mark.asyncio
    async def test_invalidate_all(self) -> None:
        """Test clearing all cache entries."""
        service = TestApiCacheService.create_service()
        await service.initialize()

        with allure.step("Populate cache"):
            await service.set_cached_result("Artist1", "Album1", "source1", True, {"year": "2020"})
            await service.set_cached_result("Artist2", "Album2", "source2", True, {"year": "2021"})
            await service.set_cached_result("Artist3", "Album3", "source3", True, {"year": "2022"})

        with allure.step("Clear all entries"):
            await service.invalidate_all()

        with allure.step("Verify cache is empty"):
            assert len(service.api_cache) == 0
            assert await service.get_cached_result("Artist1", "Album1", "source1") is None

    @allure.story("Cleanup")
    @allure.title("Should clean up expired entries")
    @allure.description("Test removing expired entries from cache")
    @pytest.mark.asyncio
    async def test_cleanup_expired(self) -> None:
        """Test cleanup of expired entries."""
        service = TestApiCacheService.create_service()
        await service.initialize()

        with allure.step("Store mixed entries"):
            # Add a successful entry that should persist indefinitely
            await service.set_cached_result("Artist1", "Album1", "source1", True, {"year": "2020"})

            # Add failed entries that should expire
            await service.set_cached_result("Artist2", "Album2", "source2", False)
            await service.set_cached_result("Artist3", "Album3", "source3", False)

        with allure.step("Make failed entries expired"):
            key2 = UnifiedHashService.hash_api_key("Artist2", "Album2", "source2")
            key3 = UnifiedHashService.hash_api_key("Artist3", "Album3", "source3")

            if key2 in service.api_cache:
                service.api_cache[key2].timestamp = 0.0
            if key3 in service.api_cache:
                service.api_cache[key3].timestamp = 0.0

        with allure.step("Run cleanup"):
            removed_count = await service.cleanup_expired()
            assert removed_count == 2

        with allure.step("Verify only successful entry remains"):
            assert len(service.api_cache) == 1
            assert await service.get_cached_result("Artist1", "Album1", "source1") is not None

    @allure.story("Persistence")
    @allure.title("Should save cache to disk")
    @allure.description("Test saving API cache to JSON file")
    @pytest.mark.asyncio
    async def test_save_to_disk(self) -> None:
        """Test saving cache to disk."""
        service = TestApiCacheService.create_service()
        await service.initialize()

        with allure.step("Populate cache"):
            await service.set_cached_result("Queen", "A Night at the Opera", "spotify", True, {"year": "1975"})
            await service.set_cached_result("Queen", "News of the World", "lastfm", True, {"year": "1977"})

        mock_file = MagicMock()

        with (
            allure.step("Mock file operations"),
            patch("pathlib.Path.open") as mock_open,
            patch("core.logger.ensure_directory"),
        ):
            mock_open.return_value = mock_file
            await service.save_to_disk()

        with allure.step("Verify save was called"):
            mock_open.assert_called_once_with("w", encoding="utf-8")
            mock_file.__enter__.assert_called_once()

    @allure.story("Persistence")
    @allure.title("Should load cache from disk")
    @allure.description("Test loading API cache from JSON file")
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

        with allure.step("Mock file operations"):
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

        with allure.step("Verify cache loaded"):
            assert len(service.api_cache) == 1
            assert "key1" in service.api_cache
            result = service.api_cache["key1"]
            assert result.artist == "The Doors"
            assert result.album == "L.A. Woman"
            assert result.year == "1971"

    @allure.story("Event Handling")
    @allure.title("Should handle track removed event")
    @allure.description("Test invalidating cache when track is removed")
    @pytest.mark.asyncio
    async def test_handle_track_removed_event(self) -> None:
        """Test handling track removed event."""
        service = TestApiCacheService.create_service()
        await service.initialize()

        artist = "Radiohead"
        album = "OK Computer"

        with allure.step("Store cache entries for album"):
            await service.set_cached_result(artist, album, "spotify", True, {"year": "1997"})
            await service.set_cached_result(artist, album, "lastfm", True, {"year": "1997"})

        with allure.step("Emit track removed event"):
            event = CacheEvent(event_type=CacheEventType.TRACK_REMOVED, track_id="track123", metadata={"artist": artist, "album": album})

            service.event_manager.emit_event(event)

            # Wait for background task to complete
            await asyncio.sleep(0.1)

        with allure.step("Verify cache invalidated"):
            # The entries should be removed
            assert await service.get_cached_result(artist, album, "spotify") is None
            assert await service.get_cached_result(artist, album, "lastfm") is None

    @allure.story("Event Handling")
    @allure.title("Should handle track modified event")
    @allure.description("Test handling track modification (no-op for API cache)")
    def test_handle_track_modified_event(self) -> None:
        """Test handling track modified event."""
        service = TestApiCacheService.create_service()

        with allure.step("Create and handle event"):
            event = CacheEvent(event_type=CacheEventType.TRACK_MODIFIED, track_id="track456")

            # Should only log, not modify cache
            service.event_manager.emit_event(event)

        with allure.step("Verify logger was called"):
            cast(MagicMock, service.logger.debug).assert_called_once_with("Track modified: %s, API cache unaffected", "track456")

    @allure.story("Statistics")
    @allure.title("Should provide cache statistics")
    @allure.description("Test getting cache statistics")
    @pytest.mark.asyncio
    async def test_get_stats(self) -> None:
        """Test cache statistics."""
        service = TestApiCacheService.create_service()
        await service.initialize()

        with allure.step("Populate cache with mixed results"):
            # Successful results (with year)
            await service.set_cached_result("Artist1", "Album1", "source1", True, {"year": "2020"})
            await service.set_cached_result("Artist2", "Album2", "source2", True, {"year": "2021"})

            # Failed results (no year)
            await service.set_cached_result("Artist3", "Album3", "source3", False)
            await service.set_cached_result("Artist4", "Album4", "source4", False, {"other": "data"})

        with allure.step("Get statistics"):
            stats = service.get_stats()

        with allure.step("Verify statistics"):
            assert stats["total_entries"] == 4
            assert stats["successful_responses"] == 2
            assert stats["failed_lookups"] == 2
            assert "cache_file" in stats
            assert "cache_file_exists" in stats
            assert "successful_policy" in stats
            assert "failed_policy" in stats
            assert stats["persistent"] is True

    @allure.story("Edge Cases")
    @allure.title("Should handle edge cases gracefully")
    @allure.description("Test edge cases and error conditions")
    @pytest.mark.asyncio
    async def test_edge_cases(self) -> None:
        """Test edge cases."""
        service = TestApiCacheService.create_service()
        await service.initialize()

        with allure.step("Store result with None year"):
            await service.set_cached_result("Artist", "Album", "source", False)

            # Mock the timestamp to be recent so it's not expired
            key = UnifiedHashService.hash_api_key("Artist", "Album", "source")
            if key in service.api_cache:
                service.api_cache[key].timestamp = datetime.now(UTC).timestamp()

            result = await service.get_cached_result("Artist", "Album", "source")
            assert result is not None
            assert result.year is None

        with allure.step("Store result with empty data"):
            await service.set_cached_result("Artist2", "Album2", "source2", True, {})

            # Mock timestamp to be recent
            key2 = UnifiedHashService.hash_api_key("Artist2", "Album2", "source2")
            if key2 in service.api_cache:
                service.api_cache[key2].timestamp = datetime.now(UTC).timestamp()

            result = await service.get_cached_result("Artist2", "Album2", "source2")
            assert result is not None
            assert result.year is None

        with allure.step("Store result with whitespace"):
            # When stored with whitespace, it should be trimmed
            await service.set_cached_result("  Artist3  ", "  Album3  ", "  source3  ", True, {"year": "2023"})
            # Should be able to retrieve with trimmed values
            result = await service.get_cached_result("  Artist3  ", "  Album3  ", "  source3  ")
            assert result is not None
            assert result.artist == "Artist3"  # Should be trimmed
            assert result.album == "Album3"  # Should be trimmed
            assert result.source == "source3"  # Should be trimmed

    @allure.story("Public API")
    @allure.title("Should emit track removed events")
    @allure.description("Test public API for emitting events")
    def test_emit_track_removed(self) -> None:
        """Test emitting track removed event."""
        service = TestApiCacheService.create_service()

        with allure.step("Mock event manager"):
            mock_emit = MagicMock()
            object.__setattr__(service.event_manager, "emit_event", mock_emit)

        with allure.step("Emit event"):
            service.emit_track_removed("track789", "Pink Floyd", "The Wall")

        with allure.step("Verify event emitted"):
            mock_emit.assert_called_once()
            event = mock_emit.call_args[0][0]
            assert event.event_type == CacheEventType.TRACK_REMOVED
            assert event.track_id == "track789"
            assert event.metadata["artist"] == "Pink Floyd"
            assert event.metadata["album"] == "The Wall"

    @allure.story("Error Handling")
    @allure.title("Should handle save errors gracefully")
    @allure.description("Test error handling during save operation")
    @pytest.mark.asyncio
    async def test_save_error_handling(self) -> None:
        """Test error handling during save."""
        service = TestApiCacheService.create_service()
        await service.initialize()

        with allure.step("Populate cache"):
            await service.set_cached_result("Artist", "Album", "source", True, {"year": "2023"})

        with (
            allure.step("Mock save failure"),
            patch("pathlib.Path.open", side_effect=OSError("Disk full")),
            patch("core.logger.ensure_directory"),
            pytest.raises(OSError, match="Disk full"),
        ):
            await service.save_to_disk()

        with allure.step("Verify logger captured exception"):
            cast(MagicMock, service.logger.exception).assert_called()

    @allure.story("Error Handling")
    @allure.title("Should handle load errors gracefully")
    @allure.description("Test error handling during load operation")
    @pytest.mark.asyncio
    async def test_load_error_handling(self) -> None:
        """Test error handling during load."""
        with (
            allure.step("Mock load failure"),
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "open", side_effect=OSError("File corrupted")),
        ):
            service = TestApiCacheService.create_service()
            await service.initialize()

        with allure.step("Verify service still initializes"):
            assert service.api_cache == {}
            cast(MagicMock, service.logger.exception).assert_called()

    @allure.story("Persistence")
    @allure.title("Should skip save when cache is empty")
    @allure.description("Test that save is skipped for empty cache")
    @pytest.mark.asyncio
    async def test_skip_save_when_empty(self) -> None:
        """Test skipping save when cache is empty."""
        service = TestApiCacheService.create_service()
        await service.initialize()

        with allure.step("Ensure cache is empty"):
            assert len(service.api_cache) == 0

        with allure.step("Attempt to save"), patch("pathlib.Path.open") as mock_open:
            await service.save_to_disk()

        with allure.step("Verify save deletes empty cache file"):
            mock_open.assert_not_called()
            cast(MagicMock, service.logger.debug).assert_any_call("API cache is empty, deleting cache file if exists")

    @allure.story("Initialization")
    @allure.title("Should cleanup expired entries on init")
    @allure.description("Test that expired entries are cleaned up during initialization")
    @pytest.mark.asyncio
    async def test_cleanup_on_init(self) -> None:
        """Test that cleanup_expired is called during initialization."""
        # Create cache data with expired entries
        cache_data = {
            "valid_key": {
                "artist": "Artist1",
                "album": "Album1",
                "year": "2023",
                "source": "spotify",
                "timestamp": datetime.now(UTC).timestamp(),
                "metadata": {},
                "api_response": {"year": "2023"},
            },
            "expired_key": {
                "artist": "Artist2",
                "album": "Album2",
                "year": None,
                "source": "lastfm",
                "timestamp": 0.0,  # Expired timestamp
                "metadata": {},
                "api_response": None,
            },
        }

        with (
            allure.step("Create service with expired cache data"),
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "open", MagicMock()),
            patch("json.load", return_value=cache_data),
        ):
            service = TestApiCacheService.create_service()
            await service.initialize()

        with allure.step("Verify expired entry was cleaned up"):
            # Only non-expired entries should remain
            assert len(service.api_cache) == 1

    @allure.story("Resource Limits")
    @allure.title("Should limit background tasks")
    @allure.description("Test that background tasks are limited to prevent unbounded growth")
    @pytest.mark.asyncio
    async def test_background_task_limit(self) -> None:
        """Test that background tasks are limited to max count."""
        service = TestApiCacheService.create_service()
        await service.initialize()

        with allure.step("Fill background tasks to limit"):
            # Create fake tasks to fill the limit
            for _ in range(service._max_background_tasks):
                fake_task = asyncio.create_task(asyncio.sleep(10))
                service._background_tasks.add(fake_task)

            assert len(service._background_tasks) == 100

        with allure.step("Attempt to add another task via event"):
            event = CacheEvent(
                event_type=CacheEventType.TRACK_REMOVED,
                track_id="test_track",
                metadata={"artist": "Test Artist", "album": "Test Album"},
            )
            initial_count = len(service._background_tasks)
            service._handle_track_removed(event)

        with allure.step("Verify task was skipped"):
            # Count should remain the same - new task was skipped
            assert len(service._background_tasks) == initial_count
            cast(MagicMock, service.logger.debug).assert_any_call(
                "Background task limit reached (%d), skipping invalidation for %s - %s",
                100,
                "Test Artist",
                "Test Album",
            )

        with allure.step("Cleanup tasks"):
            for task in list(service._background_tasks):
                task.cancel()
            await asyncio.gather(*service._background_tasks, return_exceptions=True)
            service._background_tasks.clear()
