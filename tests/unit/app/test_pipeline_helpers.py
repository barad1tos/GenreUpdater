"""Unit tests for pipeline helper functions."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.pipeline_helpers import (
    check_paths,
    is_code_action,
    is_script_action,
    is_valid_track_item,
    resolve_env_vars,
)
from core.models.track_models import CodeActionExtended, ScriptActionExtended

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class TestActionTypeGuards:
    """Tests for action type guard functions."""

    def test_is_script_action_returns_true_for_script(self) -> None:
        """is_script_action should return True for ScriptAction."""
        action = ScriptActionExtended(type="script", script_path="/path/to/test.scpt", content="script content")
        assert is_script_action(action) is True

    def test_is_script_action_returns_false_for_code(self) -> None:
        """is_script_action should return False for CodeAction."""
        action = CodeActionExtended(type="code", code="tell application Music")
        assert is_script_action(action) is False

    def test_is_code_action_returns_true_for_code(self) -> None:
        """is_code_action should return True for CodeAction."""
        action = CodeActionExtended(type="code", code="tell application Music")
        assert is_code_action(action) is True

    def test_is_code_action_returns_false_for_script(self) -> None:
        """is_code_action should return False for ScriptAction."""
        action = ScriptActionExtended(type="script", script_path="/path/to/test.scpt", content="script content")
        assert is_code_action(action) is False


class TestResolveEnvVars:
    """Tests for resolve_env_vars function."""

    def test_resolve_simple_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should resolve ${VAR_NAME} pattern."""
        monkeypatch.setenv("MY_VAR", "resolved_value")
        result = resolve_env_vars("prefix_${MY_VAR}_suffix")
        assert result == "prefix_resolved_value_suffix"

    def test_preserve_unset_env_var(self) -> None:
        """Should preserve unset environment variables."""
        result = resolve_env_vars("${NONEXISTENT_VAR_12345}")
        assert result == "${NONEXISTENT_VAR_12345}"

    def test_resolve_multiple_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should resolve multiple env vars in one string."""
        monkeypatch.setenv("VAR1", "one")
        monkeypatch.setenv("VAR2", "two")
        result = resolve_env_vars("${VAR1} and ${VAR2}")
        assert result == "one and two"

    def test_resolve_nested_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should recursively resolve env vars in dicts."""
        monkeypatch.setenv("DB_HOST", "localhost")
        monkeypatch.setenv("DB_PORT", "5432")

        value: dict[str, Any] = {
            "database": {"host": "${DB_HOST}", "port": "${DB_PORT}"},
            "name": "mydb",
        }
        result = resolve_env_vars(value)

        assert isinstance(result, dict)
        assert result["database"]["host"] == "localhost"
        assert result["database"]["port"] == "5432"
        assert result["name"] == "mydb"

    def test_resolve_nested_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should recursively resolve env vars in lists."""
        monkeypatch.setenv("ITEM1", "first")
        monkeypatch.setenv("ITEM2", "second")

        value: list[str] = ["${ITEM1}", "${ITEM2}", "third"]
        result = resolve_env_vars(value)

        assert isinstance(result, list)
        assert result == ["first", "second", "third"]

    def test_preserve_non_string_types(self) -> None:
        """Should preserve int, float, bool values."""
        assert resolve_env_vars(42) == 42
        assert resolve_env_vars(3.14) == 3.14
        assert resolve_env_vars(True) is True
        assert resolve_env_vars(False) is False


class TestCheckPaths:
    """Tests for check_paths function."""

    def test_logs_warning_for_nonexistent_paths(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Should log warnings for paths that don't exist."""
        logger = logging.getLogger("test_check_paths")
        nonexistent_paths = [
            str(tmp_path / "nonexistent1"),
            str(tmp_path / "nonexistent2"),
        ]

        with caplog.at_level(logging.WARNING):
            check_paths(nonexistent_paths, logger)

        assert "nonexistent1" in caplog.text
        assert "nonexistent2" in caplog.text

    def test_no_warning_for_existing_paths(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Should not log warnings for existing paths."""
        logger = logging.getLogger("test_check_paths")
        existing_file = tmp_path / "exists.txt"
        existing_file.write_text("content")

        with caplog.at_level(logging.WARNING):
            check_paths([str(existing_file)], logger)

        assert "exists.txt" not in caplog.text

    def test_mixed_existing_and_nonexistent(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Should only log warnings for nonexistent paths."""
        logger = logging.getLogger("test_check_paths")
        existing_file = tmp_path / "exists.txt"
        existing_file.write_text("content")

        paths = [str(existing_file), str(tmp_path / "missing")]

        with caplog.at_level(logging.WARNING):
            check_paths(paths, logger)

        assert "exists.txt" not in caplog.text
        assert "missing" in caplog.text


class TestIsValidTrackItem:
    """Tests for is_valid_track_item type guard."""

    def test_valid_track_dict(self) -> None:
        """Should return True for valid track dict."""
        track = {
            "id": "12345",
            "name": "Test Track",
            "artist": "Test Artist",
            "album": "Test Album",
        }
        assert is_valid_track_item(track) is True

    def test_valid_track_with_optional_fields(self) -> None:
        """Should return True for track with optional fields."""
        track = {
            "id": "12345",
            "name": "Test Track",
            "artist": "Test Artist",
            "album": "Test Album",
            "genre": "Rock",
            "year": "2024",
            "date_added": "2024-01-01",
            "track_status": "subscription",
        }
        assert is_valid_track_item(track) is True

    def test_missing_required_field_id(self) -> None:
        """Should return False when id is missing."""
        track = {"name": "Test Track", "artist": "Test Artist", "album": "Test Album"}
        assert is_valid_track_item(track) is False

    def test_missing_required_field_name(self) -> None:
        """Should return False when name is missing."""
        track = {"id": "12345", "artist": "Test Artist", "album": "Test Album"}
        assert is_valid_track_item(track) is False

    def test_missing_required_field_artist(self) -> None:
        """Should return False when artist is missing."""
        track = {"id": "12345", "name": "Test Track", "album": "Test Album"}
        assert is_valid_track_item(track) is False

    def test_missing_required_field_album(self) -> None:
        """Should return False when album is missing."""
        track = {"id": "12345", "name": "Test Track", "artist": "Test Artist"}
        assert is_valid_track_item(track) is False

    def test_invalid_type_for_required_field(self) -> None:
        """Should return False when required field is not a string."""
        track = {
            "id": 12345,  # Should be string
            "name": "Test Track",
            "artist": "Test Artist",
            "album": "Test Album",
        }
        assert is_valid_track_item(track) is False

    def test_invalid_type_for_optional_string_field(self) -> None:
        """Should return False when optional string field has wrong type."""
        track = {
            "id": "12345",
            "name": "Test Track",
            "artist": "Test Artist",
            "album": "Test Album",
            "genre": 123,  # Should be string or None
        }
        assert is_valid_track_item(track) is False

    def test_none_value_for_optional_field_is_valid(self) -> None:
        """Should return True when optional field is None."""
        track = {
            "id": "12345",
            "name": "Test Track",
            "artist": "Test Artist",
            "album": "Test Album",
            "genre": None,
        }
        assert is_valid_track_item(track) is True

    def test_invalid_type_for_optional_int_field(self) -> None:
        """Should return False when optional int field has wrong type."""
        track = {
            "id": "12345",
            "name": "Test Track",
            "artist": "Test Artist",
            "album": "Test Album",
            "original_pos": "not an int",
        }
        assert is_valid_track_item(track) is False

    def test_valid_original_pos_int_field(self) -> None:
        """Should return True when original_pos is valid int."""
        track = {
            "id": "12345",
            "name": "Test Track",
            "artist": "Test Artist",
            "album": "Test Album",
            "original_pos": 5,
        }
        assert is_valid_track_item(track) is True

    def test_not_a_dict_returns_false(self) -> None:
        """Should return False for non-dict inputs."""
        assert is_valid_track_item("not a dict") is False
        assert is_valid_track_item(["list", "items"]) is False
        assert is_valid_track_item(None) is False
        assert is_valid_track_item(123) is False
