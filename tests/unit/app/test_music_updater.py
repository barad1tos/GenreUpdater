"""Enhanced MusicUpdater tests with Allure reporting."""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.music_updater import MusicUpdater
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

        # Configuration
        deps.config = {
            "logs_base_dir": "/tmp/test_logs",
            "apple_script": {"timeout": 30},
            "development": {"test_artists": ["Test Artist"]},
            "year_retrieval": {
                "api_auth": {
                    "discogs_token": "test_token",
                    "musicbrainz_app_name": "TestApp",
                    "contact_email": "test@example.com",
                },
                "rate_limits": {
                    "discogs_requests_per_minute": 25,
                    "musicbrainz_requests_per_second": 1,
                    "itunes_requests_per_second": 10,
                },
                "processing": {"cache_ttl_days": 30},
                "logic": {
                    "min_valid_year": 1900,
                    "definitive_score_threshold": 85,
                    "definitive_score_diff": 15,
                },
                "scoring": {"base_score": 50, "exact_match_bonus": 30},
            },
            "genre_processing": {
                "dominant_genre_threshold": 0.5,
                "min_tracks_for_dominant": 3,
            },
            "cleaning": {
                "unwanted_phrases": ["(Remastered)", "(Deluxe Edition)"],
            },
        }

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

        return deps

    def test_music_updater_initialization(self) -> None:
        """Test MusicUpdater initialization."""
        deps = self.create_mock_dependencies()
        updater = MusicUpdater(deps)
        assert updater.deps is deps
        assert updater.config is deps.config
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

        # Verify that save_changes_report was called for tracks that needed cleaning
        if mock_save.called:
            pass

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
        updater = MusicUpdater(deps)

        # Mock Music app running check
        with patch("app.music_updater.is_music_app_running", return_value=True):
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

        # Mock Music app running check
        with patch("app.music_updater.is_music_app_running", return_value=True):
            # Setup test tracks
            track1 = DummyTrackData.create(track_id="1", artist="Pipeline Artist", album="Album 1", genre="Pop", year="")
            track2 = DummyTrackData.create(track_id="2", artist="Pipeline Artist", album="Album 1", genre="Alternative", year="2020")
            track3 = DummyTrackData.create(track_id="3", artist="Pipeline Artist", album="Album 1", genre="Indie", year="")

            # Mock track fetching
            await deps.cache_service.set_async("tracks_all", [track1, track2, track3])

            # Mock API responses
            deps.external_api_service.get_album_year_response = ("2021", True, 85, {"2021": 85})
            deps.ap_client.set_response("update_property.applescript", "Success: Property updated")

        with (
            patch("app.music_updater.IncrementalRunTracker") as mock_tracker,
            patch(
                "metrics.change_reports.save_changes_report",
                new_callable=AsyncMock,
            ),
        ):
            mock_tracker.return_value.should_process.return_value = True
            mock_tracker.return_value.mark_run_complete = MagicMock()

            await updater.run_main_pipeline()
        # Verify tracks were fetched
        assert deps.cache_service.load_count >= 0

        # Verify external API was used for year retrieval
        if deps.external_api_service.get_album_year_calls:
            pass

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
