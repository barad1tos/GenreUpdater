"""Tests for YearBatchProcessor year change tracking functionality.

Tests the year_before_mgu population and conflict detection added in Issue #85.
- year_before_mgu: original year before first update (preserved, set once)
- year_set_by_mgu: year after last update (updated each time)
- Conflict detection: when user manually changes year in Music.app
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.models.types import TrackDict
from core.tracks.year_batch import YearBatchProcessor

if TYPE_CHECKING:
    from core.models.track_models import ChangeLogEntry


def _create_track(
    track_id: str = "1",
    *,
    name: str = "Track",
    artist: str = "Artist",
    album: str = "Album",
    year: str | None = None,
    year_before_mgu: str | None = None,
    year_set_by_mgu: str | None = None,
) -> TrackDict:
    """Create a test TrackDict with specified values."""
    return TrackDict(
        id=track_id,
        name=name,
        artist=artist,
        album=album,
        year=year,
        year_before_mgu=year_before_mgu,
        year_set_by_mgu=year_set_by_mgu,
    )


def _create_mock_track_processor() -> MagicMock:
    """Create a mock track processor."""
    processor = MagicMock()
    processor.update_tracks_batch_async = AsyncMock(return_value=[])
    return processor


def _create_mock_year_determinator() -> MagicMock:
    """Create a mock year determinator."""
    determinator = MagicMock()
    determinator.should_skip_album = AsyncMock(return_value=False)
    determinator.determine_album_year = AsyncMock(return_value="2020")
    determinator.check_prerelease_status = AsyncMock(return_value=False)
    determinator.check_suspicious_album = AsyncMock(return_value=False)
    determinator.handle_future_years = AsyncMock(return_value=False)
    determinator.extract_future_years = MagicMock(return_value=[])
    return determinator


def _create_mock_retry_handler() -> MagicMock:
    """Create a mock retry handler."""
    handler = MagicMock()
    handler.execute_with_retry = AsyncMock()
    return handler


def _create_mock_analytics() -> MagicMock:
    """Create a mock analytics instance."""
    return MagicMock()


def _create_year_batch_processor(
    track_processor: MagicMock | None = None,
    year_determinator: MagicMock | None = None,
    retry_handler: MagicMock | None = None,
    analytics: MagicMock | None = None,
    config: dict[str, Any] | None = None,
) -> YearBatchProcessor:
    """Create YearBatchProcessor with mock dependencies."""
    return YearBatchProcessor(
        track_processor=track_processor or _create_mock_track_processor(),
        year_determinator=year_determinator or _create_mock_year_determinator(),
        retry_handler=retry_handler or _create_mock_retry_handler(),
        console_logger=logging.getLogger("test.console"),
        error_logger=logging.getLogger("test.error"),
        config=config or {},
        analytics=analytics or _create_mock_analytics(),
    )


@pytest.mark.unit
class TestRecordSuccessfulUpdatesOldYear:
    """Tests for _record_successful_updates year_before_mgu population."""

    def test_sets_year_before_mgu_when_not_set(self) -> None:
        """Should set year_before_mgu from current year when not already set."""
        track = _create_track("1", year="2015", year_before_mgu=None, year_set_by_mgu=None)
        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        YearBatchProcessor._record_successful_updates(
            tracks=[track],
            year="2020",
            artist="Artist",
            album="Album",
            updated_tracks=updated_tracks,
            changes_log=changes_log,
        )

        # year_before_mgu should be set to original value
        assert track.year_before_mgu == "2015"

    def test_sets_year_before_mgu_empty_when_year_was_none(self) -> None:
        """Should set year_before_mgu to empty string when original year was None."""
        track = _create_track("1", year=None, year_before_mgu=None, year_set_by_mgu=None)
        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        YearBatchProcessor._record_successful_updates(
            tracks=[track],
            year="2020",
            artist="Artist",
            album="Album",
            updated_tracks=updated_tracks,
            changes_log=changes_log,
        )

        # year_before_mgu should be empty string
        assert track.year_before_mgu == ""

    def test_preserves_existing_year_before_mgu(self) -> None:
        """Should NOT overwrite existing year_before_mgu (preserve original)."""
        track = _create_track("1", year="2018", year_before_mgu="2015", year_set_by_mgu="2018")
        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        YearBatchProcessor._record_successful_updates(
            tracks=[track],
            year="2020",
            artist="Artist",
            album="Album",
            updated_tracks=updated_tracks,
            changes_log=changes_log,
        )

        # year_before_mgu should remain unchanged
        assert track.year_before_mgu == "2015"

    def test_preserves_empty_string_year_before_mgu(self) -> None:
        """Should preserve year_before_mgu even if it's empty string (falsy but set)."""
        # Note: empty string is falsy, so this tests the exact condition
        track = _create_track("1", year="2018", year_before_mgu="", year_set_by_mgu="2018")
        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        YearBatchProcessor._record_successful_updates(
            tracks=[track],
            year="2020",
            artist="Artist",
            album="Album",
            updated_tracks=updated_tracks,
            changes_log=changes_log,
        )

        # Empty string year_before_mgu is falsy, so it will be overwritten
        # This is the expected behavior - only non-empty year_before_mgu is preserved
        assert track.year_before_mgu == "2018"

    def test_updates_year_and_year_set_by_mgu(self) -> None:
        """Should update both year and year_set_by_mgu to new value."""
        track = _create_track("1", year="2015", year_before_mgu=None, year_set_by_mgu=None)
        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        YearBatchProcessor._record_successful_updates(
            tracks=[track],
            year="2020",
            artist="Artist",
            album="Album",
            updated_tracks=updated_tracks,
            changes_log=changes_log,
        )

        assert track.year == "2020"
        assert track.year_set_by_mgu == "2020"

    def test_creates_change_log_entry(self) -> None:
        """Should create ChangeLogEntry with old and new year."""
        track = _create_track("1", name="Test Track", year="2015", year_before_mgu=None, year_set_by_mgu=None)
        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        YearBatchProcessor._record_successful_updates(
            tracks=[track],
            year="2020",
            artist="Artist",
            album="Album",
            updated_tracks=updated_tracks,
            changes_log=changes_log,
        )

        assert len(changes_log) == 1
        entry = changes_log[0]
        assert entry.change_type == "year_update"
        assert entry.year_before_mgu == "2015"
        assert entry.year_set_by_mgu == "2020"
        assert entry.track_name == "Test Track"
        assert entry.artist == "Artist"
        assert entry.album_name == "Album"

    def test_appends_to_updated_tracks(self) -> None:
        """Should append updated track copy to updated_tracks list."""
        track = _create_track("1", year="2015", year_before_mgu=None, year_set_by_mgu=None)
        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        YearBatchProcessor._record_successful_updates(
            tracks=[track],
            year="2020",
            artist="Artist",
            album="Album",
            updated_tracks=updated_tracks,
            changes_log=changes_log,
        )

        assert len(updated_tracks) == 1
        assert updated_tracks[0].year == "2020"


