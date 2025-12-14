"""Unit tests for track_sync module.

Note: This module tests internal functions that are prefixed with underscore.
Testing private functions is intentional to ensure correctness of internal logic.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.models.protocols import CacheServiceProtocol
from core.models.types import TrackDict
from metrics.track_sync import (
    _FIELD_COUNT_WITH_ALBUM_ARTIST,
    _FIELD_COUNT_WITHOUT_ALBUM_ARTIST,
    _MISSING_VALUE_PLACEHOLDER,
    _build_osascript_command,
    _convert_track_to_csv_dict,
    _create_normalized_track_dict,
    _create_track_from_row,
    _get_musicapp_syncable_fields,
    _get_processed_albums_from_csv,
    _handle_osascript_error,
    _merge_musicapp_into_csv,
    _normalize_track_year_fields,
    _parse_osascript_output,
    _parse_single_track_line,
    _resolve_field_indices,
    _sanitize_applescript_field,
    _track_fields_differ,
    _update_csv_track_from_musicapp,
    _update_track_with_cached_fields_for_sync,
    _validate_csv_header,
    load_track_list,
    ParsedTrackFields,
    save_track_map_to_csv,
)


def _create_test_track(
    track_id: str = "1",
    *,
    name: str = "Track",
    artist: str = "Artist",
    album: str = "Album",
    genre: str | None = "Rock",
    year: str | None = "2020",
    date_added: str | None = "2024-01-01",
    track_status: str | None = "OK",
    old_year: str | None = "2020",
    new_year: str | None = "2021",
) -> TrackDict:
    """Create a test TrackDict with default values."""
    return TrackDict(
        id=track_id,
        name=name,
        artist=artist,
        album=album,
        genre=genre,
        year=year,
        date_added=date_added,
        track_status=track_status,
        old_year=old_year,
        new_year=new_year,
    )


@pytest.fixture
def console_logger() -> logging.Logger:
    """Create console logger."""
    return logging.getLogger("test.console")


@pytest.fixture
def error_logger() -> logging.Logger:
    """Create error logger."""
    return logging.getLogger("test.error")


@pytest.fixture
def mock_cache_service() -> CacheServiceProtocol:
    """Create mock cache service."""
    service = MagicMock()
    service.generate_album_key = MagicMock(side_effect=lambda a, b: f"{a}|{b}")
    service.get_album_year_from_cache = AsyncMock(return_value=None)
    service.store_album_year_in_cache = AsyncMock()
    return cast(CacheServiceProtocol, service)


class TestFieldCountConstants:
    """Tests for field count constants."""

    def test_field_count_with_album_artist(self) -> None:
        """Should have correct value for format with album artist (12 fields with modification_date)."""
        assert _FIELD_COUNT_WITH_ALBUM_ARTIST == 12

    def test_field_count_without_album_artist(self) -> None:
        """Should have correct value for format without album artist (11 fields with modification_date)."""
        assert _FIELD_COUNT_WITHOUT_ALBUM_ARTIST == 11


class TestValidateCsvHeader:
    """Tests for _validate_csv_header function."""

    def test_returns_empty_when_no_fieldnames(self) -> None:
        """Should return empty list when reader has no fieldnames."""
        reader = MagicMock()
        reader.fieldnames = None
        logger = logging.getLogger("test")

        result = _validate_csv_header(reader, ["id", "name"], "test.csv", logger)

        assert result == []

    def test_returns_expected_fields_when_match(self) -> None:
        """Should return expected fields when header matches."""
        reader = MagicMock()
        reader.fieldnames = ["id", "name", "artist"]
        expected = ["id", "name"]
        logger = logging.getLogger("test")

        result = _validate_csv_header(reader, expected, "test.csv", logger)

        assert result == expected

    def test_returns_available_fields_when_mismatch(self) -> None:
        """Should return only available fields when header doesn't match."""
        reader = MagicMock()
        reader.fieldnames = ["id", "artist"]
        expected = ["id", "name", "artist"]
        logger = logging.getLogger("test")

        result = _validate_csv_header(reader, expected, "test.csv", logger)

        assert result == ["id", "artist"]


class TestCreateTrackFromRow:
    """Tests for _create_track_from_row function."""

    def test_creates_track_from_valid_row(self) -> None:
        """Should create TrackDict from valid row data."""
        row = {
            "id": "123",
            "name": "Test Track",
            "artist": "Test Artist",
            "album": "Test Album",
            "genre": "Rock",
            "year": "2020",
            "date_added": "2024-01-01",
            "track_status": "OK",
            "old_year": "2020",
            "new_year": "2021",
        }
        fields_to_read = list(row.keys())
        expected_fieldnames = fields_to_read

        track = _create_track_from_row(row, fields_to_read, expected_fieldnames)

        assert track is not None
        assert track.id == "123"
        assert track.name == "Test Track"
        assert track.artist == "Test Artist"
        assert track.genre == "Rock"

    def test_creates_track_with_year_field(self) -> None:
        """Should read year field from row (Issue #85 - delta detection)."""
        row = {
            "id": "123",
            "name": "Test",
            "artist": "Artist",
            "album": "Album",
            "genre": "Rock",
            "year": "2020",
            "date_added": "2024-01-01",
            "track_status": "OK",
            "old_year": "2015",
            "new_year": "2020",
        }
        fields_to_read = list(row.keys())
        expected_fieldnames = fields_to_read

        track = _create_track_from_row(row, fields_to_read, expected_fieldnames)

        assert track is not None
        assert track.year == "2020"
        assert track.old_year == "2015"
        assert track.new_year == "2020"

    @pytest.mark.parametrize(
        "invalid_id",
        [
            "",
            "   ",
        ],
    )
    def test_returns_none_for_invalid_id(self, invalid_id: str) -> None:
        """Should return None when id is empty or whitespace only."""
        row = {"id": invalid_id, "name": "Test"}
        fields_to_read = ["id", "name"]
        expected_fieldnames = fields_to_read

        track = _create_track_from_row(row, fields_to_read, expected_fieldnames)

        assert track is None

    def test_handles_missing_optional_fields(self) -> None:
        """Should handle missing optional fields gracefully."""
        row = {"id": "1", "name": "Track", "artist": "Artist", "album": "Album", "genre": ""}
        fields_to_read = ["id", "name", "artist", "album", "genre"]
        expected_fieldnames = ["id", "name", "artist", "album", "genre", "date_added", "track_status", "old_year", "new_year"]

        track = _create_track_from_row(row, fields_to_read, expected_fieldnames)

        assert track is not None
        assert track.genre is None


