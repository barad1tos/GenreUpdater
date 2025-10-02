"""Mock implementations of protocol interfaces for testing.

These mocks properly implement the protocol interfaces defined in src.shared.data.protocols.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, overload

from src.shared.data.models import CachedApiResult, TrackDict

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from src.shared.data.protocols import (
        AppleScriptClientProtocol,
        CacheableKey,
        CacheableValue,
        CacheServiceProtocol,
        ExternalApiServiceProtocol,
        PendingVerificationServiceProtocol,
    )


class MockAppleScriptError(RuntimeError):
    """Raised when the mock AppleScript client is configured to fail."""


class MockAppleScriptClient:
    """Mock implementation of AppleScriptClientProtocol for testing."""

    def __init__(self) -> None:
        """Initialize the mock AppleScript client."""
        self.apple_scripts_dir: str | None = "/fake/scripts"
        self.scripts_run: list[tuple[str, list[str] | None]] = []
        self.script_contexts: list[dict[str, Any]] = []
        self.script_code_calls: list[dict[str, Any]] = []
        self.script_responses: dict[str, str | None] = {}
        self.should_fail = False
        self.failure_message = "AppleScript Error"
        self.is_initialized = False

    async def initialize(self) -> None:
        """Initialize the AppleScript client."""
        self.is_initialized = True

    async def run_script(
        self,
        script_name: str,
        arguments: list[str] | None = None,
        timeout: float | None = None,
        context_artist: str | None = None,
        context_album: str | None = None,
        context_track: str | None = None,
    ) -> str | None:
        """Run an AppleScript file by name.

        Args:
            script_name: Name of the script file to execute
            arguments: Optional arguments to pass to the script
            timeout: Optional timeout in seconds
            context_artist: Artist name for contextual logging
            context_album: Album name for contextual logging
            context_track: Track name for contextual logging

        Returns:
            Script output or None if no output
        """
        self.scripts_run.append((script_name, arguments))
        self.script_contexts.append(
            {
                "script_name": script_name,
                "arguments": list(arguments) if arguments is not None else None,
                "timeout": timeout,
                "context_artist": context_artist,
                "context_album": context_album,
                "context_track": context_track,
            }
        )

        if self.should_fail:
            raise MockAppleScriptError(self.failure_message)

        # Handle batch processing for fetch_tracks.scpt
        if script_name == "fetch_tracks.scpt" and arguments and len(arguments) >= 3:
            # Check if this is a batch request (has offset and limit)
            try:
                offset = int(arguments[1]) if arguments[1] else 1
                limit = int(arguments[2]) if arguments[2] else 1000

                # If we have a predefined response with batch support
                if script_name in self.script_responses:
                    full_response = self.script_responses[script_name]
                    # Parse the full response to simulate batching
                    tracks = full_response.split("\x1d") if full_response else []

                    # Calculate batch slice (offset is 1-based in AppleScript)
                    start_idx = offset - 1
                    end_idx = start_idx + limit

                    # Get the batch of tracks
                    batch_tracks = tracks[start_idx:end_idx]

                    # Return empty if no tracks in this batch (end of data)
                    if not batch_tracks:
                        return ""

                    # Return the batch as formatted output
                    return "\x1d".join(batch_tracks)
            except (ValueError, IndexError):
                # Fall through to normal response handling
                pass

        # Return predefined response or default based on script name
        if script_name in self.script_responses:
            return self.script_responses[script_name]

        # Default responses based on script type
        if script_name == "update_property.applescript":
            return "Success: Property updated"
        if script_name == "batch_update_tracks.applescript":
            return "Success: Batch update process completed."

        return None

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
        self.script_code_calls.append(
            {
                "script_code": script_code,
                "arguments": list(arguments) if arguments is not None else None,
                "timeout": timeout,
            }
        )
        if self.should_fail:
            raise MockAppleScriptError(self.failure_message)
        return None

    def set_response(self, script_name: str, response: str | None) -> None:
        """Set a predefined response for a specific script."""
        self.script_responses[script_name] = response


class MockCacheService:
    """Mock implementation of CacheServiceProtocol for testing.

    Note: This class implements CacheServiceProtocol through duck typing,
    providing all required methods without explicit inheritance.
    """

    def __init__(self) -> None:
        """Initialize the mock cache service."""
        self.storage: dict[str, Any] = {}
        self.album_cache: dict[str, str] = {}
        self.api_cache: dict[str, CachedApiResult] = {}
        self.last_run_timestamp = datetime(2024, 1, 1, tzinfo=UTC)
        self.ttl_overrides: dict[str, int | None] = {}
        self.is_initialized = False
        self.load_count = 0
        self.save_count = 0
        self.sync_count = 0

    async def initialize(self) -> None:
        """Initialize the cache service, loading any persistent data."""
        self.is_initialized = True

    def set(self, key_data: CacheableKey, value: CacheableValue, ttl: int | None = None) -> None:
        """Set a value in the cache with optional TTL.

        Args:
            key_data: Key for the cached value
            value: Value to cache
            ttl: Time-to-live in seconds (optional)
        """
        self.storage[str(key_data)] = value
        self.ttl_overrides[str(key_data)] = ttl

    async def set_async(
        self,
        key_data: CacheableKey,
        value: CacheableValue,
        ttl: int | None = None,
    ) -> None:
        """Asynchronously set a value in the cache with optional TTL.

        Args:
            key_data: Key for the cached value
            value: Value to cache
            ttl: Time-to-live in seconds (optional)
        """
        self.set(key_data, value, ttl)

    @overload
    async def get_async(
        self,
        key_data: Literal["ALL"],
        compute_func: None = None,
    ) -> list[TrackDict]:
        """Get all tracks from cache."""
        ...

    @overload
    async def get_async(
        self,
        key_data: str,
        compute_func: None = None,
    ) -> list[TrackDict] | None:
        """Get track list from cache by string key."""
        ...

    @overload
    async def get_async(
        self,
        key_data: CacheableKey,
        compute_func: Callable[[], Awaitable[CacheableValue]] | None = None,
    ) -> CacheableValue:
        """Get value from cache with optional compute function."""
        ...

    async def get_async(
        self,
        key_data: CacheableKey,
        compute_func: Callable[[], Awaitable[CacheableValue]] | None = None,
    ) -> list[TrackDict] | CacheableValue:
        """Get a value from cache, optionally computing it if not present.

        Args:
            key_data: Key for the cached value
            compute_func: Optional compute function if not cached

        Returns:
            Cached value or computed value, or None if not found
        """
        key_str = str(key_data)

        if key_str in self.storage:
            return self.storage[key_str]

        if compute_func:
            value = await compute_func()
            self.storage[key_str] = value
            return value

        # Special handling for "ALL" key
        return self.storage.get("ALL", []) if key_data == "ALL" else None

    def invalidate(self, key_data: CacheableKey) -> None:
        """Invalidate (remove) a specific cache entry.

        Args:
            key_data: Key of the cache entry to invalidate
        """
        key_str = str(key_data)
        if key_str in self.storage:
            del self.storage[key_str]

    def clear(self) -> None:
        """Clear all entries from the cache."""
        self.storage.clear()
        self.album_cache.clear()
        self.api_cache.clear()

    async def load_cache(self) -> None:
        """Load persistent cache data from disk."""
        self.load_count += 1

    async def save_cache(self) -> None:
        """Save cache data to disk for persistence."""
        self.save_count += 1

    async def get_last_run_timestamp(self) -> datetime:
        """Get the timestamp of the last cache run.

        Returns:
            DateTime of last run, or epoch if never run
        """
        return self.last_run_timestamp

    async def get_album_year_from_cache(self, artist: str, album: str) -> str | None:
        """Get cached album year for an artist/album pair.

        Args:
            artist: Artist name
            album: Album name

        Returns:
            Cached year string or None if not found
        """
        key = self.generate_album_key(artist, album)
        return self.album_cache.get(key)

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
        key = self.generate_album_key(artist, album)
        self.album_cache[key] = year

    async def invalidate_album_cache(self, artist: str, album: str) -> None:
        """Invalidate cached data for a specific album.

        Args:
            artist: Artist name
            album: Album name
        """
        key = self.generate_album_key(artist, album)
        if key in self.album_cache:
            del self.album_cache[key]

    async def invalidate_all_albums(self) -> None:
        """Invalidate all album cache entries."""
        self.album_cache.clear()

    async def sync_cache(self) -> None:
        """Synchronize cache to persistent storage."""
        self.sync_count += 1

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
        key = f"{self.generate_album_key(artist, album)}:{source}"
        return self.api_cache.get(key)

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
        key = f"{self.generate_album_key(artist, album)}:{source}"
        metadata_payload = dict(metadata or {})
        metadata_payload["is_negative"] = is_negative
        self.api_cache[key] = CachedApiResult(
            artist=artist,
            album=album,
            year=year,
            source=source,
            timestamp=datetime.now(UTC).timestamp(),
            metadata=metadata_payload,
        )

    @staticmethod
    def generate_album_key(artist: str, album: str) -> str:
        """Generate a unique key for an artist/album pair.

        Args:
            artist: Artist name
            album: Album name

        Returns:
            Unique key string
        """
        return f"{artist}::{album}".lower()


class MockExternalApiService:
    """Mock implementation of ExternalApiServiceProtocol for testing."""

    def __init__(self) -> None:
        """Initialize the mock external API service."""
        self.get_album_year_response: tuple[str | None, bool] = ("2020", True)
        self.should_update_response = True
        self.artist_activity_response: tuple[int | None, int | None] = (1990, None)
        self.discogs_year_response: str | None = "2020"
        self.get_album_year_calls: list[tuple[str, str, str | None]] = []
        self.should_update_calls: list[dict[str, Any]] = []
        self.artist_activity_requests: list[str] = []
        self.discogs_requests: list[tuple[str, str]] = []
        self.initialize_calls: list[bool] = []
        self.close_count = 0

    async def initialize(self, force: bool = False) -> None:
        """Initialize the external API service."""
        self.initialize_calls.append(force)

    async def close(self) -> None:
        """Close all connections and clean up resources."""
        self.close_count += 1

    def should_update_album_year(
        self,
        tracks: list[dict[str, str]],
        artist: str = "",
        album: str = "",
        current_library_year: str = "",
    ) -> bool:
        """Determine whether to update the year for an album.

        Args:
            tracks: List of track dictionaries
            artist: Artist name
            album: Album name
            current_library_year: Current year in library

        Returns:
            True if should update, False otherwise
        """
        self.should_update_calls.append(
            {
                "tracks": tracks,
                "artist": artist,
                "album": album,
                "current_library_year": current_library_year,
            }
        )
        return self.should_update_response

    async def get_album_year(
        self,
        artist: str,
        album: str,
        current_library_year: str | None = None,
    ) -> tuple[str | None, bool]:
        """Get album year from external sources.

        Args:
            artist: Artist name
            album: Album name
            current_library_year: Current year in library

        Returns:
            Tuple of (year, is_definitive)
        """
        self.get_album_year_calls.append((artist, album, current_library_year))
        return self.get_album_year_response

    async def get_artist_activity_period(
        self,
        artist_norm: str,
    ) -> tuple[int | None, int | None]:
        """Get artist activity period.

        Args:
            artist_norm: Normalized artist name

        Returns:
            Tuple of (start_year, end_year)
        """
        self.artist_activity_requests.append(artist_norm)
        return self.artist_activity_response

    async def get_year_from_discogs(
        self,
        artist: str,
        album: str,
    ) -> str | None:
        """Get year from Discogs.

        Args:
            artist: Artist name
            album: Album name

        Returns:
            Year string or None
        """
        self.discogs_requests.append((artist, album))
        return self.discogs_year_response


class MockPendingVerificationService:
    """Mock implementation of PendingVerificationServiceProtocol for testing."""

    def __init__(self) -> None:
        """Initialize the mock pending verification service."""
        self.pending_albums: list[tuple[datetime, str, str, str, str]] = []
        self.marked_albums: list[tuple[str, str, str, dict[str, Any] | None]] = []
        self.year_updates: list[tuple[str, str, str]] = []
        self.is_initialized = False
        self.last_min_attempts = 0

    async def initialize(self) -> None:
        """Initialize the pending verification service."""
        self.is_initialized = True

    async def mark_for_verification(
        self,
        artist: str,
        album: str,
        reason: str = "no_year_found",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Mark an album for future verification.

        Args:
            artist: Artist name
            album: Album name
            reason: Reason for marking
            metadata: Additional metadata
        """
        self.marked_albums.append((artist, album, reason, metadata))
        self.pending_albums.append((datetime.now(UTC), artist, album, reason, str(metadata) if metadata else ""))

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
        self.pending_albums = [item for item in self.pending_albums if item[1] != artist or item[2] != album]

    async def get_all_pending_albums(self) -> list[tuple[datetime, str, str, str, str]]:
        """Get all pending albums.

        Returns:
            List of pending album tuples
        """
        return self.pending_albums

    async def generate_problematic_albums_report(
        self,
        min_attempts: int = 3,
    ) -> int:
        """Generate report of problematic albums.

        Args:
            min_attempts: Minimum attempts threshold

        Returns:
            Number of problematic albums
        """
        self.last_min_attempts = min_attempts
        # For testing, return a fixed number
        return len([album for album in self.pending_albums if "problematic" in album[3]])

    async def add_year_update_async(
        self,
        artist: str,
        album: str,
        year: str,
    ) -> None:
        """Add a year update (test-specific method).

        Args:
            artist: Artist name
            album: Album name
            year: Year to update
        """
        self.year_updates.append((artist, album, year))


if TYPE_CHECKING:
    # Type checking to ensure mock implementations conform to protocols
    from typing import cast

    _apple_client: AppleScriptClientProtocol = cast(AppleScriptClientProtocol, cast(object, MockAppleScriptClient()))
    _cache_service: CacheServiceProtocol = cast(CacheServiceProtocol, cast(object, MockCacheService()))
    _external_service: ExternalApiServiceProtocol = cast(ExternalApiServiceProtocol, cast(object, MockExternalApiService()))
    _pending_service: PendingVerificationServiceProtocol = cast(PendingVerificationServiceProtocol, cast(object, MockPendingVerificationService()))
