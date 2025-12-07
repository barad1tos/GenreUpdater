"""Service Protocol Definitions.

This module defines protocols (interfaces) for all service classes to improve
type safety, enable better testing through mocking, and reduce coupling between
layers. These protocols define the public API contract that implementations must follow.

By using protocols instead of concrete classes, we achieve:
- Better decoupling between layers (core doesn't depend on service implementations)
- Easier testing through mock implementations
- Clear API contracts for each service
- Support for multiple implementations (e.g., DryRun variants)
"""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    Protocol,
    TypeVar,
    overload,
    runtime_checkable,
)

from core.models.track_models import TrackDict

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable
    from datetime import datetime

    from core.models.track_models import CachedApiResult
    from services.pending_verification import PendingAlbumEntry

# Type variable for generic cached values
T = TypeVar("T")

# Define cacheable types
CacheableValue = str | int | float | bool | dict[str, Any] | list[Any] | list[TrackDict] | TrackDict | None
CacheableKey = str | int


# noinspection PyMissingOrEmptyDocstring
@runtime_checkable
class CacheServiceProtocol(Protocol):
    """Protocol defining the interface for cache services.

    This protocol defines methods for both in-memory caching with TTL support
    and persistent CSV-based caching for album information.
    """

    async def initialize(self) -> None:
        """Initialize the cache service, loading any persistent data."""
        ...

    def set(self, key_data: CacheableKey, value: CacheableValue, ttl: int | None = None) -> None:
        """Set a value in the cache with optional TTL.

        Args:
            key_data: Key for the cached value (will be hashed if needed)
            value: Value to cache
            ttl: Time-to-live in seconds (optional)

        """
        ...

    async def set_async(
        self,
        key_data: CacheableKey,
        value: CacheableValue,
        ttl: int | None = None,
    ) -> None:
        """Asynchronously set a value in the cache with optional TTL.

        Args:
            key_data: Key for the cached value (will be hashed if needed)
            value: Value to cache
            ttl: Time-to-live in seconds (optional)

        """
        ...

    @overload
    async def get_async(
        self,
        key_data: Literal["ALL"],
        compute_func: None = None,
    ) -> list[TrackDict]: ...

    @overload
    async def get_async(
        self,
        key_data: str,
        compute_func: None = None,
    ) -> list[TrackDict] | None: ...

    @overload
    async def get_async(
        self,
        key_data: CacheableKey,
        compute_func: Callable[[], asyncio.Future[CacheableValue]] | None = None,
    ) -> CacheableValue: ...

    async def get_async(
        self,
        key_data: CacheableKey,
        compute_func: Callable[[], asyncio.Future[CacheableValue]] | None = None,
    ) -> list[TrackDict] | CacheableValue:
        """Get a value from cache, optionally computing it if not present.

        Args:
            key_data: Key for the cached value ("ALL" for all tracks)
            compute_func: Optional compute function to calculate value if not cached

        Returns:
            Cached value or computed value from factory, or None if not found

        """
        ...

    def invalidate(self, key_data: CacheableKey) -> None:
        """Invalidate (remove) a specific cache entry.

        Args:
            key_data: Key of the cache entry to invalidate

        """
        ...

    async def clear(self) -> None:
        """Clear all entries from the cache."""
        ...

    async def load_cache(self) -> None:
        """Load persistent cache data from disk."""
        ...

    async def save_cache(self) -> None:
        """Save cache data to disk for persistence."""
        ...

    async def get_last_run_timestamp(self) -> datetime:
        """Get the timestamp of the last cache run.

        Returns:
            DateTime of last run, or epoch if never run

        """
        ...

    async def get_album_year_from_cache(self, artist: str, album: str) -> str | None:
        """Get cached album year for an artist/album pair.

        Args:
            artist: Artist name
            album: Album name

        Returns:
            Cached year string or None if not found

        """
        ...

    async def store_album_year_in_cache(
        self,
        artist: str,
        album: str,
        year: str,
    ) -> None:
        """Store album year in persistent cache.

        Args:
            artist: Artist name
            album: Album name
            year: Year to cache

        """
        ...

    async def invalidate_album_cache(self, artist: str, album: str) -> None:
        """Invalidate cached data for a specific album.

        Args:
            artist: Artist name
            album: Album name

        """
        ...

    async def invalidate_all_albums(self) -> None:
        """Invalidate all album cache entries."""
        ...

    async def sync_cache(self) -> None:
        """Synchronize cache to persistent storage."""
        ...

    async def get_cached_api_result(
        self,
        artist: str,
        album: str,
        source: str,
    ) -> CachedApiResult | None:
        """Get cached API result for an artist/album from a specific source.

        Args:
            artist: Artist name
            album: Album name
            source: API source identifier

        Returns:
            Cached API result or None if not found

        """
        ...

    async def set_cached_api_result(
        self,
        artist: str,
        album: str,
        source: str,
        year: str | None,
        *,
        metadata: dict[str, Any] | None = None,
        is_negative: bool = False,
    ) -> None:
        """Cache an API result for an artist/album from a specific source.

        Args:
            artist: Artist name
            album: Album name
            source: API source identifier
            year: Year to cache (None for negative cache)
            metadata: Optional metadata about the result
            is_negative: Whether this is a negative cache entry

        """
        ...

    async def invalidate_for_track(self, track: TrackDict) -> None:
        """Invalidate caches impacted by a specific track update."""
        ...

    @staticmethod
    def generate_album_key(artist: str, album: str) -> str:
        """Generate a unique key for an artist/album pair.

        Args:
            artist: Artist name
            album: Album name

        Returns:
            Unique key string (typically a hash)

        """
        ...


