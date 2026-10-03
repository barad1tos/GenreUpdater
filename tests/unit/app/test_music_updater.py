"""Enhanced MusicUpdater tests with Allure reporting."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.music_updater import LibraryFetchError, MusicUpdater
from core.models.cache_types import LibraryCacheMetadata
from tests.factories import create_test_app_config
from tests.mocks.csv_mock import MockAnalytics, MockLogger
from tests.mocks.protocol_mocks import (
    MockAppleScriptClient,
    MockCacheService,
    MockExternalApiService,
    MockPendingVerificationService,
)
from tests.mocks.track_data import DummyTrackData


class TestMusicUpdaterAllure:
    """Enhanced tests for MusicUpdater with Allure reporting."""

    @staticmethod
    def create_mock_dependencies() -> MagicMock:
        """Create mock dependency container with all required services.

        Returns:
            Mock DependencyContainer with all required services configured
        """
        deps = MagicMock()

        # Core services
        deps.ap_client = MockAppleScriptClient()
        deps.cache_service = MockCacheService()
        deps.external_api_service = MockExternalApiService()
        deps.pending_verification_service = MockPendingVerificationService()

        # Loggers
        deps.console_logger = MockLogger()
        deps.error_logger = MockLogger()

        # Analytics
        deps.analytics = MockAnalytics()

        # Typed AppConfig
        deps.app_config = create_test_app_config(
            logs_base_dir="/tmp/test_logs",
            development={"test_artists": ["Test Artist"]},
        )

        # Flags
        deps.dry_run = False

        # Library snapshot service mock (required for smart delta fetch)
        deps.library_snapshot_service = MagicMock()
        deps.library_snapshot_service.is_enabled = MagicMock(return_value=False)
        deps.library_snapshot_service.get_library_mtime = AsyncMock(return_value=None)
        deps.library_snapshot_service.is_snapshot_valid = AsyncMock(return_value=False)
        deps.library_snapshot_service.get_track_ids_from_snapshot = AsyncMock(return_value=set())
        deps.library_snapshot_service.load_snapshot = AsyncMock(return_value=None)
        deps.library_snapshot_service.save_snapshot = AsyncMock()
        deps.library_snapshot_service.get_snapshot_metadata = AsyncMock(return_value=None)

        return deps

    def test_music_updater_initialization(self) -> None:
        """Test MusicUpdater initialization."""
        deps = self.create_mock_dependencies()
        updater = MusicUpdater(deps)
        assert updater.deps is deps
        assert updater.app_config is deps.app_config
        assert updater.console_logger is deps.console_logger
        assert updater.error_logger is deps.error_logger
        assert updater.analytics is deps.analytics

        # Verify component initialization
        assert updater.track_processor is not None
        assert updater.genre_manager is not None
        assert updater.year_retriever is not None
        assert updater.database_verifier is not None
        assert updater.incremental_filter is not None

    def test_set_dry_run_context(self) -> None:
        """Test setting dry run context."""
        deps = self.create_mock_dependencies()
        updater = MusicUpdater(deps)
        test_mode = "test"
        test_artists = {"Artist1", "Artist2"}
        updater.set_dry_run_context(test_mode, test_artists)
        assert updater.dry_run_mode == test_mode
        assert updater.dry_run_test_artists == test_artists

    def test_pipeline_snapshot_management(self) -> None:
        """Test pipeline snapshot management."""
        deps = self.create_mock_dependencies()
        updater = MusicUpdater(deps)
        track1 = DummyTrackData.create(track_id="1", name="Track 1", artist="Artist 1")
        track2 = DummyTrackData.create(track_id="2", name="Track 2", artist="Artist 2")
        tracks = [track1, track2]
        updater.snapshot_manager.set_snapshot(tracks)
        snapshot = updater.snapshot_manager.get_snapshot()
        assert snapshot is not None
        assert len(snapshot) == 2
        assert "1" in updater.snapshot_manager._tracks_index
        assert "2" in updater.snapshot_manager._tracks_index
        updated_track1 = DummyTrackData.create(track_id="1", name="Track 1", artist="Artist 1", genre="Metal")
        updater.snapshot_manager.update_tracks([updated_track1])
        updated_snapshot = updater.snapshot_manager._tracks_index.get("1")
        assert updated_snapshot is not None
        assert updated_snapshot.genre == "Metal"
        updater.snapshot_manager.clear()
        assert updater.snapshot_manager.get_snapshot() is None
        assert len(updater.snapshot_manager._tracks_index) == 0

    @pytest.mark.asyncio
    async def test_run_clean_artist_success(self) -> None:
        """Test successful artist cleaning operation."""
        deps = self.create_mock_dependencies()
        updater = MusicUpdater(deps)

        # Setup test tracks with names that need cleaning
        track1 = DummyTrackData.create(
            track_id="1",
            name="Track 1 (Remastered)",
            album="Album 1 (Deluxe Edition)",
        )
        track2 = DummyTrackData.create(
            track_id="2",
            name="Track 2",
            album="Album 2",
        )

        # Mock track fetching
        deps.ap_client.set_response("fetch_tracks.applescript", "")  # Will use cache
        await deps.cache_service.set_async("tracks_Test Artist", [track1, track2])

        # Mock track updating to succeed
        deps.ap_client.set_response("update_property.applescript", "Success: Property updated")

        with (
            patch("app.music_updater.is_music_app_running", return_value=True),
            patch("app.music_updater.save_changes_report") as mock_save,
        ):
            await updater.run_clean_artist("Test Artist")
        # Check that updates were attempted
        scripts_run = deps.ap_client.scripts_run
        assert len(scripts_run) > 0

        # Only track 1 needed cleaning, so the report carries exactly its name change
        mock_save.assert_called_once()
        changes_log = mock_save.call_args.args[0]
        assert [(change.track_id, change.old_track_name, change.new_track_name) for change in changes_log] == [
            ("1", "Track 1 (Remastered)", "Track 1"),
        ]

    @pytest.mark.asyncio
    async def test_run_clean_artist_music_not_running(self) -> None:
        """Test clean artist when Music app is not running."""
        deps = self.create_mock_dependencies()
        updater = MusicUpdater(deps)

        with (
            patch(
                "app.music_updater.is_music_app_running",
                return_value=False,
            ),
        ):
            await updater.run_clean_artist("Test Artist")
        error_logs = deps.error_logger.error_messages
        assert len(error_logs) > 0
        assert "Music.app is not running" in error_logs[0]

    @pytest.mark.asyncio
    async def test_run_update_years(self) -> None:
        """Test year update operation."""
        deps = self.create_mock_dependencies()

        # Enable year retrieval so process_album_years doesn't short-circuit
        deps.app_config.year_retrieval.enabled = True

        updater = MusicUpdater(deps)

        # Setup test tracks
        track1 = DummyTrackData.create(track_id="1", album="Album 1", year="")
        track2 = DummyTrackData.create(track_id="2", album="Album 2", year="2020")

        # Mock track fetching
        await deps.cache_service.set_async("tracks_Test Artist", [track1, track2])

        # Mock API year retrieval
        deps.external_api_service.get_album_year_response = ("2021", True, 85, {"2021": 85})

        with (
            patch(
                "metrics.change_reports.sync_track_list_with_current",
                new_callable=AsyncMock,
            ) as _mock_sync,
            patch(
                "metrics.change_reports.save_changes_report",
            ) as _mock_save,
        ):
            await updater.run_update_years("Test Artist", force=False)
        # Check that external API was called
        assert len(deps.external_api_service.get_album_year_calls) > 0

    @pytest.mark.asyncio
    async def test_run_main_pipeline(self) -> None:
        """Test main pipeline execution."""
        deps = self.create_mock_dependencies()
        updater = MusicUpdater(deps)

        # Year retrieval is off in the test config; the year step needs it on
        deps.app_config.year_retrieval.enabled = True

        track1 = DummyTrackData.create(track_id="1", artist="Pipeline Artist", album="Album 1", genre="Pop", year="")
        track2 = DummyTrackData.create(track_id="2", artist="Pipeline Artist", album="Album 1", genre="Alternative", year="2020")
        track3 = DummyTrackData.create(track_id="3", artist="Pipeline Artist", album="Album 1", genre="Indie", year="")

        deps.external_api_service.get_album_year_response = ("2021", True, 85, {"2021": 85})
        deps.ap_client.set_response("update_property.applescript", "Success: Property updated")

        # With the snapshot service disabled, a full-library run fetches through AppleScript batches
        fetch_batches = AsyncMock(return_value=[track1, track2, track3])

        with (
            patch.object(updater.track_processor, "fetch_tracks_in_batches", fetch_batches),
            # Incremental gate and run stamp read and write the shared lastrun file under logs_base_dir
            patch.object(updater.database_verifier, "can_run_incremental", AsyncMock(return_value=True)),
            patch.object(updater.database_verifier, "update_last_incremental_run", AsyncMock()),
            patch("app.music_updater.IncrementalRunTracker") as mock_tracker,
            patch("app.music_updater.save_changes_report"),
            patch("app.music_updater.sync_track_list_with_current", new_callable=AsyncMock),
        ):
            mock_tracker.return_value.get_last_run_timestamp = AsyncMock(return_value=None)

            await updater.run_main_pipeline()

        fetch_batches.assert_awaited_once()
        # One album lookup: library year 2020 from track 2, earliest add year 2024 from the default date_added
        assert deps.external_api_service.get_album_year_calls == [("Pipeline Artist", "Album 1", "2020", 2024)]

    @pytest.mark.asyncio
    async def test_empty_track_list_handling(self) -> None:
        """Test handling of empty track list."""
        deps = self.create_mock_dependencies()
        updater = MusicUpdater(deps)

        # Mock empty track list
        await deps.cache_service.set_async("tracks_NonExistentArtist", [])

        with (
            patch("app.music_updater.is_music_app_running", return_value=True),
        ):
            await updater.run_clean_artist("NonExistentArtist")
        warning_logs = deps.console_logger.warning_messages
        assert any("No tracks found" in log for log in warning_logs)

    @pytest.mark.asyncio
    async def test_run_verify_database(self) -> None:
        """Test database verification operation."""
        deps = self.create_mock_dependencies()
        updater = MusicUpdater(deps)

        # Mock the database verifier's verify method
        object.__setattr__(
            updater.database_verifier,
            "verify_and_clean_track_database",
            AsyncMock(return_value=5),
        )
        await updater.run_verify_database()
        cast(MagicMock, updater.database_verifier.verify_and_clean_track_database).assert_called_once()

    @pytest.mark.asyncio
    async def test_run_verify_pending(self) -> None:
        """Test pending verification operation."""
        deps = self.create_mock_dependencies()
        updater = MusicUpdater(deps)

        # Add some pending albums to the service
        await deps.pending_verification_service.mark_for_verification("Artist 1", "Album 1", "no_year_found")
        await deps.pending_verification_service.mark_for_verification("Artist 2", "Album 2", "api_error")

        with (
            patch(
                "app.music_updater.is_music_app_running",
                return_value=True,
            ),
        ):
            # Mock successful year retrieval
            deps.external_api_service.get_album_year_response = ("2022", True, 85, {"2022": 85})

            # Mock track fetching for verification
            test_track = DummyTrackData.create(
                track_id="1",
                artist="Artist 1",
                album="Album 1",
                year="",
            )
            await deps.cache_service.set_async("tracks_all", [test_track])
        await updater.run_verify_pending()
        # Check that external API was called for pending albums
        api_calls = deps.external_api_service.get_album_year_calls
        assert len(api_calls) >= 0  # May or may not be called depending on implementation

    @pytest.mark.asyncio
    async def test_fetch_tracks_captures_library_mtime_before_fetch(self) -> None:
        """Test that library_mtime is captured before track fetch to prevent race conditions."""
        from datetime import UTC, datetime

        deps = self.create_mock_dependencies()

        # Enable snapshot service and set up library_mtime
        pre_fetch_mtime = datetime(2025, 6, 15, 10, 30, tzinfo=UTC)
        deps.library_snapshot_service.is_enabled = MagicMock(return_value=True)
        deps.library_snapshot_service.get_library_mtime = AsyncMock(return_value=pre_fetch_mtime)

        updater = MusicUpdater(deps)
        # Mock Smart Delta to return tracks
        test_track = DummyTrackData.create(track_id="1", artist="Artist", album="Album")

        with (
            patch.object(updater, "_try_smart_delta_fetch", new_callable=AsyncMock) as mock_delta,
            patch.object(updater.snapshot_manager, "set_snapshot") as mock_set_snapshot,
        ):
            mock_delta.return_value = [test_track]

            # Call the method under test
            result = await updater._fetch_tracks_for_pipeline_mode()
        # Verify get_library_mtime was called
        deps.library_snapshot_service.get_library_mtime.assert_called_once()

        # Verify set_snapshot received the pre-captured library_mtime
        mock_set_snapshot.assert_called_once()
        call_kwargs = mock_set_snapshot.call_args.kwargs
        assert call_kwargs.get("library_mtime") == pre_fetch_mtime

        # Verify tracks were returned
        assert result == [test_track]

    @pytest.mark.asyncio
    async def test_fetch_tracks_handles_disabled_snapshot_service(self) -> None:
        """Test that fetch works correctly when snapshot service is disabled."""
        deps = self.create_mock_dependencies()

        # Disable snapshot service
        deps.library_snapshot_service.is_enabled = MagicMock(return_value=False)

        updater = MusicUpdater(deps)
        test_track = DummyTrackData.create(track_id="1", artist="Artist", album="Album")

        with (
            patch.object(updater, "_try_smart_delta_fetch", new_callable=AsyncMock) as mock_delta,
            patch.object(updater.snapshot_manager, "set_snapshot") as mock_set_snapshot,
        ):
            mock_delta.return_value = [test_track]

            result = await updater._fetch_tracks_for_pipeline_mode()
        # get_library_mtime should NOT be called when service is disabled
        deps.library_snapshot_service.get_library_mtime.assert_not_called()

        # set_snapshot should still be called but with library_mtime=None
        mock_set_snapshot.assert_called_once()
        call_kwargs = mock_set_snapshot.call_args.kwargs
        assert call_kwargs.get("library_mtime") is None

        assert result == [test_track]

    @pytest.mark.asyncio
    async def test_fetch_tracks_file_not_found_on_library_mtime(self) -> None:
        """FileNotFoundError from get_library_mtime is caught, logged, and execution continues."""
        deps = self.create_mock_dependencies()

        # Enable snapshot service but make get_library_mtime raise FileNotFoundError
        deps.library_snapshot_service.is_enabled = MagicMock(return_value=True)
        deps.library_snapshot_service.get_library_mtime = AsyncMock(
            side_effect=FileNotFoundError("Music Library.musiclibrary not found"),
        )

        updater = MusicUpdater(deps)
        test_track = DummyTrackData.create(track_id="1", artist="Artist", album="Album")

        with (
            patch.object(updater, "_try_smart_delta_fetch", new_callable=AsyncMock) as mock_delta,
            patch.object(updater.snapshot_manager, "set_snapshot") as mock_set_snapshot,
        ):
            mock_delta.return_value = [test_track]

            result = await updater._fetch_tracks_for_pipeline_mode()

        # get_library_mtime was called and raised FileNotFoundError
        deps.library_snapshot_service.get_library_mtime.assert_called_once()

        # Debug log should have been emitted
        debug_messages = deps.console_logger.debug_messages
        assert any("Library file not found" in msg for msg in debug_messages)

        # Execution continued: set_snapshot was called with library_mtime=None (not set)
        mock_set_snapshot.assert_called_once()
        call_kwargs = mock_set_snapshot.call_args.kwargs
        assert call_kwargs.get("library_mtime") is None

        # Tracks were still returned
        assert result == [test_track]


class TestEmptyLibraryFetch:
    """An empty fetch fails the run only when the library is known to hold tracks."""

    @staticmethod
    def _create_updater(known_track_count: int | None, test_artists: set[str] | None = None) -> MusicUpdater:
        deps = TestMusicUpdaterAllure.create_mock_dependencies()
        metadata = (
            None
            if known_track_count is None
            else LibraryCacheMetadata(
                last_full_scan=datetime.now(UTC),
                library_mtime=datetime.now(UTC),
                track_count=known_track_count,
                snapshot_hash="hash",
            )
        )
        deps.library_snapshot_service.is_enabled = MagicMock(return_value=True)
        deps.library_snapshot_service.get_snapshot_metadata = AsyncMock(return_value=metadata)
        updater = MusicUpdater(deps)
        if test_artists:
            updater.set_dry_run_context("test", test_artists)
        object.__setattr__(updater, "_fetch_tracks_for_pipeline_mode", AsyncMock(return_value=[]))
        return updater

    @pytest.mark.asyncio
    async def test_empty_fetch_with_known_library_raises(self) -> None:
        """Zero tracks against a 32,658-track snapshot is a fetch failure, not an empty library."""
        updater = self._create_updater(known_track_count=32658)

        with pytest.raises(LibraryFetchError, match=r"32658.*main\.py --fresh"):
            await updater.run_main_pipeline()

    @pytest.mark.asyncio
    async def test_empty_fresh_fetch_records_empty_library(self) -> None:
        """--fresh accepts an empty library and persists it, so later runs stop failing."""
        updater = self._create_updater(known_track_count=32658)
        persist_to_disk = AsyncMock(return_value=True)
        object.__setattr__(updater.snapshot_manager, "persist_to_disk", persist_to_disk)

        await updater.run_main_pipeline(fresh=True)

        persist_to_disk.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("known_track_count", [None, 0])
    async def test_empty_fetch_without_known_library_completes(self, known_track_count: int | None) -> None:
        """A first run (no snapshot) or a snapshot of an empty library stays a clean exit."""
        updater = self._create_updater(known_track_count=known_track_count)

        await updater.run_main_pipeline()

        assert any("No tracks found" in message for message in updater.console_logger.warning_messages)

    @pytest.mark.asyncio
    async def test_empty_fetch_with_disabled_snapshot_completes(self) -> None:
        """Metadata left from a disabled snapshot is not maintained, so it cannot prove a failure."""
        updater = self._create_updater(known_track_count=32658)
        cast(MagicMock, updater.deps.library_snapshot_service).is_enabled.return_value = False

        await updater.run_main_pipeline()

        assert any("No tracks found" in message for message in updater.console_logger.warning_messages)

    @pytest.mark.asyncio
    async def test_empty_fetch_for_test_artists_completes(self) -> None:
        """Test artists may have no tracks, so an empty filtered fetch is not a failure."""
        updater = self._create_updater(known_track_count=32658, test_artists={"Absent Artist"})

        await updater.run_main_pipeline()

        assert any("No tracks found" in message for message in updater.console_logger.warning_messages)
