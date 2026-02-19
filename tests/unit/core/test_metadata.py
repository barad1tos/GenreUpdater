"""Unit tests for metadata.py functions."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pytest

from core.models.metadata_utils import (
    _get_earliest_track_across_albums,
    _get_earliest_track_per_album,
    _get_genre_from_track,
    _parse_track_date,
    clean_names,
    determine_dominant_genre_for_artist,
    group_tracks_by_artist,
    parse_tracks,
)
from core.models.track_models import AppConfig, TrackDict
from tests.factories import create_test_app_config

if TYPE_CHECKING:
    LogCaptureFixture = pytest.LogCaptureFixture


class TestDetermineDominantGenreForArtist:
    """Comprehensive tests for determine_dominant_genre_for_artist algorithm."""

    @staticmethod
    def _make_track(
        track_id: str,
        album: str,
        genre: str,
        date_added: str,
        artist: str = "Test Artist",
    ) -> TrackDict:
        """Helper to create test tracks."""
        return TrackDict(
            id=track_id,
            name=f"Track {track_id}",
            artist=artist,
            album=album,
            genre=genre,
            date_added=date_added,
        )

    def test_empty_tracks_returns_unknown(self) -> None:
        """Empty track list should return Unknown."""
        logger = logging.getLogger("test")
        result = determine_dominant_genre_for_artist([], logger)
        assert result == "Unknown"

    def test_single_track_returns_its_genre(self) -> None:
        """Single track should return its genre."""
        tracks = [self._make_track("1", "Album A", "Rock", "2020-01-01 00:00:00")]
        logger = logging.getLogger("test")
        result = determine_dominant_genre_for_artist(tracks, logger)
        assert result == "Rock"

    def test_earliest_album_determines_genre(self) -> None:
        """Genre should come from the earliest added album."""
        tracks = [
            self._make_track("1", "Album B", "Pop", "2022-06-01 00:00:00"),
            self._make_track("2", "Album A", "Rock", "2020-01-01 00:00:00"),  # Earliest
            self._make_track("3", "Album C", "Jazz", "2023-01-01 00:00:00"),
        ]
        logger = logging.getLogger("test")
        result = determine_dominant_genre_for_artist(tracks, logger)
        assert result == "Rock"

    def test_multiple_tracks_same_album_uses_earliest_track(self) -> None:
        """For same album, should use earliest track's date to represent album."""
        tracks = [
            self._make_track("1", "Album A", "Rock", "2020-06-01 00:00:00"),
            self._make_track("2", "Album A", "Rock", "2020-01-01 00:00:00"),  # Earliest in album
            self._make_track("3", "Album B", "Pop", "2019-06-01 00:00:00"),  # Earlier album
        ]
        logger = logging.getLogger("test")
        result = determine_dominant_genre_for_artist(tracks, logger)
        assert result == "Pop"

    def test_genre_from_earliest_album_not_majority(self) -> None:
        """Genre is NOT majority voting - it's from earliest album."""
        # 3 Rock tracks in newer albums, 1 Pop track in earliest album
        tracks = [
            self._make_track("1", "Album A", "Rock", "2022-01-01 00:00:00"),
            self._make_track("2", "Album A", "Rock", "2022-02-01 00:00:00"),
            self._make_track("3", "Album B", "Rock", "2023-01-01 00:00:00"),
            self._make_track("4", "Album C", "Pop", "2019-01-01 00:00:00"),  # Earliest album
        ]
        logger = logging.getLogger("test")
        result = determine_dominant_genre_for_artist(tracks, logger)
        # Despite 3 Rock vs 1 Pop, Pop wins because Album C is earliest
        assert result == "Pop"

    def test_handles_exception_returns_unknown(self, caplog: LogCaptureFixture) -> None:
        """Exceptions during processing should return Unknown and log error."""
        # Create a mock track that will cause an error
        tracks: list[Any] = [{"invalid": "data"}]  # Missing required fields
        logger = logging.getLogger("test")

        with caplog.at_level(logging.ERROR):
            result = determine_dominant_genre_for_artist(tracks, logger)  # type: ignore[arg-type]

        assert result == "Unknown"