@runtime_checkable
class ExternalApiServiceProtocol(Protocol):
    """Protocol defining the interface for external API services.

    This protocol defines methods for fetching music metadata from external
    sources like MusicBrainz, Last.fm, and Discogs.
    """

    async def initialize(self, force: bool = False) -> None:
        """Initialize the external API service.

        Args:
            force: Force re-initialization even if already initialized

        """
        ...

    async def close(self) -> None:
        """Close all connections and clean up resources."""
        ...

    def should_update_album_year(
        self,
        tracks: list[dict[str, str]],
        artist: str = "",
        album: str = "",
        current_library_year: str = "",
    ) -> bool:
        """Determine whether to update the year for an album based on the status of its tracks.

        Args:
            tracks: List of track dictionaries
            artist: Artist name (optional)
            album: Album name (optional)
            current_library_year: Current year in library (optional)

        Returns:
            True if you should update, False otherwise

        """
        ...

    async def get_album_year(
        self,
        artist: str,
        album: str,
        current_library_year: str | None = None,
    ) -> tuple[str | None, bool]:
        """Determine the original release year for an album using optimized API calls and revised scoring.

        Args:
            artist: Artist name
            album: Album name
            current_library_year: Current year in library (optional)

        Returns:
            Tuple of (year_string, is_definitive) where is_definitive indicates confidence

        """
        ...

    async def get_artist_activity_period(
        self,
        artist_norm: str,
    ) -> tuple[int | None, int | None]:
        """Retrieve the period of activity for an artist from MusicBrainz, with caching.

        Args:
            artist_norm: Normalized artist name

        Returns:
            Tuple of (start_year, end_year) as integers or (None, None) if not found

        """
        ...

    async def get_year_from_discogs(
        self,
        artist: str,
        album: str,
    ) -> str | None:
        """Fetch the earliest release year for an album from Discogs.

        Args:
            artist: Artist name
            album: Album name

        Returns:
            Year string or None if not found

        """
        ...


