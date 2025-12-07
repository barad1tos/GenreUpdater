"""Album cache service with TTL-aware entries and CSV persistence."""

from __future__ import annotations

import asyncio
import contextlib
import csv
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from services.cache.cache_config import CacheContentType, SmartCacheConfig
from services.cache.hash_service import UnifiedHashService
from core.logger import LogFormat, ensure_directory, get_full_log_path

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(slots=True)
class AlbumCacheEntry:
    """Album cache entry with timestamp for TTL management."""

    artist: str
    album: str
    year: str
    timestamp: float


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
        self.cache_config = SmartCacheConfig(config)
        self.policy = self.cache_config.get_policy(CacheContentType.ALBUM_YEAR)

        # Album years cache: {hash_key: AlbumCacheEntry}
        self.album_years_cache: dict[str, AlbumCacheEntry] = {}

        # Cache file paths - use get_full_log_path to ensure proper logs_base_dir integration
        self.album_years_cache_file = Path(get_full_log_path(config, "album_years_cache_file", "cache/album_years.csv"))

    async def initialize(self) -> None:
        """Initialize album cache by loading data from disk."""
        self.logger.info("Initializing %s...", LogFormat.entity("AlbumCacheService"))
        await self._load_album_years_cache()
        self.logger.info("%s initialized with %d albums", LogFormat.entity("AlbumCacheService"), len(self.album_years_cache))

    async def get_album_year(self, artist: str, album: str) -> str | None:
        """Get album release year from cache.

        Args:
            artist: Artist name
            album: Album name

        Returns:
            Album release year if found, None otherwise
        """
        # Yield to event loop for proper async behavior (no actual I/O, but async interface required)
        await asyncio.sleep(0)
        key = UnifiedHashService.hash_album_key(artist, album)

        if key in self.album_years_cache:
            entry = self.album_years_cache[key]

            # Validate cache entry consistency (detect true hash collision)
            if entry.artist.lower().strip() != artist.lower().strip() or entry.album.lower().strip() != album.lower().strip():
                self.logger.warning(
                    "Hash collision detected: requested '%s - %s', found '%s - %s'",
                    artist,
                    album,
                    entry.artist,
                    entry.album,
                )
                # Don't delete - keep the original entry, just miss for this request
                return None

            if self._is_entry_expired(entry):
                self.logger.debug("Album year cache expired: %s - %s", artist, album)
                del self.album_years_cache[key]
                return None

            self.logger.debug("Album year cache hit: %s - %s = %s", artist, album, entry.year)
            return entry.year

        self.logger.debug("Album year cache miss: %s - %s", artist, album)
        return None

    async def store_album_year(self, artist: str, album: str, year: str) -> None:
        """Store album release year in cache.

        Args:
            artist: Artist name
            album: Album name
            year: Album release year
        """
        # Yield to event loop for proper async behavior (no actual I/O, but async interface required)
        await asyncio.sleep(0)
        key = UnifiedHashService.hash_album_key(artist, album)

        # Store with normalized values for consistency
        normalized_artist = artist.strip()
        normalized_album = album.strip()
        normalized_year = year.strip()

        self.album_years_cache[key] = AlbumCacheEntry(
            artist=normalized_artist,
            album=normalized_album,
            year=normalized_year,
            timestamp=time.time(),
        )
        self.logger.debug("Stored album year: %s - %s = %s", normalized_artist, normalized_album, normalized_year)

    async def invalidate_album(self, artist: str, album: str) -> None:
        """Invalidate specific album from cache.

        Args:
            artist: Artist name
            album: Album name
        """
        # Yield to event loop for proper async behavior (no actual I/O, but async interface required)
        await asyncio.sleep(0)
        key = UnifiedHashService.hash_album_key(artist, album)

        if key in self.album_years_cache:
            del self.album_years_cache[key]
            self.logger.info("Invalidated album cache: %s - %s", artist, album)

    async def invalidate_all(self) -> None:
        """Clear all album cache entries."""
        # Yield to event loop for proper async behavior (no actual I/O, but async interface required)
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
                self.logger.info("Album cache saved to [cyan]%s[/cyan] (%d entries)", self.album_years_cache_file.name, len(items))

            except (OSError, UnicodeError) as e:
                self.logger.exception("Failed to save album cache: %s", e)
                raise

        # Run in thread executor to avoid blocking
        await asyncio.get_running_loop().run_in_executor(None, blocking_save)

    async def _load_album_years_cache(self) -> None:
        """Load album years cache from CSV file."""
        if not self.album_years_cache_file.exists():
            self.logger.info("Album cache file does not exist, starting with empty cache")
            return

        def blocking_load() -> dict[str, AlbumCacheEntry]:
            """Synchronous load operation for thread executor."""
            return self._read_csv_file()

        # Run in thread executor to avoid blocking
        loaded_cache = await asyncio.get_running_loop().run_in_executor(None, blocking_load)
        self.album_years_cache.update(loaded_cache)

    def _read_csv_file(self) -> dict[str, AlbumCacheEntry]:
        """Read and parse CSV file containing album years data.

        Returns:
            Dictionary mapping hash keys to (artist, album, year) tuples
        """
        album_data: dict[str, AlbumCacheEntry] = {}

        try:
            with self.album_years_cache_file.open(encoding="utf-8") as file:
                reader = csv.DictReader(file)

                # Validate CSV headers (raises ValueError if invalid)
                self._validate_csv_headers(reader.fieldnames)

                # Process each row
                for row in reader:
                    self._process_csv_row(row, album_data)

            self.logger.info("Loaded %d album entries from [cyan]%s[/cyan]", len(album_data), self.album_years_cache_file.name)

        except (OSError, UnicodeDecodeError, csv.Error) as e:
            self.logger.exception("Error reading album cache file %s: %s", self.album_years_cache_file, e)

        return album_data

    def _validate_csv_headers(self, fieldnames: Sequence[str] | None) -> bool:
        """Validate that CSV has required headers.

        Args:
            fieldnames: Sequence of CSV column headers

        Returns:
            True if headers are valid

        Raises:
            ValueError: If CSV headers are missing or invalid
        """
        required_headers = {"artist", "album", "year"}

        if not fieldnames:
            msg = "CSV file has no headers"
            self.logger.error(msg)
            raise ValueError(msg)

        if not required_headers.issubset(set(fieldnames)):
            missing = required_headers - set(fieldnames)
            msg = f"CSV file missing required headers: {missing}"
            self.logger.error(msg)
            raise ValueError(msg)

        return True

    def _process_csv_row(self, row: dict[str, str], album_data: dict[str, AlbumCacheEntry]) -> None:
        """Process a single CSV row and add to album data.

        Args:
            row: CSV row as dictionary
            album_data: Dictionary to store processed data
        """
        try:
            artist = row["artist"].strip()
            album = row["album"].strip()
            year = row["year"].strip()
            timestamp_raw = row.get("timestamp", "")
            timestamp = float(timestamp_raw) if timestamp_raw else time.time()

            if artist and album and year:
                key = UnifiedHashService.hash_album_key(artist, album)
                album_data[key] = AlbumCacheEntry(
                    artist=artist,
                    album=album,
                    year=year,
                    timestamp=timestamp,
                )
            else:
                self.logger.warning("Skipping invalid CSV row: %s", row)

        except KeyError as e:
            self.logger.warning("Missing required field in CSV row: %s", e)
        except (ValueError, TypeError) as e:
            self.logger.warning("Error processing CSV row %s: %s", row, e)

    @staticmethod
    def _write_csv_data(file_path: str, items: list[AlbumCacheEntry]) -> None:
        """Write album data to CSV file atomically.

        Uses a temporary file and atomic replace to prevent data loss
        if the write operation fails mid-way.

        Args:
            file_path: Path to CSV file
            items: List of AlbumCacheEntry objects
        """
        file_path_obj = Path(file_path)
        dir_name = file_path_obj.parent

        # Write to temp file first, then atomically replace
        fd, temp_path = tempfile.mkstemp(suffix=".csv", dir=str(dir_name))
        try:
            # Guard against fdopen failures so we don't leak the descriptor
            try:
                file_obj = os.fdopen(fd, "w", encoding="utf-8", newline="")
            except BaseException:
                # fdopen takes ownership only on success; close explicitly on failure
                with contextlib.suppress(OSError):
                    os.close(fd)
                raise

            with file_obj:
                writer = csv.writer(file_obj)
                writer.writerow(["artist", "album", "year", "timestamp"])
                for item in items:
                    writer.writerow([item.artist, item.album, item.year, f"{item.timestamp:.6f}"])
            # Atomic replace - if this fails, original file is untouched
            Path(temp_path).replace(file_path_obj)
        except BaseException:
            # Clean up temp file on any error
            with contextlib.suppress(OSError):
                Path(temp_path).unlink()
            raise

    def get_stats(self) -> dict[str, Any]:
        """Get album cache statistics.

        Returns:
            Dictionary containing cache statistics
        """
        ttl_seconds = self.policy.ttl_seconds
        return {
            "total_albums": len(self.album_years_cache),
            "cache_file": str(self.album_years_cache_file),
            "cache_file_exists": self.album_years_cache_file.exists(),
            "content_type": CacheContentType.ALBUM_YEAR.value,
            "ttl_policy": ttl_seconds,
            "persistent": ttl_seconds >= self.cache_config.INFINITE_TTL,
        }

    def _is_entry_expired(self, entry: AlbumCacheEntry) -> bool:
        """Check whether cache entry is expired based on configuration."""
        ttl_seconds = self.policy.ttl_seconds
        if ttl_seconds >= self.cache_config.INFINITE_TTL:
            return False
        return (time.time() - entry.timestamp) > ttl_seconds
