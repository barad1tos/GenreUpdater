"""Tests for src/metrics/csv_utils.py."""

from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from metrics.csv_utils import TRACK_FIELDNAMES, save_csv


@pytest.fixture
def console_logger() -> MagicMock:
    """Create a mock console logger."""
    return MagicMock()


@pytest.fixture
def error_logger() -> MagicMock:
    """Create a mock error logger."""
    return MagicMock()


class TestTrackFieldnames:
    """Tests for TRACK_FIELDNAMES constant."""

    def test_track_fieldnames_has_required_fields(self) -> None:
        """Verify all required fields are present."""
        required = ["id", "name", "artist", "album", "genre"]
        for field in required:
            assert field in TRACK_FIELDNAMES

    def test_track_fieldnames_has_year_fields(self) -> None:
        """Verify year-related fields are present."""
        assert "year" in TRACK_FIELDNAMES
        assert "year_before_mgu" in TRACK_FIELDNAMES
        assert "year_set_by_mgu" in TRACK_FIELDNAMES

    def test_track_fieldnames_year_field_position(self) -> None:
        """Verify year field comes before year_before_mgu and year_set_by_mgu."""
        year_idx = TRACK_FIELDNAMES.index("year")
        year_before_mgu_idx = TRACK_FIELDNAMES.index("year_before_mgu")
        year_set_by_mgu_idx = TRACK_FIELDNAMES.index("year_set_by_mgu")
        # year should be for delta detection (current), old/new for tracking
        assert year_idx < year_before_mgu_idx
        assert year_idx < year_set_by_mgu_idx

    def test_track_fieldnames_has_all_core_fields(self) -> None:
        """Verify all core fields for year change tracking are present."""
        core_fields = ["id", "name", "artist", "album", "genre", "year", "year_before_mgu", "year_set_by_mgu"]
        for field in core_fields:
            assert field in TRACK_FIELDNAMES, f"Missing field: {field}"

    def test_track_fieldnames_is_list(self) -> None:
        """Verify TRACK_FIELDNAMES is a list of strings."""
        assert isinstance(TRACK_FIELDNAMES, list)
        assert all(isinstance(f, str) for f in TRACK_FIELDNAMES)


class TestSaveCsv:
    """Tests for save_csv function."""

    def test_save_csv_creates_file(
        self,
        tmp_path: Path,
        console_logger: MagicMock,
        error_logger: MagicMock,
    ) -> None:
        """Test that save_csv creates a CSV file with correct content."""
        file_path = tmp_path / "test.csv"
        data = [
            {"name": "Track1", "artist": "Artist1"},
            {"name": "Track2", "artist": "Artist2"},
        ]
        fieldnames = ["name", "artist"]

        save_csv(data, fieldnames, str(file_path), console_logger, error_logger, "tracks")

        assert file_path.exists()
        with file_path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["name"] == "Track1"
        assert rows[1]["artist"] == "Artist2"

    def test_save_csv_creates_directory(
        self,
        tmp_path: Path,
        console_logger: MagicMock,
        error_logger: MagicMock,
    ) -> None:
        """Test that save_csv creates parent directory if not exists."""
        nested_path = tmp_path / "nested" / "dir" / "test.csv"
        data = [{"name": "Track1"}]
        fieldnames = ["name"]

        save_csv(data, fieldnames, str(nested_path), console_logger, error_logger, "tracks")

        assert nested_path.exists()
        assert nested_path.parent.is_dir()

    def test_save_csv_filters_extra_fields(
        self,
        tmp_path: Path,
        console_logger: MagicMock,
        error_logger: MagicMock,
    ) -> None:
        """Test that save_csv only includes specified fieldnames."""
        file_path = tmp_path / "test.csv"
        data = [{"name": "Track1", "artist": "Artist1", "extra_field": "should_not_appear"}]
        fieldnames = ["name", "artist"]

        save_csv(data, fieldnames, str(file_path), console_logger, error_logger, "tracks")

        with file_path.open(encoding="utf-8") as f:
            content = f.read()
        assert "extra_field" not in content
        assert "should_not_appear" not in content

    def test_save_csv_handles_missing_fields(
        self,
        tmp_path: Path,
        console_logger: MagicMock,
        error_logger: MagicMock,
    ) -> None:
        """Test that save_csv handles rows with missing fields."""
        file_path = tmp_path / "test.csv"
        data = [{"name": "Track1"}]  # Missing 'artist' field
        fieldnames = ["name", "artist"]

        save_csv(data, fieldnames, str(file_path), console_logger, error_logger, "tracks")

        with file_path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert rows[0]["name"] == "Track1"
        assert rows[0]["artist"] == ""  # Empty string for missing field

    def test_save_csv_handles_empty_data(
        self,
        tmp_path: Path,
        console_logger: MagicMock,
        error_logger: MagicMock,
    ) -> None:
        """Test that save_csv handles empty data list."""
        file_path = tmp_path / "test.csv"
        data: list[dict[str, str]] = []
        fieldnames = ["name", "artist"]

        save_csv(data, fieldnames, str(file_path), console_logger, error_logger, "tracks")

        assert file_path.exists()
        with file_path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert not rows

    def test_save_csv_logs_success(
        self,
        tmp_path: Path,
        console_logger: MagicMock,
        error_logger: MagicMock,
    ) -> None:
        """Test that save_csv logs success messages."""
        file_path = tmp_path / "test.csv"
        data = [{"name": "Track1"}]
        fieldnames = ["name"]

        save_csv(data, fieldnames, str(file_path), console_logger, error_logger, "tracks")

        # Should have 2 info calls: starting and completion
        assert console_logger.info.call_count == 2

    def test_save_csv_handles_unicode(
        self,
        tmp_path: Path,
        console_logger: MagicMock,
        error_logger: MagicMock,
    ) -> None:
        """Test that save_csv handles unicode characters."""
        file_path = tmp_path / "test.csv"
        data = [{"name": "Трек", "artist": "日本語アーティスト"}]
        fieldnames = ["name", "artist"]

        save_csv(data, fieldnames, str(file_path), console_logger, error_logger, "tracks")

        with file_path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert rows[0]["name"] == "Трек"
        assert rows[0]["artist"] == "日本語アーティスト"

    def test_save_csv_atomic_write_cleans_temp_on_error(
        self,
        tmp_path: Path,
        console_logger: MagicMock,
        error_logger: MagicMock,
    ) -> None:
        """Test that save_csv cleans up temp file on write error."""
        file_path = tmp_path / "test.csv"
        temp_path = Path(f"{file_path}.tmp")
        data = [{"name": "Track1"}]
        fieldnames = ["name"]

        # Mock Path.replace to raise OSError after temp file is written
        with patch.object(Path, "replace", side_effect=OSError("Simulated error")):
            save_csv(data, fieldnames, str(file_path), console_logger, error_logger, "tracks")

        # Temp file should be cleaned up
        assert not temp_path.exists()
        # Error should be logged
        error_logger.exception.assert_called_once()

    def test_save_csv_logs_error_on_write_failure(
        self,
        tmp_path: Path,
        console_logger: MagicMock,
        error_logger: MagicMock,
    ) -> None:
        """Test that save_csv logs errors on write failure."""
        file_path = tmp_path / "test.csv"
        data = [{"name": "Track1"}]
        fieldnames = ["name"]

        # Mock open to raise OSError during write
        with patch("pathlib.Path.open", side_effect=OSError("Disk full")):
            save_csv(data, fieldnames, str(file_path), console_logger, error_logger, "tracks")

        error_logger.exception.assert_called()