class TestLoadTrackList:
    """Tests for load_track_list function."""

    def test_returns_empty_when_file_not_exists(self, tmp_path: Path) -> None:
        """Should return empty dict when file doesn't exist."""
        csv_path = str(tmp_path / "nonexistent.csv")

        result = load_track_list(csv_path)

        assert result == {}

    def test_loads_tracks_from_valid_csv(self, tmp_path: Path) -> None:
        """Should load tracks from valid CSV file."""
        csv_file = tmp_path / "tracks.csv"
        csv_file.write_text(
            "id,name,artist,album,genre,date_added,last_modified,track_status,old_year,new_year\n"
            "1,Track 1,Artist,Album,Rock,2024-01-01,,OK,2020,2021\n"
            "2,Track 2,Artist,Album,Pop,2024-01-02,,OK,2019,2020\n"
        )

        result = load_track_list(str(csv_file))

        assert len(result) == 2
        assert "1" in result
        assert "2" in result
        assert result["1"].name == "Track 1"

    def test_returns_empty_when_header_invalid(self, tmp_path: Path) -> None:
        """Should return empty dict when CSV header is empty."""
        csv_file = tmp_path / "empty.csv"
        csv_file.write_text("")

        result = load_track_list(str(csv_file))

        assert result == {}

    def test_handles_csv_read_error(self, tmp_path: Path) -> None:
        """Should handle CSV read errors gracefully."""
        csv_file = tmp_path / "tracks.csv"
        csv_file.write_text("id,name\n1,Test")

        with patch("metrics.track_sync.Path.open", side_effect=OSError("Read error")):
            result = load_track_list(str(csv_file))

        assert result == {}


class TestGetProcessedAlbumsFromCsv:
    """Tests for _get_processed_albums_from_csv function."""

    def test_returns_empty_when_no_tracks(self, mock_cache_service: CacheServiceProtocol) -> None:
        """Should return empty dict when no tracks."""
        result = _get_processed_albums_from_csv({}, mock_cache_service)

        assert result == {}

    def test_returns_processed_albums(self, mock_cache_service: CacheServiceProtocol) -> None:
        """Should return mapping of album keys to new years."""
        track = _create_test_track("100", new_year="2022")
        csv_map = {"100": track}

        result = _get_processed_albums_from_csv(csv_map, mock_cache_service)

        assert "Artist|Album" in result
        assert result["Artist|Album"] == "2022"

    def test_skips_tracks_without_new_year(self, mock_cache_service: CacheServiceProtocol) -> None:
        """Should skip tracks without new_year value."""
        track = _create_test_track("101", new_year=None)
        csv_map = {"101": track}

        result = _get_processed_albums_from_csv(csv_map, mock_cache_service)

        assert result == {}


class TestNormalizeTrackYearFields:
    """Tests for _normalize_track_year_fields function."""

    def test_sets_empty_old_year_when_none(self) -> None:
        """Should set old_year to empty string when None."""
        track = _create_test_track("102", old_year=None)

        _normalize_track_year_fields(track)

        assert track.old_year == ""

    def test_sets_empty_new_year_when_none(self) -> None:
        """Should set new_year to empty string when None."""
        track = _create_test_track("103", new_year=None)

        _normalize_track_year_fields(track)

        assert track.new_year == ""

    def test_preserves_existing_values(self) -> None:
        """Should preserve existing year values."""
        track = _create_test_track("104", old_year="2019", new_year="2022")

        _normalize_track_year_fields(track)

        assert track.old_year == "2019"
        assert track.new_year == "2022"


class TestCreateNormalizedTrackDict:
    """Tests for _create_normalized_track_dict function."""

    def test_creates_normalized_track(self) -> None:
        """Should create normalized TrackDict with clean values."""
        track = _create_test_track("99", name="  Test Name  ", genre="  Pop  ")

        result = _create_normalized_track_dict(track, "99", "Normalized Artist", "Normalized Album")

        assert result.id == "99"
        assert result.name == "Test Name"
        assert result.artist == "Normalized Artist"
        assert result.album == "Normalized Album"
        assert result.genre == "Pop"


class TestGetMusicappSyncableFields:
    """Tests for _get_musicapp_syncable_fields function."""

    def test_returns_expected_fields(self) -> None:
        """Should return list of fields that sync from Music.app."""
        fields = _get_musicapp_syncable_fields()

        assert "name" in fields
        assert "artist" in fields
        assert "album" in fields
        assert "genre" in fields
        assert "date_added" in fields
        assert "track_status" in fields

    def test_returns_year_field_for_delta_detection(self) -> None:
        """Should return year field for delta detection (Issue #85)."""
        fields = _get_musicapp_syncable_fields()

        assert "year" in fields

    def test_excludes_tracking_fields(self) -> None:
        """Should NOT include old_year/new_year - they are preserved from CSV.

        These fields are managed by year_batch.py during year updates,
        not by sync operations. AppleScript doesn't provide them.
        """
        fields = _get_musicapp_syncable_fields()

        assert "old_year" not in fields
        assert "new_year" not in fields


