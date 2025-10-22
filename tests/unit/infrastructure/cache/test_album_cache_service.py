"""Comprehensive tests for AlbumCacheService with Allure reporting."""

from __future__ import annotations

import csv
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import allure
import pytest

from src.infrastructure.cache.album_cache_service import AlbumCacheService
from src.infrastructure.cache.hash_service import UnifiedHashService


@allure.epic("Music Genre Updater")
@allure.feature("Cache Infrastructure")
class TestAlbumCacheService:
    """Comprehensive tests for AlbumCacheService."""

    @staticmethod
    def create_service(config: dict[str, Any] | None = None) -> AlbumCacheService:
        """Create an AlbumCacheService instance for testing."""
        # Use tempfile for secure temporary paths
        temp_dir = tempfile.gettempdir()
        default_config = {
            "album_years_cache_file": f"{temp_dir}/test_album_years.csv",
            "log_directory": f"{temp_dir}/logs"
        }
        test_config = {**default_config, **(config or {})}
        mock_logger = MagicMock()
        return AlbumCacheService(test_config, mock_logger)

    @allure.story("Initialization")
    @allure.title("Should initialize album cache service")
    @allure.description("Test initialization with file loading")
    @pytest.mark.asyncio
    async def test_initialization(self) -> None:
        """Test album cache service initialization."""
        service = TestAlbumCacheService.create_service()

        with allure.step("Initialize service with no existing cache file"), patch.object(Path, "exists", return_value=False):
            await service.initialize()

        with allure.step("Verify initialization"):
            assert not service.album_years_cache
            assert service.cache_config is None

    @allure.story("Basic Operations")
    @allure.title("Should store and retrieve album years")
    @allure.description("Test basic get/set operations for album cache")
    @pytest.mark.asyncio
    async def test_store_and_get_album_year(self) -> None:
        """Test storing and retrieving album years."""
        service = TestAlbumCacheService.create_service()
        await service.initialize()

        with allure.step("Store album year"):
            artist = "The Beatles"
            album = "Abbey Road"
            year = "1969"
            await service.store_album_year(artist, album, year)

        with allure.step("Retrieve album year"):
            result = await service.get_album_year(artist, album)
            assert result == year

        with allure.step("Verify cache miss for non-existent album"):
            result = await service.get_album_year("Unknown Artist", "Unknown Album")
            assert result is None

    @allure.story("Basic Operations")
    @allure.title("Should normalize whitespace when storing")
    @allure.description("Test that whitespace is properly trimmed")
    @pytest.mark.asyncio
    async def test_normalize_whitespace(self) -> None:
        """Test whitespace normalization."""
        service = TestAlbumCacheService.create_service()
        await service.initialize()

        with allure.step("Store album with extra whitespace"):
            await service.store_album_year("  Pink Floyd  ", "  The Wall  ", "  1979  ")

        with allure.step("Retrieve with trimmed values"):
            result = await service.get_album_year("Pink Floyd", "The Wall")
            assert result == "1979"

        with allure.step("Retrieve with extra whitespace"):
            result = await service.get_album_year("  Pink Floyd  ", "  The Wall  ")
            assert result == "1979"

    @allure.story("Validation")
    @allure.title("Should handle case-insensitive lookups")
    @allure.description("Test that lookups are case-insensitive")
    @pytest.mark.asyncio
    async def test_case_insensitive_lookup(self) -> None:
        """Test case-insensitive artist and album lookups."""
        service = TestAlbumCacheService.create_service()
        await service.initialize()

        with allure.step("Store album"):
            await service.store_album_year("Led Zeppelin", "IV", "1971")

        with allure.step("Retrieve with different case"):
            result = await service.get_album_year("led zeppelin", "iv")
            assert result == "1971"

        with allure.step("Retrieve with mixed case"):
            result = await service.get_album_year("LED ZEPPELIN", "Iv")
            assert result == "1971"

    @allure.story("Validation")
    @allure.title("Should detect and handle hash collisions")
    @allure.description("Test hash collision detection and recovery")
    @pytest.mark.asyncio
    async def test_hash_collision_detection(self) -> None:
        """Test hash collision detection."""
        service = TestAlbumCacheService.create_service()
        await service.initialize()

        with allure.step("Manually create hash collision"):
            # Store legitimate entry
            await service.store_album_year("Queen", "A Night at the Opera", "1975")

            # Get the key
            key = UnifiedHashService.hash_album_key("Queen", "A Night at the Opera")

            # Manually corrupt the entry to simulate collision
            service.album_years_cache[key] = ("Different Artist", "Different Album", "1999")

        with allure.step("Attempt to retrieve - should detect collision"):
            result = await service.get_album_year("Queen", "A Night at the Opera")
            assert result is None

        with allure.step("Verify invalid entry was removed"):
            key = UnifiedHashService.hash_album_key("Queen", "A Night at the Opera")
            assert key not in service.album_years_cache

    @allure.story("Invalidation")
    @allure.title("Should invalidate specific album")
    @allure.description("Test removing specific album from cache")
    @pytest.mark.asyncio
    async def test_invalidate_album(self) -> None:
        """Test invalidating specific album."""
        service = TestAlbumCacheService.create_service()
        await service.initialize()

        with allure.step("Store multiple albums"):
            await service.store_album_year("Radiohead", "OK Computer", "1997")
            await service.store_album_year("Radiohead", "Kid A", "2000")

        with allure.step("Invalidate one album"):
            await service.invalidate_album("Radiohead", "OK Computer")

        with allure.step("Verify specific album removed"):
            assert await service.get_album_year("Radiohead", "OK Computer") is None

        with allure.step("Verify other album still exists"):
            assert await service.get_album_year("Radiohead", "Kid A") == "2000"

    @allure.story("Invalidation")
    @allure.title("Should clear all cache entries")
    @allure.description("Test clearing entire cache")
    @pytest.mark.asyncio
    async def test_invalidate_all(self) -> None:
        """Test clearing all cache entries."""
        service = TestAlbumCacheService.create_service()
        await service.initialize()

        with allure.step("Populate cache"):
            await service.store_album_year("Artist1", "Album1", "2020")
            await service.store_album_year("Artist2", "Album2", "2021")
            await service.store_album_year("Artist3", "Album3", "2022")

        with allure.step("Clear all entries"):
            await service.invalidate_all()

        with allure.step("Verify cache is empty"):
            assert len(service.album_years_cache) == 0
            assert await service.get_album_year("Artist1", "Album1") is None

    @allure.story("Persistence")
    @allure.title("Should save cache to CSV file")
    @allure.description("Test saving album cache to disk")
    @pytest.mark.asyncio
    async def test_save_to_disk(self) -> None:
        """Test saving cache to CSV file."""
        service = TestAlbumCacheService.create_service()

        # Initialize without loading from disk
        with patch.object(Path, "exists", return_value=False):
            await service.initialize()

        with allure.step("Populate cache"):
            await service.store_album_year("David Bowie", "The Rise and Fall of Ziggy Stardust", "1972")
            await service.store_album_year("David Bowie", "Station to Station", "1976")

        with (
            allure.step("Mock file operations"),
            patch("pathlib.Path.open", MagicMock()),
            patch("src.shared.core.logger.ensure_directory"),
            patch.object(AlbumCacheService, "_write_csv_data") as mock_write,
        ):
            await service.save_to_disk()

        with allure.step("Verify save was attempted"):
            mock_write.assert_called_once()

    @allure.story("Persistence")
    @allure.title("Should skip save when cache is empty")
    @allure.description("Test that save is skipped for empty cache")
    @pytest.mark.asyncio
    async def test_skip_save_when_empty(self) -> None:
        """Test skipping save when cache is empty."""
        service = TestAlbumCacheService.create_service()

        # Initialize without loading from disk
        with patch.object(Path, "exists", return_value=False):
            await service.initialize()

        with allure.step("Ensure cache is empty"):
            assert len(service.album_years_cache) == 0

        with allure.step("Attempt to save"), patch("pathlib.Path.open") as mock_open:
            await service.save_to_disk()

        with allure.step("Verify save was skipped"):
            mock_open.assert_not_called()
            service.logger.debug.assert_any_call("Album cache is empty, skipping save")

    @allure.story("Persistence")
    @allure.title("Should load cache from CSV file")
    @allure.description("Test loading album cache from disk")
    @pytest.mark.asyncio
    async def test_load_from_disk(self) -> None:
        """Test loading cache from CSV file."""
        with allure.step("Create test CSV data with correct hash"):
            # Use the actual hash function to create proper key
            key = UnifiedHashService.hash_album_key("Nirvana", "Nevermind")
            test_data = {key: ("Nirvana", "Nevermind", "1991")}

        with (
            allure.step("Mock file operations"),
            patch.object(Path, "exists", return_value=True),
            patch.object(AlbumCacheService, "_read_csv_file", return_value=test_data),
        ):
            service = TestAlbumCacheService.create_service()
            await service.initialize()

        with allure.step("Verify cache loaded"):
            assert len(service.album_years_cache) == 1
            result = await service.get_album_year("Nirvana", "Nevermind")
            assert result == "1991"

    @allure.story("CSV Processing")
    @allure.title("Should validate CSV headers")
    @allure.description("Test CSV header validation")
    def test_validate_csv_headers(self) -> None:
        """Test CSV header validation."""
        service = TestAlbumCacheService.create_service()

        with allure.step("Test valid headers"):
            valid_headers = ["artist", "album", "year"]
            assert service._validate_csv_headers(valid_headers) is True  # noqa: SLF001

        with allure.step("Test missing headers"):
            missing_headers = ["artist", "album"]  # Missing 'year'
            with pytest.raises(ValueError, match="missing required headers"):
                service._validate_csv_headers(missing_headers)  # noqa: SLF001

        with allure.step("Test no headers"):
            with pytest.raises(ValueError, match="has no headers"):
                service._validate_csv_headers(None)  # noqa: SLF001

        with allure.step("Test extra headers (should still be valid)"):
            extra_headers = ["artist", "album", "year", "extra_field"]
            assert service._validate_csv_headers(extra_headers) is True  # noqa: SLF001

    @allure.story("CSV Processing")
    @allure.title("Should process valid CSV rows")
    @allure.description("Test processing individual CSV rows")
    def test_process_csv_row_valid(self) -> None:
        """Test processing valid CSV rows."""
        service = TestAlbumCacheService.create_service()
        album_data: dict[str, tuple[str, str, str]] = {}

        with allure.step("Process valid row"):
            row = {"artist": "The Doors", "album": "L.A. Woman", "year": "1971"}
            service._process_csv_row(row, album_data)  # noqa: SLF001

        with allure.step("Verify data was added"):
            assert len(album_data) == 1
            # Check that some entry was added (key is hashed)
            values = next(iter(album_data.values()))
            assert values == ("The Doors", "L.A. Woman", "1971")

    @allure.story("CSV Processing")
    @allure.title("Should skip invalid CSV rows")
    @allure.description("Test handling of malformed CSV rows")
    def test_process_csv_row_invalid(self) -> None:
        """Test processing invalid CSV rows."""
        service = TestAlbumCacheService.create_service()
        album_data: dict[str, tuple[str, str, str]] = {}

        with allure.step("Process row with empty fields"):
            row = {"artist": "", "album": "Album", "year": "1999"}
            service._process_csv_row(row, album_data)  # noqa: SLF001
            assert not album_data

        with allure.step("Process row with missing field"):
            row = {"artist": "Artist", "album": "Album"}  # Missing 'year'
            service._process_csv_row(row, album_data)  # noqa: SLF001
            assert not album_data

    @allure.story("CSV Processing")
    @allure.title("Should write CSV data correctly")
    @allure.description("Test CSV file writing")
    def test_write_csv_data(self) -> None:
        """Test writing CSV data."""
        with allure.step("Prepare test data"):
            items = [("The Clash", "London Calling", "1979"), ("Joy Division", "Unknown Pleasures", "1979")]

        with allure.step("Write to temporary file"):
            with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".csv") as tmp_file:
                temp_path = tmp_file.name

            try:
                AlbumCacheService._write_csv_data(temp_path, items)  # noqa: SLF001

                with allure.step("Verify file contents"), Path(temp_path).open(encoding="utf-8") as f:
                    reader = csv.reader(f)
                    rows = list(reader)

                    # Check header
                    assert rows[0] == ["artist", "album", "year"]

                    # Check data rows
                    assert len(rows) == 3  # Header + 2 data rows
                    assert rows[1] == ["The Clash", "London Calling", "1979"]
                    assert rows[2] == ["Joy Division", "Unknown Pleasures", "1979"]
            finally:
                # Cleanup
                Path(temp_path).unlink(missing_ok=True)

    @allure.story("Statistics")
    @allure.title("Should provide cache statistics")
    @allure.description("Test getting cache statistics")
    @pytest.mark.asyncio
    async def test_get_stats(self) -> None:
        """Test cache statistics."""
        service = TestAlbumCacheService.create_service()

        # Initialize without loading from disk
        with patch.object(Path, "exists", return_value=False):
            await service.initialize()

        with allure.step("Populate cache"):
            await service.store_album_year("Artist1", "Album1", "2020")
            await service.store_album_year("Artist2", "Album2", "2021")

        with allure.step("Get statistics"):
            stats = service.get_stats()

        with allure.step("Verify statistics"):
            assert stats["total_albums"] == 2
            assert "cache_file" in stats
            assert "cache_file_exists" in stats
            assert stats["content_type"] == "album_year"
            assert stats["persistent"] is True

    @allure.story("Error Handling")
    @allure.title("Should handle save errors gracefully")
    @allure.description("Test error handling during save operation")
    @pytest.mark.asyncio
    async def test_save_error_handling(self) -> None:
        """Test error handling during save."""
        service = TestAlbumCacheService.create_service()
        await service.initialize()

        with allure.step("Populate cache"):
            await service.store_album_year("Artist", "Album", "2023")

        with (
            allure.step("Mock save failure"),
            patch("pathlib.Path.open", side_effect=OSError("Disk full")),
            patch("src.shared.core.logger.ensure_directory"),
            pytest.raises(OSError, match="Disk full"),
        ):
            await service.save_to_disk()

        with allure.step("Verify logger captured exception"):
            service.logger.exception.assert_called()

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
            # The _read_csv_file handles exceptions internally and returns empty dict
            service = TestAlbumCacheService.create_service()
            # Should not raise exception, just log it
            await service.initialize()

        with allure.step("Verify service still initializes"):
            # Cache should be empty due to load failure
            assert not service.album_years_cache
            service.logger.exception.assert_called()

    @allure.story("Edge Cases")
    @allure.title("Should handle special characters in data")
    @allure.description("Test handling of special characters and unicode")
    @pytest.mark.asyncio
    async def test_special_characters(self) -> None:
        """Test handling of special characters."""
        service = TestAlbumCacheService.create_service()
        await service.initialize()

        with allure.step("Store album with special characters"):
            artist = "Björk"
            album = "Post: The Album™"
            year = "1995"
            await service.store_album_year(artist, album, year)

        with allure.step("Retrieve album"):
            result = await service.get_album_year(artist, album)
            assert result == year

    @allure.story("Edge Cases")
    @allure.title("Should handle very long strings")
    @allure.description("Test handling of unusually long artist/album names")
    @pytest.mark.asyncio
    async def test_long_strings(self) -> None:
        """Test handling of long strings."""
        service = TestAlbumCacheService.create_service()
        await service.initialize()

        with allure.step("Store album with very long names"):
            artist = "A" * 500
            album = "B" * 500
            year = "2023"
            await service.store_album_year(artist, album, year)

        with allure.step("Retrieve album"):
            result = await service.get_album_year(artist, album)
            assert result == year