class TestGetEarliestTrackPerAlbum:
    """Tests for _get_earliest_track_per_album helper function."""

    @staticmethod
    def _make_track(track_id: str, album: str, date_added: str) -> TrackDict:
        return TrackDict(
            id=track_id,
            name=f"Track {track_id}",
            artist="Artist",
            album=album,
            genre="Rock",
            date_added=date_added,
        )

    def test_single_album_single_track(self) -> None:
        """Single track in single album."""
        tracks = [self._make_track("1", "Album A", "2020-01-01 00:00:00")]
        logger = logging.getLogger("test")
        result = _get_earliest_track_per_album(tracks, logger)
        assert len(result) == 1
        assert "Album A" in result
        assert result["Album A"].id == "1"

    def test_multiple_albums(self) -> None:
        """Multiple albums should each have their earliest track."""
        tracks = [
            self._make_track("1", "Album A", "2020-06-01 00:00:00"),
            self._make_track("2", "Album A", "2020-01-01 00:00:00"),  # Earliest in A
            self._make_track("3", "Album B", "2021-01-01 00:00:00"),
        ]
        logger = logging.getLogger("test")
        result = _get_earliest_track_per_album(tracks, logger)

        assert len(result) == 2
        assert result["Album A"].id == "2"
        assert result["Album B"].id == "3"

    def test_empty_album_name_skipped(self) -> None:
        """Tracks with empty album name are skipped (not grouped)."""
        tracks = [
            self._make_track("1", "", "2020-01-01 00:00:00"),
            self._make_track("2", "", "2021-01-01 00:00:00"),
            self._make_track("3", "Real Album", "2022-01-01 00:00:00"),
        ]
        logger = logging.getLogger("test")
        result = _get_earliest_track_per_album(tracks, logger)

        # Empty album names are skipped, only "Real Album" should be present
        assert len(result) == 1
        assert "Real Album" in result


class TestGetEarliestTrackAcrossAlbums:
    """Tests for _get_earliest_track_across_albums helper function."""

    @staticmethod
    def _make_track(track_id: str, album: str, date_added: str) -> TrackDict:
        return TrackDict(
            id=track_id,
            name=f"Track {track_id}",
            artist="Artist",
            album=album,
            genre="Rock",
            date_added=date_added,
        )

    def test_finds_earliest_across_albums(self) -> None:
        """Should find the track from the earliest album."""
        album_earliest = {
            "Album A": self._make_track("1", "Album A", "2022-01-01 00:00:00"),
            "Album B": self._make_track("2", "Album B", "2020-01-01 00:00:00"),  # Earliest
            "Album C": self._make_track("3", "Album C", "2023-01-01 00:00:00"),
        }
        logger = logging.getLogger("test")
        result = _get_earliest_track_across_albums(album_earliest, logger)

        assert result is not None
        assert result.id == "2"

    def test_empty_dict_returns_none(self) -> None:
        """Empty album dict should return None."""
        logger = logging.getLogger("test")
        result = _get_earliest_track_across_albums({}, logger)
        assert result is None

    def test_skips_invalid_dates(self, caplog: LogCaptureFixture) -> None:
        """Tracks with invalid dates should be skipped."""
        album_earliest = {
            "Album A": self._make_track("1", "Album A", ""),  # Invalid
            "Album B": self._make_track("2", "Album B", "2020-01-01 00:00:00"),
        }
        logger = logging.getLogger("test")

        with caplog.at_level(logging.WARNING):
            result = _get_earliest_track_across_albums(album_earliest, logger)

        assert result is not None
        assert result.id == "2"


class TestGetGenreFromTrack:
    """Tests for _get_genre_from_track helper function."""

    def test_valid_genre(self) -> None:
        """Valid string genre should be returned."""
        track = TrackDict(
            id="1",
            name="Track",
            artist="Artist",
            album="Album",
            genre="Rock",
            date_added="2020-01-01 00:00:00",
        )
        assert _get_genre_from_track(track) == "Rock"

    def test_empty_genre(self) -> None:
        """Empty genre should return Unknown."""
        track = TrackDict(
            id="1",
            name="Track",
            artist="Artist",
            album="Album",
            genre="",
            date_added="2020-01-01 00:00:00",
        )
        # Empty string is still a valid string, so it returns empty
        result = _get_genre_from_track(track)
        assert result == ""

    def test_none_genre_returns_unknown(self) -> None:
        """None genre should return Unknown."""
        track: dict[str, Any] = {
            "id": "1",
            "name": "Track",
            "artist": "Artist",
            "album": "Album",
            "genre": None,
            "date_added": "2020-01-01 00:00:00",
        }
        result = _get_genre_from_track(track)  # type: ignore[arg-type]
        assert result == "Unknown"


