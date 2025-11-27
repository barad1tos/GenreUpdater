"""Unit tests for incremental filter service."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

from src.core.models.track_models import TrackDict
from src.core.tracks.incremental_filter import IncrementalFilterService

if TYPE_CHECKING:
    from collections.abc import Sequence


def _create_mock_logger() -> MagicMock:
    """Create a mock logger with message tracking."""
    mock = MagicMock(spec=logging.Logger)
    mock.info_messages = []
    mock.debug_messages = []

    def track_info(msg: object, *args: object, **_kwargs: Any) -> None:
        """Track info-level log messages for assertion."""
        mock.info_messages.append(str(msg) % args if args else str(msg))

    def track_debug(msg: object, *args: object, **_kwargs: Any) -> None:
        """Track debug-level log messages for assertion."""
        mock.debug_messages.append(str(msg) % args if args else str(msg))

    mock.info.side_effect = track_info
    mock.debug.side_effect = track_debug
    return mock


def _create_track(
    track_id: str = "12345",
    name: str = "Test Track",
    artist: str = "Test Artist",
    album: str = "Test Album",
    genre: str | None = "Rock",
    date_added: str | None = "2024-01-01 12:00:00",
    last_modified: str | None = "2024-01-01 12:00:00",
    track_status: str | None = "subscription",
) -> TrackDict:
    """Create a TrackDict for testing."""
    return TrackDict(
        id=track_id,
        name=name,
        artist=artist,
        album=album,
        genre=genre,
        date_added=date_added,
        year="2024",
        last_modified=last_modified,
        track_status=track_status,
    )


class _MockLoadTrackList:
    """Mock for load_track_list function."""

    def __init__(self, tracks_to_return: Sequence[TrackDict] | None = None) -> None:
        """Initialize with tracks to return."""
        self.tracks_to_return = list(tracks_to_return) if tracks_to_return else []
        self.load_called = False

    def __call__(self, _csv_path: str) -> dict[str, TrackDict]:
        """Return tracks as dict keyed by ID."""
        self.load_called = True
        return {str(t.id): t for t in self.tracks_to_return if t.id}


class _MockGetFullLogPath:
    """Mock for get_full_log_path function."""

    def __init__(self, path: str = "/fake/path/track_list.csv") -> None:
        """Initialize with path to return."""
        self.path = path
        self.get_called = False

    def __call__(self, _config: dict[str, Any], _key: str, _default: str) -> str:
        """Return the configured path."""
        self.get_called = True
        return self.path


class TestIncrementalFilterService:
    """Tests for IncrementalFilterService class."""

    @staticmethod
    def create_service() -> IncrementalFilterService:
        """Create a service instance for testing."""
        console_logger = _create_mock_logger()
        error_logger = _create_mock_logger()
        analytics = MagicMock()
        config: dict[str, Any] = {"csv_output_file": "csv/track_list.csv"}

        return IncrementalFilterService(
            console_logger=console_logger,
            error_logger=error_logger,
            analytics=analytics,
            config=config,
        )

    def test_filter_tracks_no_last_run(self) -> None:
        """Test filtering when no last run time (first run scenario)."""
        service = TestIncrementalFilterService.create_service()
        tracks = [
            _create_track(track_id="1", name="Track 1"),
            _create_track(track_id="2", name="Track 2"),
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
        last_run_time = datetime(2024, 1, 1, 12, tzinfo=UTC)

        tracks = [
            _create_track(track_id="1", date_added="2023-12-31 12:00:00"),  # Older
            _create_track(track_id="2", date_added="2024-01-02 12:00:00"),  # Newer
        ]

        # Mock empty CSV data so no status changes are detected
        mock_load_track_list = _MockLoadTrackList([])
        mock_get_full_log_path = _MockGetFullLogPath()

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
        last_run_time = datetime(2024, 1, 1, 12, tzinfo=UTC)

        tracks = [
            _create_track(track_id="1", date_added="2023-12-31 12:00:00"),  # Old with genre (Rock default)
            _create_track(track_id="2", genre="", date_added="2023-12-31 12:00:00"),  # Old, missing genre
            _create_track(track_id="3", genre="Unknown", date_added="2023-12-31 12:00:00"),  # Old, unknown genre
        ]

        # Mock empty CSV data so no status changes are detected
        mock_load_track_list = _MockLoadTrackList([])
        mock_get_full_log_path = _MockGetFullLogPath()

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
        last_run_time = datetime(2024, 1, 1, 12, tzinfo=UTC)

        # Mock CSV data showing old status
        old_tracks = [
            _create_track(
                track_id="1", track_status="prerelease", genre="Jazz",
                date_added="2023-12-31 12:00:00"
            ),  # Status changed
            _create_track(
                track_id="2", genre="Blues",
                date_added="2023-12-31 12:00:00"
            ),  # Status unchanged (subscription default)
        ]

        # Current tracks with new status
        tracks = [
            _create_track(track_id="1", date_added="2023-12-31 12:00:00", genre="Jazz"),  # Status changed to subscription
            _create_track(track_id="2", date_added="2023-12-31 12:00:00", genre="Blues"),  # Status unchanged
        ]

        mock_load_track_list = _MockLoadTrackList(old_tracks)
        mock_get_full_log_path = _MockGetFullLogPath()

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
        last_run_time = datetime(2024, 1, 1, 12, tzinfo=UTC)

        old_tracks = [
            _create_track(track_id="1", track_status="prerelease"),  # Status changed
        ]

        tracks = [
            _create_track(track_id="1", date_added="2023-12-31 12:00:00"),  # Old, status changed
            _create_track(track_id="2", date_added="2024-01-02 12:00:00"),  # New (Rock default)
            _create_track(track_id="3", date_added="2023-12-31 12:00:00", genre=""),  # Old, missing genre
            _create_track(track_id="4", date_added="2023-12-31 12:00:00", genre="Pop"),  # Old, has genre
        ]

        mock_load_track_list = _MockLoadTrackList(old_tracks)
        mock_get_full_log_path = _MockGetFullLogPath()

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
        # Test with proper TrackDict format (uses default date_added)
        track = _create_track()

        result = IncrementalFilterService._parse_date_added(track)

        expected = datetime(2024, 1, 1, 12, tzinfo=UTC)
        assert result == expected

    def test_parse_date_added_invalid(self) -> None:
        """Test parsing invalid date_added formats."""
        # Test various invalid date formats
        for invalid_date in ["invalid-date", None, ""]:
            self._test_invalid_date_parsing(invalid_date)

    @staticmethod
    def _test_invalid_date_parsing(date_added: str | None) -> None:
        """Helper to test invalid date parsing."""
        track_invalid = _create_track(date_added=date_added)
        result = IncrementalFilterService._parse_date_added(track_invalid)
        assert result is None

    def test_is_missing_or_unknown_genre(self) -> None:
        """Test genre validation logic."""
        # Missing genre (empty string)
        track_empty = _create_track(genre="")
        assert IncrementalFilterService._is_missing_or_unknown_genre(track_empty) is True

        # Unknown genre
        track_unknown = _create_track(genre="Unknown")
        assert IncrementalFilterService._is_missing_or_unknown_genre(track_unknown) is True

        # Case insensitive unknown
        track_unknown_case = _create_track(genre="UNKNOWN")
        assert IncrementalFilterService._is_missing_or_unknown_genre(track_unknown_case) is True

        # Whitespace only
        track_whitespace = _create_track(genre="   ")
        assert IncrementalFilterService._is_missing_or_unknown_genre(track_whitespace) is True

        # Valid genre (not using default to be explicit)
        track_valid = _create_track(genre="Jazz")
        assert IncrementalFilterService._is_missing_or_unknown_genre(track_valid) is False

        # None value (if genre field can be None)
        track_none = _create_track(genre=None)
        assert IncrementalFilterService._is_missing_or_unknown_genre(track_none) is True

    def test_get_dry_run_actions(self) -> None:
        """Test dry run actions tracking."""
        service = TestIncrementalFilterService.create_service()

        # Initially should be empty
        actions = service.get_dry_run_actions()
        assert not actions

        # This method primarily returns the _dry_run_actions list
        # The actual population happens in the base processor
        # For this test, we just verify the method exists and returns a list