class TestTrackFieldsDiffer:
    """Tests for _track_fields_differ function."""

    def test_returns_false_when_identical(self) -> None:
        """Should return False when CSV and Music.app tracks are identical."""
        csv_track = _create_test_track("2", genre="Jazz")
        musicapp_track = _create_test_track("2", genre="Jazz")
        fields = ["genre"]

        result = _track_fields_differ(csv_track, musicapp_track, fields)

        assert result is False

    def test_returns_true_when_different(self) -> None:
        """Should return True when CSV and Music.app tracks differ."""
        csv_track = _create_test_track("2", genre="Jazz")
        musicapp_track = _create_test_track("2", genre="Blues")
        fields = ["genre"]

        result = _track_fields_differ(csv_track, musicapp_track, fields)

        assert result is True


class TestUpdateCsvTrackFromMusicapp:
    """Tests for _update_csv_track_from_musicapp function."""

    def test_updates_fields(self) -> None:
        """Should update CSV track with Music.app values."""
        csv_track = _create_test_track("3", genre="Jazz")
        musicapp_track = _create_test_track("3", genre="Blues")
        fields = ["genre"]

        _update_csv_track_from_musicapp(csv_track, musicapp_track, fields)

        assert csv_track.genre == "Blues"

    def test_skips_none_values(self) -> None:
        """Should not update fields with None values from Music.app."""
        csv_track = _create_test_track("3", genre="Jazz")
        musicapp_track = _create_test_track("3", genre=None)
        fields = ["genre"]

        _update_csv_track_from_musicapp(csv_track, musicapp_track, fields)

        assert csv_track.genre == "Jazz"

    def test_logs_warning_for_missing_field(self, caplog: pytest.LogCaptureFixture) -> None:
        """Should log warning when field doesn't exist."""
        csv_track = _create_test_track("3")
        musicapp_track = _create_test_track("3")
        fields = ["nonexistent_field"]

        with caplog.at_level(logging.WARNING):
            _update_csv_track_from_musicapp(csv_track, musicapp_track, fields)

        assert "does not exist" in caplog.text


class TestMergeMusicappIntoCsv:
    """Tests for _merge_musicapp_into_csv function."""

    def test_adds_new_tracks(self) -> None:
        """Should add Music.app tracks not in CSV."""
        musicapp_track = _create_test_track("4")
        musicapp_tracks = {"4": musicapp_track}
        csv_tracks: dict[str, TrackDict] = {}

        updated = _merge_musicapp_into_csv(musicapp_tracks, csv_tracks)

        assert updated == 1
        assert "4" in csv_tracks

    def test_updates_existing_tracks(self) -> None:
        """Should update CSV tracks when Music.app fields differ."""
        csv_track = _create_test_track("4", genre="Country")
        musicapp_track = _create_test_track("4", genre="Folk")
        csv_tracks = {"4": csv_track}
        musicapp_tracks = {"4": musicapp_track}

        updated = _merge_musicapp_into_csv(musicapp_tracks, csv_tracks)

        assert updated == 1
        assert csv_tracks["4"].genre == "Folk"

    def test_skips_identical_tracks(self) -> None:
        """Should not count identical tracks as updated."""
        csv_track = _create_test_track("5", genre="Classical")
        csv_tracks = {"5": csv_track}
        musicapp_tracks = {"5": _create_test_track("5", genre="Classical")}

        updated = _merge_musicapp_into_csv(musicapp_tracks, csv_tracks)

        assert updated == 0


class TestBuildOsascriptCommand:
    """Tests for _build_osascript_command function."""

    def test_builds_command_without_filter(self) -> None:
        """Should build command without artist filter."""
        result = _build_osascript_command("/path/to/script.scpt", None)

        assert result == ["osascript", "/path/to/script.scpt"]

    def test_builds_command_with_filter(self) -> None:
        """Should build command with artist filter."""
        result = _build_osascript_command("/path/to/script.scpt", "Test Artist")

        assert result == ["osascript", "/path/to/script.scpt", "Test Artist"]


class TestResolveFieldIndices:
    """Tests for _resolve_field_indices function."""

    def test_returns_indices_for_12_fields(self) -> None:
        """Should return correct indices for 12-field format (with album_artist, modification_date)."""
        result = _resolve_field_indices(12)

        assert result == (6, 7, 8, 9)

    def test_returns_indices_for_11_fields(self) -> None:
        """Should return correct indices for 11-field format (no album_artist, has modification_date)."""
        result = _resolve_field_indices(11)

        assert result == (5, 6, 7, 8)

    def test_returns_none_for_unexpected_count(self) -> None:
        """Should return None for unexpected field count."""
        result = _resolve_field_indices(9)

        assert result is None


