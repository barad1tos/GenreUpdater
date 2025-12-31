"""Unit tests for pipeline helper functions."""

from __future__ import annotations

from typing import Any

import pytest

from app.pipeline_helpers import is_valid_track_item, resolve_env_vars


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


class TestIsValidTrackItem:
    """Tests for is_valid_track_item TypeGuard function."""

    def test_valid_minimal_track(self) -> None:
        """Valid track with only required fields."""
        track: dict[str, Any] = {
            "id": "12345",
            "name": "Test Track",
            "artist": "Test Artist",
            "album": "Test Album",
        }
        assert is_valid_track_item(track) is True

    def test_valid_full_track(self) -> None:
        """Valid track with all optional fields."""
        track: dict[str, Any] = {
            "id": "12345",
            "name": "Test Track",
            "artist": "Test Artist",
            "album": "Test Album",
            "genre": "Rock",
            "year": "2024",
            "date_added": "2024-01-01",
            "last_modified": "2024-01-02",
            "track_status": "subscription",
            "original_artist": "Original Artist",
            "original_album": "Original Album",
            "year_before_mgu": "2020",
            "year_set_by_mgu": "2024",
            "release_year": "2024",
            "original_pos": 1,
        }
        assert is_valid_track_item(track) is True

    def test_invalid_not_dict(self) -> None:
        """Non-dict values should be invalid."""
        assert is_valid_track_item("not a dict") is False
        assert is_valid_track_item(123) is False
        assert is_valid_track_item(None) is False
        assert is_valid_track_item([]) is False

    def test_missing_required_field_id(self) -> None:
        """Track without 'id' field is invalid."""
        track: dict[str, Any] = {
            "name": "Test Track",
            "artist": "Test Artist",
            "album": "Test Album",
        }
        assert is_valid_track_item(track) is False

    def test_missing_required_field_name(self) -> None:
        """Track without 'name' field is invalid."""
        track: dict[str, Any] = {
            "id": "12345",
            "artist": "Test Artist",
            "album": "Test Album",
        }
        assert is_valid_track_item(track) is False

    def test_missing_required_field_artist(self) -> None:
        """Track without 'artist' field is invalid."""
        track: dict[str, Any] = {
            "id": "12345",
            "name": "Test Track",
            "album": "Test Album",
        }
        assert is_valid_track_item(track) is False

    def test_missing_required_field_album(self) -> None:
        """Track without 'album' field is invalid."""
        track: dict[str, Any] = {
            "id": "12345",
            "name": "Test Track",
            "artist": "Test Artist",
        }
        assert is_valid_track_item(track) is False

    def test_required_field_not_string(self) -> None:
        """Required fields must be strings."""
        track: dict[str, Any] = {
            "id": 12345,  # Should be string
            "name": "Test Track",
            "artist": "Test Artist",
            "album": "Test Album",
        }
        assert is_valid_track_item(track) is False

    def test_optional_string_field_wrong_type(self) -> None:
        """Optional string fields must be string or None."""
        track: dict[str, Any] = {
            "id": "12345",
            "name": "Test Track",
            "artist": "Test Artist",
            "album": "Test Album",
            "genre": 123,  # Should be string or None
        }
        assert is_valid_track_item(track) is False

    def test_optional_string_field_none(self) -> None:
        """Optional string fields can be None."""
        track: dict[str, Any] = {
            "id": "12345",
            "name": "Test Track",
            "artist": "Test Artist",
            "album": "Test Album",
            "genre": None,
            "year": None,
        }
        assert is_valid_track_item(track) is True

    def test_optional_int_field_wrong_type(self) -> None:
        """Optional int fields must be int or None."""
        track: dict[str, Any] = {
            "id": "12345",
            "name": "Test Track",
            "artist": "Test Artist",
            "album": "Test Album",
            "original_pos": "not an int",  # Should be int or None
        }
        assert is_valid_track_item(track) is False

    def test_optional_int_field_none(self) -> None:
        """Optional int fields can be None."""
        track: dict[str, Any] = {
            "id": "12345",
            "name": "Test Track",
            "artist": "Test Artist",
            "album": "Test Album",
            "original_pos": None,
        }
        assert is_valid_track_item(track) is True

    def test_extra_fields_allowed(self) -> None:
        """Extra unknown fields should not invalidate the track."""
        track: dict[str, Any] = {
            "id": "12345",
            "name": "Test Track",
            "artist": "Test Artist",
            "album": "Test Album",
            "unknown_field": "some value",
            "another_field": 42,
        }
        assert is_valid_track_item(track) is True
