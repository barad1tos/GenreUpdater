"""Unit tests for year_consistency.py suspicious year logic.

Tests for the fix that prevents misleading "No dominant year (below 60%)"
message when year IS dominant but marked as suspicious.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from core.models.track_models import TrackDict
from core.tracks.year_consistency import YearConsistencyChecker


class MockLogger(logging.Logger):
    """Mock logger that captures log calls."""

    def __init__(self, name: str = "mock") -> None:
        """Initialize mock logger."""
        super().__init__(name)
        self.logged_messages: list[tuple[int, str, tuple[Any, ...]]] = []

    def info(self, msg: object, *args: Any, **_kwargs: Any) -> None:
        """Capture info log messages."""
        self.logged_messages.append((logging.INFO, str(msg), args))

    def warning(self, msg: object, *args: Any, **_kwargs: Any) -> None:
        """Capture warning log messages."""
        self.logged_messages.append((logging.WARNING, str(msg), args))

    def debug(self, msg: object, *args: Any, **_kwargs: Any) -> None:
        """Capture debug log messages."""
        self.logged_messages.append((logging.DEBUG, str(msg), args))


def create_tracks_with_year(year: str, count: int = 5, date_added: str = "2024-01-01") -> list[TrackDict]:
    """Create test tracks with specified year."""
    return [
        TrackDict(
            id=f"track_{i}",
            name=f"Track {i}",
            artist="Test Artist",
            album="Test Album",
            genre="Rock",
            year=year,
            date_added=date_added,
        )
        for i in range(count)
    ]


@pytest.mark.unit
class TestCheckMajorityDominanceReturnType:
    """Tests for _check_majority_dominance return type changes."""

    @pytest.fixture
    def checker(self) -> YearConsistencyChecker:
        """Create checker with mock logger."""
        return YearConsistencyChecker(
            console_logger=MockLogger("test"),
            suspicion_threshold_years=10,
        )

    def test_returns_tuple_with_year_when_dominant_and_not_suspicious(
        self,
        checker: YearConsistencyChecker,
    ) -> None:
        """When year is dominant and NOT suspicious, returns (year, False)."""
        # Tracks added in 2024 with year 2023 - NOT suspicious (1 year gap < 10)
        tracks = create_tracks_with_year("2023", count=10)
        most_common = ("2023", 10)
        total_tracks = 10

        result, was_suspicious = checker._check_majority_dominance(most_common, total_tracks, tracks)

        assert result == "2023"
        assert was_suspicious is False

    def test_returns_tuple_with_none_and_true_when_suspicious(
        self,
        checker: YearConsistencyChecker,
    ) -> None:
        """When year is dominant but suspicious, returns (None, True)."""
        # Tracks added in 2024 with year 2003 - SUSPICIOUS (21 year gap > 10)
        tracks = create_tracks_with_year("2003", count=15)
        most_common = ("2003", 15)
        total_tracks = 15

        result, was_suspicious = checker._check_majority_dominance(most_common, total_tracks, tracks)

        assert result is None
        assert was_suspicious is True

    def test_returns_tuple_with_none_and_false_when_below_threshold(
        self,
        checker: YearConsistencyChecker,
    ) -> None:
        """When year is below dominance threshold, returns (None, False)."""
        # Only 4/10 tracks have this year - below 50% threshold
        tracks = create_tracks_with_year("2023", count=4)
        # Add 6 more tracks with different year
        tracks.extend(create_tracks_with_year("2022", count=6))
        most_common = ("2023", 4)
        total_tracks = 10

        result, was_suspicious = checker._check_majority_dominance(most_common, total_tracks, tracks)

        assert result is None
        assert was_suspicious is False


@pytest.mark.unit
class TestGetDominantYearNoMisleadingLog:
    """Tests that 'No dominant year' is NOT logged when year is suspicious."""

    @pytest.fixture
    def mock_logger(self) -> MockLogger:
        """Create mock logger."""
        return MockLogger("test")

    @pytest.fixture
    def checker(self, mock_logger: MockLogger) -> YearConsistencyChecker:
        """Create checker with mock logger."""
        return YearConsistencyChecker(
            console_logger=mock_logger,
            suspicion_threshold_years=10,
        )

    def test_suspicious_year_does_not_log_no_dominant_message(
        self,
        checker: YearConsistencyChecker,
        mock_logger: MockLogger,
    ) -> None:
        """When year is suspicious, should NOT log 'No dominant year (below X%)'."""
        # 100% tracks have year 2003, but added in 2024 - SUSPICIOUS
        tracks = create_tracks_with_year("2003", count=15)

        result = checker.get_dominant_year(tracks)

        # Result should be None (triggers API verification)
        assert result is None

        # Check that we DID log about suspicious year (can be WARNING or INFO)
        suspicious_logs = [
            msg for level, msg, args in mock_logger.logged_messages
            if "suspicious" in msg.lower()
        ]
        assert suspicious_logs, "Should log that year is marked suspicious"

        # Check that we did NOT log "No dominant year (below X%)"
        misleading_logs = [
            msg for level, msg, args in mock_logger.logged_messages
            if "No dominant year (below" in msg
        ]
        assert not misleading_logs, (
            f"Should NOT log 'No dominant year (below X%)' for suspicious year. "
            f"Found: {misleading_logs}"
        )

    def test_genuinely_below_threshold_logs_no_dominant_message(
        self,
        checker: YearConsistencyChecker,
        mock_logger: MockLogger,
    ) -> None:
        """When year is genuinely below threshold, SHOULD log 'No dominant year'."""
        # Create tracks where most common year has <50% but no parity
        # 4/10 = 40% for top year, and second year has only 2 (not close to 4)
        tracks = create_tracks_with_year("2023", count=4)
        tracks.extend(create_tracks_with_year("2022", count=2))
        tracks.extend(create_tracks_with_year("2021", count=2))
        tracks.extend(create_tracks_with_year("2020", count=2))

        result = checker.get_dominant_year(tracks)

        # Result should be None
        assert result is None

        # Check that we DID log "No dominant year (below X%)"
        below_threshold_logs = [
            msg for level, msg, args in mock_logger.logged_messages
            if "No dominant year (below" in msg
        ]
        assert len(below_threshold_logs) == 1, (
            f"Should log 'No dominant year (below X%)' for genuinely low dominance. "
            f"Logs: {mock_logger.logged_messages}"
        )


@pytest.mark.unit
class TestSuspiciousYearLogging:
    """Tests for correct log message when year is suspicious."""

    @pytest.fixture
    def mock_logger(self) -> MockLogger:
        """Create mock logger."""
        return MockLogger("test")

    @pytest.fixture
    def checker(self, mock_logger: MockLogger) -> YearConsistencyChecker:
        """Create checker with mock logger."""
        return YearConsistencyChecker(
            console_logger=mock_logger,
            suspicion_threshold_years=10,
        )

    def test_logs_correct_message_for_suspicious_dominant_year(
        self,
        checker: YearConsistencyChecker,
        mock_logger: MockLogger,
    ) -> None:
        """Suspicious dominant year should log informative message with percentage."""
        # 15/15 tracks (100%) have year 2003, added in 2024
        tracks = create_tracks_with_year("2003", count=15)

        checker.get_dominant_year(tracks)

        # Find INFO logs about suspicious year (from _check_majority_dominance)
        info_suspicious_logs = [
            (msg, args) for level, msg, args in mock_logger.logged_messages
            if level == logging.INFO and "suspicious" in msg.lower()
        ]

        assert info_suspicious_logs, (
            f"Should log INFO message about suspicious year. "
            f"All logs: {mock_logger.logged_messages}"
        )

        # The new message format includes percentage
        log_msg, log_args = info_suspicious_logs[0]
        formatted = log_msg % log_args if log_args else log_msg

        assert "2003" in formatted, f"Log should mention year 2003: {formatted}"
        # The new format shows percentage like "100.0%" or track counts like "15/15"
        assert "100" in formatted or "15/15" in formatted, (
            f"Log should show 100% or 15/15 in new format: {formatted}"
        )