class TestParseOsascriptOutput:
    """Tests for _parse_osascript_output function.

    Field format (12 fields with album_artist):
    0:id, 1:name, 2:artist, 3:album_artist, 4:album, 5:genre, 6:date_added,
    7:modification_date, 8:status, 9:year, 10:release_year, 11:new_year

    Field format (11 fields without album_artist):
    0:id, 1:name, 2:artist, 3:album, 4:genre, 5:date_added,
    6:modification_date, 7:status, 8:year, 9:release_year, 10:new_year
    """

    def test_parses_tab_separated_output(self) -> None:
        """Should parse tab-separated output (11 fields without album_artist)."""
        # 11 fields: id, name, artist, album, genre, date_added, mod_date, status, year, release_year, new_year
        raw_output = "1\tTrack 1\tArtist\tAlbum\tRock\t2024-01-01\t2024-01-02\tOK\t2020\t2021\tnew_value"

        result = _parse_osascript_output(raw_output)

        assert "1" in result
        assert result["1"]["date_added"] == "2024-01-01"
        assert result["1"]["last_modified"] == "2024-01-02"
        assert result["1"]["track_status"] == "OK"
        assert result["1"]["year"] == "2020"

    def test_parses_12_field_output(self) -> None:
        """Should parse 12-field output (with album_artist)."""
        # 12 fields: id, name, artist, album_artist, album, genre, date_added, mod_date, status, year, release_year, new_year
        raw_output = "1\tTrack\tArtist\tAlbum Artist\tAlbum\tRock\t2024-01-01\t2024-01-02\tOK\t2020\t2021\t"

        result = _parse_osascript_output(raw_output)

        assert "1" in result
        assert result["1"]["date_added"] == "2024-01-01"
        assert result["1"]["last_modified"] == "2024-01-02"
        assert result["1"]["track_status"] == "OK"
        assert result["1"]["year"] == "2020"

    def test_parses_chr30_separated_output(self) -> None:
        """Should parse chr(30) field-separated output."""
        sep = chr(30)
        line_sep = chr(29)
        # 11 fields: id, name, artist, album, genre, date_added, mod_date, status, year, release_year, new_year
        raw_output = sep.join(["1", "Track", "Artist", "Album", "Rock", "2024-01-01", "2024-01-02", "OK", "2020", "2021", "new"]) + line_sep

        result = _parse_osascript_output(raw_output)

        assert "1" in result
        assert result["1"]["last_modified"] == "2024-01-02"

    def test_handles_missing_value_placeholder(self) -> None:
        """Should handle 'missing value' placeholder."""
        # 11 fields with missing values for date_added, mod_date, status
        raw_output = "1\tTrack\tArtist\tAlbum\tRock\tmissing value\tmissing value\tmissing value\tmissing value\t2021\tnew"

        result = _parse_osascript_output(raw_output)

        assert result["1"]["date_added"] == ""
        assert result["1"]["last_modified"] == ""
        assert result["1"]["track_status"] == ""
        assert result["1"]["year"] == ""

    def test_skips_empty_lines(self) -> None:
        """Should skip empty lines."""
        # 11 fields: id, name, artist, album, genre, date_added, mod_date, status, year, release_year, new_year
        raw_output = "\n1\tTrack\tArtist\tAlbum\tRock\t2024\t2024\tOK\t2020\t2021\tnew\n\n"

        result = _parse_osascript_output(raw_output)

        assert len(result) == 1

    def test_logs_warning_for_invalid_field_count(self, caplog: pytest.LogCaptureFixture) -> None:
        """Should log warning for lines with unexpected field count."""
        raw_output = "1\tTrack\tArtist"

        with caplog.at_level(logging.WARNING):
            result = _parse_osascript_output(raw_output)

        assert "expected 11 or 12" in caplog.text
        assert len(result) == 0


class TestParsedTrackFieldsTyping:
    """Tests for ParsedTrackFields TypedDict strict interface compliance."""

    def test_returns_typed_dict_with_all_required_keys(self) -> None:
        """Should return dict with all 4 required ParsedTrackFields keys."""
        # 11 fields format
        raw_output = "1\tTrack\tArtist\tAlbum\tRock\t2024-01-01\t2024-01-02\tOK\t2020\t2021\tnew"

        result = _parse_osascript_output(raw_output)

        # Verify all 4 keys are present
        track_fields: ParsedTrackFields = result["1"]
        assert "date_added" in track_fields
        assert "last_modified" in track_fields
        assert "track_status" in track_fields
        assert "year" in track_fields

    def test_parsed_fields_are_strings(self) -> None:
        """Should return string values for all fields."""
        raw_output = "1\tTrack\tArtist\tAlbum\tRock\t2024-01-01\t2024-01-02\tOK\t2020\t2021\tnew"

        result = _parse_osascript_output(raw_output)
        track_fields = result["1"]

        assert isinstance(track_fields["date_added"], str)
        assert isinstance(track_fields["last_modified"], str)
        assert isinstance(track_fields["track_status"], str)
        assert isinstance(track_fields["year"], str)

    def test_typed_dict_assignment_compiles(self) -> None:
        """Should allow direct assignment to ParsedTrackFields variable."""
        # This test verifies type checker compliance at runtime
        fields: ParsedTrackFields = {
            "date_added": "2024-01-01",
            "last_modified": "2024-01-02",
            "track_status": "OK",
            "year": "2020",
        }

        assert fields["date_added"] == "2024-01-01"
        assert fields["last_modified"] == "2024-01-02"