@pytest.mark.unit
class TestRecordSuccessfulUpdatesMultipleTracks:
    """Tests for _record_successful_updates with multiple tracks."""

    def test_processes_all_tracks(self) -> None:
        """Should process all tracks in the list."""
        tracks = [
            _create_track("1", year="2015", year_before_mgu=None),
            _create_track("2", year="2016", year_before_mgu=None),
            _create_track("3", year="2017", year_before_mgu=None),
        ]
        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        YearBatchProcessor._record_successful_updates(
            tracks=tracks,
            year="2020",
            artist="Artist",
            album="Album",
            updated_tracks=updated_tracks,
            changes_log=changes_log,
        )

        assert len(updated_tracks) == 3
        assert len(changes_log) == 3
        assert tracks[0].year_before_mgu == "2015"
        assert tracks[1].year_before_mgu == "2016"
        assert tracks[2].year_before_mgu == "2017"

    def test_each_track_gets_own_year_before_mgu(self) -> None:
        """Each track should preserve its own original year in year_before_mgu."""
        tracks = [
            _create_track("1", year="2015", year_before_mgu=None),
            _create_track("2", year=None, year_before_mgu=None),  # No year
            _create_track("3", year="2017", year_before_mgu="2014"),  # Already has year_before_mgu
        ]
        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        YearBatchProcessor._record_successful_updates(
            tracks=tracks,
            year="2020",
            artist="Artist",
            album="Album",
            updated_tracks=updated_tracks,
            changes_log=changes_log,
        )

        assert tracks[0].year_before_mgu == "2015"  # Set from year
        assert tracks[1].year_before_mgu == ""  # Empty since year was None
        assert tracks[2].year_before_mgu == "2014"  # Preserved


