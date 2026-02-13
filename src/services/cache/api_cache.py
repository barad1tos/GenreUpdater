"""API Cache Service - Specialized cache for external API responses.

This module provides a dedicated cache service for storing and retrieving
external API responses (Spotify, Last.fm, etc.) with JSON persistence.

Key Features:
- JSON-based persistence for API response data
- Content-aware TTL management (eternal for successful responses, retry TTL for failures)
- Integration with SmartCacheConfig for intelligent caching policies
- Automatic cache invalidation when tracks are removed from library
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.logger import LogFormat, ensure_directory, get_full_log_path
from core.models.normalization import are_names_equal
from core.models.track_models import CachedApiResult
from services.cache.cache_config import CacheContentType, CacheEvent, CacheEventType, EventDrivenCacheManager, SmartCacheConfig
from services.cache.hash_service import UnifiedHashService

if TYPE_CHECKING:
    from core.models.track_models import AppConfig


class ApiCacheService:
    """Specialized cache service for external API responses with JSON persistence."""

    def __init__(self, config: AppConfig, logger: logging.Logger | None = None) -> None:
        """Initialize API cache service.

        Args:
            config: Typed application configuration
            logger: Optional logger instance
        """
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        self.cache_config = SmartCacheConfig(config)
        self.event_manager = EventDrivenCacheManager(self.cache_config)

        # API cache: {hash_key: CachedApiResult}
        self.api_cache: dict[str, CachedApiResult] = {}

        # Background tasks to prevent garbage collection
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._max_background_tasks = 100
        self._shutting_down = False  # Flag to prevent new tasks during shutdown

        # Lock for thread-safe cache operations
        self._cache_lock = asyncio.Lock()

        # Cache file path
        self.api_cache_file = Path(get_full_log_path(config, "api_cache_file", "cache/cache.json"))

        # Register for cache events
        self._register_event_handlers()

    def _register_event_handlers(self) -> None:
        """Register event handlers for cache invalidation."""
        self.event_manager.register_event_handler(CacheEventType.TRACK_REMOVED, self._handle_track_removed)
        self.event_manager.register_event_handler(CacheEventType.TRACK_MODIFIED, self._handle_track_modified)

    def _schedule_invalidation(self, artist: str, album: str, log_message: str | None = None) -> bool:
        """Schedule a background cache invalidation task.

        Args:
            artist: Artist name for cache invalidation
            album: Album name for cache invalidation
            log_message: Optional message to log before scheduling

        Returns:
            True if task was scheduled, False if limit reached or shutting down
        """
        # Prevent new tasks during shutdown to avoid race conditions
        if self._shutting_down:
            self.logger.debug(
                "Shutdown in progress, skipping invalidation for %s - %s",
                artist,
                album,
            )
            return False

        if len(self._background_tasks) >= self._max_background_tasks:
            self.logger.debug(
                "Background task limit reached (%d), skipping invalidation for %s - %s",
                self._max_background_tasks,
                artist,
                album,
            )
            return False

        if log_message:
            self.logger.info(log_message)

        task = asyncio.create_task(self.invalidate_for_album(artist, album))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return True

    def _handle_track_removed(self, event: CacheEvent) -> None:
        """Handle track removal event by invalidating related API cache.

        Args:
            event: Cache event with track information
        """
        if event.track_id and event.metadata:
            artist = event.metadata.get("artist")
            album = event.metadata.get("album")

            if artist and album:
                self._schedule_invalidation(artist, album)

    def _handle_track_modified(self, event: CacheEvent) -> None:
        """Handle track modification event by invalidating cache for old artist/album.

        When user changes artist/album in Music.app, the API cache for the OLD
        combination becomes stale and must be invalidated.

        Args:
            event: Cache event with old artist/album in metadata
        """
        if event.track_id and event.metadata:
            artist = event.metadata.get("artist")
            album = event.metadata.get("album")

            if artist and album:
                self._schedule_invalidation(
                    artist,
                    album,
                    f"Track {event.track_id} identity changed, invalidating cache for: {artist} - {album}",
                )

    async def initialize(self) -> None:
        """Initialize API cache by loading data from disk."""
        self.logger.info("Initializing %s...", LogFormat.entity("ApiCacheService"))
        await self._load_api_cache()
        await self.cleanup_expired()
        self.logger.info("%s initialized with %d entries (after cleanup)", LogFormat.entity("ApiCacheService"), len(self.api_cache))

    async def get_cached_result(self, artist: str, album: str, source: str) -> CachedApiResult | None:
        """Get cached API result.

        Args:
            artist: The artist name for cache key generation.
            album: The album name for cache key generation.
            source: API source (musicbrainz, discogs, etc.)

        Returns:
            Cached API result if found and valid, None otherwise
        """
        async with self._cache_lock:
            key = UnifiedHashService.hash_api_key(artist, album, source)

            if key not in self.api_cache:
                self.logger.debug("API cache miss: %s - %s (%s)", artist, album, source)
                return None

            cached_result = self.api_cache[key]

            # Check TTL based on content type
            if self._is_cache_expired(cached_result):
                self.logger.debug("API cache expired: %s - %s (%s)", artist, album, source)
                del self.api_cache[key]
                return None

            self.logger.debug("API cache hit: %s - %s (%s)", artist, album, source)
            return cached_result

    def _is_cache_expired(self, cached_result: CachedApiResult) -> bool:
        """Check if cached result is expired based on content type.

        Args:
            cached_result: Cached API result to check

        Returns:
            True if expired, False otherwise
        """
        # Successful results are eternal (immutable data like release years)
        # Determine success by checking if we have a year
        has_year = cached_result.year is not None and cached_result.year.strip()
        content_type = CacheContentType.SUCCESSFUL_API_METADATA if has_year else CacheContentType.FAILED_API_LOOKUP

        policy = self.cache_config.get_policy(content_type)

        # Infinite TTL for successful API metadata
        if policy.ttl_seconds >= self.cache_config.INFINITE_TTL:
            return False

        # Check TTL for failed lookups
        cached_time = datetime.fromtimestamp(cached_result.timestamp, UTC)
        age_seconds = (datetime.now(UTC) - cached_time).total_seconds()
        return age_seconds > policy.ttl_seconds

    async def set_cached_result(
        self, artist: str, album: str, source: str, success: bool, data: dict[str, Any] | None = None, metadata: dict[str, Any] | None = None
    ) -> None:
        """Store API result in cache.

        Args:
            artist: The artist name for cache key generation.
            album: The album name for cache key generation.
            source: API source identifier.
            success: Whether the API call was successful.
            data: API response data (if successful).
            metadata: Additional metadata to store.
        """
        key = UnifiedHashService.hash_api_key(artist, album, source)

        # Extract year from data if available (explicit None check to handle falsy values like 0 or empty string)
        year = None
        if data and isinstance(data, dict):
            year_value = data.get("year")
            if year_value is not None:
                year_str = str(year_value).strip()
                year = year_str or None

        # Create cached result - ensure api_response is always a dict for consumers
        # Use explicit dict() constructor to satisfy both runtime and static analysis
        response_data: dict[str, Any] | None = dict(data) if data else None
        cached_result = CachedApiResult(
            artist=artist.strip(),
            album=album.strip(),
            year=year,
            source=source.strip(),
            timestamp=datetime.now(UTC).timestamp(),
            metadata=metadata or {},
            api_response=response_data,
        )

        async with self._cache_lock:
            self.api_cache[key] = cached_result

        self.logger.debug("Stored API result: %s - %s (%s) success=%s", artist, album, source, success)

    async def invalidate_for_album(self, artist: str, album: str) -> None:
        """Invalidate all API cache entries for specific album.

        Args:
            artist: The artist name for cache key generation.
            album: The album name for cache key generation.
        """
        async with self._cache_lock:
            # Find all keys for this artist/album across all sources
            keys_to_remove: list[str] = []

            keys_to_remove.extend(
                key
                for key, cached_result in self.api_cache.items()
                if are_names_equal(cached_result.artist, artist) and are_names_equal(cached_result.album, album)
            )
            # Remove found entries
            for key in keys_to_remove:
                del self.api_cache[key]

        if keys_to_remove:
            self.logger.info("Invalidated %d API cache entries for %s - %s", len(keys_to_remove), artist, album)

    async def invalidate_all(self) -> None:
        """Clear all API cache entries."""
        async with self._cache_lock:
            count = len(self.api_cache)
            self.api_cache.clear()
        self.logger.info("Cleared all API cache entries (%d items)", count)

    async def cleanup_expired(self) -> int:
        """Remove expired entries from API cache.

        Returns:
            Number of entries removed
        """
        async with self._cache_lock:
            expired_keys: list[str] = []

            expired_keys.extend(key for key, cached_result in self.api_cache.items() if self._is_cache_expired(cached_result))
            # Remove expired entries
            for key in expired_keys:
                del self.api_cache[key]

        if expired_keys:
            self.logger.info("Cleaned up %d expired API cache entries", len(expired_keys))

        return len(expired_keys)

    async def save_to_disk(self) -> None:
        """Save API cache to JSON file."""
        if not self.api_cache:
            self.logger.debug("API cache is empty, deleting cache file if exists")
            # Delete cache file to prevent loading stale data on next initialization
            if self.api_cache_file.exists():
                self.api_cache_file.unlink()
                self.logger.info("Deleted empty API cache file: %s", self.api_cache_file)
            return

        def blocking_save() -> None:
            """Synchronous save operation for thread executor."""
            try:
                # Ensure directory exists
                ensure_directory(str(self.api_cache_file.parent))

                # Serialize cache data
                def serialize_model(model: CachedApiResult) -> dict[str, Any]:
                    """Convert CachedApiResult to JSON-serializable dict."""
                    return {
                        "artist": model.artist,
                        "album": model.album,
                        "year": model.year,
                        "source": model.source,
                        "timestamp": model.timestamp,
                        "ttl": model.ttl,
                        "metadata": model.metadata,
                        "api_response": model.api_response,
                    }

                cache_data = {key: serialize_model(result) for key, result in self.api_cache.items()}

                # Write JSON file
                with self.api_cache_file.open("w", encoding="utf-8") as file:
                    json.dump(cache_data, file, indent=2, ensure_ascii=False)

                self.logger.info("API cache saved to %s (%d entries)", self.api_cache_file, len(cache_data))

            except Exception as e:
                self.logger.exception("Failed to save API cache: %s", e)
                raise

        # Run in thread to avoid blocking
        await asyncio.to_thread(blocking_save)

    async def _load_api_cache(self) -> None:
        """Load API cache from JSON file."""
        if not self.api_cache_file.exists():
            self.logger.info("API cache file does not exist, starting with empty cache")
            return

        def blocking_load() -> dict[str, CachedApiResult]:
            """Synchronous load operation for thread executor."""
            try:
                with self.api_cache_file.open(encoding="utf-8") as file:
                    cache_data = json.load(file)

                cache_entries: dict[str, CachedApiResult] = {}

                for key, item in cache_data.items():
                    try:
                        # Create CachedApiResult object with proper fields
                        cached_result = CachedApiResult(
                            artist=item["artist"],
                            album=item["album"],
                            year=item.get("year"),
                            source=item["source"],
                            timestamp=item.get("timestamp", 0.0),
                            ttl=item.get("ttl"),
                            metadata=item.get("metadata", {}),
                            api_response=item.get("api_response"),
                        )

                        cache_entries[key] = cached_result

                    except (KeyError, ValueError, TypeError) as e:
                        self.logger.warning("Skipping invalid API cache entry %s: %s", key, e)

                self.logger.info("Loaded %d API cache entries from %s", len(cache_entries), self.api_cache_file)
                return cache_entries

            except (OSError, ValueError) as e:
                self.logger.exception("Error loading API cache file %s: %s", self.api_cache_file, e)
                return {}

        # Run in thread to avoid blocking
        loaded_cache = await asyncio.to_thread(blocking_load)
        self.api_cache.update(loaded_cache)

    def emit_track_removed(self, track_id: str, artist: str, album: str) -> None:
        """Emit track removed event for cache invalidation.

        Args:
            track_id: Unique track identifier
            artist: Artist name
            album: Album name
        """
        event = CacheEvent(event_type=CacheEventType.TRACK_REMOVED, track_id=track_id, metadata={"artist": artist, "album": album})
        self.event_manager.emit_event(event)

    def emit_track_modified(self, track_id: str, old_artist: str, old_album: str) -> None:
        """Emit track modified event for cache invalidation.

        Called when user changes artist/album in Music.app. Invalidates cache
        for the OLD artist/album combination since it's now stale.

        Args:
            track_id: Unique track identifier
            old_artist: Previous artist name (before user edit)
            old_album: Previous album name (before user edit)
        """
        event = CacheEvent(event_type=CacheEventType.TRACK_MODIFIED, track_id=track_id, metadata={"artist": old_artist, "album": old_album})
        self.event_manager.emit_event(event)

    async def shutdown(self) -> None:
        """Gracefully shutdown background tasks.

        Waits for all pending background tasks to complete, handling
        any exceptions that may occur during task execution.
        """
        # Set flag first to prevent new tasks from being scheduled during shutdown
        self._shutting_down = True

        if not self._background_tasks:
            self.logger.debug("ApiCacheService shutdown complete (no pending tasks)")
            return

        self.logger.debug(
            "Shutting down ApiCacheService with %d pending tasks",
            len(self._background_tasks),
        )

        # Wait for all tasks, capturing exceptions
        await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()

        self.logger.debug("ApiCacheService shutdown complete")

    def get_stats(self) -> dict[str, Any]:
        """Get API cache statistics.

        Returns:
            Dictionary containing cache statistics
        """
        successful_results = [result for result in self.api_cache.values() if result.year is not None and result.year.strip()]
        successful_count = len(successful_results)
        failed_count = len(self.api_cache) - successful_count

        return {
            "total_entries": len(self.api_cache),
            "successful_responses": successful_count,
            "failed_lookups": failed_count,
            "cache_file": str(self.api_cache_file),
            "cache_file_exists": self.api_cache_file.exists(),
            "successful_policy": self.cache_config.get_policy(CacheContentType.SUCCESSFUL_API_METADATA).ttl_seconds,
            "failed_policy": self.cache_config.get_policy(CacheContentType.FAILED_API_LOOKUP).ttl_seconds,
            "persistent": self.cache_config.is_persistent_cache(CacheContentType.SUCCESSFUL_API_METADATA),
        }