@runtime_checkable
class AppleScriptClientProtocol(Protocol):
    """Protocol defining the interface for AppleScript clients.

    This protocol allows both real and dry-run implementations to be used
    interchangeably for executing AppleScript commands.
    """

    apple_scripts_dir: str | None

    async def initialize(self) -> None:
        """Initialize the AppleScript client."""
        ...

    async def run_script(
        self,
        script_name: str,
        arguments: list[str] | None = None,
        timeout: float | None = None,
        context_artist: str | None = None,
        context_album: str | None = None,
        context_track: str | None = None,
        label: str | None = None,
    ) -> str | None:
        """Run an AppleScript file by name.

        Args:
            script_name: Name of the script file to execute
            arguments: Optional arguments to pass to the script
            timeout: Optional timeout in seconds
            context_artist: Artist name for contextual logging (optional)
            context_album: Album name for contextual logging (optional)
            context_track: Track name for contextual logging (optional)
            label: Custom label for logging (defaults to script_name)

        Returns:
            Script output or None if no output

        """
        ...

    async def run_script_code(
        self,
        script_code: str,
        arguments: list[str] | None = None,
        timeout: float | None = None,
    ) -> str | None:
        """Run raw AppleScript code.

        Args:
            script_code: AppleScript code to execute
            arguments: Optional arguments to pass to the script
            timeout: Optional timeout in seconds

        Returns:
            Script output or None if no output

        """
        ...

    async def fetch_tracks_by_ids(
        self,
        track_ids: list[str],
        batch_size: int = 1000,
        timeout: float | None = None,
    ) -> list[dict[str, str]]:
        """Fetch tracks by their IDs using fetch_tracks_by_ids.scpt.

        Args:
            track_ids: List of track IDs to fetch
            batch_size: Maximum number of IDs per batch (default: 1000)
            timeout: Timeout in seconds for script execution

        Returns:
            List of track dictionaries with metadata

        """
        ...

    async def fetch_all_track_ids(self, timeout: float | None = None) -> list[str]:
        """Fetch just track IDs from Music.app (lightweight operation).

        This is used by Smart Delta to detect new/removed tracks without
        fetching full metadata.

        Args:
            timeout: Timeout in seconds for script execution

        Returns:
            List of track ID strings

        """
        ...


@runtime_checkable
class PendingVerificationServiceProtocol(Protocol):
    """Protocol defining the interface for pending verification services.

    This protocol defines methods for managing albums that need manual
    verification or have pending updates.
    """

    async def initialize(self) -> None:
        """Initialize the pending verification service."""
        ...

    async def mark_for_verification(
        self,
        artist: str,
        album: str,
        reason: str = "no_year_found",
        metadata: dict[str, Any] | None = None,
        recheck_days: int | None = None,
    ) -> None:
        """Mark an album for future verification.

        Args:
            artist: Artist name
            album: Album name
            reason: Reason for marking for verification
            metadata: Additional metadata to store
            recheck_days: Optional override for verification interval in days

        """
        ...

    async def remove_from_pending(
        self,
        artist: str,
        album: str,
    ) -> None:
        """Remove an album from pending verification.

        Args:
            artist: Artist name
            album: Album name

        """
        ...

    async def get_all_pending_albums(
        self,
    ) -> list[PendingAlbumEntry]:
        """Get all pending albums.

        Returns:
            List of PendingAlbumEntry objects

        """
        ...

    async def generate_problematic_albums_report(
        self,
        min_attempts: int = 3,
    ) -> int:
        """Generate report of albums that failed to get year after multiple attempts.

        Args:
            min_attempts: Minimum number of attempts to consider problematic

        Returns:
            Number of problematic albums found

        """
        ...


@runtime_checkable
class RateLimiterProtocol(Protocol):
    """Protocol defining the interface for rate limiting services.

    This protocol ensures API calls respect rate limits and prevent
    overwhelming external services.
    """

    async def acquire(self) -> float:
        """Acquire permission to make a request.

        Returns:
            Wait time before the request was allowed

        """
        ...

    def release(self) -> None:
        """Release a request slot (for cleanup/error cases)."""
        ...

    def get_stats(self) -> dict[str, Any]:
        """Get rate limiter statistics.

        Returns:
            Dictionary containing rate limit statistics

        """
        ...


@runtime_checkable
class AnalyticsProtocol(Protocol):
    """Protocol defining the interface for analytics services.

    This protocol defines methods for tracking application metrics,
    performance data, and usage statistics.
    """

    def track_event(
        self,
        event_name: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        """Track an analytics event.

        Args:
            event_name: Name of the event
            properties: Optional event properties

        """
        ...

    def track_error(
        self,
        error: Exception,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Track an error occurrence.

        Args:
            error: Exception that occurred
            context: Optional context information

        """
        ...

    def track_performance(
        self,
        operation: str,
        duration: float,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Track performance metrics.

        Args:
            operation: Name of the operation
            duration: Duration in seconds
            metadata: Optional additional metadata

        """
        ...

    def get_statistics(self) -> dict[str, Any]:
        """Get analytics statistics.

        Returns:
            Dictionary containing analytics data

        """
        ...

    def flush(self) -> None:
        """Flush any pending analytics data."""
        ...