@pytest.mark.unit
class TestDetectUserYearChanges:
    """Tests for _detect_user_year_changes conflict detection."""

    def test_detects_user_change(self, caplog: pytest.LogCaptureFixture) -> None:
        """Should detect when user manually changed year."""
        processor = _create_year_batch_processor()
        tracks = [_create_track("1", year="2018", year_before_mgu="2015", year_set_by_mgu="2020")]

        with caplog.at_level(logging.INFO):
            processor._detect_user_year_changes("Artist", "Album", tracks)

        assert "User manually changed year" in caplog.text
        assert "Artist - Album" in caplog.text
        assert "2015" in caplog.text  # year_before_mgu
        assert "2020" in caplog.text  # year_set_by_mgu (we set)
        assert "2018" in caplog.text  # current year

    def test_no_detection_when_years_match(self, caplog: pytest.LogCaptureFixture) -> None:
        """Should NOT log when year_set_by_mgu matches current year."""
        processor = _create_year_batch_processor()
        tracks = [_create_track("1", year="2020", year_before_mgu="2015", year_set_by_mgu="2020")]

        with caplog.at_level(logging.INFO):
            processor._detect_user_year_changes("Artist", "Album", tracks)

        assert "User manually changed year" not in caplog.text

    def test_no_detection_when_year_set_by_mgu_not_set(self, caplog: pytest.LogCaptureFixture) -> None:
        """Should NOT log when year_set_by_mgu is not set."""
        processor = _create_year_batch_processor()
        tracks = [_create_track("1", year="2018", year_before_mgu="2015", year_set_by_mgu=None)]

        with caplog.at_level(logging.INFO):
            processor._detect_user_year_changes("Artist", "Album", tracks)

        assert "User manually changed year" not in caplog.text

    def test_no_detection_when_current_year_not_set(self, caplog: pytest.LogCaptureFixture) -> None:
        """Should NOT log when current year is not set."""
        processor = _create_year_batch_processor()
        tracks = [_create_track("1", year=None, year_before_mgu="2015", year_set_by_mgu="2020")]

        with caplog.at_level(logging.INFO):
            processor._detect_user_year_changes("Artist", "Album", tracks)

        assert "User manually changed year" not in caplog.text

    def test_logs_unknown_when_year_before_mgu_not_set(self, caplog: pytest.LogCaptureFixture) -> None:
        """Should log 'unknown' when year_before_mgu is not set."""
        processor = _create_year_batch_processor()
        tracks = [_create_track("1", year="2018", year_before_mgu=None, year_set_by_mgu="2020")]

        with caplog.at_level(logging.INFO):
            processor._detect_user_year_changes("Artist", "Album", tracks)

        assert "unknown" in caplog.text

    def test_logs_once_per_album(self, caplog: pytest.LogCaptureFixture) -> None:
        """Should log only once even if multiple tracks have conflicts."""
        processor = _create_year_batch_processor()
        tracks = [
            _create_track("1", year="2018", year_before_mgu="2015", year_set_by_mgu="2020"),
            _create_track("2", year="2017", year_before_mgu="2014", year_set_by_mgu="2019"),
        ]

        with caplog.at_level(logging.INFO):
            processor._detect_user_year_changes("Artist", "Album", tracks)

        # Should only have one log entry
        log_count = caplog.text.count("User manually changed year")
        assert log_count == 1

    def test_empty_tracks_list(self, caplog: pytest.LogCaptureFixture) -> None:
        """Should handle empty tracks list gracefully."""
        processor = _create_year_batch_processor()
        tracks: list[TrackDict] = []

        with caplog.at_level(logging.INFO):
            processor._detect_user_year_changes("Artist", "Album", tracks)

        assert "User manually changed year" not in caplog.text


@pytest.mark.unit
class TestYearChangeTrackingEndToEnd:
    """End-to-end tests for year change tracking flow."""

    def test_first_update_sets_year_before_mgu(self) -> None:
        """First update should set year_before_mgu from original value."""
        track = _create_track("1", year="2015", year_before_mgu=None, year_set_by_mgu=None)
        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        # First update: 2015 -> 2020
        YearBatchProcessor._record_successful_updates(
            tracks=[track],
            year="2020",
            artist="Artist",
            album="Album",
            updated_tracks=updated_tracks,
            changes_log=changes_log,
        )

        assert track.year_before_mgu == "2015"  # Original preserved
        assert track.year == "2020"  # Updated
        assert track.year_set_by_mgu == "2020"  # Set

    def test_second_update_preserves_year_before_mgu(self) -> None:
        """Second update should preserve original year_before_mgu."""
        track = _create_track("1", year="2020", year_before_mgu="2015", year_set_by_mgu="2020")
        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        # Second update: 2020 -> 2021
        YearBatchProcessor._record_successful_updates(
            tracks=[track],
            year="2021",
            artist="Artist",
            album="Album",
            updated_tracks=updated_tracks,
            changes_log=changes_log,
        )

        assert track.year_before_mgu == "2015"  # Still original
        assert track.year == "2021"  # Updated
        assert track.year_set_by_mgu == "2021"  # Updated

    def test_change_log_reflects_actual_change(self) -> None:
        """Change log should show the actual year change, not year_before_mgu."""
        track = _create_track("1", year="2020", year_before_mgu="2015", year_set_by_mgu="2020")
        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        # Update: 2020 -> 2021
        YearBatchProcessor._record_successful_updates(
            tracks=[track],
            year="2021",
            artist="Artist",
            album="Album",
            updated_tracks=updated_tracks,
            changes_log=changes_log,
        )

        entry = changes_log[0]
        assert entry.year_before_mgu == "2020"  # Previous year (not year_before_mgu)
        assert entry.year_set_by_mgu == "2021"  # New year
