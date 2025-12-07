"""Unit tests for track_sync module.

Note: This module tests internal functions that are prefixed with underscore.
Testing private functions is intentional to ensure correctness of internal logic.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.models.types import TrackDict
from metrics.track_sync import (
    _FIELD_COUNT_WITH_ALBUM_ARTIST,
    _FIELD_COUNT_WITHOUT_ALBUM_ARTIST,
    _build_osascript_command,
    _check_if_track_needs_update,
    _convert_track_to_csv_dict,
    _create_normalized_track_dict,
    _create_track_from_row,
    _get_fields_to_check,
    _get_processed_albums_from_csv,
    _handle_osascript_error,
    _merge_current_into_csv,
    _normalize_track_year_fields,
    _parse_osascript_output,
    _resolve_field_indices,
    _update_existing_track_fields,
    _update_track_with_cached_fields_for_sync,
    _validate_csv_header,
    load_track_list,
    save_track_map_to_csv,
)


def _create_test_track(
    track_id: str = "1",
    *,
    name: str = "Track",
    artist: str = "Artist",
    album: str = "Album",
    genre: str | None = "Rock",
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
def mock_cache_service() -> MagicMock:
    """Create mock cache service."""
    service = MagicMock()
    service.generate_album_key = MagicMock(side_effect=lambda a, b: f"{a}|{b}")
    service.get_album_year_from_cache = AsyncMock(return_value=None)
    service.store_album_year_in_cache = AsyncMock()
    return service


class TestFieldCountConstants:
    """Tests for field count constants."""

    def test_field_count_with_album_artist(self) -> None:
        """Should have correct value for format with album artist."""
        assert _FIELD_COUNT_WITH_ALBUM_ARTIST == 11

    def test_field_count_without_album_artist(self) -> None:
        """Should have correct value for format without album artist."""
        assert _FIELD_COUNT_WITHOUT_ALBUM_ARTIST == 10


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

    def test_returns_empty_when_no_tracks(self, mock_cache_service: MagicMock) -> None:
        """Should return empty dict when no tracks."""
        result = _get_processed_albums_from_csv({}, mock_cache_service)

        assert result == {}

    def test_returns_processed_albums(self, mock_cache_service: MagicMock) -> None:
        """Should return mapping of album keys to new years."""
        track = _create_test_track("100", new_year="2022")
        csv_map = {"100": track}

        result = _get_processed_albums_from_csv(csv_map, mock_cache_service)

        assert "Artist|Album" in result
        assert result["Artist|Album"] == "2022"

    def test_skips_tracks_without_new_year(self, mock_cache_service: MagicMock) -> None:
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


class TestGetFieldsToCheck:
    """Tests for _get_fields_to_check function."""

    def test_returns_expected_fields(self) -> None:
        """Should return list of fields to check during merge."""
        fields = _get_fields_to_check()

        assert "name" in fields
        assert "artist" in fields
        assert "album" in fields
        assert "genre" in fields
        assert "old_year" in fields
        assert "new_year" in fields


class TestCheckIfTrackNeedsUpdate:
    """Tests for _check_if_track_needs_update function."""

    def test_returns_false_when_identical(self) -> None:
        """Should return False when tracks are identical."""
        old_track = _create_test_track("2", genre="Jazz")
        new_track = _create_test_track("2", genre="Jazz")
        fields = ["genre"]

        result = _check_if_track_needs_update(old_track, new_track, fields)

        assert result is False

    def test_returns_true_when_different(self) -> None:
        """Should return True when tracks differ."""
        old_track = _create_test_track("2", genre="Jazz")
        new_track = _create_test_track("2", genre="Blues")
        fields = ["genre"]

        result = _check_if_track_needs_update(old_track, new_track, fields)

        assert result is True


class TestUpdateExistingTrackFields:
    """Tests for _update_existing_track_fields function."""

    def test_updates_fields(self) -> None:
        """Should update fields from new to old track."""
        old_track = _create_test_track("3", genre="Jazz")
        new_track = _create_test_track("3", genre="Blues")
        fields = ["genre"]

        _update_existing_track_fields(old_track, new_track, fields)

        assert old_track.genre == "Blues"

    def test_skips_none_values(self) -> None:
        """Should not update fields with None values."""
        old_track = _create_test_track("3", genre="Jazz")
        new_track = _create_test_track("3", genre=None)
        fields = ["genre"]

        _update_existing_track_fields(old_track, new_track, fields)

        assert old_track.genre == "Jazz"

    def test_logs_warning_for_missing_field(self, caplog: pytest.LogCaptureFixture) -> None:
        """Should log warning when field doesn't exist."""
        old_track = _create_test_track("3")
        new_track = _create_test_track("3")
        fields = ["nonexistent_field"]

        with caplog.at_level(logging.WARNING):
            _update_existing_track_fields(old_track, new_track, fields)

        assert "does not exist" in caplog.text