class TestLastModifiedParsing:
    """Tests for last_modified (modification_date) field parsing edge cases."""

    def test_parses_last_modified_different_from_date_added(self) -> None:
        """Should correctly parse last_modified when different from date_added."""
        # Real-world scenario: track added at 02:35, modified at 03:35
        raw_output = "99148\tBlackened\tMetallica\tMetallica\tAlbum\tDSBM\t2022-02-12 02:35:43\t2022-02-12 03:35:43\tsubscription\t1988\t1988\t"

        result = _parse_osascript_output(raw_output)

        assert result["99148"]["date_added"] == "2022-02-12 02:35:43"
        assert result["99148"]["last_modified"] == "2022-02-12 03:35:43"
        # Verify they are different (modification happened 1 hour after adding)
        assert result["99148"]["date_added"] != result["99148"]["last_modified"]

    def test_parses_last_modified_same_as_date_added(self) -> None:
        """Should correctly parse last_modified when same as date_added (never modified)."""
        raw_output = "1\tTrack\tArtist\tAlbum\tRock\t2024-01-01 12:00:00\t2024-01-01 12:00:00\tOK\t2020\t2021\tnew"

        result = _parse_osascript_output(raw_output)

        assert result["1"]["date_added"] == "2024-01-01 12:00:00"
        assert result["1"]["last_modified"] == "2024-01-01 12:00:00"

    def test_handles_empty_last_modified(self) -> None:
        """Should handle empty last_modified field gracefully."""
        raw_output = "1\tTrack\tArtist\tAlbum\tRock\t2024-01-01\t\tOK\t2020\t2021\tnew"

        result = _parse_osascript_output(raw_output)

        assert result["1"]["last_modified"] == ""
        assert result["1"]["date_added"] == "2024-01-01"

    def test_handles_missing_value_for_last_modified(self) -> None:
        """Should convert 'missing value' to empty string for last_modified."""
        raw_output = "1\tTrack\tArtist\tAlbum\tRock\t2024-01-01\tmissing value\tOK\t2020\t2021\tnew"

        result = _parse_osascript_output(raw_output)

        assert result["1"]["last_modified"] == ""
        assert result["1"]["date_added"] == "2024-01-01"

    def test_parses_multiple_tracks_with_different_modification_dates(self) -> None:
        """Should correctly parse multiple tracks with varying modification dates."""
        raw_output = (
            "1\tTrack1\tArtist\tAlbum\tRock\t2024-01-01\t2024-01-05\tOK\t2020\t2021\t\n"
            "2\tTrack2\tArtist\tAlbum\tRock\t2024-01-01\t2024-02-10\tOK\t2020\t2021\t\n"
            "3\tTrack3\tArtist\tAlbum\tRock\t2024-01-01\t2024-01-01\tOK\t2020\t2021\t"
        )

        result = _parse_osascript_output(raw_output)

        assert result["1"]["last_modified"] == "2024-01-05"
        assert result["2"]["last_modified"] == "2024-02-10"
        assert result["3"]["last_modified"] == "2024-01-01"


class TestStripPreservesTrailingTabs:
    """Tests for strip('\\n\\r') fix that preserves trailing tabs (empty fields)."""

    def test_preserves_trailing_empty_field(self) -> None:
        """Should preserve 12th empty field when line ends with tab."""
        # 12 fields with trailing tab for empty new_year
        raw_output = "1\tTrack\tArtist\tAlbumArtist\tAlbum\tRock\t2024-01-01\t2024-01-02\tOK\t2020\t2021\t"

        result = _parse_osascript_output(raw_output)

        assert "1" in result
        assert result["1"]["date_added"] == "2024-01-01"
        assert result["1"]["last_modified"] == "2024-01-02"

    def test_handles_multiple_trailing_empty_fields(self) -> None:
        """Should handle lines with multiple empty fields at end."""
        # 12 fields: year and new_year are empty
        raw_output = "1\tTrack\tArtist\tAlbumArtist\tAlbum\tRock\t2024-01-01\t2024-01-02\tOK\t\t\t"

        result = _parse_osascript_output(raw_output)

        assert "1" in result
        assert result["1"]["year"] == ""

    def test_strip_only_removes_newlines_not_tabs(self) -> None:
        """Should strip newlines/carriage returns but not tabs."""
        # Line wrapped in newlines - should be stripped without affecting tabs
        raw_output = "\n\r1\tTrack\tArtist\tAlbum\tRock\t2024-01-01\t2024-01-02\tOK\t2020\t2021\tnew\n\r"

        result = _parse_osascript_output(raw_output)

        assert "1" in result
        assert len(result) == 1

    def test_handles_chr29_separator_with_trailing_fields(self) -> None:
        """Should preserve trailing fields with chr(29) line separator."""
        sep = chr(30)
        line_sep = chr(29)
        # 12 fields with empty last field
        raw_output = (
            sep.join(["1", "Track", "Artist", "AlbumArtist", "Album", "Rock", "2024-01-01", "2024-01-02", "OK", "2020", "2021", ""]) + line_sep
        )

        result = _parse_osascript_output(raw_output)

        assert "1" in result
        assert result["1"]["last_modified"] == "2024-01-02"


class TestResolveFieldIndicesTyping:
    """Tests for _resolve_field_indices return type (4-tuple)."""

    def test_returns_4_tuple_for_12_fields(self) -> None:
        """Should return 4-element tuple for 12-field format."""
        result = _resolve_field_indices(12)

        assert result is not None
        assert len(result) == 4
        date_added_idx, mod_date_idx, status_idx, year_idx = result
        assert isinstance(date_added_idx, int)
        assert isinstance(mod_date_idx, int)
        assert isinstance(status_idx, int)
        assert isinstance(year_idx, int)

    def test_returns_4_tuple_for_11_fields(self) -> None:
        """Should return 4-element tuple for 11-field format."""
        result = _resolve_field_indices(11)

        assert result is not None
        assert len(result) == 4

    def test_modification_date_index_follows_date_added(self) -> None:
        """Should have modification_date index immediately after date_added."""
        result_12 = _resolve_field_indices(12)
        result_11 = _resolve_field_indices(11)

        assert result_12 is not None
        assert result_11 is not None

        # For 12 fields: date_added=6, mod_date=7
        assert result_12[1] == result_12[0] + 1

        # For 11 fields: date_added=5, mod_date=6
        assert result_11[1] == result_11[0] + 1

    def test_indices_are_consecutive_for_parsed_fields(self) -> None:
        """Should have consecutive indices for all 4 parsed fields."""
        result = _resolve_field_indices(12)

        assert result is not None
        date_added_idx, mod_date_idx, status_idx, year_idx = result

        # All 4 indices should be consecutive
        assert mod_date_idx == date_added_idx + 1
        assert status_idx == mod_date_idx + 1
        assert year_idx == status_idx + 1


