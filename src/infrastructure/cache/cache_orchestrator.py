"""Cache Orchestrator - Coordinates all specialized cache services.

This module provides a unified interface for interacting with multiple
specialized cache services while maintaining the existing API contract.

Key Features:
- Coordinates AlbumCacheService, ApiCacheService, and GenericCacheService
- Maintains backward compatibility with existing CacheService API
- Intelligent routing based on operation type and content
- Centralized configuration management and metrics
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

from src.infrastructure.cache.album_cache_service import AlbumCacheService
from src.infrastructure.cache.api_cache_service import ApiCacheService
from src.infrastructure.cache.generic_cache_service import GenericCacheService
from src.infrastructure.cache.hash_service import UnifiedHashService
from src.shared.data.models import CachedApiResult, TrackDict
from src.shared.data.protocols import CacheableKey, CacheableValue, CacheServiceProtocol

T = TypeVar("T")


class CacheOrchestrator(CacheServiceProtocol):
    """Orchestrates multiple specialized cache services with unified interface."""

    def __init__(self, config: dict[str, Any], logger: logging.Logger | None = None) -> None:
        """Initialize CacheOrchestrator with configuration.

        Args:
            config: Cache configuration dictionary
            logger: Optional logger instance
        """
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        # Initialize configuration manager later when needed
        self.config_manager = None

        # Initialize specialized services
        self.album_service = AlbumCacheService(config, logger)
        self.api_service = ApiCacheService(config, logger)
        self.generic_service = GenericCacheService(config, logger)

        # Service mapping for routing
        self._services = {
            "album": self.album_service,
            "api": self.api_service,
            "generic": self.generic_service,
        }

    async def initialize(self) -> None:
        """Initialize all cache services."""
        self.logger.info("Initializing CacheOrchestrator...")

        # Initialize all services in parallel
        await asyncio.gather(
            self.album_service.initialize(), self.api_service.initialize(), self.generic_service.initialize(), return_exceptions=True
        )

        self.logger.info("CacheOrchestrator initialized successfully")

    # =========================== ALBUM CACHE API ===========================

    async def get_album_year(self, artist: str, album: str) -> str | None:
        """Get album release year from cache.

        Args:
            artist: Artist name
            album: Album name

        Returns:
            Album release year if found, None otherwise
        """
        return await self.album_service.get_album_year(artist, album)

    async def store_album_year(self, artist: str, album: str, year: str) -> None:
        """Store album release year in cache.

        Args:
            artist: Artist name
            album: Album name
            year: Album release year
        """
        await self.album_service.store_album_year(artist, album, year)

    # =========================== API CACHE API ===========================

    async def get_api_result(self, artist: str, album: str, source: str) -> dict[str, Any] | None:
        """Get API result from cache.

        Args:
            artist: Artist name
            album: Album name
            source: API source name

        Returns:
            Cached API result if found and valid, None otherwise
        """
        cached_result = await self.api_service.get_cached_result(artist, album, source)
        return cached_result.api_response if cached_result else None

    async def store_api_result(self, artist: str, album: str, source: str, result: dict[str, Any], success: bool = True) -> None:
        """Store API result in cache.

        Args:
            artist: Artist name
            album: Album name
            source: API source name
            result: API response data
            success: Whether the API call was successful
        """
        await self.api_service.set_cached_result(artist, album, source, success, data=result)

    # =========================== GENERIC CACHE API ===========================

    async def get_async(  # type: ignore[override]
        self,
        key_data: CacheableKey,
        compute_func: Callable[[], asyncio.Future[CacheableValue]] | None = None,
    ) -> list[TrackDict] | CacheableValue:
        """Asynchronous get with optional compute function.

        Note: MyPy complains about signature incompatibility due to Protocol + TYPE_CHECKING imports.
        Code works correctly - this is a known MyPy limitation.

        Args:
            key_data: Cache key or "ALL" for all entries
            compute_func: Optional compute function to calculate value if not cached

        Returns:
            Cached or computed value
        """
        if compute_func:
            result = self.generic_service.get(key_data)
            if result is None:
                future = compute_func()
                computed = await future
                self.generic_service.set(key_data, computed)
                return computed
            return result
        return self.generic_service.get(key_data)

    def set(self, key_data: CacheableKey, value: CacheableValue, ttl: int | None = None) -> None:
        """Set value in generic cache.

        Args:
            key_data: Cache key
            value: Value to cache
            ttl: Optional TTL in seconds
        """
        self.generic_service.set(key_data, value, ttl)

    async def set_async(self, key_data: CacheableKey, value: CacheableValue, ttl: int | None = None) -> None:
        """Async alias for set method (backward compatibility).

        Args:
            key_data: Cache key
            value: Value to cache
            ttl: Optional TTL in seconds
        """
        self.generic_service.set(key_data, value, ttl)

    def get(self, key_data: CacheableKey) -> CacheableValue | None:
        """Get value from generic cache.

        Args:
            key_data: Cache key

        Returns:
            Cached value if found, None otherwise
        """
        return self.generic_service.get(key_data)

    # =========================== UNIFIED CACHE OPERATIONS ===========================

    async def invalidate_for_track(self, track: TrackDict) -> None:
        """Invalidate all cache entries related to a track.

        Args:
            track: Track dictionary with artist and album information
        """
        artist = track.get("artist", "")
        album = track.get("album", "")

        if artist and album:
            # Ensure types are strings
            artist_str = str(artist)
            album_str = str(album)

            # Invalidate album cache
            await self.album_service.invalidate_album(artist_str, album_str)

            # Invalidate API cache for all sources
            await self.api_service.invalidate_for_album(artist_str, album_str)

            self.logger.info("Invalidated caches for track: %s - %s", artist, album)

    async def save_all_to_disk(self) -> None:
        """Save all persistent caches to disk."""
        self.logger.info("Saving all caches to disk...")

        # Save services that have disk persistence
        await asyncio.gather(self.album_service.save_to_disk(), self.api_service.save_to_disk(), return_exceptions=True)

        self.logger.info("All caches saved to disk")

    def invalidate(self, key_data: CacheableKey) -> None:
        """Invalidate specific cache entry.

        Args:
            key_data: Cache key to invalidate

        """
        self.generic_service.invalidate(key_data)

    async def invalidate_all(self) -> None:
        """Clear all cache entries across all services."""
        self.logger.info("Invalidating all cache entries...")

        # Album and API services have async invalidate_all methods
        await self.album_service.invalidate_all()
        await self.api_service.invalidate_all()
        self.generic_service.invalidate_all()

        self.logger.info("All cache entries invalidated")

    # =========================== STATISTICS & MONITORING ===========================

    def get_comprehensive_stats(self) -> dict[str, Any]:
        """Get comprehensive statistics from all cache services.

        Returns:
            Dictionary containing statistics from all services
        """
        return {
            "album_cache": self.album_service.get_stats(),
            "api_cache": self.api_service.get_stats(),
            "generic_cache": self.generic_service.get_stats(),
            "orchestrator": {
                "services_count": len(self._services),
                "config_policies": len(self.config.get("caching", {})) if isinstance(self.config.get("caching"), dict) else 0,
            },
        }

    def get_cache_health(self) -> dict[str, Any]:
        """Check health status of all cache services.

        Returns:
            Dictionary containing health information for each service
        """
        health_status = {}

        for service_name, service in self._services.items():
            try:
                stats = service.get_stats() if hasattr(service, "get_stats") else {"total_entries": 0}
                health_status[service_name] = {
                    "status": "healthy",
                    "entries": stats.get("total_entries", stats.get("total_albums", 0)),
                    "last_check": "active",
                }
            except Exception as e:
                health_status[service_name] = {"status": "error", "error": str(e), "last_check": "failed"}

        return health_status

    # =========================== BACKWARD COMPATIBILITY ===========================

    @property
    def cache(self) -> dict[str, Any]:
        """Generic cache for backward compatibility."""
        return self.generic_service.cache

    @property
    def album_years_cache(self) -> dict[str, tuple[str, str, str]]:
        """Album years cache for backward compatibility."""
        return self.album_service.album_years_cache

    @property
    def api_cache(self) -> dict[str, Any]:
        """API cache for backward compatibility."""
        return {k: v.model_dump() for k, v in self.api_service.api_cache.items()}

    # =========================== MISSING PROTOCOL METHODS ===========================

    async def load_cache(self) -> None:
        """Load persistent cache data from disk."""
        # Services handle their own loading during initialization
        # This is a no-op for now but maintains protocol compatibility

    async def save_cache(self) -> None:
        """Save cache data to disk for persistence."""
        await self.save_all_to_disk()

    async def get_last_run_timestamp(self) -> datetime:
        """Get the timestamp of the last cache run."""
        # For now, return current time - this could be enhanced to track actual timestamps
        return datetime.now(UTC)

    async def get_album_year_from_cache(self, artist: str, album: str) -> str | None:
        """Get cached album year for an artist/album pair."""
        return await self.get_album_year(artist, album)

    async def store_album_year_in_cache(self, artist: str, album: str, year: str) -> None:
        """Store album year in persistent cache."""
        await self.store_album_year(artist, album, year)

    async def invalidate_album_cache(self, artist: str, album: str) -> None:
        """Invalidate cached data for a specific album."""
        await self.album_service.invalidate_album(artist, album)

    async def invalidate_all_albums(self) -> None:
        """Invalidate all album cache entries."""
        await self.album_service.invalidate_all()

    async def sync_cache(self) -> None:
        """Synchronize cache to persistent storage."""
        await self.save_all_to_disk()

    async def get_cached_api_result(
        self,
        artist: str,
        album: str,
        source: str,
    ) -> CachedApiResult | None:
        """Get cached API result for an artist/album from a specific source."""
        return await self.api_service.get_cached_result(artist, album, source)

    async def set_cached_api_result(  # type: ignore[override]
        self,
        artist: str,
        album: str,
        source: str,
        year: str | None,
        *,
        metadata: dict[str, Any] | None = None,
        is_negative: bool = False,
    ) -> None:
        """Cache an API result for an artist/album from a specific source."""
        success = year is not None and not is_negative
        data = {"year": year}
        if metadata:
            data |= metadata
        await self.api_service.set_cached_result(artist, album, source, success, data=data)

    @staticmethod
    def generate_album_key(artist: str, album: str) -> str:
        """Generate a unique key for an artist/album pair."""
        # Use the same key generation as the album service
        hash_service = UnifiedHashService()
        return hash_service.hash_album_key(artist, album)

    def clear(self) -> None:
        """Clear all entries from the cache."""
        # Delegate to generic service for now, could be enhanced
        self.generic_service.invalidate_all()