class TestMergeCurrentIntoCsv:
    """Tests for _merge_current_into_csv function."""

    def test_adds_new_tracks(self) -> None:
        """Should add new tracks to CSV map."""
        track = _create_test_track("4")
        current_map = {"4": track}
        csv_map: dict[str, TrackDict] = {}

        updated = _merge_current_into_csv(current_map, csv_map)

        assert updated == 1
        assert "4" in csv_map

    def test_updates_existing_tracks(self) -> None:
        """Should update existing tracks when fields differ."""
        old_track = _create_test_track("4", genre="Country")
        new_track = _create_test_track("4", genre="Folk")
        csv_map = {"4": old_track}
        current_map = {"4": new_track}

        updated = _merge_current_into_csv(current_map, csv_map)

        assert updated == 1
        assert csv_map["4"].genre == "Folk"

    def test_skips_identical_tracks(self) -> None:
        """Should not count identical tracks as updated."""
        track = _create_test_track("5", genre="Classical")
        csv_map = {"5": track}
        current_map = {"5": _create_test_track("5", genre="Classical")}

        updated = _merge_current_into_csv(current_map, csv_map)

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

    def test_returns_indices_for_11_fields(self) -> None:
        """Should return correct indices for 11-field format."""
        result = _resolve_field_indices(11)

        assert result == (6, 7, 8)

    def test_returns_indices_for_10_fields(self) -> None:
        """Should return correct indices for 10-field format."""
        result = _resolve_field_indices(10)

        assert result == (5, 6, 7)

    def test_returns_none_for_unexpected_count(self) -> None:
        """Should return None for unexpected field count."""
        result = _resolve_field_indices(9)

        assert result is None


class TestParseOsascriptOutput:
    """Tests for _parse_osascript_output function."""

    def test_parses_tab_separated_output(self) -> None:
        """Should parse tab-separated output."""
        raw_output = "1\tTrack 1\tArtist\tAlbum\tRock\t2024-01-01\tOK\t2020\t2021\tnew_value"

        result = _parse_osascript_output(raw_output)

        assert "1" in result
        assert result["1"]["date_added"] == "2024-01-01"
        assert result["1"]["track_status"] == "OK"
        assert result["1"]["old_year"] == "2020"

    def test_parses_chr30_separated_output(self) -> None:
        """Should parse chr(30) field-separated output."""
        sep = chr(30)
        line_sep = chr(29)
        raw_output = sep.join(["1", "Track", "Artist", "Album", "Rock", "2024-01-01", "OK", "2020", "2021", "new"]) + line_sep

        result = _parse_osascript_output(raw_output)

        assert "1" in result

    def test_handles_missing_value_placeholder(self) -> None:
        """Should handle 'missing value' placeholder."""
        raw_output = "1\tTrack\tArtist\tAlbum\tRock\tmissing value\tmissing value\tmissing value\t2021\tnew"

        result = _parse_osascript_output(raw_output)

        assert result["1"]["date_added"] == ""
        assert result["1"]["track_status"] == ""
        assert result["1"]["old_year"] == ""

    def test_skips_empty_lines(self) -> None:
        """Should skip empty lines."""
        raw_output = "\n1\tTrack\tArtist\tAlbum\tRock\t2024\tOK\t2020\t2021\tnew\n\n"

        result = _parse_osascript_output(raw_output)

        assert len(result) == 1

    def test_logs_warning_for_invalid_field_count(self, caplog: pytest.LogCaptureFixture) -> None:
        """Should log warning for lines with unexpected field count."""
        raw_output = "1\tTrack\tArtist"

        with caplog.at_level(logging.WARNING):
            result = _parse_osascript_output(raw_output)

        assert "expected 10 or 11" in caplog.text
        assert len(result) == 0


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
    """Tests for _update_track_with_cached_fields_for_sync function."""

    def test_updates_empty_fields_from_cache(self) -> None:
        """Should update empty fields from cache."""
        track = _create_test_track("6", date_added=None, track_status=None, old_year=None)
        tracks_cache = {
            "6": {
                "date_added": "2024-06-01",
                "track_status": "Playing",
                "old_year": "2018",
            }
        }

        _update_track_with_cached_fields_for_sync(track, tracks_cache)

        assert track.date_added == "2024-06-01"
        assert track.track_status == "Playing"
        assert track.old_year == "2018"

    def test_preserves_existing_fields(self) -> None:
        """Should not overwrite existing field values."""
        track = _create_test_track("6", date_added="2023-05-01", track_status="Paused", old_year="2017")
        tracks_cache = {
            "6": {
                "date_added": "2024-06-01",
                "track_status": "Playing",
                "old_year": "2018",
            }
        }

        _update_track_with_cached_fields_for_sync(track, tracks_cache)

        assert track.date_added == "2023-05-01"
        assert track.track_status == "Paused"
        assert track.old_year == "2017"

    def test_skips_track_not_in_cache(self) -> None:
        """Should skip tracks not in cache."""
        track = _create_test_track("7", date_added=None)
        tracks_cache: dict[str, dict[str, str]] = {}

        _update_track_with_cached_fields_for_sync(track, tracks_cache)

        assert track.date_added is None

    def test_skips_track_without_id(self) -> None:
        """Should skip tracks without id."""
        track = TrackDict(id="", name="Test", artist="Artist", album="Album")
        tracks_cache = {"": {"date_added": "2024-01-01"}}

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
        assert result["date_added"] == "2024-03-15"
        assert result["track_status"] == "Active"
        assert result["old_year"] == "2018"
        assert result["new_year"] == "2019"

    def test_handles_none_values(self) -> None:
        """Should convert None values to empty strings."""
        track = _create_test_track("9", genre=None, date_added=None, old_year=None, new_year=None)

        result = _convert_track_to_csv_dict(track)

        assert result["genre"] == ""
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