class TestSanitizeApplescriptField:
    """Tests for _sanitize_applescript_field helper function."""

    def test_returns_empty_for_missing_value(self) -> None:
        """Should convert 'missing value' placeholder to empty string."""
        result = _sanitize_applescript_field(_MISSING_VALUE_PLACEHOLDER)

        assert result == ""

    def test_returns_value_unchanged_for_normal_string(self) -> None:
        """Should return normal strings unchanged."""
        result = _sanitize_applescript_field("2024-01-01 12:00:00")

        assert result == "2024-01-01 12:00:00"

    def test_returns_empty_string_unchanged(self) -> None:
        """Should return empty string unchanged."""
        result = _sanitize_applescript_field("")

        assert result == ""

    def test_case_sensitive_missing_value(self) -> None:
        """Should only match exact 'missing value' case."""
        result = _sanitize_applescript_field("Missing Value")

        assert result == "Missing Value"


class TestParseSingleTrackLine:
    """Tests for _parse_single_track_line helper function."""

    def test_parses_fields_with_12_field_indices(self) -> None:
        """Should parse fields correctly using 12-field indices."""
        fields = ["1", "Track", "Artist", "AlbumArtist", "Album", "Rock", "2024-01-01", "2024-01-02", "OK", "2020", "2021", ""]
        indices = (6, 7, 8, 9)

        result = _parse_single_track_line(fields, indices)

        assert result["date_added"] == "2024-01-01"
        assert result["last_modified"] == "2024-01-02"
        assert result["track_status"] == "OK"
        assert result["year"] == "2020"

    def test_parses_fields_with_11_field_indices(self) -> None:
        """Should parse fields correctly using 11-field indices."""
        fields = ["1", "Track", "Artist", "Album", "Rock", "2024-01-01", "2024-01-02", "OK", "2020", "2021", ""]
        indices = (5, 6, 7, 8)

        result = _parse_single_track_line(fields, indices)

        assert result["date_added"] == "2024-01-01"
        assert result["last_modified"] == "2024-01-02"
        assert result["track_status"] == "OK"
        assert result["year"] == "2020"

    def test_sanitizes_missing_value_in_all_fields(self) -> None:
        """Should sanitize 'missing value' placeholder in all fields."""
        fields = ["1", "Track", "Artist", "Album", "Rock", "missing value", "missing value", "missing value", "missing value", "", ""]
        indices = (5, 6, 7, 8)

        result = _parse_single_track_line(fields, indices)

        assert result["date_added"] == ""
        assert result["last_modified"] == ""
        assert result["track_status"] == ""
        assert result["year"] == ""

    def test_returns_typed_dict(self) -> None:
        """Should return properly typed ParsedTrackFields."""
        fields = ["1", "T", "A", "B", "R", "d", "m", "s", "y", "", ""]
        indices = (5, 6, 7, 8)

        result: ParsedTrackFields = _parse_single_track_line(fields, indices)

        assert isinstance(result, dict)
        assert set(result.keys()) == {"date_added", "last_modified", "track_status", "year"}


class TestHandleOsascriptError:
    """Tests for _handle_osascript_error function."""

    def test_logs_error_with_stderr(self, caplog: pytest.LogCaptureFixture) -> None:
        """Should log error message from stderr."""
        with caplog.at_level(logging.WARNING):
            _handle_osascript_error(1, b"stdout", b"Error message")

        assert "osascript failed" in caplog.text

    def test_handles_none_stderr(self, caplog: pytest.LogCaptureFixture) -> None:
        """Should handle None stderr."""
        with caplog.at_level(logging.WARNING):
            _handle_osascript_error(1, None, None)

        assert "No error message" in caplog.text or "osascript failed" in caplog.text


class TestUpdateTrackWithCachedFieldsForSync:
    """Tests for _update_track_with_cached_fields_for_sync function.

    Note: ParsedTrackFields uses 'year' key for Music.app's current year.
    This gets mapped to track.old_year (for new tracks) and track.year (for delta detection).
    """

    def test_updates_empty_fields_from_cache(self) -> None:
        """Should update empty fields from cache."""
        track = _create_test_track("6", date_added=None, track_status=None, year=None, old_year=None)
        tracks_cache: dict[str, ParsedTrackFields] = {
            "6": {
                "date_added": "2024-06-01",
                "last_modified": "2024-06-02",
                "track_status": "Playing",
                "year": "2018",  # Music.app's current year → populates track.old_year AND track.year
            }
        }

        _update_track_with_cached_fields_for_sync(track, tracks_cache)

        assert track.date_added == "2024-06-01"
        assert track.track_status == "Playing"
        assert track.year == "2018"  # Populated from cache
        assert track.old_year == "2018"  # Populated from cache (original value for rollback)

    def test_preserves_existing_fields(self) -> None:
        """Should not overwrite existing field values."""
        track = _create_test_track("6", date_added="2023-05-01", track_status="Paused", year="2016", old_year="2017")
        tracks_cache: dict[str, ParsedTrackFields] = {
            "6": {
                "date_added": "2024-06-01",
                "last_modified": "2024-06-02",
                "track_status": "Playing",
                "year": "2018",
            }
        }

        _update_track_with_cached_fields_for_sync(track, tracks_cache)

        assert track.date_added == "2023-05-01"
        assert track.track_status == "Paused"
        assert track.year == "2016"  # Preserved (not overwritten)
        assert track.old_year == "2017"  # Preserved (original value)

    def test_skips_track_not_in_cache(self) -> None:
        """Should skip tracks not in cache."""
        track = _create_test_track("7", date_added=None)
        tracks_cache: dict[str, ParsedTrackFields] = {}

        _update_track_with_cached_fields_for_sync(track, tracks_cache)

        assert track.date_added is None

    def test_skips_track_without_id(self) -> None:
        """Should skip tracks without id."""
        track = TrackDict(id="", name="Test", artist="Artist", album="Album")
        tracks_cache: dict[str, ParsedTrackFields] = {"": {"date_added": "2024-01-01", "last_modified": "", "track_status": "", "year": ""}}

        _update_track_with_cached_fields_for_sync(track, tracks_cache)


