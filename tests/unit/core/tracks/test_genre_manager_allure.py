"""Enhanced GenreManager tests with Allure reporting."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import allure
import pytest
from src.core.tracks.genre import GenreManager
from src.core.models.track_models import ChangeLogEntry

from tests.mocks.csv_mock import MockAnalytics, MockLogger
from tests.mocks.track_data import DummyTrackData

if TYPE_CHECKING:
    from src.core.tracks.track_processor import TrackProcessor


@allure.epic("Music Genre Updater")
@allure.feature("Genre Management")
class TestGenreManagerAllure:
    """Enhanced tests for GenreManager with Allure reporting."""

    @staticmethod
    def create_manager(
        mock_track_processor: TrackProcessor | None = None,
        config: dict[str, Any] | None = None,
    ) -> GenreManager:
        """Create a GenreManager instance for testing."""
        if mock_track_processor is None:
            mock_track_processor = MagicMock()
            mock_track_processor.update_track_async = AsyncMock(return_value=True)

        console_logger = MockLogger()
        error_logger = MockLogger()
        analytics = MockAnalytics()
        test_config = config or {
            "genre_update": {
                "batch_size": 10,
                "concurrent_limit": 2,
            }
        }

        return GenreManager(
            track_processor=mock_track_processor,
            console_logger=console_logger,  # type: ignore[arg-type]
            error_logger=error_logger,  # type: ignore[arg-type]
            analytics=analytics,  # type: ignore[arg-type]
            config=test_config,
            dry_run=False,
        )

    @allure.story("Genre Validation")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should identify missing genre as invalid")
    @allure.description("Test that empty genre strings are correctly identified as missing/unknown")
    @pytest.mark.parametrize(
        ("genre", "expected"),
        [
            ("", True),
            ("Unknown", True),
            ("UNKNOWN", True),
            ("   ", True),
            (None, True),
            ("Rock", False),
            ("Jazz", False),
        ],
    )
    def test_is_missing_or_unknown_genre_parametrized(self, genre: str | None, expected: bool) -> None:
        """Test genre validation with various inputs."""
        with allure.step(f"Testing genre validation for: '{genre}'"):
            track = DummyTrackData.create(genre=genre)
            manager = TestGenreManagerAllure.create_manager()
            result = manager.is_missing_or_unknown_genre(track)

            allure.attach(str(genre), "Input Genre", allure.attachment_type.TEXT)
            allure.attach(str(result), "Validation Result", allure.attachment_type.TEXT)

            assert result is expected

    @allure.story("Date Parsing")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Date parsing functionality")
    def test_parse_date_added_scenarios(self) -> None:
        """Test various date parsing scenarios."""
        test_cases = [
            ("2024-01-01 12:00:00", datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)),
            ("invalid-date", None),
            ("", None),
            (None, None),
        ]

        for date_str, expected in test_cases:
            with allure.step(f"Parsing date: '{date_str}'"):
                track = DummyTrackData.create(date_added=date_str)
                manager = TestGenreManagerAllure.create_manager()
                result = manager.parse_date_added(track)

                allure.attach(str(date_str), "Input Date", allure.attachment_type.TEXT)
                allure.attach(str(result), "Parsed Result", allure.attachment_type.TEXT)

                assert result == expected

    @allure.story("Track Filtering")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Incremental track filtering")
    @allure.description("Test filtering tracks for incremental updates based on last run time")
    def test_filter_tracks_for_incremental_update_comprehensive(self) -> None:
        """Comprehensive test of incremental track filtering."""
        manager = TestGenreManagerAllure.create_manager()

        with allure.step("Setup test data"):
            last_run_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
            tracks = [
                DummyTrackData.create(track_id="1", name="Old Track", genre="Rock", date_added="2023-12-31 12:00:00"),
                DummyTrackData.create(track_id="2", name="New Track", genre="Jazz", date_added="2024-01-02 12:00:00"),
                DummyTrackData.create(track_id="3", name="Missing Genre", genre="", date_added="2023-12-31 12:00:00"),
            ]

            allure.attach(f"Total tracks: {len(tracks)}", "Input Data", allure.attachment_type.TEXT)
            allure.attach(str(last_run_time), "Last Run Time", allure.attachment_type.TEXT)

        with allure.step("Execute filtering"):
            result = manager.filter_tracks_for_incremental_update(tracks, last_run_time)

        with allure.step("Validate results"):
            expected_ids = {"2", "3"}  # New track + missing genre track
            actual_ids = {track.id for track in result}

            allure.attach(f"Expected: {expected_ids}", "Expected Track IDs", allure.attachment_type.TEXT)
            allure.attach(f"Actual: {actual_ids}", "Actual Track IDs", allure.attachment_type.TEXT)

            assert len(result) == 2
            assert actual_ids == expected_ids

    @allure.story("Genre Updates")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Successful track genre update")
    @allure.description("Test successful update of track genre with change log creation")
    @pytest.mark.asyncio
    async def test_update_track_genre_success_detailed(self) -> None:
        """Test successful track genre update with detailed logging."""
        with allure.step("Setup mock processor"):
            mock_processor = MagicMock()
            mock_processor.update_track_async = AsyncMock(return_value=True)
            manager = TestGenreManagerAllure.create_manager(mock_processor)

        with allure.step("Create test track"):
            track = DummyTrackData.create(
                track_id="123",
                name="Test Track",
                artist="Test Artist",
                album="Test Album",
                genre="Old Genre",
            )

            allure.attach(track.name, "Track Name", allure.attachment_type.TEXT)
            allure.attach(track.artist, "Artist", allure.attachment_type.TEXT)
            allure.attach(track.genre or "", "Old Genre", allure.attachment_type.TEXT)

        with allure.step("Execute genre update"):
            result_track, change_log = await manager.test_update_track_genre(track, "New Genre", False)

        with allure.step("Validate results"):
            assert result_track is not None, "Updated track should not be None"
            assert result_track.genre == "New Genre", f"Genre should be 'New Genre', got '{result_track.genre}'"
            assert change_log is not None, "Change log should not be None"
            assert isinstance(change_log, ChangeLogEntry), "Change log should be ChangeLogEntry instance"

            allure.attach(result_track.genre or "", "New Genre", allure.attachment_type.TEXT)
            allure.attach(change_log.track_id, "Updated Track ID", allure.attachment_type.TEXT)
            allure.attach(change_log.old_genre, "Old Genre (Log)", allure.attachment_type.TEXT)
            allure.attach(change_log.new_genre, "New Genre (Log)", allure.attachment_type.TEXT)

        with allure.step("Verify processor was called"):
            mock_processor.update_track_async.assert_called_once_with(
                track_id="123",
                new_genre="New Genre",
                original_artist="Test Artist",
                original_album="Test Album",
                original_track="Test Track",
            )

    @allure.story("Error Handling")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Skip prerelease tracks")
    @allure.description("Test that prerelease tracks are skipped during updates")
    @pytest.mark.asyncio
    async def test_update_track_genre_prerelease_skip_detailed(self) -> None:
        """Test skipping prerelease tracks with detailed reporting."""
        manager = TestGenreManagerAllure.create_manager()

        with allure.step("Create prerelease track"):
            track = DummyTrackData.create(
                track_id="123",
                genre="Rock",
                track_status="prerelease",
            )

            allure.attach(track.track_status or "", "Track Status", allure.attachment_type.TEXT)
            allure.attach("Prerelease tracks are read-only", "Business Rule", allure.attachment_type.TEXT)

        with allure.step("Attempt genre update"):
            result_track, change_log = await manager.test_update_track_genre(track, "Jazz", False)

        with allure.step("Verify no update occurred"):
            assert result_track is None, "Prerelease track should not be updated"
            assert change_log is None, "No change log should be created for prerelease tracks"

            allure.attach("Track skipped successfully", "Result", allure.attachment_type.TEXT)

    @allure.story("Batch Processing")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Async batch processing with error handling")
    @pytest.mark.asyncio
    async def test_gather_with_error_handling_comprehensive(self) -> None:
        """Test async batch processing with mixed success/failure scenarios."""
        manager = TestGenreManagerAllure.create_manager()

        async def success_task(task_id: int) -> str:
            """Task that simulates successful completion."""
            await asyncio.sleep(0.01)  # Simulate work
            return f"success-{task_id}"

        async def failure_task(task_id: int) -> str:
            """Task that simulates a failure."""
            await asyncio.sleep(0.01)
            error_message = f"Task {task_id} failed"
            raise ValueError(error_message)

        with allure.step("Create mixed task batch"):
            tasks = [
                asyncio.create_task(success_task(1)),
                asyncio.create_task(failure_task(2)),
                asyncio.create_task(success_task(3)),
                asyncio.create_task(failure_task(4)),
                asyncio.create_task(success_task(5)),
            ]

            allure.attach(f"Total tasks: {len(tasks)}", "Batch Size", allure.attachment_type.TEXT)
            allure.attach("2 success, 2 failure, 1 success", "Expected Pattern", allure.attachment_type.TEXT)

        with allure.step("Execute batch processing"):
            results = await manager.test_gather_with_error_handling(tasks, "test batch operation")

        with allure.step("Validate batch results"):
            # Should only get successful results
            expected_successful = 3
            assert len(results) == expected_successful, f"Expected {expected_successful} successful results, got {len(results)}"

            expected_results = ["success-1", "success-3", "success-5"]
            assert results == expected_results, f"Results mismatch: expected {expected_results}, got {results}"

            allure.attach(f"Successful tasks: {len(results)}", "Success Count", allure.attachment_type.TEXT)
            allure.attach(str(results), "Successful Results", allure.attachment_type.TEXT)

    @allure.story("Artist Processing")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("End-to-end artist genre processing")
    @allure.description("Test complete artist processing workflow with mocked dependencies")
    @pytest.mark.asyncio
    async def test_update_genres_by_artist_async_e2e(self) -> None:
        """End-to-end test of artist genre processing."""
        with allure.step("Setup mock processor"):
            mock_processor = MagicMock()
            mock_processor.update_track_async = AsyncMock(return_value=True)
            manager = TestGenreManagerAllure.create_manager(mock_processor)

        with allure.step("Create test tracks"):
            tracks = [
                DummyTrackData.create(track_id="1", artist="Artist1", genre="", name="Track 1"),
                DummyTrackData.create(track_id="2", artist="Artist1", genre="Unknown", name="Track 2"),
                DummyTrackData.create(track_id="3", artist="Artist2", genre="", name="Track 3"),
            ]

            allure.attach(f"Total tracks: {len(tracks)}", "Input Tracks", allure.attachment_type.TEXT)
            allure.attach("Artist1: 2 tracks, Artist2: 1 track", "Artist Distribution", allure.attachment_type.TEXT)

        with (
            allure.step("Mock external dependencies"),
            patch("src.core.tracks.genre.group_tracks_by_artist") as mock_group,
            patch("src.core.tracks.genre.determine_dominant_genre_for_artist") as mock_determine,
        ):
            mock_group.return_value = {
                "Artist1": tracks[:2],
                "Artist2": tracks[2:3],
            }
            mock_determine.return_value = "Rock"

            allure.attach("Rock", "Mocked Dominant Genre", allure.attachment_type.TEXT)

            with allure.step("Execute genre update workflow"):
                updated_tracks, change_logs = await manager.update_genres_by_artist_async(tracks)

        with allure.step("Validate end-to-end results"):
            assert len(updated_tracks) == 3, f"Expected 3 updated tracks, got {len(updated_tracks)}"
            assert len(change_logs) == 3, f"Expected 3 change logs, got {len(change_logs)}"

            # Verify all tracks got the Rock genre
            for track in updated_tracks:
                assert track.genre == "Rock", f"Track {track.id} should have Rock genre, got '{track.genre}'"

            allure.attach(f"Updated tracks: {len(updated_tracks)}", "Final Update Count", allure.attachment_type.TEXT)
            allure.attach(f"Change logs: {len(change_logs)}", "Change Log Count", allure.attachment_type.TEXT)

            # Create summary attachment
            summary = "\n".join([f"Track {track.id}: {track.name} -> {track.genre}" for track in updated_tracks])
            allure.attach(summary, "Update Summary", allure.attachment_type.TEXT)
