"""Unit tests for metadata.py functions."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.core.models.metadata import clean_names, determine_dominant_genre_for_artist
from src.core.models.track import TrackDict

if TYPE_CHECKING:
    import pytest

    LogCaptureFixture = pytest.LogCaptureFixture


class TestDominantGenreWithEmptyDates:
    """Test dominant genre calculation with empty/invalid dates."""

    @staticmethod
    def test_skips_tracks_with_empty_dates(caplog: LogCaptureFixture) -> None:
        """Test that tracks with empty date_added are skipped from dominant genre calculation."""
        tracks: list[TrackDict] = [
            TrackDict(
                id="1",
                name="Track 1",
                artist="Test Artist",
                album="Album A",
                genre="Metal",
                date_added="",  # Empty date - should be skipped
            ),
            TrackDict(
                id="2",
                name="Track 2",
                artist="Test Artist",
                album="Album B",
                genre="Gothic / Industrial Metal",
                date_added="2022-02-07 22:05:59",  # Valid date
            ),
        ]

        logger = logging.getLogger("test")
        with caplog.at_level(logging.WARNING):
            result = determine_dominant_genre_for_artist(tracks, logger)

        # Should use the genre from track with valid date
        assert result == "Gothic / Industrial Metal"
        # Should log warning about invalid date format
        assert "Invalid date format" in caplog.text or "empty or invalid" in caplog.text

    @staticmethod
    def test_returns_unknown_when_all_dates_empty() -> None:
        """Test that Unknown is returned when all tracks have empty dates."""
        tracks: list[TrackDict] = [
            TrackDict(
                id="1",
                name="Track 1",
                artist="Test Artist",
                album="Album A",
                genre="Metal",
                date_added="",  # Empty date
            ),
            TrackDict(
                id="2",
                name="Track 2",
                artist="Test Artist",
                album="Album B",
                genre="Rock",
                date_added="",  # Empty date
            ),
        ]

        logger = logging.getLogger("test")
        result = determine_dominant_genre_for_artist(tracks, logger)

        # Should return Unknown when no valid dates
        assert result == "Unknown"

    @staticmethod
    def test_prefers_valid_date_over_empty() -> None:
        """Test that tracks with valid dates are preferred over those with empty dates."""
        tracks: list[TrackDict] = [
            TrackDict(
                id="1",
                name="Track 1",
                artist="Test Artist",
                album="Album A",
                genre="Metal",
                date_added="",  # Empty - would be "earliest" if not skipped
            ),
            TrackDict(
                id="2",
                name="Track 2",
                artist="Test Artist",
                album="Album A",
                genre="Gothic / Industrial Metal",
                date_added="2023-01-01 00:00:00",  # Valid date
            ),
            TrackDict(
                id="3",
                name="Track 3",
                artist="Test Artist",
                album="Album B",
                genre="Alternative",
                date_added="2022-01-01 00:00:00",  # Earlier valid date
            ),
        ]

        logger = logging.getLogger("test")
        result = determine_dominant_genre_for_artist(tracks, logger)

        # Should use earliest valid date (2022), not empty date
        assert result == "Alternative"


class TestCleanNamesSuffixRemoval:
    """Test album suffix removal for EP/Single variations."""

    @staticmethod
    def _make_config(suffixes: list[str]) -> dict[str, Any]:
        return {
            "cleaning": {
                "remaster_keywords": [],
                "album_suffixes_to_remove": suffixes,
            },
            "exceptions": {"track_cleaning": []},
        }

    @staticmethod
    def _make_loggers() -> tuple[logging.Logger, logging.Logger]:
        console_logger = logging.getLogger("test.clean.console")
        error_logger = logging.getLogger("test.clean.error")
        return console_logger, error_logger

    def test_removes_ep_with_dash_suffix(self) -> None:
        """Ensure '- EP' suffix with spacing is removed."""
        config = self._make_config([" - EP", "EP"])
        console_logger, error_logger = self._make_loggers()

        track, album = clean_names(
            artist="Artist",
            track_name="Track",
            album_name="Dead End Dreams (Chapter 1) - EP",
            config=config,
            console_logger=console_logger,
            error_logger=error_logger,
        )

        assert track == "Track"
        assert album == "Dead End Dreams (Chapter 1)"

    def test_removes_ep_after_em_dash(self) -> None:
        """Ensure em dash before EP is handled."""
        config = self._make_config(["EP"])
        console_logger, error_logger = self._make_loggers()

        _, album = clean_names(
            artist="Artist",
            track_name="Track",
            album_name="Dead End Dreams —EP",
            config=config,
            console_logger=console_logger,
            error_logger=error_logger,
        )

        assert album == "Dead End Dreams"

    def test_removes_single_with_whitespace(self) -> None:
        """Ensure 'Single' suffix with spaces is removed."""
        config = self._make_config([" - Single", "Single"])
        console_logger, error_logger = self._make_loggers()

        _, album = clean_names(
            artist="Artist",
            track_name="Track",
            album_name="Another Song   - Single",
            config=config,
            console_logger=console_logger,
            error_logger=error_logger,
        )

        assert album == "Another Song"

    def test_does_not_trim_words_ending_with_ep(self) -> None:
        """Ensure bare words ending with 'ep' are not truncated."""
        config = self._make_config(["EP"])
        console_logger, error_logger = self._make_loggers()

        _, album = clean_names(
            artist="Artist",
            track_name="Track",
            album_name="Sleep",
            config=config,
            console_logger=console_logger,
            error_logger=error_logger,
        )

        assert album == "Sleep"