class TestConvertTrackToCsvDict:
    """Tests for _convert_track_to_csv_dict function."""

    def test_converts_track_to_dict(self) -> None:
        """Should convert TrackDict to CSV dictionary format."""
        track = _create_test_track(
            "8",
            name="My Song",
            artist="My Artist",
            album="My Album",
            genre="Electronic",
            year="2019",
            date_added="2024-03-15",
            track_status="Active",
            old_year="2018",
            new_year="2019",
        )

        result = _convert_track_to_csv_dict(track)

        assert result["id"] == "8"
        assert result["name"] == "My Song"
        assert result["artist"] == "My Artist"
        assert result["album"] == "My Album"
        assert result["genre"] == "Electronic"
        assert result["year"] == "2019"
        assert result["date_added"] == "2024-03-15"
        assert result["track_status"] == "Active"
        assert result["old_year"] == "2018"
        assert result["new_year"] == "2019"

    def test_converts_year_field_to_csv(self) -> None:
        """Should include year field in CSV dict (Issue #85 - delta detection)."""
        track = _create_test_track("10", old_year="2015", new_year="2020")

        result = _convert_track_to_csv_dict(track)

        assert result["year"] == "2020"
        assert result["old_year"] == "2015"
        assert result["new_year"] == "2020"

    def test_handles_none_values(self) -> None:
        """Should convert None values to empty strings."""
        track = _create_test_track("9", genre=None, year=None, date_added=None, old_year=None, new_year=None)

        result = _convert_track_to_csv_dict(track)

        assert result["genre"] == ""
        assert result["year"] == ""
        assert result["date_added"] == ""
        assert result["old_year"] == ""
        assert result["new_year"] == ""


class TestSaveTrackMapToCsv:
    """Tests for save_track_map_to_csv function."""

    def test_saves_sorted_tracks_to_csv(
        self,
        tmp_path: Path,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
    ) -> None:
        """Should save tracks sorted by ID to CSV."""
        track1 = _create_test_track("20", name="Second Track")
        track2 = _create_test_track("10", name="First Track")
        track_map = {"20": track1, "10": track2}
        csv_path = str(tmp_path / "output.csv")

        with patch("metrics.track_sync.save_csv") as mock_save:
            save_track_map_to_csv(track_map, csv_path, console_logger, error_logger)

            mock_save.assert_called_once()
            args = mock_save.call_args
            track_dicts = args[0][0]
            assert track_dicts[0]["id"] == "10"
            assert track_dicts[1]["id"] == "20"


class TestBuildMusicappTrackMap:
    """Tests for _build_musicapp_track_map async function."""

    @pytest.mark.asyncio
    async def test_builds_map_from_tracks(
        self,
        mock_cache_service: CacheServiceProtocol,
        error_logger: logging.Logger,
    ) -> None:
        """Should build map from track list."""
        from metrics.track_sync import _build_musicapp_track_map

        tracks = [_create_test_track("11", artist="Test Artist", album="Test Album")]
        processed_albums: dict[str, str] = {}

        result = await _build_musicapp_track_map(tracks, processed_albums, mock_cache_service, partial_sync=False, error_logger=error_logger)

        assert "11" in result
        assert result["11"].artist == "Test Artist"

    @pytest.mark.asyncio
    async def test_skips_tracks_without_id(
        self,
        mock_cache_service: CacheServiceProtocol,
        error_logger: logging.Logger,
    ) -> None:
        """Should skip tracks without id."""
        from metrics.track_sync import _build_musicapp_track_map

        track = TrackDict(id="", name="No ID", artist="Artist", album="Album")
        tracks = [track]
        processed_albums: dict[str, str] = {}

        result = await _build_musicapp_track_map(tracks, processed_albums, mock_cache_service, partial_sync=False, error_logger=error_logger)

        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_handles_partial_sync(
        self,
        mock_cache_service: CacheServiceProtocol,
        error_logger: logging.Logger,
    ) -> None:
        """Should handle partial sync with processed albums."""
        from metrics.track_sync import _build_musicapp_track_map

        tracks = [_create_test_track("12", artist="Test", album="Album2", new_year=None)]
        processed_albums = {"Test|Album2": "2023"}

        result = await _build_musicapp_track_map(tracks, processed_albums, mock_cache_service, partial_sync=True, error_logger=error_logger)

        assert "12" in result
        assert result["12"].new_year == "2023"


