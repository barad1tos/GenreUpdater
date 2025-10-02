"""Unit tests for GenreManager class."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.domain.tracks.genre_manager import GenreManager
from src.shared.data.models import ChangeLogEntry, TrackDict
from src.shared.monitoring.analytics import Analytics

from tests.mocks.csv_mock import MockAnalytics, MockLogger
from tests.mocks.track_data import DummyTrackData

if TYPE_CHECKING:
    from src.domain.tracks.track_processor import TrackProcessor


class TestGenreManager:
    """Tests for GenreManager class."""

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
        analytics = cast(Analytics, cast(object, MockAnalytics()))
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
            analytics=analytics,
            config=test_config
        )

    def test_is_missing_or_unknown_genre_empty(self) -> None:
        """Test genre validation - empty genre."""
        track = DummyTrackData.create(genre="")
        assert GenreManager.is_missing_or_unknown_genre(track) is True

    def test_is_missing_or_unknown_genre_unknown(self) -> None:
        """Test genre validation - unknown genre."""
        track = DummyTrackData.create(genre="Unknown")
        assert GenreManager.is_missing_or_unknown_genre(track) is True

    def test_is_missing_or_unknown_genre_unknown_case_insensitive(self) -> None:
        """Test genre validation - unknown genre case insensitive."""
        track = DummyTrackData.create(genre="UNKNOWN")
        assert GenreManager.is_missing_or_unknown_genre(track) is True

    def test_is_missing_or_unknown_genre_whitespace(self) -> None:
        """Test genre validation - whitespace only."""
        track = DummyTrackData.create(genre="   ")
        assert GenreManager.is_missing_or_unknown_genre(track) is True

    def test_is_missing_or_unknown_genre_valid(self) -> None:
        """Test genre validation - valid genre."""
        track = DummyTrackData.create()
        assert GenreManager.is_missing_or_unknown_genre(track) is False

    def test_is_missing_or_unknown_genre_non_string(self) -> None:
        """Test genre validation - non-string genre."""
        track = DummyTrackData.create(genre=None)
        assert GenreManager.is_missing_or_unknown_genre(track) is True

    def test_parse_date_added_valid(self) -> None:
        """Test parsing valid date_added."""
        track = DummyTrackData.create()
        result = GenreManager.parse_date_added(track)
        expected = datetime(2024, 1, 1, 12, tzinfo=UTC)
        assert result == expected

    def test_parse_date_added_invalid_format(self) -> None:
        """Test parsing invalid date format."""
        track = DummyTrackData.create(date_added="invalid-date")
        result = GenreManager.parse_date_added(track)
        assert result is None

    def test_parse_date_added_empty(self) -> None:
        """Test parsing empty date."""
        track = DummyTrackData.create(date_added="")
        result = GenreManager.parse_date_added(track)
        assert result is None

    def test_parse_date_added_none(self) -> None:
        """Test parsing None date."""
        track = DummyTrackData.create(date_added=None)
        result = GenreManager.parse_date_added(track)
        assert result is None

    def test_filter_tracks_for_incremental_update_no_last_run(self) -> None:
        """Test filtering when no last run time."""
        manager = TestGenreManager.create_manager()
        tracks = [
            DummyTrackData.create(track_id="1", name="Track 1"),
            DummyTrackData.create(track_id="2", name="Track 2"),
        ]

        result = manager.filter_tracks_for_incremental_update(tracks, None)

        assert len(result) == 2
        assert result[0].id == "1"
        assert result[1].id == "2"

    def test_filter_tracks_for_incremental_update_with_new_tracks(self) -> None:
        """Test filtering with new tracks."""
        manager = TestGenreManager.create_manager()
        last_run_time = datetime(2024, 1, 1, 12, tzinfo=UTC)

        tracks = [
            DummyTrackData.create(track_id="1", date_added="2023-12-31 12:00:00"),  # Older
            DummyTrackData.create(track_id="2", date_added="2024-01-02 12:00:00"),  # Newer
        ]

        result = manager.filter_tracks_for_incremental_update(tracks, last_run_time)

        assert len(result) == 1
        assert result[0].id == "2"

    def test_filter_tracks_for_incremental_update_missing_genre(self) -> None:
        """Test filtering includes tracks with missing genre."""
        manager = TestGenreManager.create_manager()
        last_run_time = datetime(2024, 1, 1, 12, tzinfo=UTC)

        tracks = [
            DummyTrackData.create(track_id="1", date_added="2023-12-31 12:00:00"),  # Old with genre
            DummyTrackData.create(track_id="2", genre="", date_added="2023-12-31 12:00:00"),  # Old, missing genre
            DummyTrackData.create(track_id="3", genre="Unknown", date_added="2023-12-31 12:00:00"),  # Old, unknown genre
        ]

        result = manager.filter_tracks_for_incremental_update(tracks, last_run_time)

        assert len(result) == 2
        track_ids = {track.id for track in result}
        assert track_ids == {"2", "3"}

    def test_filter_tracks_for_incremental_update_deduplication(self) -> None:
        """Test deduplication in incremental filtering."""
        manager = TestGenreManager.create_manager()
        last_run_time = datetime(2024, 1, 1, 12, tzinfo=UTC)

        tracks = [
            DummyTrackData.create(track_id="1", genre="", date_added="2024-01-02 12:00:00"),  # New + missing genre
        ]

        result = manager.filter_tracks_for_incremental_update(tracks, last_run_time)

        # Should appear only once despite matching multiple criteria
        assert len(result) == 1
        assert result[0].id == "1"

    @pytest.mark.asyncio
    async def test_update_track_genre_success(self) -> None:
        """Test successful track genre update."""
        mock_processor = MagicMock()
        mock_processor.update_track_async = AsyncMock(return_value=True)
        manager = TestGenreManager.create_manager(mock_processor)

        track = DummyTrackData.create(
            track_id="123",
            genre="Old Genre",
        )

        result_track, change_log = await manager.test_update_track_genre(track, "New Genre", False)

        assert result_track is not None
        assert result_track.genre == "New Genre"
        assert change_log is not None
        assert isinstance(change_log, ChangeLogEntry)
        assert change_log.track_id == "123"
        assert change_log.old_genre == "Old Genre"
        assert change_log.new_genre == "New Genre"

        mock_processor.update_track_async.assert_called_once_with(
            track_id="123",
            new_genre="New Genre",
            original_artist="Test Artist",
            original_album="Test Album",
            original_track="Test Track",
        )

    @pytest.mark.asyncio
    async def test_update_track_genre_no_update_needed(self) -> None:
        """Test when no genre update is needed."""
        manager = TestGenreManager.create_manager()

        track = DummyTrackData.create()

        result_track, change_log = await manager.test_update_track_genre(track, "Rock", False)

        assert result_track is None
        assert change_log is None

    @pytest.mark.asyncio
    async def test_update_track_genre_force_update(self) -> None:
        """Test force update with same genre."""
        mock_processor = MagicMock()
        mock_processor.update_track_async = AsyncMock(return_value=True)
        manager = TestGenreManager.create_manager(mock_processor)

        track = DummyTrackData.create(
            track_id="123",
        )

        result_track, change_log = await manager.test_update_track_genre(track, "Rock", True)

        assert result_track is not None
        assert change_log is not None
        mock_processor.update_track_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_track_genre_prerelease_skip(self) -> None:
        """Test skipping prerelease tracks."""
        manager = TestGenreManager.create_manager()

        track = DummyTrackData.create(
            track_id="123",
            track_status="prerelease",
        )

        result_track, change_log = await manager.test_update_track_genre(track, "Jazz", False)

        assert result_track is None
        assert change_log is None

    @pytest.mark.asyncio
    async def test_update_track_genre_missing_id(self) -> None:
        """Test handling track with missing ID."""
        manager = TestGenreManager.create_manager()

        track = DummyTrackData.create(track_id="")

        result_track, change_log = await manager.test_update_track_genre(track, "Jazz", False)

        assert result_track is None
        assert change_log is None

    @pytest.mark.asyncio
    async def test_update_track_genre_update_failure(self) -> None:
        """Test handling track update failure."""
        mock_processor = MagicMock()
        mock_processor.update_track_async = AsyncMock(return_value=False)
        manager = TestGenreManager.create_manager(mock_processor)

        track = DummyTrackData.create(
            track_id="123",
            genre="Old Genre",
        )

        result_track, change_log = await manager.test_update_track_genre(track, "New Genre", False)

        assert result_track is None
        assert change_log is None

    @pytest.mark.asyncio
    async def test_gather_with_error_handling_success(self) -> None:
        """Test gathering tasks with all successes."""
        manager = TestGenreManager.create_manager()

        async def success_task() -> str:
            """Create a task that succeeds."""
            return "success"

        tasks = [asyncio.create_task(success_task()) for _ in range(3)]

        results = await manager.test_gather_with_error_handling(tasks, "test operation")

        assert len(results) == 3
        assert all(result == "success" for result in results)

    @pytest.mark.asyncio
    async def test_gather_with_error_handling_with_failures(self) -> None:
        """Test gathering tasks with some failures."""
        manager = TestGenreManager.create_manager()

        async def success_task() -> str:
            """Create a task that succeeds."""
            return "success"

        async def failure_task() -> str:
            """Create a task that fails."""
            error_message = "Test error"
            raise ValueError(error_message)

        tasks = [
            asyncio.create_task(success_task()),
            asyncio.create_task(failure_task()),
            asyncio.create_task(success_task()),
        ]

        results = await manager.test_gather_with_error_handling(tasks, "test operation")

        assert len(results) == 2  # Only successful results
        assert all(result == "success" for result in results)

    def test_log_artist_debug_info_green_carnation(self) -> None:
        """Test debug logging for Green Carnation artist."""
        manager = TestGenreManager.create_manager()
        tracks = [
            DummyTrackData.create(track_id="1", name="Song 1", album="Album 1"),
            DummyTrackData.create(track_id="2", name="Song 2", album="Album 2"),
        ]

        # Should not raise exception
        manager.test_log_artist_debug_info("Green Carnation", tracks)

    def test_log_artist_debug_info_other_artist(self) -> None:
        """Test debug logging for other artists (should not log details)."""
        manager = TestGenreManager.create_manager()
        tracks = [DummyTrackData.create(track_id="1", name="Song 1")]

        # Should not raise exception
        manager.test_log_artist_debug_info("Other Artist", tracks)

    def test_process_batch_results_with_updates(self) -> None:
        """Test processing batch results with updates."""
        updated_tracks: list[TrackDict] = []
        change_logs: list[ChangeLogEntry] = []

        track1 = DummyTrackData.create(track_id="1")
        track2 = DummyTrackData.create(track_id="2")
        change_log1 = ChangeLogEntry(
            timestamp="2024-01-01 12:00:00",
            change_type="genre_update",
            track_id="1",
            artist="Artist",
            track_name="Track",
            album_name="Album",
            old_genre="Old",
            new_genre="New",
        )

        batch_results = [
            (track1, change_log1),
            (track2, None),
            None,  # Failed result
        ]

        GenreManager.process_batch_results(batch_results, updated_tracks, change_logs)

        assert len(updated_tracks) == 2
        assert len(change_logs) == 1
        assert updated_tracks[0].id == "1"
        assert updated_tracks[1].id == "2"

    def test_process_batch_results_empty(self) -> None:
        """Test processing empty batch results."""
        updated_tracks: list[TrackDict] = []
        change_logs: list[ChangeLogEntry] = []

        GenreManager.process_batch_results([], updated_tracks, change_logs)

        assert not updated_tracks
        assert not change_logs

    def test_filter_tracks_for_update_force_flag(self) -> None:
        """Test filtering tracks with force flag."""
        manager = TestGenreManager.create_manager()
        tracks = [
            DummyTrackData.create(track_id="1", date_added="2023-01-01 12:00:00"),
            DummyTrackData.create(track_id="2", genre="Jazz", date_added="2023-01-01 12:00:00"),
        ]

        result = manager.test_filter_tracks_for_update(tracks, datetime(2024, 1, 1, tzinfo=UTC), True, "Metal")

        assert len(result) == 2

    def test_filter_tracks_for_update_missing_genre(self) -> None:
        """Test filtering tracks with missing genre."""
        manager = TestGenreManager.create_manager()
        tracks = [
            DummyTrackData.create(track_id="1", genre="", date_added="2023-01-01 12:00:00"),  # Missing genre
            DummyTrackData.create(track_id="2", genre="Metal", date_added="2023-01-01 12:00:00"),  # Matches dominant
        ]

        result = manager.test_filter_tracks_for_update(tracks, datetime(2024, 1, 1, tzinfo=UTC), False, "Metal")

        # Only track with missing genre should be returned
        assert len(result) == 1
        assert result[0].id == "1"

    def test_filter_tracks_for_update_new_tracks(self) -> None:
        """Test filtering tracks added after last run."""
        manager = TestGenreManager.create_manager()
        last_run = datetime(2024, 1, 1, tzinfo=UTC)
        tracks = [
            DummyTrackData.create(track_id="1", genre="Metal", date_added="2023-12-31 12:00:00"),  # Old, matches dominant
            DummyTrackData.create(track_id="2", genre="Metal", date_added="2024-01-02 12:00:00"),  # New, matches dominant
        ]

        result = manager.test_filter_tracks_for_update(tracks, last_run, False, "Metal")

        # Only new track should be returned (matches by date)
        assert len(result) == 1
        assert result[0].id == "2"

    def test_filter_tracks_for_update_different_genre(self) -> None:
        """Test filtering tracks with different genre from dominant."""
        manager = TestGenreManager.create_manager()
        tracks = [
            DummyTrackData.create(track_id="1", date_added="2023-01-01 12:00:00"),
            DummyTrackData.create(track_id="2", genre="Metal", date_added="2023-01-01 12:00:00"),  # Matches dominant
        ]

        result = manager.test_filter_tracks_for_update(tracks, datetime(2024, 1, 1, tzinfo=UTC), False, "Metal")

        assert len(result) == 1
        assert result[0].id == "1"  # Different from dominant genre

    def test_deduplicate_tracks_by_id(self) -> None:
        """Test track deduplication by ID."""
        tracks = [
            DummyTrackData.create(track_id="1", name="Track 1"),
            DummyTrackData.create(track_id="2", name="Track 2"),
            DummyTrackData.create(track_id="1", name="Track 1 Duplicate"),  # Duplicate
            DummyTrackData.create(track_id="", name="Empty ID"),  # Empty ID
        ]

        result = GenreManager.deduplicate_tracks_by_id(tracks)

        assert len(result) == 2
        track_ids = {track.id for track in result}
        assert track_ids == {"1", "2"}

    def test_select_tracks_to_update_for_artist_no_dominant_genre(self) -> None:
        """Test track selection when no dominant genre found."""
        manager = TestGenreManager.create_manager()
        tracks = [DummyTrackData.create(track_id="1")]

        result = manager.test_select_tracks_to_update_for_artist(tracks, datetime(2024, 1, 1, tzinfo=UTC), False, None)

        assert len(result) == 0

    def test_select_tracks_to_update_for_artist_force_no_dominant(self) -> None:
        """Test track selection with force flag even without dominant genre."""
        manager = TestGenreManager.create_manager()
        tracks = [DummyTrackData.create(track_id="1")]

        result = manager.test_select_tracks_to_update_for_artist(tracks, datetime(2024, 1, 1, tzinfo=UTC), True, None)

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_process_artist_genres_no_dominant_genre(self) -> None:
        """Test processing artist with no determinable dominant genre."""
        manager = TestGenreManager.create_manager()
        tracks = [DummyTrackData.create(track_id="1")]

        with patch("src.domain.tracks.genre_manager.determine_dominant_genre_for_artist") as mock_determine:
            mock_determine.return_value = None

            updated_tracks, change_logs = await manager.test_process_artist_genres("Test Artist", tracks, False)

            assert len(updated_tracks) == 0
            assert len(change_logs) == 0

    @pytest.mark.asyncio
    async def test_process_artist_genres_with_dominant_genre(self) -> None:
        """Test processing artist with dominant genre."""
        mock_processor = MagicMock()
        mock_processor.update_track_async = AsyncMock(return_value=True)
        manager = TestGenreManager.create_manager(mock_processor)

        tracks = [DummyTrackData.create(track_id="1", genre="Old Genre")]

        with patch("src.domain.tracks.genre_manager.determine_dominant_genre_for_artist") as mock_determine:
            mock_determine.return_value = "New Genre"

            updated_tracks, change_logs = await manager.test_process_artist_genres("Test Artist", tracks, False)

            assert len(updated_tracks) == 1
            assert len(change_logs) == 1

    def test_get_dry_run_actions(self) -> None:
        """Test getting dry run actions."""
        manager = TestGenreManager.create_manager()

        # Initially should be empty
        actions = manager.get_dry_run_actions()
        assert not actions

    @pytest.mark.asyncio
    async def test_update_genres_by_artist_async_empty_tracks(self) -> None:
        """Test genre update with empty track list."""
        manager = TestGenreManager.create_manager()

        updated_tracks, change_logs = await manager.update_genres_by_artist_async([])

        assert len(updated_tracks) == 0
        assert len(change_logs) == 0

    @pytest.mark.asyncio
    async def test_update_genres_by_artist_async_with_tracks(self) -> None:
        """Test genre update with tracks."""
        mock_processor = MagicMock()
        mock_processor.update_track_async = AsyncMock(return_value=True)
        manager = TestGenreManager.create_manager(mock_processor)

        tracks = [
            DummyTrackData.create(track_id="1", artist="Artist1", genre=""),
            DummyTrackData.create(track_id="2", artist="Artist1", genre=""),
        ]

        with patch("src.domain.tracks.genre_manager.group_tracks_by_artist") as mock_group:
            mock_group.return_value = {"Artist1": tracks}

            with patch("src.domain.tracks.genre_manager.determine_dominant_genre_for_artist") as mock_determine:
                mock_determine.return_value = "Rock"

                updated_tracks, change_logs = await manager.update_genres_by_artist_async(tracks)

                assert len(updated_tracks) == 2
                assert len(change_logs) == 2

    @pytest.mark.asyncio
    async def test_process_single_artist_wrapper(self) -> None:
        """Test single artist wrapper processing."""
        mock_processor = MagicMock()
        mock_processor.update_track_async = AsyncMock(return_value=True)
        manager = TestGenreManager.create_manager(mock_processor)

        tracks = [DummyTrackData.create(track_id="1", genre="")]
        semaphore = asyncio.Semaphore()

        with patch("src.domain.tracks.genre_manager.determine_dominant_genre_for_artist") as mock_determine:
            mock_determine.return_value = "Rock"

            updated_tracks, change_logs = await manager.test_process_single_artist_wrapper("Test Artist", tracks, None, False, semaphore)

            assert len(updated_tracks) == 1
            assert len(change_logs) == 1

    @pytest.mark.asyncio
    async def test_process_single_artist_wrapper_no_tracks_to_update(self) -> None:
        """Test single artist wrapper with no tracks needing updates."""
        manager = TestGenreManager.create_manager()

        tracks = [DummyTrackData.create(track_id="1")]
        semaphore = asyncio.Semaphore()

        with patch("src.domain.tracks.genre_manager.determine_dominant_genre_for_artist") as mock_determine:
            mock_determine.return_value = "Rock"  # Same genre, no update needed

            updated_tracks, change_logs = await manager.test_process_single_artist_wrapper(
                "Test Artist", tracks, datetime(2024, 1, 1, tzinfo=UTC), False, semaphore
            )

            assert len(updated_tracks) == 0
            assert len(change_logs) == 0
