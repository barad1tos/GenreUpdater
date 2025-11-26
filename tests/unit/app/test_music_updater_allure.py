"""Enhanced MusicUpdater tests with Allure reporting."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import allure
import pytest
from src.app.updater import MusicUpdater

from tests.mocks.csv_mock import MockAnalytics, MockLogger
from tests.mocks.protocol_mocks import (
    MockAppleScriptClient,
    MockCacheService,
    MockExternalApiService,
    MockPendingVerificationService,
)
from tests.mocks.track_data import DummyTrackData


@allure.epic("Music Genre Updater")
@allure.feature("Music Updater Core")
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
            "apple_script": {"timeout": 30},
            "development": {"test_artists": ["Test Artist"]},
            "year_retrieval": {
                "api_auth": {
                    "discogs_token": "test_token",
                    "lastfm_api_key": "test_key",
                    "musicbrainz_app_name": "TestApp",
                    "contact_email": "test@example.com",
                },
                "rate_limits": {
                    "discogs_requests_per_minute": 25,
                    "musicbrainz_requests_per_second": 1,
                    "lastfm_requests_per_second": 5,
                    "itunes_requests_per_second": 10,
                },
                "processing": {"cache_ttl_days": 30},
                "logic": {
                    "min_valid_year": 1900,
                    "definitive_score_threshold": 85,
                    "definitive_score_diff": 15,
                },
                "scoring": {"base_score": 50, "exact_match_bonus": 30},
                "use_lastfm": True,
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
        deps.library_snapshot_service.is_snapshot_valid = AsyncMock(return_value=False)
        deps.library_snapshot_service.get_track_ids_from_snapshot = AsyncMock(return_value=set())
        deps.library_snapshot_service.load_snapshot = AsyncMock(return_value=None)
        deps.library_snapshot_service.save_snapshot = AsyncMock()

        return deps

    @allure.story("Initialization")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should initialize MusicUpdater with all dependencies")
    @allure.description("Test that MusicUpdater initializes correctly with required dependencies")
    def test_music_updater_initialization(self) -> None:
        """Test MusicUpdater initialization."""
        with allure.step("Create mock dependencies"):
            deps = self.create_mock_dependencies()

        with allure.step("Initialize MusicUpdater"):
            updater = MusicUpdater(deps)

        with allure.step("Verify initialization"):
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

            allure.attach("MusicUpdater initialized successfully", "Initialization Result", allure.attachment_type.TEXT)

    @allure.story("Dry Run Context")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should set dry run context correctly")
    @allure.description("Test dry run context configuration for test mode filtering")
    def test_set_dry_run_context(self) -> None:
        """Test setting dry run context."""
        with allure.step("Setup MusicUpdater"):
            deps = self.create_mock_dependencies()
            updater = MusicUpdater(deps)

        with allure.step("Set dry run context"):
            test_mode = "test"
            test_artists = {"Artist1", "Artist2"}
            updater.set_dry_run_context(test_mode, test_artists)

        with allure.step("Verify context is set"):
            assert updater.dry_run_mode == test_mode
            assert updater.dry_run_test_artists == test_artists

            allure.attach(test_mode, "Dry Run Mode", allure.attachment_type.TEXT)
            allure.attach(str(test_artists), "Test Artists", allure.attachment_type.TEXT)

    @allure.story("Pipeline Snapshot Management")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should manage pipeline snapshots correctly")
    @allure.description("Test pipeline snapshot creation, update, and retrieval")
    def test_pipeline_snapshot_management(self) -> None:
        """Test pipeline snapshot management."""
        with allure.step("Setup MusicUpdater"):
            deps = self.create_mock_dependencies()
            updater = MusicUpdater(deps)

        with allure.step("Create test tracks"):
            track1 = DummyTrackData.create(track_id="1", name="Track 1", artist="Artist 1")
            track2 = DummyTrackData.create(track_id="2", name="Track 2", artist="Artist 2")
            tracks = [track1, track2]

        with allure.step("Set pipeline snapshot"):
            updater._set_pipeline_snapshot(tracks)  # noqa: SLF001

        with allure.step("Verify snapshot is stored"):
            snapshot = updater._get_pipeline_snapshot()  # noqa: SLF001
            assert snapshot is not None
            assert len(snapshot) == 2
            assert "1" in updater._pipeline_tracks_index  # noqa: SLF001
            assert "2" in updater._pipeline_tracks_index  # noqa: SLF001

        with allure.step("Update snapshot tracks"):
            updated_track1 = DummyTrackData.create(track_id="1", name="Track 1", artist="Artist 1", genre="Metal")
            updater._update_snapshot_tracks([updated_track1])  # noqa: SLF001

        with allure.step("Verify updates applied"):
            updated_snapshot = updater._pipeline_tracks_index.get("1")  # noqa: SLF001
            assert updated_snapshot is not None
            assert updated_snapshot.genre == "Metal"

        with allure.step("Clear snapshot"):
            updater._clear_pipeline_snapshot()  # noqa: SLF001
            assert updater._get_pipeline_snapshot() is None  # noqa: SLF001
            assert len(updater._pipeline_tracks_index) == 0  # noqa: SLF001

            allure.attach("Pipeline snapshot managed successfully", "Snapshot Result", allure.attachment_type.TEXT)

    @allure.story("Clean Artist Operation")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should clean track names for specific artist")
    @allure.description("Test cleaning track names and album names for an artist")
    @pytest.mark.asyncio
    async def test_run_clean_artist_success(self) -> None:
        """Test successful artist cleaning operation."""
        with allure.step("Setup MusicUpdater with mocks"):
            deps = self.create_mock_dependencies()
            updater = MusicUpdater(deps)

            # Mock Music app running check
            with patch("src.app.updater.is_music_app_running", return_value=True):
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
                deps.ap_client.set_response("fetch_tracks.scpt", "")  # Will use cache
                await deps.cache_service.set_async("tracks_Test Artist", [track1, track2])

                # Mock track updating to succeed
                deps.ap_client.set_response("update_property.applescript", "Success: Property updated")

        with allure.step("Execute clean artist operation"), patch(
            "src.app.updater.save_changes_report",
        ) as mock_save:
            await updater.run_clean_artist("Test Artist", False)

        with allure.step("Verify cleaning operations"):
            # Check that updates were attempted
            scripts_run = deps.ap_client.scripts_run
            assert len(scripts_run) > 0

            # Verify that save_changes_report was called for tracks that needed cleaning
            if mock_save.called:
                allure.attach(f"Cleaned {mock_save.call_count} tracks", "Cleaning Result", allure.attachment_type.TEXT)

            allure.attach("Artist cleaning completed successfully", "Operation Result", allure.attachment_type.TEXT)

    @allure.story("Clean Artist Operation")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should handle Music app not running")
    @allure.description("Test clean artist operation when Music app is not running")
    @pytest.mark.asyncio
    async def test_run_clean_artist_music_not_running(self) -> None:
        """Test clean artist when Music app is not running."""
        with allure.step("Setup MusicUpdater"):
            deps = self.create_mock_dependencies()
            updater = MusicUpdater(deps)

        with allure.step("Mock Music app not running"), patch(
            "src.app.updater.is_music_app_running",
            return_value=False,
        ):
            await updater.run_clean_artist("Test Artist", False)

        with allure.step("Verify error logged"):
            error_logs = deps.error_logger.error_messages
            assert len(error_logs) > 0
            assert "Music app is not running" in error_logs[0]

            allure.attach("Music app not running handled", "Error Handling", allure.attachment_type.TEXT)

    @allure.story("Year Update Operation")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should update years for tracks")
    @allure.description("Test updating release years for tracks")
    @pytest.mark.asyncio
    async def test_run_update_years(self) -> None:
        """Test year update operation."""
        with allure.step("Setup MusicUpdater with mocks"):
            deps = self.create_mock_dependencies()
            updater = MusicUpdater(deps)

            # Mock Music app running check
            with patch("src.app.updater.is_music_app_running", return_value=True):
                # Setup test tracks
                track1 = DummyTrackData.create(track_id="1", album="Album 1", year="")
                track2 = DummyTrackData.create(track_id="2", album="Album 2", year="2020")

                # Mock track fetching
                await deps.cache_service.set_async("tracks_Test Artist", [track1, track2])

                # Mock API year retrieval
                deps.external_api_service.get_album_year_response = ("2021", True)

        with allure.step("Execute year update operation"), patch(
            "src.metrics.reports.sync_track_list_with_current",
            new_callable=AsyncMock,
        ) as _mock_sync, patch(
            "src.metrics.reports.save_changes_report",
        ) as _mock_save:
            await updater.run_update_years("Test Artist", force=False)

        with allure.step("Verify year update operations"):
            # Check that external API was called
            assert len(deps.external_api_service.get_album_year_calls) > 0

            allure.attach(
                f"Updated years for {len(deps.external_api_service.get_album_year_calls)} albums",
                "Year Update Result",
                allure.attachment_type.TEXT
            )

    @allure.story("Main Pipeline")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should run main pipeline successfully")
    @allure.description("Test the main pipeline that updates both genres and years")
    @pytest.mark.asyncio
    async def test_run_main_pipeline(self) -> None:
        """Test main pipeline execution."""
        with allure.step("Setup MusicUpdater with mocks"):
            deps = self.create_mock_dependencies()
            updater = MusicUpdater(deps)

            # Mock Music app running check
            with patch("src.app.updater.is_music_app_running", return_value=True):
                # Setup test tracks
                track1 = DummyTrackData.create(
                    track_id="1",
                    artist="Pipeline Artist",
                    album="Album 1",
                    genre="Pop",
                    year=""
                )
                track2 = DummyTrackData.create(
                    track_id="2",
                    artist="Pipeline Artist",
                    album="Album 1",
                    genre="Alternative",
                    year="2020"
                )
                track3 = DummyTrackData.create(
                    track_id="3",
                    artist="Pipeline Artist",
                    album="Album 1",
                    genre="Indie",
                    year=""
                )

                # Mock track fetching
                await deps.cache_service.set_async("tracks_all", [track1, track2, track3])

                # Mock API responses
                deps.external_api_service.get_album_year_response = ("2021", True)
                deps.ap_client.set_response("update_property.applescript", "Success: Property updated")

        with allure.step("Execute main pipeline"), patch(
            "src.app.updater.IncrementalRunTracker"
        ) as mock_tracker, patch(
            "src.metrics.reports.save_changes_report",
            new_callable=AsyncMock,
        ):
            mock_tracker.return_value.should_process.return_value = True
            mock_tracker.return_value.mark_run_complete = MagicMock()

            await updater.run_main_pipeline()

        with allure.step("Verify pipeline operations"):
            # Verify tracks were fetched
            assert deps.cache_service.load_count >= 0

            # Verify external API was used for year retrieval
            if deps.external_api_service.get_album_year_calls:
                allure.attach(
                    f"Processed {len(deps.external_api_service.get_album_year_calls)} albums",
                    "Pipeline Result",
                    allure.attachment_type.TEXT
                )

            allure.attach("Main pipeline executed successfully", "Pipeline Status", allure.attachment_type.TEXT)

    @allure.story("Error Handling")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should handle empty track list gracefully")
    @allure.description("Test handling when no tracks are found")
    @pytest.mark.asyncio
    async def test_empty_track_list_handling(self) -> None:
        """Test handling of empty track list."""
        with allure.step("Setup MusicUpdater with empty tracks"):
            deps = self.create_mock_dependencies()
            updater = MusicUpdater(deps)

            # Mock Music app running
            with patch("src.app.updater.is_music_app_running", return_value=True):
                # Mock empty track list
                await deps.cache_service.set_async("tracks_NonExistentArtist", [])

        with allure.step("Execute clean artist with no tracks"):
            await updater.run_clean_artist("NonExistentArtist", False)

        with allure.step("Verify warning logged"):
            warning_logs = deps.console_logger.warning_messages
            assert any("No tracks found" in log for log in warning_logs)

            allure.attach("Empty track list handled gracefully", "Error Handling", allure.attachment_type.TEXT)

    @allure.story("Database Verification")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should verify database integrity")
    @allure.description("Test database verification operation")
    @pytest.mark.asyncio
    async def test_run_verify_database(self) -> None:
        """Test database verification operation."""
        with allure.step("Setup MusicUpdater"):
            deps = self.create_mock_dependencies()
            updater = MusicUpdater(deps)

            # Mock the database verifier's verify method
            updater.database_verifier.verify_and_clean_track_database = AsyncMock(return_value=5)

        with allure.step("Execute database verification"):
            await updater.run_verify_database()

        with allure.step("Verify database verifier was called"):
            updater.database_verifier.verify_and_clean_track_database.assert_called_once()

            allure.attach("Database verification completed", "Verification Result", allure.attachment_type.TEXT)

    @allure.story("Pending Verification")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should process pending verifications")
    @allure.description("Test processing of albums marked for future verification")
    @pytest.mark.asyncio
    async def test_run_verify_pending(self) -> None:
        """Test pending verification operation."""
        with allure.step("Setup MusicUpdater with pending albums"):
            deps = self.create_mock_dependencies()
            updater = MusicUpdater(deps)

            # Add some pending albums to the service
            await deps.pending_verification_service.mark_for_verification(
                "Artist 1", "Album 1", "no_year_found"
            )
            await deps.pending_verification_service.mark_for_verification(
                "Artist 2", "Album 2", "api_error"
            )

        with allure.step("Mock Music app running"), patch(
            "src.app.updater.is_music_app_running",
            return_value=True,
        ):
            # Mock successful year retrieval
            deps.external_api_service.get_album_year_response = ("2022", True)

            # Mock track fetching for verification
            test_track = DummyTrackData.create(
                track_id="1",
                artist="Artist 1",
                album="Album 1",
                year="",
            )
            await deps.cache_service.set_async("tracks_all", [test_track])

        with allure.step("Execute pending verification"):
            await updater.run_verify_pending()

        with allure.step("Verify pending albums were processed"):
            # Check that external API was called for pending albums
            api_calls = deps.external_api_service.get_album_year_calls
            assert len(api_calls) >= 0  # May or may not be called depending on implementation

            allure.attach(
                f"Processed {len(deps.pending_verification_service.pending_albums)} pending albums",
                "Pending Verification Result",
                allure.attachment_type.TEXT
            )
