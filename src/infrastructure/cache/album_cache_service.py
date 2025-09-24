"""Album Cache Service - Specialized cache for album release years.

This module provides a dedicated cache service for storing and retrieving
album release years with CSV persistence and efficient lookup operations.

Key Features:
- CSV-based persistence for album metadata
- Efficient artist/album -> year lookups
- TTL management and cache validation
- Integration with SmartCacheConfig for content-aware policies
"""

import asyncio
import csv
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from src.infrastructure.cache.cache_config import CacheContentType
from src.infrastructure.cache.hash_service import UnifiedHashService
from src.shared.core.logger import ensure_directory


class AlbumCacheService:
    """Specialized cache service for album release years with CSV persistence."""

    def __init__(self, config: dict[str, Any], logger: logging.Logger | None = None) -> None:
        """Initialize album cache service.

        Args:
            config: Cache configuration dictionary
            logger: Optional logger instance
        """
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        # Initialize cache config later when needed
        self.cache_config = None

        # Album years cache: {hash_key: (artist, album, year)}
        self.album_years_cache: dict[str, tuple[str, str, str]] = {}

        # Cache file paths
        self.album_years_cache_file = Path(config.get("album_years_cache_file", "cache/album_years.csv"))

    async def initialize(self) -> None:
        """Initialize album cache by loading data from disk."""
        self.logger.info("Initializing AlbumCacheService...")
        await self._load_album_years_cache()
        self.logger.info("AlbumCacheService initialized with %d albums", len(self.album_years_cache))

    async def get_album_year(self, artist: str, album: str) -> str | None:
        """Get album release year from cache.

        Args:
            artist: Artist name
            album: Album name

        Returns:
            Album release year if found, None otherwise
        """
        # Use asyncio.sleep(0) to yield control and satisfy async requirements
        await asyncio.sleep(0)
        key = UnifiedHashService.hash_album_key(artist, album)

        if key in self.album_years_cache:
            cached_artist, cached_album, cached_year = self.album_years_cache[key]

            # Validate cache entry consistency
            if cached_artist.lower().strip() == artist.lower().strip() and cached_album.lower().strip() == album.lower().strip():
                self.logger.debug("Album year cache hit: %s - %s = %s", artist, album, cached_year)
                return cached_year
            # Hash collision - remove invalid entry
            self.logger.warning("Hash collision detected for %s - %s, removing invalid entry", artist, album)
            del self.album_years_cache[key]

        self.logger.debug("Album year cache miss: %s - %s", artist, album)
        return None

    async def store_album_year(self, artist: str, album: str, year: str) -> None:
        """Store album release year in cache.

        Args:
            artist: Artist name
            album: Album name
            year: Album release year
        """
        # Use asyncio.sleep(0) to yield control and satisfy async requirements
        await asyncio.sleep(0)
        key = UnifiedHashService.hash_album_key(artist, album)

        # Store with normalized values for consistency
        normalized_artist = artist.strip()
        normalized_album = album.strip()
        normalized_year = year.strip()

        self.album_years_cache[key] = (normalized_artist, normalized_album, normalized_year)
        self.logger.debug("Stored album year: %s - %s = %s", normalized_artist, normalized_album, normalized_year)

    async def invalidate_album(self, artist: str, album: str) -> None:
        """Invalidate specific album from cache.

        Args:
            artist: Artist name
            album: Album name
        """
        # Use asyncio.sleep(0) to yield control and satisfy async requirements
        await asyncio.sleep(0)
        key = UnifiedHashService.hash_album_key(artist, album)

        if key in self.album_years_cache:
            del self.album_years_cache[key]
            self.logger.info("Invalidated album cache: %s - %s", artist, album)

    async def invalidate_all(self) -> None:
        """Clear all album cache entries."""
        # Use asyncio.sleep(0) to yield control and satisfy async requirements
        await asyncio.sleep(0)
        count = len(self.album_years_cache)
        self.album_years_cache.clear()
        self.logger.info("Cleared all album cache entries (%d items)", count)

    async def save_to_disk(self) -> None:
        """Save album cache to CSV file."""
        if not self.album_years_cache:
            self.logger.debug("Album cache is empty, skipping save")
            return

        def blocking_save() -> None:
            """Synchronous save operation for thread executor."""
            try:
                # Ensure directory exists
                ensure_directory(str(self.album_years_cache_file.parent))

                # Prepare data for CSV
                items = list(self.album_years_cache.values())

                # Write CSV file
                self._write_csv_data(str(self.album_years_cache_file), items)
                self.logger.info("Album cache saved to %s (%d entries)", self.album_years_cache_file, len(items))

            except (OSError, UnicodeError) as e:
                self.logger.exception("Failed to save album cache: %s", e)
                raise

        # Run in thread executor to avoid blocking
        await asyncio.get_event_loop().run_in_executor(None, blocking_save)  # type: ignore[arg-type]

    async def _load_album_years_cache(self) -> None:
        """Load album years cache from CSV file."""
        if not self.album_years_cache_file.exists():
            self.logger.info("Album cache file does not exist, starting with empty cache")
            return

        def blocking_load() -> dict[str, tuple[str, str, str]]:
            """Synchronous load operation for thread executor."""
            return self._read_csv_file()

        # Run in thread executor to avoid blocking
        loaded_cache = await asyncio.get_event_loop().run_in_executor(None, blocking_load)  # type: ignore[arg-type]
        self.album_years_cache.update(loaded_cache)

    def _read_csv_file(self) -> dict[str, tuple[str, str, str]]:
        """Read and parse CSV file containing album years data.

        Returns:
            Dictionary mapping hash keys to (artist, album, year) tuples
        """
        album_data: dict[str, tuple[str, str, str]] = {}

        try:
            with self.album_years_cache_file.open(encoding="utf-8") as file:
                reader = csv.DictReader(file)

                # Validate CSV headers
                if not self._validate_csv_headers(reader.fieldnames):
                    return album_data

                # Process each row
                for row in reader:
                    self._process_csv_row(row, album_data)

            self.logger.info("Loaded %d album entries from %s", len(album_data), self.album_years_cache_file)

        except (OSError, UnicodeDecodeError, csv.Error) as e:
            self.logger.exception("Error reading album cache file %s: %s", self.album_years_cache_file, e)

        return album_data

    def _validate_csv_headers(self, fieldnames: Sequence[str] | None) -> bool:
        """Validate that CSV has required headers.

        Args:
            fieldnames: Sequence of CSV column headers

        Returns:
            True if headers are valid, False otherwise
        """
        required_headers = {"artist", "album", "year"}

        if not fieldnames:
            self.logger.error("CSV file has no headers")
            return False

        if not required_headers.issubset(set(fieldnames)):
            missing = required_headers - set(fieldnames)
            self.logger.error("CSV file missing required headers: %s", missing)
            return False

        return True

    def _process_csv_row(self, row: dict[str, str], album_data: dict[str, tuple[str, str, str]]) -> None:
        """Process a single CSV row and add to album data.

        Args:
            row: CSV row as dictionary
            album_data: Dictionary to store processed data
        """
        try:
            artist = row["artist"].strip()
            album = row["album"].strip()
            year = row["year"].strip()

            if artist and album and year:
                key = UnifiedHashService.hash_album_key(artist, album)
                album_data[key] = (artist, album, year)
            else:
                self.logger.warning("Skipping invalid CSV row: %s", row)

        except KeyError as e:
            self.logger.warning("Missing required field in CSV row: %s", e)
        except (ValueError, TypeError) as e:
            self.logger.warning("Error processing CSV row %s: %s", row, e)

    @staticmethod
    def _write_csv_data(file_path: str, items: list[tuple[str, str, str]]) -> None:
        """Write album data to CSV file.

        Args:
            file_path: Path to CSV file
            items: List of (artist, album, year) tuples
        """
        with Path(file_path).open("w", encoding="utf-8", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["artist", "album", "year"])
            writer.writerows(items)

    def get_stats(self) -> dict[str, Any]:
        """Get album cache statistics.

        Returns:
            Dictionary containing cache statistics
        """
        return {
            "total_albums": len(self.album_years_cache),
            "cache_file": str(self.album_years_cache_file),
            "cache_file_exists": self.album_years_cache_file.exists(),
            "content_type": CacheContentType.ALBUM_YEAR.value,
            "ttl_policy": 86400,  # Default 1 day
            "persistent": True,  # Album cache is persistent
        }
