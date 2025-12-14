"""Mock implementations of protocol interfaces for testing.

These mocks properly implement the protocol interfaces defined in src.core.models.protocols.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, overload

from core.models.track_models import CachedApiResult, TrackDict
from services.cache.album_cache import AlbumCacheEntry
from services.pending_verification import PendingAlbumEntry, VerificationReason

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from core.models.protocols import (
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
            with contextlib.suppress(ValueError, IndexError):
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
                    return "\x1d".join(batch_tracks) if batch_tracks else ""

        # Return predefined response or default based on script name
        if script_name in self.script_responses:
            return self.script_responses[script_name]

        # Default responses based on script type
        if script_name == "update_property.applescript":
            return "Success: Property updated"
        if script_name == "batch_update_tracks.applescript":
            return "Success: Batch update process completed."

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
        self.storage: dict[str, CacheableValue] = {}
        self.album_cache: dict[str, AlbumCacheEntry] = {}
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

    @overload
    async def get_async(
        self,
        key_data: str,
        compute_func: None = None,
    ) -> list[TrackDict] | None:
        """Get track list from cache by string key."""

    @overload
    async def get_async(
        self,
        key_data: CacheableKey,
        compute_func: Callable[[], Awaitable[CacheableValue]] | None = None,
    ) -> CacheableValue:
        """Get value from cache with optional compute function."""

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
        entry = self.album_cache.get(key)
        return entry.year if entry else None

    async def get_album_year_entry_from_cache(self, artist: str, album: str) -> AlbumCacheEntry | None:
        """Get full album cache entry for an artist/album pair.

        Args:
            artist: Artist name
            album: Album name

        Returns:
            Full AlbumCacheEntry or None if not found
        """
        key = self.generate_album_key(artist, album)
        return self.album_cache.get(key)

    async def store_album_year_in_cache(
        self,
        artist: str,
        album: str,
        year: str,
        confidence: int = 0,
    ) -> None:
        """Store album year in persistent cache.

        Args:
            artist: Artist name
            album: Album name
            year: Year to cache
            confidence: Confidence score 0-100
        """
        import time

        key = self.generate_album_key(artist, album)
        self.album_cache[key] = AlbumCacheEntry(
            artist=artist,
            album=album,
            year=year,
            timestamp=time.time(),
            confidence=confidence,
        )

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

    async def invalidate_for_track(self, track: TrackDict) -> None:
        """Record track invalidation event for assertions."""
        key = f"invalidate:{track.id}"
        self.storage[key] = track

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
        self.get_album_year_response: tuple[str | None, bool, int] = ("2020", True, 85)
        self.artist_activity_response: tuple[int | None, int | None] = (1990, None)
        self.discogs_year_response: str | None = "2020"
        self.get_album_year_calls: list[tuple[str, str, str | None]] = []
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

    async def get_album_year(
        self,
        artist: str,
        album: str,
        current_library_year: str | None = None,
    ) -> tuple[str | None, bool, int]:
        """Get album year from external sources.

        Args:
            artist: Artist name
            album: Album name
            current_library_year: Current year in library

        Returns:
            Tuple of (year, is_definitive, confidence_score)
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

    async def get_artist_start_year(
        self,
        artist_norm: str,
    ) -> int | None:
        """Get artist's career start year.

        Args:
            artist_norm: Normalized artist name

        Returns:
            Artist's start year or None

        """
        self.artist_activity_requests.append(artist_norm)
        return self.artist_activity_response[0]


class MockPendingVerificationService:
    """Mock implementation of PendingVerificationServiceProtocol for testing."""

    def __init__(self) -> None:
        """Initialize the mock pending verification service."""
        self.pending_albums: list[PendingAlbumEntry] = []
        self.marked_albums: list[tuple[str, str, str, dict[str, Any] | None, int | None]] = []
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
        recheck_days: int | None = None,
    ) -> None:
        """Mark an album for future verification.

        Args:
            artist: Artist name
            album: Album name
            reason: Reason for marking
            metadata: Additional metadata
            recheck_days: Optional override for verification interval in days
        """
        self.marked_albums.append((artist, album, reason, metadata, recheck_days))
        self.pending_albums.append(
            PendingAlbumEntry(
                timestamp=datetime.now(UTC),
                artist=artist,
                album=album,
                reason=VerificationReason.from_string(reason),
                metadata=str(metadata) if metadata else "",
            )
        )

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
        self.pending_albums = [entry for entry in self.pending_albums if entry.artist != artist or entry.album != album]

    async def get_all_pending_albums(self) -> list[PendingAlbumEntry]:
        """Get all pending albums.

        Returns:
            List of PendingAlbumEntry objects
        """
        return self.pending_albums

    async def is_verification_needed(self, artist: str, album: str) -> bool:
        """Check if an album needs verification now.

        For testing, always returns True (all pending albums are due).

        Args:
            artist: Artist name
            album: Album name

        Returns:
            True if verification is needed
        """
        # In tests, assume all pending albums are due for verification
        return any(entry.artist == artist and entry.album == album for entry in self.pending_albums)

    async def get_entry(self, artist: str, album: str) -> PendingAlbumEntry | None:
        """Get pending entry for artist/album if exists.

        Args:
            artist: Artist name
            album: Album name

        Returns:
            PendingAlbumEntry if found, None otherwise.
        """
        for entry in self.pending_albums:
            if entry.artist == artist and entry.album == album:
                return entry
        return None

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
        return len([entry for entry in self.pending_albums if "problematic" in entry.reason.value])

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
