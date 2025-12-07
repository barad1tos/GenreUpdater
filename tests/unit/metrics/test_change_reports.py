"""Tests for src/metrics/change_reports.py."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from core.models.track_models import ChangeLogEntry
from metrics.change_reports import (
    ChangeType,
    Color,
    Format,
    Key,
    Misc,
    _add_timestamp_to_filename,
    _convert_changelog_to_dict,
    _determine_change_type,
    _determine_if_changed,
    _get_change_type_color,
    _get_csv_fieldnames,
    _group_changes_by_type,
    _is_real_change,
    _map_genre_field_values,
    _map_year_field_values,
    _normalize_field_mappings,
    _sort_changes_by_artist_album,
    save_changes_report,
    save_unified_changes_report,
)


@pytest.fixture
def console_logger() -> MagicMock:
    """Create a mock console logger."""
    return MagicMock()


@pytest.fixture
def error_logger() -> MagicMock:
    """Create a mock error logger."""
    return MagicMock()


class TestColorConstants:
    """Tests for Color class constants."""

    def test_color_red(self) -> None:
        """Verify RED ANSI code."""
        assert Color.RED == "\033[31m"

    def test_color_yellow(self) -> None:
        """Verify YELLOW ANSI code."""
        assert Color.YELLOW == "\033[33m"

    def test_color_green(self) -> None:
        """Verify GREEN ANSI code."""
        assert Color.GREEN == "\033[32m"

    def test_color_reset(self) -> None:
        """Verify RESET ANSI code."""
        assert Color.RESET == "\033[0m"


class TestChangeTypeConstants:
    """Tests for ChangeType class constants."""

    def test_change_type_genre(self) -> None:
        """Verify GENRE constant."""
        assert ChangeType.GENRE == "genre"

    def test_change_type_year(self) -> None:
        """Verify YEAR constant."""
        assert ChangeType.YEAR == "year"

    def test_change_type_name(self) -> None:
        """Verify NAME constant."""
        assert ChangeType.NAME == "name"

    def test_change_type_other(self) -> None:
        """Verify OTHER constant."""
        assert ChangeType.OTHER == "other"


class TestKeyConstants:
    """Tests for Key class constants."""

    def test_key_change_type(self) -> None:
        """Verify CHANGE_TYPE constant."""
        assert Key.CHANGE_TYPE == "change_type"

    def test_key_artist(self) -> None:
        """Verify ARTIST constant."""
        assert Key.ARTIST == "artist"

    def test_key_album(self) -> None:
        """Verify ALBUM constant."""
        assert Key.ALBUM == "album"

    def test_key_track_name(self) -> None:
        """Verify TRACK_NAME constant."""
        assert Key.TRACK_NAME == "track_name"

    def test_key_old_genre(self) -> None:
        """Verify OLD_GENRE constant."""
        assert Key.OLD_GENRE == "old_genre"

    def test_key_new_genre(self) -> None:
        """Verify NEW_GENRE constant."""
        assert Key.NEW_GENRE == "new_genre"

    def test_key_old_year(self) -> None:
        """Verify OLD_YEAR constant."""
        assert Key.OLD_YEAR == "old_year"

    def test_key_new_year(self) -> None:
        """Verify NEW_YEAR constant."""
        assert Key.NEW_YEAR == "new_year"

    def test_key_timestamp(self) -> None:
        """Verify TIMESTAMP constant."""
        assert Key.TIMESTAMP == "timestamp"


class TestFormatConstants:
    """Tests for Format class constants."""

    def test_col_widths(self) -> None:
        """Verify column width constants."""
        assert Format.COL_WIDTH_30 == 30
        assert Format.COL_WIDTH_40 == 40
        assert Format.COL_WIDTH_38 == 38
        assert Format.COL_WIDTH_10 == 10

    def test_separator_widths(self) -> None:
        """Verify separator width constants."""
        assert Format.SEPARATOR_80 == 80
        assert Format.SEPARATOR_100 == 100

    def test_format_strings(self) -> None:
        """Verify format string constants."""
        assert Format.TRUNCATE_SUFFIX == ".."
        assert Format.ARROW == "→"
        assert Format.HEADER_OLD_NEW == "Old → New"


class TestMiscConstants:
    """Tests for Misc class constants."""

    def test_misc_report_type(self) -> None:
        """Verify CHANGES_REPORT_TYPE constant."""
        assert Misc.CHANGES_REPORT_TYPE == "changes report"

    def test_misc_unknown_values(self) -> None:
        """Verify UNKNOWN constants."""
        assert Misc.UNKNOWN == "Unknown"
        assert Misc.UNKNOWN_ARTIST == "Unknown Artist"
        assert Misc.UNKNOWN_ALBUM == "Unknown Album"
        assert Misc.UNKNOWN_TRACK == "Unknown Track"


class TestIsRealChange:
    """Tests for _is_real_change function."""

    def test_genre_update_with_real_change(self) -> None:
        """Test genre update with different values."""
        change = {
            "change_type": "genre_update",
            "old_genre": "Rock",
            "new_genre": "Metal",
        }
        assert _is_real_change(change) is True

    def test_genre_update_no_change(self) -> None:
        """Test genre update with same values."""
        change = {
            "change_type": "genre_update",
            "old_genre": "Rock",
            "new_genre": "Rock",
        }
        assert _is_real_change(change) is False

    def test_year_update_with_real_change(self) -> None:
        """Test year update with different values."""
        change = {
            "change_type": "year_update",
            "old_year": "2020",
            "new_year": "2021",
        }
        assert _is_real_change(change) is True

    def test_year_update_no_change(self) -> None:
        """Test year update with same values."""
        change = {
            "change_type": "year_update",
            "old_year": "2020",
            "new_year": "2020",
        }
        assert _is_real_change(change) is False

    def test_name_clean_with_real_change(self) -> None:
        """Test name clean with different values."""
        change = {
            "change_type": "name_clean",
            "old_track_name": "Song (Remastered)",
            "new_track_name": "Song",
        }
        assert _is_real_change(change) is True

    def test_name_clean_no_change(self) -> None:
        """Test name clean with same values."""
        change = {
            "change_type": "name_clean",
            "old_track_name": "Song",
            "new_track_name": "Song",
        }
        assert _is_real_change(change) is False

    def test_metadata_cleaning_album_changed(self) -> None:
        """Test metadata cleaning with album change."""
        change = {
            "change_type": "metadata_cleaning",
            "old_album_name": "Album (Deluxe)",
            "new_album_name": "Album",
            "old_track_name": "Track",
            "new_track_name": "Track",
        }
        assert _is_real_change(change) is True

    def test_metadata_cleaning_track_changed(self) -> None:
        """Test metadata cleaning with track change."""
        change = {
            "change_type": "metadata_cleaning",
            "old_album_name": "Album",
            "new_album_name": "Album",
            "old_track_name": "Track (Live)",
            "new_track_name": "Track",
        }
        assert _is_real_change(change) is True

    def test_metadata_cleaning_no_change(self) -> None:
        """Test metadata cleaning with no changes."""
        change = {
            "change_type": "metadata_cleaning",
            "old_album_name": "Album",
            "new_album_name": "Album",
            "old_track_name": "Track",
            "new_track_name": "Track",
        }
        assert _is_real_change(change) is False

    def test_unknown_type_defaults_to_true(self) -> None:
        """Test unknown change type defaults to True."""
        change = {"change_type": "unknown_type"}
        assert _is_real_change(change) is True

    def test_unknown_type_logs_warning(self) -> None:
        """Test unknown change type logs a warning."""
        logger = MagicMock()
        change = {"change_type": "custom_type"}
        _is_real_change(change, logger)
        logger.warning.assert_called_once()


class TestDetermineChangeType:
    """Tests for _determine_change_type function."""

    def test_genre_from_new_genre_field(self) -> None:
        """Test genre detection from new_genre field."""
        change = {"new_genre": "Rock"}
        assert _determine_change_type(change) == "genre"

    def test_genre_from_field_attribute(self) -> None:
        """Test genre detection from field attribute."""
        change = {"field": "genre"}
        assert _determine_change_type(change) == "genre"

    def test_year_from_new_year_field(self) -> None:
        """Test year detection from new_year field."""
        change = {"new_year": "2020"}
        assert _determine_change_type(change) == "year"

    def test_year_from_field_attribute(self) -> None:
        """Test year detection from field attribute."""
        change = {"field": "year"}
        assert _determine_change_type(change) == "year"

    def test_name_from_new_track_name(self) -> None:
        """Test name detection from new_track_name field."""
        change = {"new_track_name": "New Song"}
        assert _determine_change_type(change) == "name"

    def test_name_from_new_album_name(self) -> None:
        """Test name detection from new_album_name field."""
        change = {"new_album_name": "New Album"}
        assert _determine_change_type(change) == "name"

    def test_name_from_field_attribute(self) -> None:
        """Test name detection from field attribute."""
        change = {"field": "name"}
        assert _determine_change_type(change) == "name"

    def test_other_for_unknown(self) -> None:
        """Test other for unknown change type."""
        change = {"some_field": "value"}
        assert _determine_change_type(change) == "other"

    def test_empty_new_genre_not_detected(self) -> None:
        """Test that empty new_genre is not detected as genre change."""
        change = {"new_genre": ""}
        assert _determine_change_type(change) == "other"


class TestGetChangeTypeColor:
    """Tests for _get_change_type_color function."""

    def test_genre_update_color(self) -> None:
        """Test color for genre update."""
        assert _get_change_type_color("genre_update") == "cyan"

    def test_year_update_color(self) -> None:
        """Test color for year update."""
        assert _get_change_type_color("year_update") == "green"

    def test_name_change_color(self) -> None:
        """Test color for name change."""
        assert _get_change_type_color("name_change") == "magenta"

    def test_other_color(self) -> None:
        """Test color for other change type."""
        assert _get_change_type_color("other") == "white"

    def test_unknown_type_defaults_to_white(self) -> None:
        """Test unknown type defaults to white."""
        assert _get_change_type_color("nonexistent") == "white"


class TestSortChangesByArtistAlbum:
    """Tests for _sort_changes_by_artist_album function."""

    def test_sort_by_artist_then_album(self) -> None:
        """Test sorting by artist first, then album."""
        changes: list[dict[str, Any]] = [
            {"artist": "Zebra", "album": "Album A"},
            {"artist": "Alpha", "album": "Album Z"},
            {"artist": "Alpha", "album": "Album A"},
        ]
        sorted_changes = _sort_changes_by_artist_album(changes)
        assert sorted_changes[0]["artist"] == "Alpha"
        assert sorted_changes[0]["album"] == "Album A"
        assert sorted_changes[1]["artist"] == "Alpha"
        assert sorted_changes[1]["album"] == "Album Z"
        assert sorted_changes[2]["artist"] == "Zebra"

    def test_sort_with_missing_fields(self) -> None:
        """Test sorting handles missing fields gracefully."""
        changes: list[dict[str, Any]] = [
            {"artist": "Beta"},
            {"album": "Album"},
            {"artist": "Alpha", "album": "Album"},
        ]
        sorted_changes = _sort_changes_by_artist_album(changes)
        # Alpha comes first, then Beta, then Unknown (missing artist)
        assert sorted_changes[0]["artist"] == "Alpha"
        assert sorted_changes[1]["artist"] == "Beta"

    def test_sort_empty_list(self) -> None:
        """Test sorting empty list."""
        result = _sort_changes_by_artist_album([])
        assert result == []


class TestGroupChangesByType:
    """Tests for _group_changes_by_type function."""

    def test_group_by_change_type(self) -> None:
        """Test grouping changes by type."""
        changes: list[dict[str, Any]] = [
            {"change_type": "genre_update", "artist": "A"},
            {"change_type": "year_update", "artist": "B"},
            {"change_type": "genre_update", "artist": "C"},
        ]
        grouped = _group_changes_by_type(changes)
        assert len(grouped["genre_update"]) == 2
        assert len(grouped["year_update"]) == 1

    def test_group_missing_type_uses_default(self) -> None:
        """Test missing change_type uses default OTHER."""
        changes: list[dict[str, Any]] = [
            {"artist": "A"},
            {"change_type": "genre_update", "artist": "B"},
        ]
        grouped = _group_changes_by_type(changes)
        assert ChangeType.OTHER in grouped
        assert len(grouped[ChangeType.OTHER]) == 1


class TestAddTimestampToFilename:
    """Tests for _add_timestamp_to_filename function."""

    def test_adds_timestamp(self) -> None:
        """Test timestamp is added to filename."""
        result = _add_timestamp_to_filename("/path/to/report.csv")
        assert result is not None
        assert result.startswith("/path/to/report_")
        assert result.endswith(".csv")

    def test_none_input_returns_none(self) -> None:
        """Test None input returns None."""
        result = _add_timestamp_to_filename(None)
        assert result is None

    def test_preserves_extension(self) -> None:
        """Test file extension is preserved."""
        result = _add_timestamp_to_filename("/path/to/file.html")
        assert result is not None
        assert result.endswith(".html")

    def test_timestamp_format(self) -> None:
        """Test timestamp follows expected format (YYYYMMDD_HHMMSS)."""
        import re

        result = _add_timestamp_to_filename("/path/report.csv")
        assert result is not None
        # Verify format: /path/report_YYYYMMDD_HHMMSS.csv
        pattern = r"/path/report_\d{8}_\d{6}\.csv"
        assert re.match(pattern, result) is not None


class TestDetermineIfChanged:
    """Tests for _determine_if_changed function."""

    def test_year_changed(self) -> None:
        """Test year change detection."""
        record = {Key.OLD_YEAR: "2020", Key.NEW_YEAR: "2021"}
        assert _determine_if_changed("new_year", "2021", record) is True

    def test_year_not_changed(self) -> None:
        """Test year not changed."""
        record = {Key.OLD_YEAR: "2020", Key.NEW_YEAR: "2020"}
        assert _determine_if_changed("new_year", "2020", record) is False

    def test_genre_changed(self) -> None:
        """Test genre change detection."""
        record = {Key.OLD_GENRE: "Rock", Key.NEW_GENRE: "Metal"}
        assert _determine_if_changed("new_genre", "Metal", record) is True

    def test_genre_not_changed(self) -> None:
        """Test genre not changed."""
        record = {Key.OLD_GENRE: "Rock", Key.NEW_GENRE: "Rock"}
        assert _determine_if_changed("new_genre", "Rock", record) is False

    def test_track_name_changed(self) -> None:
        """Test track name change detection."""
        record = {Key.OLD_TRACK_NAME: "Old Song", Key.NEW_TRACK_NAME: "New Song"}
        assert _determine_if_changed("new_track_name", "New Song", record) is True

    def test_non_new_header_returns_false(self) -> None:
        """Test non-'new' header returns False."""
        record = {Key.OLD_GENRE: "Rock"}
        assert _determine_if_changed("old_genre", "Rock", record) is False

    def test_empty_value_returns_false(self) -> None:
        """Test empty value returns False."""
        record = {Key.OLD_YEAR: "2020"}
        assert _determine_if_changed("new_year", "", record) is False


class TestMapGenreFieldValues:
    """Tests for _map_genre_field_values function."""

    def test_maps_old_value_to_old_genre(self) -> None:
        """Test old_value is mapped to old_genre."""
        change: dict[str, Any] = {"old_value": "Rock"}
        _map_genre_field_values(change)
        assert change["old_genre"] == "Rock"

    def test_maps_new_value_to_new_genre(self) -> None:
        """Test new_value is mapped to new_genre."""
        change: dict[str, Any] = {"new_value": "Metal"}
        _map_genre_field_values(change)
        assert change["new_genre"] == "Metal"

    def test_does_not_override_existing_old_genre(self) -> None:
        """Test existing old_genre is not overridden."""
        change: dict[str, Any] = {"old_value": "Rock", "old_genre": "Pop"}
        _map_genre_field_values(change)
        assert change["old_genre"] == "Pop"

    def test_does_not_override_existing_new_genre(self) -> None:
        """Test existing new_genre is not overridden."""
        change: dict[str, Any] = {"new_value": "Metal", "new_genre": "Jazz"}
        _map_genre_field_values(change)
        assert change["new_genre"] == "Jazz"


class TestMapYearFieldValues:
    """Tests for _map_year_field_values function."""

    def test_maps_old_value_to_old_year(self) -> None:
        """Test old_value is mapped to old_year."""
        change: dict[str, Any] = {"old_value": "2020"}
        _map_year_field_values(change)
        assert change["old_year"] == "2020"

    def test_maps_new_value_to_new_year(self) -> None:
        """Test new_value is mapped to new_year."""
        change: dict[str, Any] = {"new_value": "2021"}
        _map_year_field_values(change)
        assert change["new_year"] == "2021"

    def test_does_not_override_existing_old_year(self) -> None:
        """Test existing old_year is not overridden."""
        change: dict[str, Any] = {"old_value": "2020", "old_year": "2019"}
        _map_year_field_values(change)
        assert change["old_year"] == "2019"


class TestNormalizeFieldMappings:
    """Tests for _normalize_field_mappings function."""

    def test_genre_field_mapping(self) -> None:
        """Test genre field triggers genre mapping."""
        change: dict[str, Any] = {"field": "genre", "old_value": "Rock", "new_value": "Metal"}
        _normalize_field_mappings(change)
        assert change["old_genre"] == "Rock"
        assert change["new_genre"] == "Metal"

    def test_year_field_mapping(self) -> None:
        """Test year field triggers year mapping."""
        change: dict[str, Any] = {"field": "year", "old_value": "2020", "new_value": "2021"}
        _normalize_field_mappings(change)
        assert change["old_year"] == "2020"
        assert change["new_year"] == "2021"

    def test_name_to_track_name_mapping(self) -> None:
        """Test name is mapped to track_name."""
        change: dict[str, Any] = {"name": "Test Song"}
        _normalize_field_mappings(change)
        assert change["track_name"] == "Test Song"

    def test_no_mapping_without_field(self) -> None:
        """Test no mapping happens without field attribute."""
        change: dict[str, Any] = {"old_value": "Value"}
        _normalize_field_mappings(change)
        assert "old_genre" not in change
        assert "old_year" not in change


class TestConvertChangelogToDict:
    """Tests for _convert_changelog_to_dict function."""

    def test_dict_input_returns_same(self) -> None:
        """Test dict input returns the same dict."""
        change = {"artist": "Test", "album": "Album"}
        result = _convert_changelog_to_dict(change)
        assert result is change

    def test_changelog_entry_converted(self) -> None:
        """Test ChangeLogEntry is converted to dict."""
        entry = ChangeLogEntry(
            timestamp="2024-01-15T10:30:00",
            change_type="genre_update",
            track_id="123",
            artist="Test Artist",
            track_name="Test Song",
            album_name="Test Album",
            old_genre="Rock",
            new_genre="Metal",
        )
        result = _convert_changelog_to_dict(entry)
        assert isinstance(result, dict)
        assert result["artist"] == "Test Artist"
        assert result["track_name"] == "Test Song"
        assert result["old_genre"] == "Rock"
        assert result["new_genre"] == "Metal"

    def test_album_key_added_for_compatibility(self) -> None:
        """Test album key is added from album_name for compatibility."""
        entry = ChangeLogEntry(
            timestamp="2024-01-15T10:30:00",
            change_type="genre_update",
            track_id="123",
            artist="Test Artist",
            album_name="Test Album",
        )
        result = _convert_changelog_to_dict(entry)
        assert result["album"] == "Test Album"


class TestGetCsvFieldnames:
    """Tests for _get_csv_fieldnames function."""

    def test_returns_list(self) -> None:
        """Test returns a list."""
        result = _get_csv_fieldnames()
        assert isinstance(result, list)

    def test_contains_required_fields(self) -> None:
        """Test contains all required fields."""
        result = _get_csv_fieldnames()
        assert Key.CHANGE_TYPE in result
        assert Key.ARTIST in result
        assert Key.ALBUM in result
        assert Key.TRACK_NAME in result
        assert Key.OLD_GENRE in result
        assert Key.NEW_GENRE in result
        assert Key.OLD_YEAR in result
        assert Key.NEW_YEAR in result
        assert Key.TIMESTAMP in result

    def test_field_count(self) -> None:
        """Test correct number of fields."""
        result = _get_csv_fieldnames()
        assert len(result) == 13


class TestSaveUnifiedChangesReport:
    """Tests for save_unified_changes_report function."""

    def test_empty_changes_prints_no_changes(
        self,
        console_logger: MagicMock,
        error_logger: MagicMock,
    ) -> None:
        """Test empty changes prints no changes summary."""
        with patch("metrics.change_reports._print_no_changes_summary") as mock_print:
            save_unified_changes_report([], None, console_logger, error_logger)
            mock_print.assert_called_once()

    def test_filtered_out_changes_prints_no_changes(
        self,
        console_logger: MagicMock,
        error_logger: MagicMock,
    ) -> None:
        """Test all-filtered changes prints no changes summary."""
        # Changes where old == new (will be filtered out)
        changes: list[dict[str, Any]] = [
            {"change_type": "genre_update", "old_genre": "Rock", "new_genre": "Rock"},
        ]
        with patch("metrics.change_reports._print_no_changes_summary") as mock_print:
            save_unified_changes_report(changes, None, console_logger, error_logger)
            mock_print.assert_called_once()

    def test_creates_csv_when_file_path_provided(
        self,
        tmp_path: Path,
        console_logger: MagicMock,
        error_logger: MagicMock,
    ) -> None:
        """Test CSV is created when file path is provided."""
        file_path = tmp_path / "report.csv"
        changes: list[dict[str, Any]] = [
            {
                "change_type": "genre_update",
                "artist": "Artist",
                "album": "Album",
                "track_name": "Track",
                "old_genre": "Rock",
                "new_genre": "Metal",
            },
        ]
        save_unified_changes_report(changes, str(file_path), console_logger, error_logger)
        assert file_path.exists()

    def test_skips_csv_when_file_path_none(
        self,
        console_logger: MagicMock,
        error_logger: MagicMock,
    ) -> None:
        """Test CSV creation is skipped when file path is None."""
        changes: list[dict[str, Any]] = [
            {
                "change_type": "genre_update",
                "artist": "Artist",
                "old_genre": "Rock",
                "new_genre": "Metal",
            },
        ]
        # Should not raise when file_path is None
        save_unified_changes_report(changes, None, console_logger, error_logger)


class TestSaveChangesReport:
    """Tests for save_changes_report function."""

    def test_saves_report_with_dict_changes(
        self,
        tmp_path: Path,
        console_logger: MagicMock,
        error_logger: MagicMock,
    ) -> None:
        """Test saving report with dictionary changes."""
        file_path = tmp_path / "report.csv"
        changes: list[dict[str, Any]] = [
            {
                "change_type": "genre_update",
                "artist": "Artist",
                "album": "Album",
                "track_name": "Track",
                "old_genre": "Rock",
                "new_genre": "Metal",
            },
        ]
        save_changes_report(changes, str(file_path), console_logger, error_logger)
        assert file_path.exists()

    def test_saves_report_with_changelog_entries(
        self,
        tmp_path: Path,
        console_logger: MagicMock,
        error_logger: MagicMock,
    ) -> None:
        """Test saving report with ChangeLogEntry objects."""
        file_path = tmp_path / "report.csv"
        entries = [
            ChangeLogEntry(
                timestamp="2024-01-15T10:30:00",
                change_type="genre_update",
                track_id="123",
                artist="Test Artist",
                track_name="Test Song",
                album_name="Test Album",
                old_genre="Rock",
                new_genre="Metal",
            ),
        ]
        save_changes_report(entries, str(file_path), console_logger, error_logger)
        assert file_path.exists()

    def test_adds_timestamp_when_requested(
        self,
        tmp_path: Path,
        console_logger: MagicMock,
        error_logger: MagicMock,
    ) -> None:
        """Test timestamp is added to filename when requested."""
        base_path = tmp_path / "report.csv"
        changes: list[dict[str, Any]] = [
            {
                "change_type": "genre_update",
                "artist": "Artist",
                "old_genre": "Rock",
                "new_genre": "Metal",
            },
        ]
        save_changes_report(changes, str(base_path), console_logger, error_logger, add_timestamp=True)
        # The original file should not exist, but a timestamped one should
        assert not base_path.exists()
        csv_files = list(tmp_path.glob("report_*.csv"))
        assert len(csv_files) == 1

    def test_handles_none_file_path(
        self,
        console_logger: MagicMock,
        error_logger: MagicMock,
    ) -> None:
        """Test handles None file path gracefully."""
        changes: list[dict[str, Any]] = [
            {
                "change_type": "genre_update",
                "artist": "Artist",
                "old_genre": "Rock",
                "new_genre": "Metal",
            },
        ]
        # Should not raise
        save_changes_report(changes, None, console_logger, error_logger)

    def test_determines_change_type_if_missing(
        self,
        tmp_path: Path,
        console_logger: MagicMock,
        error_logger: MagicMock,
    ) -> None:
        """Test change_type is determined if not present."""
        file_path = tmp_path / "report.csv"
        changes: list[dict[str, Any]] = [
            {
                "artist": "Artist",
                "album": "Album",
                "new_genre": "Metal",
                "old_genre": "Rock",
            },
        ]
        save_changes_report(changes, str(file_path), console_logger, error_logger)
        # Should have detected as genre change
        assert file_path.exists()
        with file_path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            row = next(reader)
            assert row["change_type"] == "genre"

    def test_default_loggers_when_none_provided(
        self,
        tmp_path: Path,
    ) -> None:
        """Test default loggers are used when None provided."""
        file_path = tmp_path / "report.csv"
        changes: list[dict[str, Any]] = [
            {
                "change_type": "genre_update",
                "artist": "Artist",
                "old_genre": "Rock",
                "new_genre": "Metal",
            },
        ]
        # Should not raise with None loggers
        save_changes_report(changes, str(file_path))
        assert file_path.exists()

    def test_compact_mode_default_true(
        self,
        tmp_path: Path,
        console_logger: MagicMock,
        error_logger: MagicMock,
    ) -> None:
        """Test compact_mode defaults to True."""
        file_path = tmp_path / "report.csv"
        changes: list[dict[str, Any]] = [
            {
                "change_type": "genre_update",
                "artist": "Artist",
                "old_genre": "Rock",
                "new_genre": "Metal",
            },
        ]
        with patch("metrics.change_reports.save_unified_changes_report") as mock_save:
            save_changes_report(changes, str(file_path), console_logger, error_logger)
            # Check that compact_mode=True was passed
            call_args = mock_save.call_args
            assert call_args[0][4] is True  # compact_mode is 5th positional arg

    def test_normalizes_field_mappings(
        self,
        tmp_path: Path,
        console_logger: MagicMock,
        error_logger: MagicMock,
    ) -> None:
        """Test field mappings are normalized."""
        file_path = tmp_path / "report.csv"
        changes: list[dict[str, Any]] = [
            {
                "field": "genre",
                "artist": "Artist",
                "album": "Album",
                "old_value": "Rock",
                "new_value": "Metal",
            },
        ]
        save_changes_report(changes, str(file_path), console_logger, error_logger)
        assert file_path.exists()
        with file_path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            row = next(reader)
            assert row["old_genre"] == "Rock"
            assert row["new_genre"] == "Metal"


class TestIntegration:
    """Integration tests for change_reports module."""

    def test_full_workflow_genre_change(
        self,
        tmp_path: Path,
    ) -> None:
        """Test full workflow for genre changes."""
        file_path = tmp_path / "changes.csv"
        changes: list[dict[str, Any]] = [
            {
                "artist": "Artist1",
                "album": "Album1",
                "track_name": "Track1",
                "old_genre": "Rock",
                "new_genre": "Metal",
            },
            {
                "artist": "Artist2",
                "album": "Album2",
                "track_name": "Track2",
                "old_genre": "Pop",
                "new_genre": "Jazz",
            },
        ]
        save_changes_report(changes, str(file_path))

        assert file_path.exists()
        with file_path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        # Artist1 comes before Artist2 alphabetically
        assert len(rows) == 2
        assert rows[0]["artist"] == "Artist1"
        assert rows[1]["artist"] == "Artist2"

    def test_full_workflow_year_change(
        self,
        tmp_path: Path,
    ) -> None:
        """Test full workflow for year changes."""
        file_path = tmp_path / "changes.csv"
        changes: list[dict[str, Any]] = [
            {
                "artist": "Artist",
                "album": "Album",
                "track_name": "Track",
                "old_year": "2020",
                "new_year": "2021",
            },
        ]
        save_changes_report(changes, str(file_path))

        assert file_path.exists()
        with file_path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            row = next(reader)
        assert row["change_type"] == "year"

    def test_mixed_change_types(
        self,
        tmp_path: Path,
    ) -> None:
        """Test report with mixed change types."""
        file_path = tmp_path / "changes.csv"
        changes: list[dict[str, Any]] = [
            {
                "change_type": "genre_update",
                "artist": "Artist1",
                "album": "Album",
                "old_genre": "Rock",
                "new_genre": "Metal",
            },
            {
                "change_type": "year_update",
                "artist": "Artist2",
                "album": "Album",
                "old_year": "2020",
                "new_year": "2021",
            },
            {
                "change_type": "name_clean",
                "artist": "Artist3",
                "album": "Album",
                "old_track_name": "Song (Live)",
                "new_track_name": "Song",
            },
        ]
        save_changes_report(changes, str(file_path))

        assert file_path.exists()
        with file_path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 3
