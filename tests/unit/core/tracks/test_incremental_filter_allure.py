"""Enhanced incremental filter tests with Allure reporting."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import allure
import pytest

from core.models.track_models import TrackDict
from core.tracks.incremental_filter import IncrementalFilterService
from core.tracks.track_utils import parse_track_date_added
from core.tracks.track_delta import TrackDelta
from metrics import Analytics
from metrics.analytics import LoggerContainer

if TYPE_CHECKING:
    from collections.abc import Sequence


class _MockLogger(logging.Logger):
    """Mock logger for testing with message tracking."""

    def __init__(self, name: str = "mock") -> None:
        """Initialize mock logger with message collections."""
        super().__init__(name)
        self.level = 0
        self.handlers: list[Any] = []
        self.parent = None
        self.propagate = True
        self.info_messages: list[str] = []
        self.warning_messages: list[str] = []
        self.error_messages: list[str] = []
        self.debug_messages: list[str] = []

    @staticmethod
    def _format_message(message: str, *args: object) -> str:
        """Format message with args for logging."""
        if args:
            try:
                return message % args
            except (TypeError, ValueError):
                return f"{message} {args}"
        return message

    def info(self, msg: object, *args: object, **_kwargs: Any) -> None:
        """Track info-level log messages."""
        self.info_messages.append(self._format_message(str(msg), *args))

    def warning(self, msg: object, *args: object, **_kwargs: Any) -> None:
        """Track warning-level log messages."""
        self.warning_messages.append(self._format_message(str(msg), *args))

    def error(self, msg: object, *args: object, **_kwargs: Any) -> None:
        """Track error-level log messages."""
        self.error_messages.append(self._format_message(str(msg), *args))

    def debug(self, msg: object, *args: object, **_kwargs: Any) -> None:
        """Track debug-level log messages."""
        self.debug_messages.append(self._format_message(str(msg), *args))


class _MockAnalytics(Analytics):
    """Mock analytics for testing."""

    def __init__(self) -> None:
        """Initialize mock analytics with mock loggers."""
        mock_console = _MockLogger("console")
        mock_error = _MockLogger("error")
        mock_analytics = _MockLogger("analytics")
        loggers = LoggerContainer(mock_console, mock_error, mock_analytics)
        super().__init__(config={}, loggers=loggers, max_events=1000)
        self.events: list[dict[str, Any]] = []

    def track_event(self, event: dict[str, Any]) -> None:
        """Track event for testing verification."""
        self.events.append(event)


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


@allure.epic("Music Genre Updater")
@allure.feature("Incremental Filtering")
class TestIncrementalFilterServiceAllure:
    """Enhanced tests for IncrementalFilterService with Allure reporting."""

    @staticmethod
    def create_service(
        config: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> IncrementalFilterService:
        """Create an IncrementalFilterService instance for testing."""
        test_config = config or {"logs_base_dir": "/tmp/test_logs", "csv_output_file": "csv/track_list.csv", "logs": {"directory": "/fake/logs"}}

        return IncrementalFilterService(
            console_logger=_MockLogger(),  # type: ignore[arg-type]
            error_logger=_MockLogger(),  # type: ignore[arg-type]
            analytics=_MockAnalytics(),  # type: ignore[arg-type]
            config=test_config,
            dry_run=dry_run,
        )

    @staticmethod
    def create_dummy_track(
        track_id: str = "12345",
        genre: str = "Rock",
        date_added: str | None = None,
        track_status: str = "subscription",
    ) -> TrackDict:
        """Create a dummy track for testing."""
        return TrackDict(
            id=track_id,
            name="Test Track",
            artist="Test Artist",
            album="Test Album",
            genre=genre,
            date_added=date_added or "2024-01-01 10:00:00",
            track_status=track_status,
            year=None,
            kind="music",  # type: ignore[call-arg]  # extra="allow" config should handle this
            sort_album="Test Album",  # type: ignore[call-arg]
            album_artist="Test Artist",  # type: ignore[call-arg]
            track_number=1,  # type: ignore[call-arg]
            last_modified="2024-01-01 10:00:00",
        )

    @allure.story("First Run Behavior")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should process all tracks when no last run time exists")
    @allure.description("Test that all tracks are processed when this is the first run")
    def test_filter_tracks_no_last_run(self) -> None:
        """Test filtering behavior when no last run time is available."""
        with allure.step("Setup service and test data"):
            service = self.create_service()
            tracks = [
                self.create_dummy_track("1"),
                self.create_dummy_track("2", "Pop"),
                self.create_dummy_track("3", ""),  # Missing genre
            ]

            allure.attach(
                json.dumps(
                    [
                        {
                            "id": track.id,
                            "genre": track.genre,
                            "date_added": track.date_added,
                        }
                        for track in tracks
                    ],
                    indent=2,
                ),
                "Input Tracks",
                allure.attachment_type.JSON,
            )

        with allure.step("Filter tracks with no last run time"):
            result = service.filter_tracks_for_incremental_update(
                tracks=tracks,
                last_run_time=None,
            )

        with allure.step("Verify all tracks are included"):
            assert len(result) == 3
            assert result == tracks

            allure.attach(str(len(result)), "Filtered Track Count", allure.attachment_type.TEXT)
            allure.attach("All tracks processed on first run", "Filter Result", allure.attachment_type.TEXT)

    @allure.story("New Track Detection")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.title("Should detect tracks added after last run")
    @allure.description("Test filtering of tracks based on date_added compared to last run time")
    def test_filter_tracks_with_new_tracks(self) -> None:
        """Test filtering for tracks added after the last run."""
        with allure.step("Setup timeline and test data"):
            last_run = datetime(2024, 1, 10, 12, tzinfo=UTC)
            before_run = last_run - timedelta(days=1)
            after_run = last_run + timedelta(days=1)

            tracks = [
                self.create_dummy_track("1", date_added=before_run.strftime("%Y-%m-%d %H:%M:%S")),  # Before run
                self.create_dummy_track("2", "Pop", after_run.strftime("%Y-%m-%d %H:%M:%S")),  # After run
                self.create_dummy_track("3", "", before_run.strftime("%Y-%m-%d %H:%M:%S")),  # Missing genre, old date
            ]

            allure.attach(last_run.strftime("%Y-%m-%d %H:%M:%S"), "Last Run Time", allure.attachment_type.TEXT)
            allure.attach(
                json.dumps(
                    [
                        {
                            "id": track.id,
                            "genre": track.genre,
                            "date_added": track.date_added,
                            "is_new": track.date_added > last_run.strftime("%Y-%m-%d %H:%M:%S") if track.date_added else False,
                            "has_missing_genre": not track.genre.strip() if track.genre else True,
                        }
                        for track in tracks
                    ],
                    indent=2,
                ),
                "Track Analysis",
                allure.attachment_type.JSON,
            )

        with allure.step("Filter tracks for incremental update"):
            service = self.create_service()

            # Mock empty CSV data so no status changes are detected
            mock_load_track_list = _MockLoadTrackList([])
            mock_get_full_log_path = _MockGetFullLogPath()

            with (
                patch("core.tracks.incremental_filter.load_track_list", mock_load_track_list),
                patch("core.tracks.incremental_filter.get_full_log_path", mock_get_full_log_path),
            ):
                result = service.filter_tracks_for_incremental_update(
                    tracks=tracks,
                    last_run_time=last_run,
                )

        with allure.step("Verify correct tracks are included"):
            # Should include: track 2 (new) + track 3 (missing genre)
            assert len(result) == 2

            result_ids = {track.id for track in result}
            assert "2" in result_ids  # New track
            assert "3" in result_ids  # Missing genre
            assert "1" not in result_ids  # Old track with genre

            allure.attach(str(result_ids), "Filtered Track IDs", allure.attachment_type.TEXT)
            allure.attach("New tracks and missing genre tracks included", "Filter Logic", allure.attachment_type.TEXT)

    @allure.story("Missing Genre Detection")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should include tracks with missing or unknown genres")
    @allure.description("Test that tracks without proper genres are always included for processing")
    @pytest.mark.parametrize(
        ("genre", "should_include"),
        [
            ("", True),  # Empty genre
            ("  ", True),  # Whitespace only
            ("Unknown", True),  # Unknown genre
            ("UNKNOWN", True),  # Case insensitive
            ("Rock", False),  # Valid genre
            ("Pop", False),  # Valid genre
        ],
    )
    def test_filter_tracks_missing_genre(self, genre: str, should_include: bool) -> None:
        """Test filtering behavior for tracks with missing or unknown genres."""
        with allure.step(f"Testing genre filtering for: '{genre}'"):
            service = self.create_service()
            last_run = datetime(2024, 1, 10, 12, tzinfo=UTC)
            before_run = last_run - timedelta(days=1)

            # Create track with old date but specific genre
            track = self.create_dummy_track("test_track", genre, before_run.strftime("%Y-%m-%d %H:%M:%S"))

            allure.attach(genre, "Test Genre", allure.attachment_type.TEXT)
            allure.attach(str(should_include), "Expected Include", allure.attachment_type.TEXT)

        with allure.step("Filter tracks"):
            result = service.filter_tracks_for_incremental_update(
                tracks=[track],
                last_run_time=last_run,
            )

        with allure.step("Verify filtering result"):
            if should_include:
                assert len(result) == 1
                assert result[0].id == "test_track"
                allure.attach("✅ Track included (missing/unknown genre)", "Result", allure.attachment_type.TEXT)
            else:
                assert len(result) == 0
                allure.attach("❌ Track excluded (valid genre)", "Result", allure.attachment_type.TEXT)

    @allure.story("Status Change Detection")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should detect tracks with changed status")
    @allure.description("Test detection of tracks whose status changed since last run")
    @patch("core.tracks.incremental_filter.get_full_log_path")
    @patch("core.tracks.incremental_filter.load_track_list")
    @patch("core.tracks.incremental_filter.compute_track_delta")
    def test_filter_tracks_status_changes(
        self,
        mock_compute_delta: MagicMock,
        mock_load_track_list: MagicMock,
        mock_get_path: MagicMock,
    ) -> None:
        """Test filtering for tracks with status changes."""
        with allure.step("Setup mock dependencies"):
            # Setup mocks
            mock_get_path.return_value = "/fake/path/track_list.csv"
            mock_load_track_list.return_value = {"123": self.create_dummy_track("123")}

            # Mock delta with status changes
            mock_delta = TrackDelta(
                new_ids=[],
                updated_ids=["123"],  # Track with status change
                removed_ids=[],
            )
            mock_compute_delta.return_value = mock_delta

        with allure.step("Setup service and test data"):
            service = self.create_service()
            last_run = datetime(2024, 1, 10, 12, tzinfo=UTC)
            before_run = last_run - timedelta(days=1)

            tracks = [
                self.create_dummy_track("123", date_added=before_run.strftime("%Y-%m-%d %H:%M:%S")),
                self.create_dummy_track("456", "Pop", before_run.strftime("%Y-%m-%d %H:%M:%S")),
            ]

            allure.attach(
                json.dumps(
                    [
                        {
                            "track_id": track.id,
                            "track_status": track.track_status,
                        }
                        for track in tracks
                    ],
                    indent=2,
                ),
                "Current Tracks",
                allure.attachment_type.JSON,
            )

        with allure.step("Filter tracks with status changes"):
            result = service.filter_tracks_for_incremental_update(
                tracks=tracks,
                last_run_time=last_run,
            )

        with allure.step("Verify status change detection"):
            # Should include track 123 (status changed)
            assert len(result) == 1
            assert result[0].id == "123"

            # Verify mocks were called correctly
            mock_get_path.assert_called_once()
            mock_load_track_list.assert_called_once()
            mock_compute_delta.assert_called_once()

            allure.attach("Track 123 included due to status change", "Status Change Result", allure.attachment_type.TEXT)

    @allure.story("Combined Filtering Criteria")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should combine multiple filtering criteria without duplicates")
    @allure.description("Test that tracks meeting multiple criteria are included only once")
    def test_filter_tracks_combined_criteria(self) -> None:
        """Test filtering with multiple criteria and deduplication."""
        with allure.step("Setup complex test scenario"):
            service = self.create_service()
            last_run = datetime(2024, 1, 10, 12, tzinfo=UTC)
            after_run = last_run + timedelta(days=1)

            # Track that meets multiple criteria
            track = self.create_dummy_track(
                "multi_criteria",
                "",  # Missing genre
                after_run.strftime("%Y-%m-%d %H:%M:%S"),  # New track
            )

            allure.attach("Track meets both: new + missing genre", "Test Scenario", allure.attachment_type.TEXT)

        with allure.step("Filter tracks"):
            result = service.filter_tracks_for_incremental_update(
                tracks=[track],
                last_run_time=last_run,
            )

        with allure.step("Verify no duplicates"):
            # Should be included only once despite meeting multiple criteria
            assert len(result) == 1
            assert result[0].id == "multi_criteria"

            allure.attach("1", "Result Count", allure.attachment_type.TEXT)
            allure.attach("✅ No duplicates despite multiple criteria", "Deduplication", allure.attachment_type.TEXT)

    @allure.story("Status Change Integration")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should successfully find status changed tracks")
    @allure.description("Test successful status change detection with proper CSV loading")
    @patch("core.tracks.incremental_filter.get_full_log_path")
    @patch("core.tracks.incremental_filter.load_track_list")
    @patch("core.tracks.incremental_filter.compute_track_delta")
    def test_find_status_changed_tracks_success(
        self,
        mock_compute_delta: MagicMock,
        mock_load_track_list: MagicMock,
        mock_get_path: MagicMock,
    ) -> None:
        """Test successful status change detection."""
        with allure.step("Setup successful status change scenario"):
            service = self.create_service()

            # Setup mocks for successful operation
            mock_get_path.return_value = "/fake/path/track_list.csv"
            mock_load_track_list.return_value = {"123": self.create_dummy_track("123")}

            mock_delta = TrackDelta(
                new_ids=[],
                updated_ids=["123", "456"],
                removed_ids=[],
            )
            mock_compute_delta.return_value = mock_delta

            tracks = [
                self.create_dummy_track("123"),
                self.create_dummy_track("456", "Pop"),
                self.create_dummy_track("789", "Jazz"),  # Not in updated_ids
            ]

            allure.attach("3 tracks to check for status changes", "Input Tracks", allure.attachment_type.TEXT)

        with allure.step("Execute status change detection"):
            result = service._find_status_changed_tracks(tracks)

        with allure.step("Verify successful detection"):
            assert len(result) == 2
            result_ids = {track.id for track in result}
            assert result_ids == {"123", "456"}

            allure.attach(str(result_ids), "Changed Track IDs", allure.attachment_type.TEXT)
            allure.attach("✅ Status changes detected successfully", "Detection Result", allure.attachment_type.TEXT)

    @allure.story("Error Handling")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should handle missing CSV file gracefully")
    @allure.description("Test behavior when CSV file for status comparison doesn't exist")
    @patch("core.tracks.incremental_filter.get_full_log_path")
    @patch("core.tracks.incremental_filter.load_track_list")
    def test_find_status_changed_tracks_no_csv(
        self,
        mock_load_track_list: MagicMock,
        mock_get_path: MagicMock,
    ) -> None:
        """Test handling when CSV file doesn't exist."""
        with allure.step("Setup missing CSV scenario"):
            service = self.create_service()

            # Mock empty CSV (file doesn't exist)
            mock_get_path.return_value = "/fake/path/track_list.csv"
            mock_load_track_list.return_value = {}

            tracks = [self.create_dummy_track("123")]

            allure.attach("Empty CSV (file missing)", "Error Scenario", allure.attachment_type.TEXT)

        with allure.step("Execute status change detection"):
            result = service._find_status_changed_tracks(tracks)

        with allure.step("Verify graceful handling"):
            assert result == []

            allure.attach("[]", "Result", allure.attachment_type.TEXT)
            allure.attach("✅ Gracefully handled missing CSV", "Error Handling", allure.attachment_type.TEXT)

    @allure.story("Error Handling")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should handle CSV loading errors gracefully")
    @allure.description("Test behavior when CSV loading raises an exception")
    @patch("core.tracks.incremental_filter.get_full_log_path")
    @patch("core.tracks.incremental_filter.load_track_list")
    def test_find_status_changed_tracks_error(
        self,
        mock_load_track_list: MagicMock,
        mock_get_path: MagicMock,
    ) -> None:
        """Test handling when CSV loading raises an exception."""
        with allure.step("Setup CSV loading error scenario"):
            service = self.create_service()

            # Mock CSV loading error
            mock_get_path.return_value = "/fake/path/track_list.csv"
            mock_load_track_list.side_effect = Exception("CSV loading failed")

            tracks = [self.create_dummy_track("123")]

            allure.attach("CSV loading exception", "Error Scenario", allure.attachment_type.TEXT)

        with allure.step("Execute status change detection"):
            result = service._find_status_changed_tracks(tracks)

        with allure.step("Verify error handling"):
            assert result == []

            # Verify warning was logged
            console_logger = service.console_logger
            assert hasattr(console_logger, "warning_messages")  # type: ignore[attr-defined]
            assert len(console_logger.warning_messages) > 0  # type: ignore[attr-defined]
            assert "Failed to check status changes" in console_logger.warning_messages[0]  # type: ignore[attr-defined]

            allure.attach("[]", "Result", allure.attachment_type.TEXT)
            allure.attach("✅ Exception handled gracefully with warning", "Error Handling", allure.attachment_type.TEXT)

    @allure.story("Date Parsing")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should parse valid date formats correctly")
    @allure.description("Test parsing of various valid date string formats")
    @pytest.mark.parametrize(
        ("date_string", "expected_timestamp"),
        [
            ("2024-01-15 14:30:00", "2024-01-15 14:30:00"),
            ("2023-12-31 23:59:59", "2023-12-31 23:59:59"),
            ("2024-06-01 00:00:00", "2024-06-01 00:00:00"),
        ],
    )
    def test_parse_date_added_valid(self, date_string: str, expected_timestamp: str) -> None:
        """Test parsing of valid date strings."""
        with allure.step(f"Testing date parsing for: '{date_string}'"):
            track = self.create_dummy_track(date_added=date_string)

            allure.attach(date_string, "Input Date String", allure.attachment_type.TEXT)

        with allure.step("Parse date"):
            result = parse_track_date_added(track)

        with allure.step("Verify parsed date"):
            assert result is not None
            assert result.tzinfo is UTC
            assert result.strftime("%Y-%m-%d %H:%M:%S") == expected_timestamp

            allure.attach(expected_timestamp, "Expected Timestamp", allure.attachment_type.TEXT)
            allure.attach(str(result), "Parsed Result", allure.attachment_type.TEXT)
            allure.attach("✅ Valid date parsed correctly", "Parse Result", allure.attachment_type.TEXT)

    @allure.story("Date Parsing")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.title("Should handle invalid date formats gracefully")
    @allure.description("Test handling of invalid or malformed date strings")
    @pytest.mark.parametrize(
        "invalid_date",
        [
            "",  # Empty string
            "invalid date",  # Invalid format
            "2024-13-01 10:00:00",  # Invalid month
            "2024-01-32 10:00:00",  # Invalid day
            "not a date at all",  # Completely invalid
            None,  # None value
        ],
    )
    def test_parse_date_added_invalid(self, invalid_date: str | None) -> None:
        """Test parsing of invalid date strings."""
        with allure.step(f"Testing invalid date parsing for: '{invalid_date}'"):
            track = self.create_dummy_track()
            track.date_added = invalid_date if invalid_date is not None else None
            allure.attach(str(invalid_date), "Invalid Date Input", allure.attachment_type.TEXT)

        with allure.step("Attempt to parse invalid date"):
            result = parse_track_date_added(track)

        with allure.step("Verify graceful handling"):
            assert result is None

            allure.attach("None", "Parse Result", allure.attachment_type.TEXT)
            allure.attach("✅ Invalid date handled gracefully", "Error Handling", allure.attachment_type.TEXT)

    @allure.story("Dry Run Support")
    @allure.severity(allure.severity_level.MINOR)
    @allure.title("Should support dry run mode")
    @allure.description("Test that service correctly implements dry run functionality")
    def test_dry_run_functionality(self) -> None:
        """Test dry run mode functionality."""
        with allure.step("Create service in dry run mode"):
            service = self.create_service(dry_run=True)

            allure.attach("True", "Dry Run Mode", allure.attachment_type.TEXT)

        with allure.step("Verify dry run properties"):
            assert service.dry_run is True
            assert hasattr(service, "get_dry_run_actions")  # type: ignore[attr-defined]

            dry_run_actions = service.get_dry_run_actions()
            assert isinstance(dry_run_actions, list)

            allure.attach("✅ Dry run mode activated", "Dry Run Status", allure.attachment_type.TEXT)
            allure.attach(str(len(dry_run_actions)), "Initial Actions Count", allure.attachment_type.TEXT)
