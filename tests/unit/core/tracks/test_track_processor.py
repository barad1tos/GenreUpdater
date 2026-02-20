"""Enhanced TrackProcessor tests with Allure reporting."""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
import pytest

from core.models.track_models import CachedApiResult, TrackDict
from core.models.validators import SecurityValidator
from core.tracks.track_processor import TrackProcessor
from metrics import Analytics
from metrics.analytics import LoggerContainer
from tests.factories import create_test_app_config  # sourcery skip: dont-import-test-modules

if TYPE_CHECKING:
    from core.models.protocols import (
        AnalyticsProtocol,
        AppleScriptClientProtocol,
        CacheableKey,
        CacheableValue,
        CacheServiceProtocol,
    )
    from core.models.track_models import AppConfig


class _MockLogger(logging.Logger):
    """Mock logger for testing with message tracking."""

    def __init__(self, name: str = "mock") -> None:
        """Initialize mock logger with message collections."""
        super().__init__(name)
        self.level = 0
        self.handlers: list[Any] = []
        self.parent = None
        self.propagate = True
        self.info_messages: list[str] = []
        self.warning_messages: list[str] = []
        self.error_messages: list[str] = []
        self.debug_messages: list[str] = []

    @staticmethod
    def _format_message(message: str, *args: object) -> str:
        """Format message with args for logging."""
        if args:
            try:
                return message % args
            except (TypeError, ValueError):
                return f"{message} {args}"
        return message

    def info(self, msg: object, *args: object, **_kwargs: Any) -> None:
        """Track info-level log messages."""
        self.info_messages.append(self._format_message(str(msg), *args))

    def warning(self, msg: object, *args: object, **_kwargs: Any) -> None:
        """Track warning-level log messages."""
        self.warning_messages.append(self._format_message(str(msg), *args))

    def error(self, msg: object, *args: object, **_kwargs: Any) -> None:
        """Track error-level log messages."""
        self.error_messages.append(self._format_message(str(msg), *args))

    def debug(self, msg: object, *args: object, **_kwargs: Any) -> None:
        """Track debug-level log messages."""
        self.debug_messages.append(self._format_message(str(msg), *args))


class _MockAnalytics(Analytics):
    """Mock analytics for testing."""

    def __init__(self) -> None:
        """Initialize mock analytics with mock loggers."""
        mock_console = _MockLogger("console")
        mock_error = _MockLogger("error")
        mock_analytics = _MockLogger("analytics")
        loggers = LoggerContainer(mock_console, mock_error, mock_analytics)
        super().__init__(config=create_test_app_config(), loggers=loggers, max_events=1000)


class _MockAppleScriptClient:
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
        """Run an AppleScript file by name."""
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
            msg = self.failure_message
            raise RuntimeError(msg)

        # Handle batch processing for fetch_tracks.applescript
        if script_name == "fetch_tracks.applescript" and arguments and len(arguments) >= 3:
            with contextlib.suppress(ValueError, IndexError):
                offset = int(arguments[1]) if arguments[1] else 1
                limit = int(arguments[2]) if arguments[2] else 1000

                if script_name in self.script_responses:
                    full_response = self.script_responses[script_name]
                    tracks = full_response.split("\x1d") if full_response else []
                    start_idx = offset - 1
                    end_idx = start_idx + limit
                    batch_tracks = tracks[start_idx:end_idx]
                    return "\x1d".join(batch_tracks) if batch_tracks else ""

        if script_name in self.script_responses:
            return self.script_responses[script_name]

        if script_name == "update_property.applescript":
            return "Success: Property updated"
        if script_name == "batch_update_tracks.applescript":
            return "Success: Batch update process completed."

        return None

    def set_response(self, script_name: str, response: str | None) -> None:
        """Set a predefined response for a specific script."""
        self.script_responses[script_name] = response