class TestParseTrackDate:
    """Tests for _parse_track_date helper function."""

    def test_valid_date_format(self) -> None:
        """Valid date string should be parsed."""
        logger = logging.getLogger("test")
        result = _parse_track_date("2020-01-15 12:30:45", logger)
        assert result is not None
        assert result.year == 2020
        assert result.month == 1
        assert result.day == 15

    def test_invalid_date_format(self, caplog: LogCaptureFixture) -> None:
        """Invalid date format should return None and log warning."""
        logger = logging.getLogger("test")

        with caplog.at_level(logging.WARNING):
            result = _parse_track_date("not-a-date", logger)

        assert result is None
        assert "Invalid date format" in caplog.text

    def test_empty_string(self, caplog: LogCaptureFixture) -> None:
        """Empty string should return None."""
        logger = logging.getLogger("test")

        with caplog.at_level(logging.WARNING):
            result = _parse_track_date("", logger)

        assert result is None


class TestGroupTracksByArtist:
    """Tests for group_tracks_by_artist function."""

    @staticmethod
    def _make_track(track_id: str, artist: str, album_artist: str = "") -> TrackDict:
        return TrackDict(
            id=track_id,
            name=f"Track {track_id}",
            artist=artist,
            album_artist=album_artist,
            album="Album",
            genre="Rock",
            date_added="2020-01-01 00:00:00",
        )

    def test_groups_by_artist(self) -> None:
        """Tracks should be grouped by artist name."""
        tracks = [
            self._make_track("1", "Artist A"),
            self._make_track("2", "Artist B"),
            self._make_track("3", "Artist A"),
        ]
        result = group_tracks_by_artist(tracks)

        assert len(result) == 2
        assert len(result["artist a"]) == 2
        assert len(result["artist b"]) == 1

    def test_case_insensitive_grouping(self) -> None:
        """Artist names should be grouped case-insensitively."""
        tracks = [
            self._make_track("1", "Artist A"),
            self._make_track("2", "ARTIST A"),
            self._make_track("3", "artist a"),
        ]
        result = group_tracks_by_artist(tracks)

        assert len(result) == 1
        assert len(result["artist a"]) == 3

    def test_empty_artist_name_skipped(self) -> None:
        """Empty artist names are skipped (not grouped)."""
        tracks = [
            self._make_track("1", ""),
            self._make_track("2", ""),
            self._make_track("3", "Real Artist"),
        ]
        result = group_tracks_by_artist(tracks)

        # Empty artist names are skipped, only "Real Artist" (lowercased) should be present
        assert len(result) == 1
        assert "real artist" in result

    def test_uses_album_artist_when_present(self) -> None:
        """Tracks with same album_artist should be grouped together."""
        tracks = [
            self._make_track("1", "Lord of the Lost", "Lord of the Lost"),
            self._make_track("2", "Lord of the Lost & IAMX", "Lord of the Lost"),
            self._make_track("3", "Lord of the Lost feat. Mono Inc.", "Lord of the Lost"),
        ]
        result = group_tracks_by_artist(tracks)

        # All tracks have same album_artist, should be one group
        assert len(result) == 1
        assert "lord of the lost" in result
        assert len(result["lord of the lost"]) == 3

    def test_normalizes_collaboration_when_album_artist_empty(self) -> None:
        """When album_artist is empty, extract main artist from collaboration."""
        tracks = [
            self._make_track("1", "Artist A feat. Artist B", ""),
            self._make_track("2", "Artist A", ""),
        ]
        result = group_tracks_by_artist(tracks)

        # Both should be grouped under normalized "Artist A"
        assert len(result) == 1
        assert "artist a" in result
        assert len(result["artist a"]) == 2

    def test_collaboration_separators(self) -> None:
        """Various collaboration separators should all normalize correctly."""
        tracks = [
            self._make_track("1", "Main Artist & Other", ""),
            self._make_track("2", "Main Artist feat. Someone", ""),
            self._make_track("3", "Main Artist ft. Another", ""),
            self._make_track("4", "Main Artist vs. Rival", ""),
            self._make_track("5", "Main Artist with Friend", ""),
            self._make_track("6", "Main Artist x Collab", ""),
        ]
        result = group_tracks_by_artist(tracks)

        # All should be grouped under "main artist"
        assert len(result) == 1
        assert "main artist" in result
        assert len(result["main artist"]) == 6

    def test_various_artists_grouping(self) -> None:
        """Compilation tracks with 'Various Artists' album_artist group together."""
        tracks = [
            self._make_track("1", "Band One", "Various Artists"),
            self._make_track("2", "Band Two", "Various Artists"),
            self._make_track("3", "Band Three", "Various Artists"),
        ]
        result = group_tracks_by_artist(tracks)

        # All tracks have same album_artist "Various Artists"
        assert len(result) == 1
        assert "various artists" in result
        assert len(result["various artists"]) == 3

    def test_album_artist_whitespace_only_falls_back(self) -> None:
        """Whitespace-only album_artist should be treated as empty."""
        tracks = [
            self._make_track("1", "Test Artist feat. Guest", "   "),
            self._make_track("2", "Test Artist", "   "),
        ]
        result = group_tracks_by_artist(tracks)

        # Whitespace album_artist falls back to normalized artist
        assert len(result) == 1
        assert "test artist" in result
        assert len(result["test artist"]) == 2


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
    def _make_config(suffixes: list[str]) -> AppConfig:
        return create_test_app_config(
            cleaning={
                "remaster_keywords": [],
                "album_suffixes_to_remove": suffixes,
            },
            exceptions={"track_cleaning": []},
        )

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


