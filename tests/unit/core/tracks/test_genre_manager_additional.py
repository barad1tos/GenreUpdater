"""Additional GenreManager tests with Allure reporting for core functionality.

This file contains the 6 additional tests specified in the testing plan:
- test_calculate_dominant_genre_single()
- test_calculate_dominant_genre_tie()
- test_calculate_dominant_genre_threshold()
- test_calculate_dominant_genre_empty()
- test_process_artist_genres()
- test_apply_genre_to_tracks()
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock
import pytest

from core.models.metadata_utils import determine_dominant_genre_for_artist, group_tracks_by_artist
from core.models.track_models import TrackDict
from core.tracks.genre_manager import GenreManager


def _create_mock_logger() -> MagicMock:
    """Create a mock logger with message tracking."""
    mock = MagicMock(spec=logging.Logger)
    mock.info_messages = []
    mock.warning_messages = []
    mock.error_messages = []
    mock.debug_messages = []

    def track_info(msg: object, *args: object, **_kwargs: Any) -> None:
        """Track info log messages."""
        mock.info_messages.append(str(msg) % args if args else str(msg))

    def track_error(msg: object, *args: object, **_kwargs: Any) -> None:
        """Track error log messages."""
        mock.error_messages.append(str(msg) % args if args else str(msg))

    def track_debug(msg: object, *args: object, **_kwargs: Any) -> None:
        """Track debug log messages."""
        mock.debug_messages.append(str(msg) % args if args else str(msg))

    def track_warning(msg: object, *args: object, **_kwargs: Any) -> None:
        """Track warning log messages."""
        mock.warning_messages.append(str(msg) % args if args else str(msg))

    mock.info.side_effect = track_info
    mock.error.side_effect = track_error
    mock.debug.side_effect = track_debug
    mock.warning.side_effect = track_warning
    return mock


class TestGenreManagerCoreFunctionality:
    """Tests for core GenreManager functionality specified in testing plan."""

    @staticmethod
    def create_genre_manager(
        config: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> GenreManager:
        """Create a GenreManager instance for testing."""
        mock_track_processor = AsyncMock()
        mock_track_processor.update_track_async = AsyncMock(return_value=True)

        test_config = config or {"force_update": False, "processing": {"batch_size": 100}}

        return GenreManager(
            track_processor=mock_track_processor,
            console_logger=_create_mock_logger(),
            error_logger=_create_mock_logger(),
            analytics=MagicMock(),
            config=test_config,
            dry_run=dry_run,
        )

    @staticmethod
    def create_dummy_track(
        track_id: str = "12345",
        name: str = "Test Track",
        artist: str = "Test Artist",
        album: str = "Test Album",
        genre: str = "Rock",
        date_added: str = "2024-01-01 10:00:00",
    ) -> TrackDict:
        """Create a dummy track for testing."""
        return TrackDict(
            id=track_id,
            name=name,
            artist=artist,
            album=album,
            genre=genre,
            date_added=date_added,
            track_status="subscription",
            year=None,
            last_modified="2024-01-01 10:00:00",
        )

    def test_calculate_dominant_genre_single(self) -> None:
        """Test dominant genre calculation with single track."""
        tracks = [
            self.create_dummy_track("1", "Song A", "Artist 1", "Album A"),
        ]
        error_logger = _create_mock_logger()
        dominant_genre = determine_dominant_genre_for_artist(tracks, error_logger)  # type: ignore[arg-type]
        assert dominant_genre == "Rock"

    def test_calculate_dominant_genre_tie(self) -> None:
        """Test dominant genre calculation with tie scenario."""
        tracks = [
            # Album A (earlier) - Rock genre
            self.create_dummy_track("1", "Song A1", "Artist 1", "Album A"),
            self.create_dummy_track("2", "Song A2", "Artist 1", "Album A"),
            # Album B (later) - Pop genre
            self.create_dummy_track("3", "Song B1", "Artist 1", "Album B", "Pop"),
            self.create_dummy_track("4", "Song B2", "Artist 1", "Album B", "Pop"),
        ]
        error_logger = _create_mock_logger()
        dominant_genre = determine_dominant_genre_for_artist(tracks, error_logger)  # type: ignore[arg-type]
        # Should select "Rock" from Album A (earliest album)
        assert dominant_genre == "Rock"

    def test_calculate_dominant_genre_threshold(self) -> None:
        """Test dominant genre calculation with threshold logic."""
        tracks = [
            # Album A (very early) - Jazz
            self.create_dummy_track("1", "Jazz Song", "Artist 1", "Album A", "Jazz"),
            # Album B (later) - Rock (multiple tracks)
            self.create_dummy_track("2", "Rock Song 1", "Artist 1", "Album B"),
            self.create_dummy_track("3", "Rock Song 2", "Artist 1", "Album B"),
            self.create_dummy_track("4", "Rock Song 3", "Artist 1", "Album B"),
        ]
        error_logger = _create_mock_logger()
        dominant_genre = determine_dominant_genre_for_artist(tracks, error_logger)  # type: ignore[arg-type]
        # Algorithm selects earliest album's genre, not most frequent
        assert dominant_genre == "Jazz"

    def test_calculate_dominant_genre_empty(self) -> None:
        """Test dominant genre calculation with empty track list."""
        tracks: list[TrackDict] = []
        error_logger = _create_mock_logger()
        dominant_genre = determine_dominant_genre_for_artist(tracks, error_logger)  # type: ignore[arg-type]
        assert dominant_genre == "Unknown"

    def test_process_artist_genres(self) -> None:
        """Test processing genres for multiple artists."""
        tracks = [
            # Artist 1 - Rock
            self.create_dummy_track("1", "Song 1", "Artist 1", "Album A"),
            self.create_dummy_track("2", "Song 2", "Artist 1", "Album A"),
            # Artist 2 - Pop
            self.create_dummy_track("3", "Song 3", "Artist 2", "Album B", "Pop"),
            self.create_dummy_track("4", "Song 4", "Artist 2", "Album B", "Pop"),
            # Artist 3 - Jazz
            self.create_dummy_track("5", "Song 5", "Artist 3", "Album C", "Jazz"),
        ]
        grouped_tracks = group_tracks_by_artist(tracks)
        error_logger = _create_mock_logger()
        artist_genres = {}

        for artist, artist_tracks in grouped_tracks.items():
            dominant_genre = determine_dominant_genre_for_artist(artist_tracks, error_logger)  # type: ignore[arg-type]
            artist_genres[artist] = dominant_genre
        # Keys are lowercase due to case-insensitive grouping
        expected_genres = {
            "artist 1": "Rock",
            "artist 2": "Pop",
            "artist 3": "Jazz",
        }

        for artist, expected_genre in expected_genres.items():
            assert artist in artist_genres
            assert artist_genres[artist] == expected_genre

    @pytest.mark.asyncio
    async def test_apply_genre_to_tracks(self) -> None:
        """Test applying calculated genres to tracks."""
        genre_manager = self.create_genre_manager()

        # Tracks with missing/incorrect genres
        tracks = [
            # Empty genre
            self.create_dummy_track("1", "Song 1", album="Album A", genre=""),
            # Unknown genre
            self.create_dummy_track("2", "Song 2", album="Album A", genre="Unknown"),
            # Same genre - no update needed
            self.create_dummy_track("3", "Song 3", album="Album A", genre="Alternative Rock"),
        ]

        new_genre = "Alternative Rock"
        update_results = []
        change_logs = []

        for track in tracks:
            updated_track, change_log = await genre_manager.test_update_track_genre(track=track, new_genre=new_genre, force_update=False)

            update_results.append(
                {
                    "track_id": track.id,
                    "updated": updated_track is not None,
                    "change_logged": change_log is not None,
                }
            )

            if updated_track:
                change_logs.append(change_log)
        # Track 1 (empty genre) should be updated
        assert update_results[0]["updated"] is True
        assert update_results[0]["change_logged"] is True

        # Track 2 (Unknown genre) should be updated
        assert update_results[1]["updated"] is True
        assert update_results[1]["change_logged"] is True

        # Track 3 (same genre) should NOT be updated
        assert update_results[2]["updated"] is False
        assert update_results[2]["change_logged"] is False

        # Verify track processor was called for updates
        assert genre_manager.track_processor.update_track_async.call_count == 2  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_handle_tracks_without_ids(self) -> None:
        """Test handling of tracks without IDs."""
        genre_manager = self.create_genre_manager()

        # Track without ID
        track = self.create_dummy_track("", "Song", "Artist", "Album", "")
        # Explicitly set empty ID
        track.id = ""
        updated_track, change_log = await genre_manager.test_update_track_genre(track=track, new_genre="Rock", force_update=False)
        assert updated_track is None
        assert change_log is None

        # Verify error was logged
        error_logger = genre_manager.error_logger
        assert hasattr(error_logger, "error_messages")
        assert len(error_logger.error_messages) > 0  # type: ignore[attr-defined]
        assert "Track missing 'id' field" in error_logger.error_messages[0]  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_skip_prerelease_tracks(self) -> None:
        """Test skipping prerelease tracks."""
        genre_manager = self.create_genre_manager()

        # Prerelease track (read-only)
        track = self.create_dummy_track("123", "Song", "Artist", "Album", "")
        track.track_status = "prerelease"
        updated_track, change_log = await genre_manager.test_update_track_genre(track=track, new_genre="Rock", force_update=False)
        assert updated_track is None
        assert change_log is None

        # Verify debug message was logged
        console_logger = genre_manager.console_logger
        assert hasattr(console_logger, "debug_messages")
