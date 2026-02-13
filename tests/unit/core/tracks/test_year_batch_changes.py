"""Tests for YearBatchProcessor year change tracking functionality.

Tests the year_before_mgu population and conflict detection added in Issue #85.
- year_before_mgu: original year before first update (preserved, set once)
- year_set_by_mgu: year after last update (updated each time)
- Conflict detection: when user manually changes year in Music.app
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from core.tracks.track_updater import TrackUpdater
from tests.unit.core.tracks.conftest import create_test_track as _create_test_track
from tests.unit.core.tracks.conftest import create_year_batch_processor as _create_year_batch_processor

if TYPE_CHECKING:
    from core.models.track_models import ChangeLogEntry
    from core.models.types import TrackDict
    from core.tracks.year_batch import YearBatchProcessor


def create_test_track(
    track_id: str,
    *,
    name: str = "Track",
    artist: str = "Artist",
    album: str = "Album",
    genre: str | None = None,
    year: str | None = None,
    date_added: str | None = None,
    last_modified: str | None = None,
    track_status: str | None = None,
    year_before_mgu: str | None = None,
    year_set_by_mgu: str | None = None,
    release_year: str | None = None,
) -> TrackDict:
    """Create a test track, forwarding to conftest factory."""
    return _create_test_track(
        track_id,
        name=name,
        artist=artist,
        album=album,
        genre=genre,
        year=year,
        date_added=date_added,
        last_modified=last_modified,
        track_status=track_status,
        year_before_mgu=year_before_mgu,
        year_set_by_mgu=year_set_by_mgu,
        release_year=release_year,
    )


def create_year_batch_processor() -> YearBatchProcessor:
    """Create a year batch processor, forwarding to conftest factory."""
    return _create_year_batch_processor()


@pytest.mark.unit
class TestRecordSuccessfulUpdatesOldYear:
    """Tests for _record_successful_updates year_before_mgu population."""

    def test_sets_year_before_mgu_when_not_set(self) -> None:
        """Should set year_before_mgu from current year when not already set."""
        track = create_test_track("1", year="2015")
        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        TrackUpdater.record_successful_updates(
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
        track = create_test_track("1")
        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        TrackUpdater.record_successful_updates(
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
        track = create_test_track("1", year="2018", year_before_mgu="2015", year_set_by_mgu="2018")
        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        TrackUpdater.record_successful_updates(
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
        track = create_test_track("1", year="2018", year_before_mgu="", year_set_by_mgu="2018")
        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        TrackUpdater.record_successful_updates(
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
        track = create_test_track("1", year="2015")
        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        TrackUpdater.record_successful_updates(
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
        track = create_test_track("1", name="Test Track", year="2015")
        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        TrackUpdater.record_successful_updates(
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
        track = create_test_track("1", year="2015")
        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        TrackUpdater.record_successful_updates(
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
            create_test_track("1", year="2015"),
            create_test_track("2", year="2016"),
            create_test_track("3", year="2017"),
        ]
        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        TrackUpdater.record_successful_updates(
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
            create_test_track("1", year="2015"),
            create_test_track("2"),  # No year
            create_test_track("3", year="2017", year_before_mgu="2014"),  # Already has year_before_mgu
        ]
        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        TrackUpdater.record_successful_updates(
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

    @staticmethod
    def _run_detection(
        caplog: pytest.LogCaptureFixture,
        tracks: list[TrackDict],
    ) -> None:
        """Run _detect_user_year_changes with logging enabled."""
        processor = create_year_batch_processor()
        with caplog.at_level(logging.INFO):
            processor._detect_user_year_changes("Artist", "Album", tracks)

    def test_detects_user_change(self, caplog: pytest.LogCaptureFixture) -> None:
        """Should detect when user manually changed year."""
        tracks = [create_test_track("1", year="2018", year_before_mgu="2015", year_set_by_mgu="2020")]

        self._run_detection(caplog, tracks)

        assert "User manually changed year" in caplog.text
        assert "Artist - Album" in caplog.text
        assert "2015" in caplog.text  # year_before_mgu
        assert "2020" in caplog.text  # year_set_by_mgu (we set)
        assert "2018" in caplog.text  # current year

    def test_no_detection_when_years_match(self, caplog: pytest.LogCaptureFixture) -> None:
        """Should NOT log when year_set_by_mgu matches current year."""
        tracks = [create_test_track("1", year="2020", year_before_mgu="2015", year_set_by_mgu="2020")]

        self._run_detection(caplog, tracks)

        assert "User manually changed year" not in caplog.text

    def test_no_detection_when_year_set_by_mgu_not_set(self, caplog: pytest.LogCaptureFixture) -> None:
        """Should NOT log when year_set_by_mgu is not set."""
        tracks = [create_test_track("1", year="2018", year_before_mgu="2015")]

        self._run_detection(caplog, tracks)

        assert "User manually changed year" not in caplog.text

    def test_no_detection_when_current_year_not_set(self, caplog: pytest.LogCaptureFixture) -> None:
        """Should NOT log when current year is not set."""
        tracks = [create_test_track("1", year_before_mgu="2015", year_set_by_mgu="2020")]

        self._run_detection(caplog, tracks)

        assert "User manually changed year" not in caplog.text

    def test_logs_unknown_when_year_before_mgu_not_set(self, caplog: pytest.LogCaptureFixture) -> None:
        """Should log 'unknown' when year_before_mgu is not set."""
        tracks = [create_test_track("1", year="2018", year_set_by_mgu="2020")]

        self._run_detection(caplog, tracks)

        assert "unknown" in caplog.text

    def test_logs_once_per_album(self, caplog: pytest.LogCaptureFixture) -> None:
        """Should log only once even if multiple tracks have conflicts."""
        tracks = [
            create_test_track("1", year="2018", year_before_mgu="2015", year_set_by_mgu="2020"),
            create_test_track("2", year="2017", year_before_mgu="2014", year_set_by_mgu="2019"),
        ]

        self._run_detection(caplog, tracks)

        # Should only have one log entry
        log_count = caplog.text.count("User manually changed year")
        assert log_count == 1

    def test_empty_tracks_list(self, caplog: pytest.LogCaptureFixture) -> None:
        """Should handle empty tracks list gracefully."""
        tracks: list[TrackDict] = []

        self._run_detection(caplog, tracks)

        assert "User manually changed year" not in caplog.text


@pytest.mark.unit
class TestYearChangeTrackingEndToEnd:
    """End-to-end tests for year change tracking flow."""

    def test_first_update_sets_year_before_mgu(self) -> None:
        """First update should set year_before_mgu from original value."""
        track = create_test_track("1", year="2015")
        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        # First update: 2015 -> 2020
        TrackUpdater.record_successful_updates(
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
        track = create_test_track("1", year="2020", year_before_mgu="2015", year_set_by_mgu="2020")
        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        # Second update: 2020 -> 2021
        TrackUpdater.record_successful_updates(
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
        track = create_test_track("1", year="2020", year_before_mgu="2015", year_set_by_mgu="2020")
        updated_tracks: list[TrackDict] = []
        changes_log: list[ChangeLogEntry] = []

        # Update: 2020 -> 2021
        TrackUpdater.record_successful_updates(
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