class _MockCacheService:
    """Mock implementation of CacheServiceProtocol for testing."""

    def __init__(self) -> None:
        """Initialize the mock cache service."""
        self.storage: dict[str, CacheableValue] = {}
        self.album_cache: dict[str, str] = {}
        self.api_cache: dict[str, CachedApiResult] = {}
        self.last_run_timestamp = datetime(2024, 1, 1, tzinfo=UTC)
        self.ttl_overrides: dict[str, int | None] = {}
        self.is_initialized = False

    async def initialize(self) -> None:
        """Initialize the cache service."""
        self.is_initialized = True

    def set(self, key_data: CacheableKey, value: CacheableValue, ttl: int | None = None) -> None:
        """Set a value in the cache."""
        self.storage[str(key_data)] = value
        self.ttl_overrides[str(key_data)] = ttl

    async def set_async(self, key_data: CacheableKey, value: CacheableValue, ttl: int | None = None) -> None:
        """Asynchronously set a value in the cache."""
        self.set(key_data, value, ttl)

    async def get_async(self, key_data: CacheableKey, compute_func: Any = None) -> Any:
        """Get a value from cache."""
        key_str = str(key_data)
        if key_str in self.storage:
            return self.storage[key_str]
        if compute_func:
            value = await compute_func()
            self.storage[key_str] = value
            return value
        return self.storage.get("ALL", []) if key_data == "ALL" else None

    def invalidate(self, key_data: CacheableKey) -> None:
        """Invalidate a cache entry."""
        key_str = str(key_data)
        if key_str in self.storage:
            del self.storage[key_str]

    async def clear(self) -> None:
        """Clear all cache entries."""
        self.storage.clear()
        self.album_cache.clear()
        self.api_cache.clear()

    async def load_cache(self) -> None:
        """Load persistent cache data."""

    async def save_cache(self) -> None:
        """Save cache data to disk."""

    async def get_last_run_timestamp(self) -> datetime:
        """Get the timestamp of the last cache run."""
        return self.last_run_timestamp

    async def get_album_year_from_cache(self, artist: str, album: str) -> str | None:
        """Get cached album year."""
        key = f"{artist}::{album}".lower()
        return self.album_cache.get(key)

    async def get_album_year_entry_from_cache(self, _artist: str, _album: str) -> None:
        """Get album year entry from cache (returns None to trigger API call)."""

    async def store_album_year_in_cache(self, artist: str, album: str, year: str, _confidence: int = 0) -> None:
        """Store album year in cache."""
        key = f"{artist}::{album}".lower()
        self.album_cache[key] = year

    async def invalidate_album_cache(self, artist: str, album: str) -> None:
        """Invalidate cached data for an album."""
        key = f"{artist}::{album}".lower()
        if key in self.album_cache:
            del self.album_cache[key]

    async def invalidate_all_albums(self) -> None:
        """Invalidate all album cache entries."""
        self.album_cache.clear()

    async def sync_cache(self) -> None:
        """Synchronize cache to persistent storage."""

    async def get_cached_api_result(self, artist: str, album: str, source: str) -> CachedApiResult | None:
        """Get cached API result."""
        key = f"{f'{artist}::{album}'.lower()}:{source}"
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
        """Cache an API result."""
        key = f"{f'{artist}::{album}'.lower()}:{source}"
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
        """Record track invalidation event."""
        key = f"invalidate:{track.id}"
        self.storage[key] = track


def _create_track(
    track_id: str = "12345",
    name: str = "Test Track",
    artist: str = "Test Artist",
    album: str = "Test Album",
    genre: str | None = "Rock",
    date_added: str | None = "2024-01-01 12:00:00",
    year: str | None = "2024",
) -> TrackDict:
    """Create a TrackDict for testing."""
    return TrackDict(
        id=track_id,
        name=name,
        artist=artist,
        album=album,
        genre=genre,
        date_added=date_added,
        year=year,
        last_modified="2024-01-01 12:00:00",
        track_status="subscription",
    )