class TestParseTracksSingleTrack:
    """Tests for parse_tracks handling of single-track AppleScript output."""

    @staticmethod
    def test_single_track_without_line_separator() -> None:
        """Single-track output has no LINE_SEPARATOR — must not use splitlines().

        Python's splitlines() treats \\x1e (our FIELD_SEPARATOR) as a line
        boundary, breaking a single-track response into per-field rows.
        """
        field_sep = "\x1e"
        # 12 fields: id, name, artist, album_artist, album, genre, date_added,
        #   modification_date, track_status, year, release_year, ""
        raw = field_sep.join(
            [
                "120116",
                "Isle of Bliss",
                "Hanging Garden",
                "Hanging Garden",
                "Isle of Bliss",
                "Death Metal/Black Metal",
                "2026-01-23 22:12:27",
                "2026-01-23 22:12:27",
                "subscription",
                "",
                "",
                "",
            ]
        )
        tracks = parse_tracks(raw, logging.getLogger("test"))
        assert len(tracks) == 1
        assert tracks[0].id == "120116"
        assert tracks[0].name == "Isle of Bliss"
        assert tracks[0].artist == "Hanging Garden"

    @staticmethod
    def test_multi_track_with_line_separator() -> None:
        """Multi-track output with LINE_SEPARATOR parses correctly."""
        field_sep = "\x1e"
        line_sep = "\x1d"
        track1 = field_sep.join(
            [
                "1",
                "Track A",
                "Artist A",
                "Artist A",
                "Album A",
                "Rock",
                "2020-01-01 00:00:00",
                "2020-01-01 00:00:00",
                "subscription",
                "",
                "",
                "",
            ]
        )
        track2 = field_sep.join(
            [
                "2",
                "Track B",
                "Artist B",
                "Artist B",
                "Album B",
                "Pop",
                "2021-01-01 00:00:00",
                "2021-01-01 00:00:00",
                "subscription",
                "",
                "",
                "",
            ]
        )
        raw = line_sep.join([track1, track2])
        tracks = parse_tracks(raw, logging.getLogger("test"))
        assert len(tracks) == 2
        assert tracks[0].id == "1"
        assert tracks[1].id == "2"
