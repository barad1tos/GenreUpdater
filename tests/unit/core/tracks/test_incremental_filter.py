"""Unit tests for incremental filter service."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from src.core.tracks.incremental_filter import IncrementalFilterService

from tests.mocks.csv_mock import MockAnalytics, MockGetFullLogPath, MockLoadTrackList, MockLogger
from tests.mocks.track_data import DummyTrackData


class TestIncrementalFilterService:
    """Tests for IncrementalFilterService class."""

    @staticmethod
    def create_service() -> IncrementalFilterService:
        """Create a service instance for testing."""
        console_logger = MockLogger()
        error_logger = MockLogger()
        analytics = MockAnalytics()
        config = {"csv_output_file": "csv/track_list.csv"}

        return IncrementalFilterService(
            console_logger=console_logger,  # type: ignore[arg-type]
            error_logger=error_logger,  # type: ignore[arg-type]
            analytics=analytics,  # type: ignore[arg-type]
            config=config,
        )

    def test_filter_tracks_no_last_run(self) -> None:
        """Test filtering when no last run time (first run scenario)."""
        service = TestIncrementalFilterService.create_service()
        tracks = [
            DummyTrackData.create(track_id="1", name="Track 1"),
            DummyTrackData.create(track_id="2", name="Track 2"),
        ]

        result = service.filter_tracks_for_incremental_update(
            tracks=tracks,
            last_run_time=None,
        )

        # Should return all tracks on first run
        assert len(result) == 2
        assert result[0].id == "1"
        assert result[1].id == "2"

        # Check logging
        console_logger = service.console_logger
        assert any("No last run time found, processing all 2 tracks" in msg for msg in console_logger.info_messages)  # type: ignore[attr-defined]

    def test_filter_tracks_with_new_tracks(self) -> None:
        """Test filtering with new tracks based on date_added."""
        service = TestIncrementalFilterService.create_service()
        last_run_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

        tracks = [
            DummyTrackData.create(track_id="1", date_added="2023-12-31 12:00:00"),  # Older
            DummyTrackData.create(track_id="2", date_added="2024-01-02 12:00:00"),  # Newer
        ]

        # Mock empty CSV data so no status changes are detected
        mock_load_track_list = MockLoadTrackList([])
        mock_get_full_log_path = MockGetFullLogPath()

        with (
            patch("src.core.tracks.incremental_filter.load_track_list", mock_load_track_list),
            patch("src.core.tracks.incremental_filter.get_full_log_path", mock_get_full_log_path),
        ):
            result = service.filter_tracks_for_incremental_update(
                tracks=tracks,
                last_run_time=last_run_time,
            )

        # Should only return the newer track
        assert len(result) == 1
        assert result[0].id == "2"

    def test_filter_tracks_missing_genre(self) -> None:
        """Test filtering includes tracks with missing or unknown genre."""
        service = TestIncrementalFilterService.create_service()
        last_run_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

        tracks = [
            DummyTrackData.create(track_id="1", genre="Rock", date_added="2023-12-31 12:00:00"),  # Old with genre
            DummyTrackData.create(track_id="2", genre="", date_added="2023-12-31 12:00:00"),  # Old, missing genre
            DummyTrackData.create(track_id="3", genre="Unknown", date_added="2023-12-31 12:00:00"),  # Old, unknown genre
        ]

        # Mock empty CSV data so no status changes are detected
        mock_load_track_list = MockLoadTrackList([])
        mock_get_full_log_path = MockGetFullLogPath()

        with (
            patch("src.core.tracks.incremental_filter.load_track_list", mock_load_track_list),
            patch("src.core.tracks.incremental_filter.get_full_log_path", mock_get_full_log_path),
        ):
            result = service.filter_tracks_for_incremental_update(
                tracks=tracks,
                last_run_time=last_run_time,
            )

        # Should return tracks 2 and 3 (missing/unknown genre)
        assert len(result) == 2
        track_ids = {track.id for track in result}
        assert track_ids == {"2", "3"}

    def test_filter_tracks_status_changes(self) -> None:
        """Test filtering includes tracks with status changes."""
        service = TestIncrementalFilterService.create_service()
        last_run_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

        # Mock CSV data showing old status
        old_tracks = [
            DummyTrackData.create(
                track_id="1", track_status="prerelease", genre="Jazz",
                date_added="2023-12-31 12:00:00", last_modified="2024-01-01 12:00:00"
            ),  # Status changed
            DummyTrackData.create(
                track_id="2", track_status="subscription", genre="Blues",
                date_added="2023-12-31 12:00:00", last_modified="2024-01-01 12:00:00"
            ),  # Status unchanged
        ]

        # Current tracks with new status
        tracks = [
            DummyTrackData.create(track_id="1", date_added="2023-12-31 12:00:00", track_status="subscription", genre="Jazz"),  # Status changed
            DummyTrackData.create(track_id="2", date_added="2023-12-31 12:00:00", track_status="subscription", genre="Blues"),  # Status unchanged
        ]

        mock_load_track_list = MockLoadTrackList(old_tracks)
        mock_get_full_log_path = MockGetFullLogPath()

        with (
            patch("src.core.tracks.incremental_filter.load_track_list", mock_load_track_list),
            patch("src.core.tracks.incremental_filter.get_full_log_path", mock_get_full_log_path),
        ):
            result = service.filter_tracks_for_incremental_update(
                tracks=tracks,
                last_run_time=last_run_time,
            )

        # Should return track 1 (status changed from prerelease to subscription)
        assert len(result) == 1
        assert result[0].id == "1"

        # Verify mocks were called
        assert mock_load_track_list.load_called
        assert mock_get_full_log_path.get_called

    def test_filter_tracks_combined_criteria(self) -> None:
        """Test filtering with multiple criteria combined."""
        service = TestIncrementalFilterService.create_service()
        last_run_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

        old_tracks = [
            DummyTrackData.create(track_id="1", track_status="prerelease"),  # Status changed
        ]

        tracks = [
            DummyTrackData.create(track_id="1", date_added="2023-12-31 12:00:00", track_status="subscription"),  # Old, status changed
            DummyTrackData.create(track_id="2", date_added="2024-01-02 12:00:00", genre="Rock"),  # New
            DummyTrackData.create(track_id="3", date_added="2023-12-31 12:00:00", genre=""),  # Old, missing genre
            DummyTrackData.create(track_id="4", date_added="2023-12-31 12:00:00", genre="Pop"),  # Old, has genre
        ]

        mock_load_track_list = MockLoadTrackList(old_tracks)
        mock_get_full_log_path = MockGetFullLogPath()

        with (
            patch("src.core.tracks.incremental_filter.load_track_list", mock_load_track_list),
            patch("src.core.tracks.incremental_filter.get_full_log_path", mock_get_full_log_path),
        ):
            result = service.filter_tracks_for_incremental_update(
                tracks=tracks,
                last_run_time=last_run_time,
            )

        # Should return tracks 1 (status changed), 2 (new), and 3 (missing genre)
        assert len(result) == 3
        track_ids = {track.id for track in result}
        assert track_ids == {"1", "2", "3"}

        # Should deduplicate (track shouldn't appear twice even if it matches multiple criteria)
        # Verify no duplicates by checking length matches unique IDs
        assert len(result) == len({track.id for track in result})



    def test_parse_date_added_valid(self) -> None:
        """Test parsing valid date_added values."""
        # Test with proper TrackDict format
        track = DummyTrackData.create(date_added="2024-01-01 12:00:00")

        result = IncrementalFilterService._parse_date_added(track)  # noqa: SLF001

        expected = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        assert result == expected

    def test_parse_date_added_invalid(self) -> None:
        """Test parsing invalid date_added formats."""
        # Test various invalid date formats
        for invalid_date in ["invalid-date", None, ""]:
            self._test_invalid_date_parsing(invalid_date)

    def _test_invalid_date_parsing(self, date_added: str | None) -> None:
        """Helper to test invalid date parsing."""
        track_invalid = DummyTrackData.create(date_added=date_added)
        result = IncrementalFilterService._parse_date_added(track_invalid)  # noqa: SLF001
        assert result is None

    def test_is_missing_or_unknown_genre(self) -> None:
        """Test genre validation logic."""
        # Missing genre (empty string)
        track_empty = DummyTrackData.create(genre="")
        assert IncrementalFilterService._is_missing_or_unknown_genre(track_empty) is True  # noqa: SLF001

        # Unknown genre
        track_unknown = DummyTrackData.create(genre="Unknown")
        assert IncrementalFilterService._is_missing_or_unknown_genre(track_unknown) is True  # noqa: SLF001

        # Case insensitive unknown
        track_unknown_case = DummyTrackData.create(genre="UNKNOWN")
        assert IncrementalFilterService._is_missing_or_unknown_genre(track_unknown_case) is True  # noqa: SLF001

        # Whitespace only
        track_whitespace = DummyTrackData.create(genre="   ")
        assert IncrementalFilterService._is_missing_or_unknown_genre(track_whitespace) is True  # noqa: SLF001

        # Valid genre
        track_valid = DummyTrackData.create(genre="Rock")
        assert IncrementalFilterService._is_missing_or_unknown_genre(track_valid) is False  # noqa: SLF001

        # None value (if genre field can be None)
        track_none = DummyTrackData.create(genre=None)
        assert IncrementalFilterService._is_missing_or_unknown_genre(track_none) is True  # noqa: SLF001

    def test_get_dry_run_actions(self) -> None:
        """Test dry run actions tracking."""
        service = TestIncrementalFilterService.create_service()

        # Initially should be empty
        actions = service.get_dry_run_actions()
        assert not actions

        # This method primarily returns the _dry_run_actions list
        # The actual population happens in the base processor
        # For this test, we just verify the method exists and returns a list