class TestBuildCurrentMap:
    """Tests for _build_current_map async function."""

    @pytest.mark.asyncio
    async def test_builds_map_from_tracks(
        self,
        mock_cache_service: MagicMock,
        error_logger: logging.Logger,
    ) -> None:
        """Should build map from track list."""
        from metrics.track_sync import _build_current_map

        tracks = [_create_test_track("11", artist="Test Artist", album="Test Album")]
        processed_albums: dict[str, str] = {}

        result = await _build_current_map(tracks, processed_albums, mock_cache_service, partial_sync=False, error_logger=error_logger)

        assert "11" in result
        assert result["11"].artist == "Test Artist"

    @pytest.mark.asyncio
    async def test_skips_tracks_without_id(
        self,
        mock_cache_service: MagicMock,
        error_logger: logging.Logger,
    ) -> None:
        """Should skip tracks without id."""
        from metrics.track_sync import _build_current_map

        track = TrackDict(id="", name="No ID", artist="Artist", album="Album")
        tracks = [track]
        processed_albums: dict[str, str] = {}

        result = await _build_current_map(tracks, processed_albums, mock_cache_service, partial_sync=False, error_logger=error_logger)

        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_handles_partial_sync(
        self,
        mock_cache_service: MagicMock,
        error_logger: logging.Logger,
    ) -> None:
        """Should handle partial sync with processed albums."""
        from metrics.track_sync import _build_current_map

        tracks = [_create_test_track("12", artist="Test", album="Album2", new_year=None)]
        processed_albums = {"Test|Album2": "2023"}

        result = await _build_current_map(tracks, processed_albums, mock_cache_service, partial_sync=True, error_logger=error_logger)

        assert "12" in result
        assert result["12"].new_year == "2023"


class TestHandlePartialSyncCache:
    """Tests for _handle_partial_sync_cache async function."""

    @pytest.mark.asyncio
    async def test_skips_when_album_not_processed(
        self,
        mock_cache_service: MagicMock,
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
        mock_cache_service: MagicMock,
        error_logger: logging.Logger,
    ) -> None:
        """Should update new_year from processed albums."""
        from metrics.track_sync import _handle_partial_sync_cache

        track = _create_test_track("14", new_year=None)
        processed_albums = {"Artist|Album": "2023"}
        mock_cache_service.get_album_year_from_cache = AsyncMock(return_value=None)

        await _handle_partial_sync_cache(track, processed_albums, mock_cache_service, "Artist|Album", "Artist", "Album", error_logger)

        assert track.new_year == "2023"
        mock_cache_service.store_album_year_in_cache.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_cache_error(
        self,
        mock_cache_service: MagicMock,
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

        output = "1\tTrack\tArtist\tAlbum\tRock\t2024-01-01\tOK\t2020\t2021\tnew"
        with patch("metrics.track_sync._execute_osascript_process", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = (0, output.encode(), b"")

            result = await _fetch_track_fields_direct("/path/to/script.scpt", None)

            assert "1" in result


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
        mock_cache_service: MagicMock,
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
        mock_cache_service: MagicMock,
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