class TestHandlePartialSyncCache:
    """Tests for _handle_partial_sync_cache async function."""

    @pytest.mark.asyncio
    async def test_skips_when_album_not_processed(
        self,
        mock_cache_service: CacheServiceProtocol,
        error_logger: logging.Logger,
    ) -> None:
        """Should skip when album not in processed albums."""
        from metrics.track_sync import _handle_partial_sync_cache

        track = _create_test_track("13", new_year=None)
        processed_albums: dict[str, str] = {}

        await _handle_partial_sync_cache(track, processed_albums, mock_cache_service, "Artist|Album", "Artist", "Album", error_logger)

        assert track.new_year is None

    @pytest.mark.asyncio
    async def test_updates_year_from_processed_albums(
        self,
        mock_cache_service: CacheServiceProtocol,
        error_logger: logging.Logger,
    ) -> None:
        """Should update new_year from processed albums."""
        from metrics.track_sync import _handle_partial_sync_cache

        track = _create_test_track("14", new_year=None)
        processed_albums = {"Artist|Album": "2023"}
        mock_cache_service.get_album_year_from_cache = AsyncMock(return_value=None)

        await _handle_partial_sync_cache(track, processed_albums, mock_cache_service, "Artist|Album", "Artist", "Album", error_logger)

        assert track.new_year == "2023"
        cast(AsyncMock, mock_cache_service.store_album_year_in_cache).assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_cache_error(
        self,
        mock_cache_service: CacheServiceProtocol,
        error_logger: logging.Logger,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Should handle cache errors gracefully."""
        from metrics.track_sync import _handle_partial_sync_cache

        track = _create_test_track("15", new_year=None)
        processed_albums = {"Artist|Album": "2023"}
        mock_cache_service.get_album_year_from_cache = AsyncMock(side_effect=OSError("Cache error"))

        with caplog.at_level(logging.ERROR):
            await _handle_partial_sync_cache(track, processed_albums, mock_cache_service, "Artist|Album", "Artist", "Album", error_logger)

        assert track.new_year == "2023"


class TestExecuteOsascriptProcess:
    """Tests for _execute_osascript_process async function."""

    @pytest.mark.asyncio
    async def test_executes_command(self) -> None:
        """Should execute osascript command."""
        from metrics.track_sync import _execute_osascript_process

        with patch("metrics.track_sync.asyncio.create_subprocess_exec") as mock_exec:
            mock_process = AsyncMock()
            mock_process.returncode = 0
            mock_process.communicate = AsyncMock(return_value=(b"output", b""))
            mock_exec.return_value = mock_process

            returncode, stdout, stderr = await _execute_osascript_process(["osascript", "test.scpt"])

            assert returncode == 0
            assert stdout == b"output"


class TestFetchTrackFieldsDirect:
    """Tests for _fetch_track_fields_direct async function."""

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self) -> None:
        """Should return empty dict on subprocess error."""
        from metrics.track_sync import _fetch_track_fields_direct

        with patch("metrics.track_sync._execute_osascript_process", new_callable=AsyncMock) as mock_exec:
            mock_exec.side_effect = OSError("Failed")

            result = await _fetch_track_fields_direct("/path/to/script.scpt", None)

            assert result == {}

    @pytest.mark.asyncio
    async def test_parses_successful_output(self) -> None:
        """Should parse successful osascript output."""
        from metrics.track_sync import _fetch_track_fields_direct

        # 11 fields: id, name, artist, album, genre, date_added, mod_date, status, year, release_year, new_year
        output = "1\tTrack\tArtist\tAlbum\tRock\t2024-01-01\t2024-01-02\tOK\t2020\t2021\tnew"
        with patch("metrics.track_sync._execute_osascript_process", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = (0, output.encode(), b"")

            result = await _fetch_track_fields_direct("/path/to/script.scpt", None)

            assert "1" in result
            assert result["1"]["last_modified"] == "2024-01-02"


class TestFetchMissingTrackFieldsForSync:
    """Tests for _fetch_missing_track_fields_for_sync async function."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_missing_fields(
        self,
        console_logger: logging.Logger,
    ) -> None:
        """Should return empty dict when all fields present."""
        from metrics.track_sync import _fetch_missing_track_fields_for_sync

        tracks = [_create_test_track("16")]

        result = await _fetch_missing_track_fields_for_sync(tracks, None, console_logger)

        assert result == {}

    @pytest.mark.asyncio
    async def test_fetches_when_fields_missing(
        self,
        console_logger: logging.Logger,
    ) -> None:
        """Should fetch fields when missing."""
        from metrics.track_sync import _fetch_missing_track_fields_for_sync

        track = _create_test_track("17", date_added=None)
        tracks = [track]
        mock_client = MagicMock()
        mock_client.apple_scripts_dir = "/path/to/scripts"

        with patch("metrics.track_sync._fetch_track_fields_direct", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = {"17": {"date_added": "2024-01-01"}}

            result = await _fetch_missing_track_fields_for_sync(tracks, mock_client, console_logger)

            assert "17" in result


class TestSyncTrackListWithCurrent:
    """Tests for sync_track_list_with_current async function."""

    @pytest.mark.asyncio
    async def test_syncs_tracks_to_csv(
        self,
        tmp_path: Path,
        mock_cache_service: CacheServiceProtocol,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
    ) -> None:
        """Should sync tracks to CSV file."""
        from metrics.track_sync import sync_track_list_with_current

        tracks = [_create_test_track("18", artist="Sync Artist", album="Sync Album")]
        csv_path = str(tmp_path / "sync_test.csv")

        with patch("metrics.track_sync.save_csv"):
            await sync_track_list_with_current(tracks, csv_path, mock_cache_service, console_logger, error_logger)

    @pytest.mark.asyncio
    async def test_removes_deleted_tracks(
        self,
        tmp_path: Path,
        mock_cache_service: CacheServiceProtocol,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
    ) -> None:
        """Should remove tracks that no longer exist."""
        from metrics.track_sync import sync_track_list_with_current

        csv_file = tmp_path / "tracks.csv"
        csv_file.write_text(
            "id,name,artist,album,genre,date_added,last_modified,track_status,old_year,new_year\n"
            "old_id,Old Track,Artist,Album,Rock,2024-01-01,,OK,2020,2021\n"
        )
        tracks = [_create_test_track("19", artist="New Artist")]

        with patch("metrics.track_sync.save_csv"):
            await sync_track_list_with_current(tracks, str(csv_file), mock_cache_service, console_logger, error_logger)
