"""Comprehensive tests for AlbumCacheService with Allure reporting."""

from __future__ import annotations

import csv
import tempfile
import time
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch
import pytest

from services.cache.album_cache import AlbumCacheEntry, AlbumCacheService
from services.cache.cache_config import CacheContentType
from services.cache.hash_service import UnifiedHashService


class TestAlbumCacheService:
    """Comprehensive tests for AlbumCacheService."""

    @staticmethod
    def create_service(config: dict[str, Any] | None = None) -> AlbumCacheService:
        """Create an AlbumCacheService instance for testing."""
        # Use tempfile for secure temporary paths
        temp_dir = tempfile.gettempdir()
        default_config = {"album_years_cache_file": f"{temp_dir}/test_album_years.csv", "log_directory": f"{temp_dir}/logs"}
        test_config = {**default_config, **(config or {})}
        mock_logger = MagicMock()
        return AlbumCacheService(test_config, mock_logger)

    @pytest.mark.asyncio
    async def test_initialization(self) -> None:
        """Test album cache service initialization."""
        service = TestAlbumCacheService.create_service()

        with patch.object(Path, "exists", return_value=False):
            await service.initialize()
        assert not service.album_years_cache
        assert service.policy.ttl_seconds == service.cache_config.get_ttl(CacheContentType.ALBUM_YEAR)

    @pytest.mark.asyncio
    async def test_store_and_get_album_year(self) -> None:
        """Test storing and retrieving album years."""
        service = TestAlbumCacheService.create_service()
        await service.initialize()
        artist = "The Beatles"
        album = "Abbey Road"
        year = "1969"
        await service.store_album_year(artist, album, year)
        result = await service.get_album_year(artist, album)
        assert result == year
        result = await service.get_album_year("Unknown Artist", "Unknown Album")
        assert result is None

    @pytest.mark.asyncio
    async def test_normalize_whitespace(self) -> None:
        """Test whitespace normalization."""
        service = TestAlbumCacheService.create_service()
        await service.initialize()
        await service.store_album_year("  Pink Floyd  ", "  The Wall  ", "  1979  ")
        result = await service.get_album_year("Pink Floyd", "The Wall")
        assert result == "1979"
        result = await service.get_album_year("  Pink Floyd  ", "  The Wall  ")
        assert result == "1979"

    @pytest.mark.asyncio
    async def test_case_insensitive_lookup(self) -> None:
        """Test case-insensitive artist and album lookups."""
        service = TestAlbumCacheService.create_service()
        await service.initialize()
        await service.store_album_year("Led Zeppelin", "IV", "1971")
        result = await service.get_album_year("led zeppelin", "iv")
        assert result == "1971"
        result = await service.get_album_year("LED ZEPPELIN", "Iv")
        assert result == "1971"

    @pytest.mark.asyncio
    async def test_expired_entry_returns_none(self) -> None:
        """Ensure expired entries are removed on access."""
        service = TestAlbumCacheService.create_service()
        await service.initialize()

        base_time = 1000.0
        ttl_seconds = service.policy.ttl_seconds

        with patch("services.cache.album_cache.time.time", return_value=base_time):
            await service.store_album_year("Artist", "Album", "2000")

        with patch("services.cache.album_cache.time.time", return_value=base_time + ttl_seconds + 5):
            result = await service.get_album_year("Artist", "Album")
            assert result is None

    @pytest.mark.asyncio
    async def test_hash_collision_detection(self) -> None:
        """Test hash collision detection."""
        service = TestAlbumCacheService.create_service()
        await service.initialize()
        # Store legitimate entry
        await service.store_album_year("Queen", "A Night at the Opera", "1975")

        # Get the key
        key = UnifiedHashService.hash_album_key("Queen", "A Night at the Opera")

        # Manually corrupt the entry to simulate collision
        service.album_years_cache[key] = AlbumCacheEntry(
            artist="Different Artist",
            album="Different Album",
            year="1999",
            timestamp=time.time(),
        )
        result = await service.get_album_year("Queen", "A Night at the Opera")
        assert result is None
        key = UnifiedHashService.hash_album_key("Queen", "A Night at the Opera")
        # Entry should remain - we keep the original owner's data
        assert key in service.album_years_cache
        # But lookup for mismatched request should still return None
        result = await service.get_album_year("Queen", "A Night at the Opera")
        assert result is None

    @pytest.mark.asyncio
    async def test_invalidate_album(self) -> None:
        """Test invalidating specific album."""
        service = TestAlbumCacheService.create_service()
        await service.initialize()
        await service.store_album_year("Radiohead", "OK Computer", "1997")
        await service.store_album_year("Radiohead", "Kid A", "2000")
        await service.invalidate_album("Radiohead", "OK Computer")
        assert await service.get_album_year("Radiohead", "OK Computer") is None
        assert await service.get_album_year("Radiohead", "Kid A") == "2000"

    @pytest.mark.asyncio
    async def test_invalidate_all(self) -> None:
        """Test clearing all cache entries."""
        service = TestAlbumCacheService.create_service()
        await service.initialize()
        await service.store_album_year("Artist1", "Album1", "2020")
        await service.store_album_year("Artist2", "Album2", "2021")
        await service.store_album_year("Artist3", "Album3", "2022")
        await service.invalidate_all()
        assert len(service.album_years_cache) == 0
        assert await service.get_album_year("Artist1", "Album1") is None

    @pytest.mark.asyncio
    async def test_save_to_disk(self) -> None:
        """Test saving cache to CSV file."""
        service = TestAlbumCacheService.create_service()

        # Initialize without loading from disk
        with patch.object(Path, "exists", return_value=False):
            await service.initialize()
        await service.store_album_year("David Bowie", "The Rise and Fall of Ziggy Stardust", "1972")
        await service.store_album_year("David Bowie", "Station to Station", "1976")

        with (
            patch("pathlib.Path.open", MagicMock()),
            patch("core.logger.ensure_directory"),
            patch.object(AlbumCacheService, "_write_csv_data") as mock_write,
        ):
            await service.save_to_disk()
        mock_write.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_save_when_empty(self) -> None:
        """Test skipping save when cache is empty."""
        service = TestAlbumCacheService.create_service()

        # Initialize without loading from disk
        with patch.object(Path, "exists", return_value=False):
            await service.initialize()
        assert len(service.album_years_cache) == 0

        with patch("pathlib.Path.open") as mock_open:
            await service.save_to_disk()
        mock_open.assert_not_called()
        logger_mock = cast(MagicMock, service.logger)
        logger_mock.debug.assert_any_call("Album cache is empty, skipping save")

    @pytest.mark.asyncio
    async def test_load_from_disk(self) -> None:
        """Test loading cache from CSV file."""
        # Use the actual hash function to create proper key
        key = UnifiedHashService.hash_album_key("Nirvana", "Nevermind")
        test_data = {
            key: AlbumCacheEntry(
                artist="Nirvana",
                album="Nevermind",
                year="1991",
                timestamp=time.time(),
            )
        }

        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(AlbumCacheService, "_read_csv_file", return_value=test_data),
        ):
            service = TestAlbumCacheService.create_service()
            await service.initialize()
        assert len(service.album_years_cache) == 1
        result = await service.get_album_year("Nirvana", "Nevermind")
        assert result == "1991"

    def test_validate_csv_headers(self) -> None:
        """Test CSV header validation."""
        service = TestAlbumCacheService.create_service()
        valid_headers = ["artist", "album", "year"]
        assert service._validate_csv_headers(valid_headers) is True
        missing_headers = ["artist", "album"]  # Missing 'year'
        with pytest.raises(ValueError, match="missing required headers"):
            service._validate_csv_headers(missing_headers)

        with pytest.raises(ValueError, match="has no headers"):
            service._validate_csv_headers(None)
        extra_headers = ["artist", "album", "year", "extra_field"]
        assert service._validate_csv_headers(extra_headers) is True

    def test_process_csv_row_valid(self) -> None:
        """Test processing valid CSV rows."""
        service = TestAlbumCacheService.create_service()
        album_data: dict[str, AlbumCacheEntry] = {}
        row = {"artist": "The Doors", "album": "L.A. Woman", "year": "1971"}
        service._process_csv_row(row, album_data)
        assert len(album_data) == 1
        # Check that some entry was added (key is hashed)
        values = next(iter(album_data.values()))
        assert values.artist == "The Doors"
        assert values.album == "L.A. Woman"
        assert values.year == "1971"

    def test_process_csv_row_invalid(self) -> None:
        """Test processing invalid CSV rows."""
        service = TestAlbumCacheService.create_service()
        album_data: dict[str, AlbumCacheEntry] = {}
        row = {"artist": "", "album": "Album", "year": "1999"}
        service._process_csv_row(row, album_data)
        assert not album_data
        row = {"artist": "Artist", "album": "Album"}  # Missing 'year'
        service._process_csv_row(row, album_data)
        assert not album_data

    def test_write_csv_data(self) -> None:
        """Test writing CSV data."""
        items = [
            AlbumCacheEntry("The Clash", "London Calling", "1979", 123.456, 85),
            AlbumCacheEntry("Joy Division", "Unknown Pleasures", "1979", 789.012, 70),
        ]
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".csv") as tmp_file:
            temp_path = tmp_file.name

        try:
            AlbumCacheService._write_csv_data(temp_path, items)

            with Path(temp_path).open(encoding="utf-8") as f:
                reader = csv.reader(f)
                rows = list(reader)

                # Check header
                assert rows[0] == ["artist", "album", "year", "timestamp", "confidence"]

                # Check data rows
                assert len(rows) == 3  # Header + 2 data rows
                assert rows[1] == ["The Clash", "London Calling", "1979", "123.456000", "85"]
                assert rows[2] == ["Joy Division", "Unknown Pleasures", "1979", "789.012000", "70"]
        finally:
            # Cleanup
            Path(temp_path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_get_stats(self) -> None:
        """Test cache statistics."""
        service = TestAlbumCacheService.create_service()

        # Initialize without loading from disk
        with patch.object(Path, "exists", return_value=False):
            await service.initialize()
        await service.store_album_year("Artist1", "Album1", "2020")
        await service.store_album_year("Artist2", "Album2", "2021")
        stats = service.get_stats()
        assert stats["total_albums"] == 2
        assert "cache_file" in stats
        assert "cache_file_exists" in stats
        assert stats["content_type"] == "album_year"
        assert stats["persistent"] is False

    @pytest.mark.asyncio
    async def test_save_error_handling(self) -> None:
        """Test error handling during save."""
        service = TestAlbumCacheService.create_service()
        await service.initialize()
        await service.store_album_year("Artist", "Album", "2023")

        with (
            patch("tempfile.mkstemp", side_effect=OSError("Disk full")),
            patch("core.logger.ensure_directory"),
            pytest.raises(OSError, match="Disk full"),
        ):
            await service.save_to_disk()
        logger_mock = cast(MagicMock, service.logger)
        logger_mock.exception.assert_called()

    @pytest.mark.asyncio
    async def test_temp_file_cleanup_on_replace_failure(self, tmp_path: Path) -> None:
        """Test temp file is cleaned up when replace operation fails."""
        cache_path = tmp_path / "album_years.csv"
        service = TestAlbumCacheService.create_service({"album_years_cache_file": str(cache_path)})
        await service.initialize()
        await service.store_album_year("Artist", "Album", "2023")
        # Track temp file path
        created_temp_path: str | None = None
        original_mkstemp = tempfile.mkstemp

        def tracking_mkstemp(*args: Any, **kwargs: Any) -> tuple[int, str]:
            nonlocal created_temp_path
            fd, path = original_mkstemp(*args, **kwargs)
            created_temp_path = path
            return fd, path

        with (
            patch("tempfile.mkstemp", side_effect=tracking_mkstemp),
            patch.object(Path, "replace", side_effect=OSError("Replace failed")),
            patch("core.logger.ensure_directory"),
            pytest.raises(OSError, match="Replace failed"),
        ):
            await service.save_to_disk()
        assert created_temp_path is not None
        assert not Path(created_temp_path).exists()

    @pytest.mark.asyncio
    async def test_load_error_handling(self) -> None:
        """Test error handling during load."""
        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "open", side_effect=OSError("File corrupted")),
        ):
            # The _read_csv_file handles exceptions internally and returns empty dict
            service = TestAlbumCacheService.create_service()
            # Should not raise exception, just log it
            await service.initialize()
        # Cache should be empty due to load failure
        assert not service.album_years_cache
        logger_mock = cast(MagicMock, service.logger)
        logger_mock.exception.assert_called()

    @pytest.mark.asyncio
    async def test_special_characters(self) -> None:
        """Test handling of special characters."""
        service = TestAlbumCacheService.create_service()
        await service.initialize()
        artist = "Björk"
        album = "Post: The Album™"
        year = "1995"
        await service.store_album_year(artist, album, year)
        result = await service.get_album_year(artist, album)
        assert result == year

    @pytest.mark.asyncio
    async def test_long_strings(self) -> None:
        """Test handling of long strings."""
        service = TestAlbumCacheService.create_service()
        await service.initialize()
        artist = "A" * 500
        album = "B" * 500
        year = "2023"
        await service.store_album_year(artist, album, year)
        result = await service.get_album_year(artist, album)
        assert result == year
