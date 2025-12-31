"""Unit tests for pipeline helper functions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.pipeline_helpers import resolve_env_vars

if TYPE_CHECKING:
    import pytest


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
