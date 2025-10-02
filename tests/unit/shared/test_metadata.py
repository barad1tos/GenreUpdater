"""Unit tests for metadata.py functions."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest
from src.shared.data.metadata import determine_dominant_genre_for_artist
from src.shared.data.models import TrackDict

if TYPE_CHECKING:
    from pytest import LogCaptureFixture


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