class TestTrackProcessorAllure:
    """Enhanced tests for TrackProcessor with Allure reporting."""

    @staticmethod
    def create_processor(
        ap_client: _MockAppleScriptClient | None = None,
        cache_service: _MockCacheService | None = None,
        config: AppConfig | None = None,
        dry_run: bool = False,
    ) -> TrackProcessor:
        """Create a TrackProcessor instance for testing.

        Args:
            ap_client: AppleScript client instance
            cache_service: Cache service instance
            config: Typed application configuration
            dry_run: Whether to run in dry-run mode

        Returns:
            Configured TrackProcessor instance
        """
        if ap_client is None:
            ap_client = _MockAppleScriptClient()

        if cache_service is None:
            cache_service = _MockCacheService()

        console_logger = _MockLogger()
        error_logger = _MockLogger()
        analytics = _MockAnalytics()

        test_config = config or create_test_app_config(
            development={"test_artists": ["Test Artist"]},
        )

        return TrackProcessor(
            ap_client=cast("AppleScriptClientProtocol", cast(object, ap_client)),
            cache_service=cast("CacheServiceProtocol", cast(object, cache_service)),
            console_logger=console_logger,
            error_logger=error_logger,
            config=test_config,
            analytics=cast("AnalyticsProtocol", cast(object, analytics)),
            dry_run=dry_run,
        )

    def test_processor_initialization_comprehensive(self) -> None:
        """Test comprehensive TrackProcessor initialization."""
        mock_ap_client = _MockAppleScriptClient()
        mock_cache_service = _MockCacheService()
        mock_security_validator = SecurityValidator(_MockLogger())
        processor = TrackProcessor(
            ap_client=cast("AppleScriptClientProtocol", cast(object, mock_ap_client)),
            cache_service=cast("CacheServiceProtocol", cast(object, mock_cache_service)),
            console_logger=_MockLogger(),
            error_logger=_MockLogger(),
            config=create_test_app_config(),
            analytics=cast("AnalyticsProtocol", cast(object, _MockAnalytics())),
            dry_run=True,
            security_validator=mock_security_validator,
        )
        # Verify the processor was initialized with our mocks (compare by attribute)
        assert processor.ap_client.apple_scripts_dir == mock_ap_client.apple_scripts_dir
        assert processor.cache_service is not None
        assert processor.dry_run is True
        assert processor.security_validator is mock_security_validator
        assert isinstance(processor._dry_run_actions, list)

    def test_set_dry_run_context_detailed(self) -> None:
        """Test setting dry run context with detailed validation."""
        processor = self.create_processor()
        test_mode = "test"
        test_artists = {"Artist1", "Artist2", "Artist3"}
        processor.set_dry_run_context(test_mode, test_artists)
        assert processor.dry_run_mode == test_mode
        assert processor.dry_run_test_artists == test_artists

    def test_validate_tracks_security_comprehensive(self) -> None:
        """Test comprehensive track security validation."""
        processor = self.create_processor()
        safe_track = _create_track(track_id="safe_001", name="Safe Track", artist="Safe Artist")

        # Create potentially unsafe track (but within bounds for testing)
        edge_case_track = _create_track(track_id="edge_001", name="Track with Special Characters: !@#$%", artist="Artist & Co.", genre="Rock/Pop")

        test_tracks = [safe_track, edge_case_track]
        validated_tracks = processor._validate_tracks_security(test_tracks)
        assert isinstance(validated_tracks, list)
        assert len(validated_tracks) <= len(test_tracks)  # Some might be filtered out

        # All returned tracks should be valid
        for track in validated_tracks:
            assert hasattr(track, "id")
            assert hasattr(track, "name")
            assert hasattr(track, "artist")

    @pytest.mark.parametrize(
        ("is_single_artist", "expected_timeout"),
        [
            (True, 600),  # Single artist gets default 600s timeout
            (False, 3600),  # Full library gets default 3600s timeout
        ],
    )
    def test_applescript_timeout_calculation(self, is_single_artist: bool, expected_timeout: int) -> None:
        """Test AppleScript timeout calculation for different scenarios."""
        # Test with default timeouts (uses AppConfig defaults)
        processor = self.create_processor()
        timeout = processor._get_applescript_timeout(is_single_artist)
        assert timeout == expected_timeout

    @pytest.mark.asyncio
    async def test_fetch_tracks_from_applescript_success(self) -> None:
        """Test successful track fetching from AppleScript."""
        mock_ap_client = _MockAppleScriptClient()
        # Mock the run_script method to return serialized track data with all required fields
        # Fields: ID, NAME, ARTIST, ALBUM_ARTIST, ALBUM, GENRE, DATE_ADDED
        track1 = "1\x1eTrack 1\x1eArtist 1\x1eAlbum Artist 1\x1eAlbum 1\x1eRock\x1e2024-01-01 12:00:00"
        track2 = "2\x1eTrack 2\x1eArtist 2\x1eAlbum Artist 2\x1eAlbum 2\x1ePop\x1e2024-01-02 13:00:00"
        mock_raw_output = f"{track1}\x1d{track2}"
        mock_ap_client.set_response("fetch_tracks.applescript", mock_raw_output)
        processor = self.create_processor(ap_client=mock_ap_client)
        result = await processor._fetch_tracks_from_applescript("Test Artist")
        assert isinstance(result, list)
        assert len(result) == 2

        # Verify tracks are properly structured
        for track in result:
            assert hasattr(track, "id")
            assert hasattr(track, "name")
            assert hasattr(track, "artist")

    @pytest.mark.asyncio
    async def test_fetch_tracks_from_applescript_with_min_date(self) -> None:
        """Test that min_date_added is converted to Unix timestamp."""
        mock_ap_client = _MockAppleScriptClient()
        mock_ap_client.set_response("fetch_tracks.applescript", "")
        processor = self.create_processor(ap_client=mock_ap_client)
        min_date = datetime(2024, 1, 1, 10, 30, tzinfo=UTC)
        await processor._fetch_tracks_from_applescript(min_date_added=min_date)
        _, arguments = mock_ap_client.scripts_run[-1]
        assert arguments is not None
        assert len(arguments) >= 4
        expected_ts = str(int(min_date.replace(second=0, microsecond=0).timestamp()))
        assert arguments[-1] == expected_ts

    @pytest.mark.asyncio
    async def test_update_track_async_success(self) -> None:
        """Test successful track property update."""
        mock_ap_client = _MockAppleScriptClient()
        processor = self.create_processor(ap_client=mock_ap_client)
        success = await processor.update_track_async(
            track_id="test_001",
            new_genre="Jazz",
            original_artist="Test Artist",
            original_album="Test Album",
            original_track="Test Track",
        )
        assert success is True

        # Verify AppleScript client was called correctly
        assert len(mock_ap_client.scripts_run) > 0
        script_name, args = mock_ap_client.scripts_run[0]
        assert script_name == "update_property.applescript"
        assert args is not None
        assert "test_001" in args
        assert "genre" in args
        assert "Jazz" in args

    @pytest.mark.asyncio
    async def test_dry_run_update_behavior(self) -> None:
        """Test dry run update behavior with comprehensive validation."""
        processor = self.create_processor(dry_run=True)
        success = await processor.update_track_async(
            track_id="dry_run_001",
            new_genre="Electronic",
            original_artist="Dry Run Artist",
            original_album="Dry Run Album",
            original_track="Dry Run Track",
        )
        assert success is True  # Dry run should always succeed

        # Check that dry run action was recorded
        dry_run_actions = processor.get_dry_run_actions()
        assert len(dry_run_actions) > 0

        latest_action = dry_run_actions[-1]
        assert latest_action["track_id"] == "dry_run_001"
        assert latest_action["action"] == "update_track"
        assert "genre" in latest_action["updates"]
        assert latest_action["updates"]["genre"] == "Electronic"

    @pytest.mark.asyncio
    async def test_applescript_error_handling(self) -> None:
        """Test error handling for AppleScript failures."""
        mock_ap_client = _MockAppleScriptClient()
        # Return error response instead of raising exception
        mock_ap_client.set_response("update_property.applescript", "Error: Track not found")
        processor = self.create_processor(ap_client=mock_ap_client)
        success = await processor.update_track_async(
            track_id="error_001",
            new_genre="Rock",
            original_artist="Error Artist",
            original_album="Error Album",
            original_track="Error Track",
        )
        assert success is False  # Should return False on error

    @pytest.mark.asyncio
    async def test_fetch_tracks_in_batches(self) -> None:
        """Test batch processing of track fetching."""
        mock_ap_client = _MockAppleScriptClient()

        # Create mock track data for 15 tracks (simulating 3 batches of 5)
        track_data = [f"{i}\x1eTrack {i}\x1eArtist {i}\x1eAlbum Artist {i}\x1eAlbum {i}\x1eGenre {i}\x1e2024-01-01 12:00:00" for i in range(15)]
        mock_raw_output = "\x1d".join(track_data)
        mock_ap_client.set_response("fetch_tracks.applescript", mock_raw_output)
        processor = self.create_processor(ap_client=mock_ap_client)
        tracks = await processor.fetch_tracks_in_batches(batch_size=5)
        assert isinstance(tracks, list)
