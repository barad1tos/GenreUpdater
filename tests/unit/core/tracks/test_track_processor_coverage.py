"""Additional TrackProcessor tests for full coverage."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.models.track_models import TrackDict
from core.models.validators import SecurityValidationError, SecurityValidator
from core.tracks.track_processor import TrackProcessor

if TYPE_CHECKING:
    from core.models.protocols import AppleScriptClientProtocol, CacheServiceProtocol


@pytest.fixture
def mock_ap_client() -> AsyncMock:
    """Create a mock AppleScript client."""
    client = AsyncMock()
    client.run_script = AsyncMock(return_value="")
    return client


@pytest.fixture
def mock_cache_service() -> AsyncMock:
    """Create a mock cache service."""
    service = AsyncMock()
    service.get_async = AsyncMock(return_value=None)
    service.set_async = AsyncMock()
    return service


@pytest.fixture
def mock_snapshot_service() -> AsyncMock:
    """Create a mock snapshot service."""
    service = AsyncMock()
    service.load_snapshot = AsyncMock(return_value=None)
    service.is_snapshot_valid = AsyncMock(return_value=True)
    service.is_delta_enabled = MagicMock(return_value=False)
    service.save_snapshot = AsyncMock()
    service.get_snapshot_metadata = AsyncMock(return_value=None)
    service.load_delta = AsyncMock(return_value=None)
    return service


@pytest.fixture
def logger() -> logging.Logger:
    """Create a test logger."""
    return logging.getLogger("test.track_processor")


@pytest.fixture
def error_logger() -> logging.Logger:
    """Create a test error logger."""
    return logging.getLogger("test.track_processor.error")


@pytest.fixture
def config() -> dict[str, Any]:
    """Create a test config."""
    return {
        "apple_script": {"timeout": 30},
        "applescript_timeouts": {
            "single_artist_fetch": 600,
            "full_library_fetch": 3600,
        },
        "development": {"test_artists": []},
        "experimental": {"batch_updates_enabled": False},
        "library_snapshot": {"enabled": False},
    }


@pytest.fixture
def processor(
    mock_ap_client: AsyncMock,
    mock_cache_service: AsyncMock,
    config: dict[str, Any],
    logger: logging.Logger,
    error_logger: logging.Logger,
) -> TrackProcessor:
    """Create a TrackProcessor instance for testing."""
    security_validator = SecurityValidator(logger)
    analytics = MagicMock()
    return TrackProcessor(
        ap_client=cast("AppleScriptClientProtocol", mock_ap_client),
        cache_service=cast("CacheServiceProtocol", mock_cache_service),
        console_logger=logger,
        error_logger=error_logger,
        config=config,
        analytics=analytics,
        security_validator=security_validator,
    )


@pytest.fixture
def sample_track() -> TrackDict:
    """Create a sample track."""
    return TrackDict(
        id="123",
        name="Test Track",
        artist="Test Artist",
        album="Test Album",
        genre="Rock",
        year="2020",
    )


class TestSetArtistRenamer:
    """Tests for set_artist_renamer method."""

    def test_sets_artist_renamer(self, processor: TrackProcessor) -> None:
        """Test sets artist renamer."""
        mock_renamer = MagicMock()
        processor.set_artist_renamer(mock_renamer)
        assert processor.artist_renamer is mock_renamer


class TestCurrentTime:
    """Tests for _current_time static method."""

    def test_returns_utc_datetime(self) -> None:
        """Test _current_time returns naive UTC datetime."""
        result = TrackProcessor._current_time()
        assert isinstance(result, datetime)
        assert result.tzinfo is None  # Naive datetime


class TestProcessTestArtists:
    """Tests for _process_test_artists method."""

    @pytest.mark.asyncio
    async def test_uses_dry_run_test_artists(self, processor: TrackProcessor, mock_ap_client: AsyncMock) -> None:
        """Test uses dry run test artists when available."""
        processor.dry_run_mode = "test"
        processor.dry_run_test_artists = {"Test Artist 1"}
        mock_ap_client.run_script.return_value = "123\x1eTest Track\x1eTest Artist 1\x1eTest Artist 1\x1eAlbum\x1eRock\x1e2020-01-01\x1d"

        result = await processor._process_test_artists(False)
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_uses_config_test_artists(self, processor: TrackProcessor, mock_ap_client: AsyncMock) -> None:
        """Test uses config test artists when dry run not set."""
        processor.config["development"]["test_artists"] = ["Config Artist"]
        mock_ap_client.run_script.return_value = "456\x1eTrack\x1eConfig Artist\x1eConfig Artist\x1eAlbum\x1eRock\x1e2021-01-01\x1d"

        result = await processor._process_test_artists(False)
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_test_artists(self, processor: TrackProcessor) -> None:
        """Test returns empty list when no test artists configured."""
        processor.dry_run_test_artists = set()
        processor.config["development"]["test_artists"] = []

        result = await processor._process_test_artists(False)
        assert result == []


class TestApplyArtistRenames:
    """Tests for _apply_artist_renames method."""

    @pytest.mark.asyncio
    async def test_skips_when_no_renamer(self, processor: TrackProcessor, sample_track: TrackDict) -> None:
        """Test does nothing when artist_renamer is None."""
        processor.artist_renamer = None
        await processor._apply_artist_renames([sample_track])
        # Should not raise

    @pytest.mark.asyncio
    async def test_handles_renamer_exception(self, processor: TrackProcessor, sample_track: TrackDict) -> None:
        """Test handles exception from artist renamer."""
        mock_renamer = AsyncMock()
        mock_renamer.rename_tracks.side_effect = RuntimeError("Rename failed")
        processor.artist_renamer = mock_renamer

        # Should not raise, just log the error
        await processor._apply_artist_renames([sample_track])


class TestMaterializeCachedTracks:
    """Tests for _materialize_cached_tracks method."""

    @pytest.mark.asyncio
    async def test_returns_validated_cached_tracks(
        self,
        processor: TrackProcessor,
        sample_track: TrackDict,
    ) -> None:
        """Test returns validated cached tracks when cache hits."""
        # Mock cache_manager to return cached tracks
        processor.cache_manager = AsyncMock()
        processor.cache_manager.get_cached_tracks = AsyncMock(return_value=[sample_track])

        result = await processor._materialize_cached_tracks("cache_key", "Test Artist")
        assert result is not None
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_returns_none_when_cache_miss(
        self,
        processor: TrackProcessor,
    ) -> None:
        """Test returns None when cache misses."""
        # Mock cache_manager to return None
        processor.cache_manager = AsyncMock()
        processor.cache_manager.get_cached_tracks = AsyncMock(return_value=None)

        result = await processor._materialize_cached_tracks("cache_key", "Test Artist")
        assert result is None


class TestTryFetchTestTracks:
    """Tests for _try_fetch_test_tracks method."""

    @pytest.mark.asyncio
    async def test_returns_none_when_artist_specified(self, processor: TrackProcessor) -> None:
        """Test returns None when artist filter is specified."""
        result = await processor._try_fetch_test_tracks(False, False, "Some Artist")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_ignore_test_filter(self, processor: TrackProcessor) -> None:
        """Test returns None when ignore_test_filter is True."""
        result = await processor._try_fetch_test_tracks(False, True, None)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_test_artists(self, processor: TrackProcessor) -> None:
        """Test returns None when no test artists configured."""
        processor.config["development"]["test_artists"] = []
        result = await processor._try_fetch_test_tracks(False, False, None)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_empty_when_test_artists_configured_but_no_tracks(self, processor: TrackProcessor, mock_ap_client: AsyncMock) -> None:
        """Test returns empty list when test artists configured but no tracks found."""
        processor.config["development"]["test_artists"] = ["Nonexistent Artist"]
        # AppleScript returns empty for nonexistent artist
        mock_ap_client.run_script.return_value = ""

        result = await processor._try_fetch_test_tracks(False, False, None)
        # Should return empty list (not None) because test_artists is configured
        assert result == []


class TestTryFetchSnapshotTracks:
    """Tests for _try_fetch_snapshot_tracks method."""

    @pytest.mark.asyncio
    async def test_returns_none_when_snapshot_disabled(self, processor: TrackProcessor) -> None:
        """Test returns None when use_snapshot is False."""
        result = await processor._try_fetch_snapshot_tracks("cache_key", False, False)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_force_refresh(self, processor: TrackProcessor) -> None:
        """Test returns None when force_refresh is True."""
        result = await processor._try_fetch_snapshot_tracks("cache_key", True, True)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_tracks_from_snapshot(
        self,
        processor: TrackProcessor,
        mock_snapshot_service: AsyncMock,
        mock_cache_service: AsyncMock,
        sample_track: TrackDict,
    ) -> None:
        """Test returns tracks from snapshot when available."""
        processor.snapshot_service = mock_snapshot_service
        mock_snapshot_service.load_snapshot.return_value = [sample_track]

        result = await processor._try_fetch_snapshot_tracks("cache_key", True, False)

        assert result is not None
        assert len(result) == 1
        mock_cache_service.set_async.assert_called_once()


class TestLoadTracksFromSnapshot:
    """Tests for _load_tracks_from_snapshot method."""

    @pytest.mark.asyncio
    async def test_returns_none_when_no_snapshot_service(self, processor: TrackProcessor) -> None:
        """Test returns None when snapshot_service is None."""
        processor.snapshot_service = None
        result = await processor._load_tracks_from_snapshot()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_snapshot_missing(
        self,
        processor: TrackProcessor,
        mock_snapshot_service: AsyncMock,
    ) -> None:
        """Test returns None when snapshot is not on disk."""
        processor.snapshot_service = mock_snapshot_service
        mock_snapshot_service.load_snapshot.return_value = None

        result = await processor._load_tracks_from_snapshot()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_tracks_when_snapshot_valid(
        self,
        processor: TrackProcessor,
        mock_snapshot_service: AsyncMock,
        sample_track: TrackDict,
    ) -> None:
        """Test returns tracks when snapshot is valid."""
        processor.snapshot_service = mock_snapshot_service
        mock_snapshot_service.load_snapshot.return_value = [sample_track]
        mock_snapshot_service.is_snapshot_valid.return_value = True

        result = await processor._load_tracks_from_snapshot()
        assert result is not None
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_returns_none_when_snapshot_invalid_and_delta_disabled(
        self,
        processor: TrackProcessor,
        mock_snapshot_service: AsyncMock,
        sample_track: TrackDict,
    ) -> None:
        """Test returns None when snapshot invalid and delta disabled."""
        processor.snapshot_service = mock_snapshot_service
        mock_snapshot_service.load_snapshot.return_value = [sample_track]
        mock_snapshot_service.is_snapshot_valid.return_value = False
        mock_snapshot_service.is_delta_enabled.return_value = False

        result = await processor._load_tracks_from_snapshot()
        assert result is None

    @pytest.mark.asyncio
    async def test_attempts_delta_refresh_when_enabled(
        self,
        processor: TrackProcessor,
        mock_snapshot_service: AsyncMock,
        sample_track: TrackDict,
    ) -> None:
        """Test attempts delta refresh when delta is enabled."""
        processor.snapshot_service = mock_snapshot_service
        mock_snapshot_service.load_snapshot.return_value = [sample_track]
        mock_snapshot_service.is_snapshot_valid.return_value = False
        mock_snapshot_service.is_delta_enabled.return_value = True
        mock_snapshot_service.get_snapshot_metadata.return_value = None
        mock_snapshot_service.load_delta.return_value = None

        result = await processor._load_tracks_from_snapshot()
        # Should return None because min_date cannot be determined
        assert result is None


class TestRefreshSnapshotFromDelta:
    """Tests for _refresh_snapshot_from_delta method."""

    @pytest.mark.asyncio
    async def test_returns_none_when_no_min_date(
        self,
        processor: TrackProcessor,
        mock_snapshot_service: AsyncMock,
        sample_track: TrackDict,
    ) -> None:
        """Test returns None when min_date cannot be determined."""
        mock_snapshot_service.get_snapshot_metadata.return_value = None
        mock_snapshot_service.load_delta.return_value = None

        result = await processor._refresh_snapshot_from_delta([sample_track], mock_snapshot_service)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_delta_fetch_empty(
        self,
        processor: TrackProcessor,
        mock_snapshot_service: AsyncMock,
        mock_ap_client: AsyncMock,
        sample_track: TrackDict,
    ) -> None:
        """Test returns None when delta fetch returns no tracks."""
        metadata = MagicMock()
        metadata.last_full_scan = datetime.now(UTC).replace(tzinfo=None)
        mock_snapshot_service.get_snapshot_metadata.return_value = metadata
        mock_snapshot_service.load_delta.return_value = None
        mock_ap_client.run_script.return_value = ""  # Empty result

        result = await processor._refresh_snapshot_from_delta([sample_track], mock_snapshot_service)
        assert result is None

    @pytest.mark.asyncio
    async def test_merges_tracks_when_delta_available(
        self,
        processor: TrackProcessor,
        mock_snapshot_service: AsyncMock,
        mock_ap_client: AsyncMock,
        sample_track: TrackDict,
    ) -> None:
        """Test merges tracks when delta is available."""
        metadata = MagicMock()
        metadata.last_full_scan = datetime.now(UTC).replace(tzinfo=None)
        mock_snapshot_service.get_snapshot_metadata.return_value = metadata
        mock_snapshot_service.load_delta.return_value = None

        # Return a new track from the delta fetch (7 fields: ID|NAME|ARTIST|ALBUM_ARTIST|ALBUM|GENRE|DATE_ADDED)
        mock_ap_client.run_script.return_value = "456\x1eNew Track\x1eArtist\x1eArtist\x1eAlbum\x1eRock\x1e2021-01-01\x1d"

        result = await processor._refresh_snapshot_from_delta([sample_track], mock_snapshot_service)
        assert result is not None
        assert len(result) == 2  # Original + delta

    @pytest.mark.asyncio
    async def test_uses_max_date_from_delta_cache(
        self,
        processor: TrackProcessor,
        mock_snapshot_service: AsyncMock,
        mock_ap_client: AsyncMock,
        sample_track: TrackDict,
    ) -> None:
        """Test uses max date when delta cache has last_run."""
        # Set up metadata with earlier date
        metadata = MagicMock()
        metadata.last_full_scan = datetime(2021, 1, 1, tzinfo=UTC)
        mock_snapshot_service.get_snapshot_metadata.return_value = metadata

        # Set up delta cache with more recent last_run
        delta_cache = MagicMock()
        delta_cache.last_run = datetime(2021, 6, 1, tzinfo=UTC)
        mock_snapshot_service.load_delta.return_value = delta_cache

        # Return a new track from the delta fetch
        mock_ap_client.run_script.return_value = "456\x1eNew Track\x1eArtist\x1eArtist\x1eAlbum\x1eRock\x1e2021-01-01\x1d"

        result = await processor._refresh_snapshot_from_delta([sample_track], mock_snapshot_service)
        assert result is not None
        assert len(result) == 2  # Original + delta


class TestUpdateSnapshot:
    """Tests for _update_snapshot method."""

    @pytest.mark.asyncio
    async def test_delegates_to_cache_manager(self, processor: TrackProcessor, sample_track: TrackDict) -> None:
        """Test delegates to cache_manager.update_snapshot."""
        processor.cache_manager = AsyncMock()
        processor.cache_manager.update_snapshot = AsyncMock()

        await processor._update_snapshot([sample_track], ["123"])

        processor.cache_manager.update_snapshot.assert_called_once()


class TestValidateTracksSecurity:
    """Tests for _validate_tracks_security method."""

    def test_filters_invalid_tracks(self, processor: TrackProcessor) -> None:
        """Test filters out tracks that fail security validation."""
        mock_validator = MagicMock()
        error_message = "Invalid track"

        def validate_side_effect(track_dict: dict[str, Any]) -> dict[str, Any]:
            """Validate track and raise error for bad tracks."""
            if track_dict.get("id") == "bad_track":
                raise SecurityValidationError(error_message)
            return track_dict

        mock_validator.validate_track_data = MagicMock(side_effect=validate_side_effect)
        processor.security_validator = mock_validator

        tracks = [
            TrackDict(id="good_track", name="Good", artist="Artist", album="Album", genre="Rock", year="2020"),
            TrackDict(id="bad_track", name="Bad", artist="Artist", album="Album", genre="Rock", year="2020"),
        ]

        result = processor._validate_tracks_security(tracks)
        assert len(result) == 1
        assert result[0].id == "good_track"


class TestFetchTracksFromApplescript:
    """Tests for _fetch_tracks_from_applescript error handling."""

    @pytest.mark.asyncio
    async def test_handles_error_prefix(self, processor: TrackProcessor, mock_ap_client: AsyncMock) -> None:
        """Test handles ERROR: prefix in output."""
        mock_ap_client.run_script.return_value = "ERROR: Something went wrong"

        result = await processor._fetch_tracks_from_applescript()
        assert result == []

    @pytest.mark.asyncio
    async def test_handles_no_tracks_found(self, processor: TrackProcessor, mock_ap_client: AsyncMock) -> None:
        """Test handles NO_TRACKS_FOUND output."""
        mock_ap_client.run_script.return_value = "NO_TRACKS_FOUND"

        result = await processor._fetch_tracks_from_applescript()
        assert result == []

    @pytest.mark.asyncio
    async def test_handles_exception(self, processor: TrackProcessor, mock_ap_client: AsyncMock) -> None:
        """Test handles exceptions from AppleScript execution."""
        mock_ap_client.run_script.side_effect = OSError("Script failed")

        result = await processor._fetch_tracks_from_applescript()
        assert result == []


class TestFetchTracksByIds:
    """Tests for fetch_tracks_by_ids method."""

    @pytest.mark.asyncio
    async def test_returns_empty_for_empty_ids(self, processor: TrackProcessor) -> None:
        """Test returns empty list for empty track IDs."""
        result = await processor.fetch_tracks_by_ids([])
        assert result == []

    @pytest.mark.asyncio
    async def test_fetches_tracks_by_ids(self, processor: TrackProcessor, mock_ap_client: AsyncMock) -> None:
        """Test fetches tracks by IDs."""
        mock_ap_client.run_script.return_value = "123\x1eTrack\x1eArtist\x1eArtist\x1eAlbum\x1eRock\x1e2020-01-01\x1d"

        result = await processor.fetch_tracks_by_ids(["123"])
        assert len(result) == 1
        assert result[0].id == "123"

    @pytest.mark.asyncio
    async def test_handles_empty_batch_response(self, processor: TrackProcessor, mock_ap_client: AsyncMock) -> None:
        """Test handles empty response for batch."""
        mock_ap_client.run_script.return_value = ""

        result = await processor.fetch_tracks_by_ids(["123", "456"])
        assert result == []

    @pytest.mark.asyncio
    async def test_processes_batches(self, processor: TrackProcessor, mock_ap_client: AsyncMock) -> None:
        """Test processes tracks in batches."""
        processor.config["batch_processing"] = {"ids_batch_size": 2}
        mock_ap_client.run_script.return_value = "1\x1eT\x1eA\x1eA\x1eAl\x1eR\x1e2020-01-01\x1d"

        await processor.fetch_tracks_by_ids(["1", "2", "3", "4"])
        # Should make 2 calls (batch size 2, 4 tracks)
        assert mock_ap_client.run_script.call_count == 2


class TestFetchTracksAsync:
    """Tests for fetch_tracks_async method."""

    @pytest.mark.asyncio
    async def test_returns_dry_run_test_tracks(self, processor: TrackProcessor, sample_track: TrackDict) -> None:
        """Test returns dry_run_test_tracks when provided."""
        result = await processor.fetch_tracks_async(dry_run_test_tracks=[sample_track])
        assert len(result) == 1
        assert result[0] == sample_track

    @pytest.mark.asyncio
    async def test_updates_snapshot_on_fetch(
        self,
        processor: TrackProcessor,
        mock_ap_client: AsyncMock,
        mock_cache_service: AsyncMock,
    ) -> None:
        """Test updates snapshot when fetching tracks."""
        processor.config["library_snapshot"]["enabled"] = True
        processor.snapshot_service = None
        mock_ap_client.run_script.return_value = "123\x1eTrack\x1eArtist\x1eArtist\x1eAlbum\x1eRock\x1e2020-01-01\x1d"

        with (
            patch.object(processor, "_can_use_snapshot", return_value=True),
            patch.object(processor, "_update_snapshot", new_callable=AsyncMock) as mock_update,
        ):
            result = await processor.fetch_tracks_async()

            assert len(result) == 1
            mock_update.assert_called_once()
            mock_cache_service.set_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_caches_fetched_tracks(
        self,
        processor: TrackProcessor,
        mock_ap_client: AsyncMock,
        mock_cache_service: AsyncMock,
    ) -> None:
        """Test caches tracks after fetching."""
        mock_ap_client.run_script.return_value = "123\x1eTrack\x1eArtist\x1eArtist\x1eAlbum\x1eRock\x1e2020-01-01\x1d"

        result = await processor.fetch_tracks_async()

        assert len(result) == 1
        mock_cache_service.set_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_logs_warning_when_no_tracks_fetched(
        self,
        processor: TrackProcessor,
        mock_ap_client: AsyncMock,
    ) -> None:
        """Test logs warning when no tracks are fetched."""
        mock_ap_client.run_script.return_value = ""

        with patch.object(processor.console_logger, "warning") as mock_warning:
            result = await processor.fetch_tracks_async()

            assert result == []
            mock_warning.assert_called_once()


class TestUpdateArtistAsync:
    """Tests for update_artist_async method."""

    @pytest.mark.asyncio
    async def test_delegates_to_update_executor(self, processor: TrackProcessor, sample_track: TrackDict) -> None:
        """Test delegates to update_executor.update_artist_async."""
        processor.update_executor = AsyncMock()
        processor.update_executor.update_artist_async = AsyncMock(return_value=True)

        result = await processor.update_artist_async(
            track=sample_track,
            new_artist_name="New Artist",
            original_artist="Old Artist",
            update_album_artist=True,
        )

        assert result is True
        processor.update_executor.update_artist_async.assert_called_once_with(
            track=sample_track,
            new_artist_name="New Artist",
            original_artist="Old Artist",
            update_album_artist=True,
        )

    @pytest.mark.asyncio
    async def test_returns_false_on_failure(self, processor: TrackProcessor, sample_track: TrackDict) -> None:
        """Test returns False when update fails."""
        processor.update_executor = AsyncMock()
        processor.update_executor.update_artist_async = AsyncMock(return_value=False)

        result = await processor.update_artist_async(
            track=sample_track,
            new_artist_name="New Artist",
        )

        assert result is False
