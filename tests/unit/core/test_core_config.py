"""Unit tests for core configuration module.

Note: This module tests internal config functions that are prefixed with underscore.
Testing private functions is intentional to ensure correctness of internal logic.
"""
# noinspection PyProtectedMember

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from core.core_config import (
    REQUIRED_ENV_VARS,
    _read_and_parse_config,
    _validate_config_data_type,
    _validate_config_path,
    format_pydantic_errors,
    resolve_env_vars,
    validate_api_auth,
    validate_required_env_vars,
)

if TYPE_CHECKING:
    import pathlib
    from pathlib import Path


def _create_config_file(tmp_path: pathlib.Path, name: str, content: str) -> pathlib.Path:
    """Helper to create a config file in tmp_path."""
    config_file = tmp_path / name
    config_file.write_text(content)
    return config_file


class TestResolveEnvVars:
    """Tests for resolve_env_vars function."""

    def test_resolve_simple_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should resolve ${VAR} syntax."""
        monkeypatch.setenv("MY_VAR", "resolved_value")
        result = resolve_env_vars("${MY_VAR}")
        assert result == "resolved_value"

    def test_resolve_empty_for_unset_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should return empty string for unset ${VAR}."""
        monkeypatch.delenv("NONEXISTENT_VAR_12345", raising=False)
        result = resolve_env_vars("${NONEXISTENT_VAR_12345}")
        assert result == ""

    def test_resolve_dollar_var_in_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should expand $VAR in paths."""
        monkeypatch.setenv("HOME", "/home/testuser")
        result = resolve_env_vars("$HOME/config")
        assert result == "/home/testuser/config"

    def test_resolve_nested_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should recursively resolve env vars in dicts."""
        monkeypatch.setenv("DB_HOST", "localhost")
        value: dict[str, Any] = {
            "database": {"host": "${DB_HOST}"},
            "name": "mydb",
        }
        result = resolve_env_vars(value)
        assert isinstance(result, dict)
        assert result["database"]["host"] == "localhost"
        assert result["name"] == "mydb"

    def test_resolve_nested_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should recursively resolve env vars in lists."""
        monkeypatch.setenv("ITEM1", "first")
        value: list[str] = ["${ITEM1}", "second"]
        result = resolve_env_vars(value)
        assert isinstance(result, list)
        assert result == ["first", "second"]

    def test_preserve_non_string_types(self) -> None:
        """Should preserve int, float, bool values."""
        assert resolve_env_vars(42) == 42
        assert resolve_env_vars(3.14) == 3.14
        assert resolve_env_vars(True) is True
        assert resolve_env_vars(None) is None


class TestValidateConfigPath:
    """Tests for _validate_config_path function."""

    def test_valid_config_in_cwd(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should accept config file in current working directory."""
        self._assert_config_path_valid(monkeypatch, tmp_path, "config.yaml")

    def test_raises_for_nonexistent_file(self, tmp_path: pathlib.Path) -> None:
        """Should raise FileNotFoundError for missing file."""
        nonexistent = tmp_path / "nonexistent.yaml"

        with pytest.raises(FileNotFoundError, match="Config file not found"):
            _validate_config_path(str(nonexistent))

    def test_raises_for_directory(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should raise FileNotFoundError if path is a directory."""
        monkeypatch.chdir(tmp_path)

        with pytest.raises(FileNotFoundError, match="does not point to a file"):
            _validate_config_path(str(tmp_path))

    def test_raises_for_wrong_extension(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should raise ValueError for non-YAML extension."""
        monkeypatch.chdir(tmp_path)
        config_file = _create_config_file(tmp_path, "config.txt", "key: value")

        with pytest.raises(ValueError, match=r"must have a \.yaml or \.yml extension"):
            _validate_config_path(str(config_file))

    def test_accepts_yml_extension(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should accept .yml extension."""
        self._assert_config_path_valid(monkeypatch, tmp_path, "config.yml")

    @staticmethod
    def _assert_config_path_valid(
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        filename: str,
    ) -> None:
        """Assert that config path validation succeeds for given filename."""
        monkeypatch.chdir(tmp_path)
        config_file = _create_config_file(tmp_path, filename, "key: value")
        result = _validate_config_path(str(config_file))
        assert result == config_file.resolve()


class TestReadAndParseConfig:
    """Tests for _read_and_parse_config function."""

    def test_reads_valid_yaml(self, tmp_path: pathlib.Path) -> None:
        """Should parse valid YAML file."""
        config_file = _create_config_file(tmp_path, "config.yaml", "database:\n  host: localhost\n  port: 5432\n")

        result = _read_and_parse_config(config_file)
        assert isinstance(result, dict)
        assert result["database"]["host"] == "localhost"
        assert result["database"]["port"] == 5432

    def test_raises_for_oversized_file(self, tmp_path: pathlib.Path) -> None:
        """Should raise ValueError for files over 1MB."""
        config_file = tmp_path / "large.yaml"
        # Create a file larger than 1MB
        config_file.write_text("x" * (1024 * 1024 + 1))

        with pytest.raises(ValueError, match="too large"):
            _read_and_parse_config(config_file)


class TestValidateRequiredEnvVars:
    """Tests for validate_required_env_vars function."""

    def test_returns_empty_when_all_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should return empty list when all vars are set."""
        for var in REQUIRED_ENV_VARS:
            monkeypatch.setenv(var, "test_value")

        result = validate_required_env_vars()
        assert result == []

    def test_returns_missing_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should return list of missing variables."""
        for var in REQUIRED_ENV_VARS:
            monkeypatch.delenv(var, raising=False)

        result = validate_required_env_vars()
        assert set(result) == set(REQUIRED_ENV_VARS)

    def test_detects_unresolved_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should detect vars that start with $ (unresolved)."""
        monkeypatch.setenv("DISCOGS_TOKEN", "$ANOTHER_VAR")
        monkeypatch.setenv("CONTACT_EMAIL", "test@example.com")

        result = validate_required_env_vars()
        assert "DISCOGS_TOKEN" in result
        assert "CONTACT_EMAIL" not in result


class TestValidateConfigDataType:
    """Tests for _validate_config_data_type function."""

    def test_accepts_dict(self) -> None:
        """Should return dict unchanged."""
        data: dict[str, Any] = {"key": "value"}
        result = _validate_config_data_type(data)
        assert result == data

    def test_raises_for_list(self) -> None:
        """Should raise TypeError for list."""
        with pytest.raises(TypeError, match="not a dictionary"):
            _validate_config_data_type(["item1", "item2"])  # type: ignore[arg-type]

    def test_raises_for_string(self) -> None:
        """Should raise TypeError for string."""
        with pytest.raises(TypeError, match="not a dictionary"):
            _validate_config_data_type("string value")

    def test_raises_for_none(self) -> None:
        """Should raise TypeError for None."""
        with pytest.raises(TypeError, match="not a dictionary"):
            _validate_config_data_type(None)


class TestFormatPydanticErrors:
    """Tests for format_pydantic_errors function."""

    @staticmethod
    def _create_mock_validation_error(
        errors: list[dict[str, Any]],
    ) -> MagicMock:
        """Create a mock ValidationError with given errors."""
        mock_error = MagicMock(spec=ValidationError)
        mock_error.errors.return_value = errors
        return mock_error

    def test_formats_missing_field_error(self) -> None:
        """Should format missing field errors."""
        mock_error = self._create_mock_validation_error([{"loc": ("database", "host"), "msg": "field required", "type": "missing"}])

        result = format_pydantic_errors(mock_error)
        assert "database.host: Missing required field" in result

    def test_formats_type_error(self) -> None:
        """Should format type errors."""
        mock_error = self._create_mock_validation_error([{"loc": ("port",), "msg": "value is not a valid integer", "type": "type_error"}])

        result = format_pydantic_errors(mock_error)
        assert "port: value is not a valid integer" in result

    def test_formats_other_errors_with_type(self) -> None:
        """Should include type for other error types."""
        mock_error = self._create_mock_validation_error([{"loc": ("field",), "msg": "custom error", "type": "custom_type"}])

        result = format_pydantic_errors(mock_error)
        assert "field: custom error (type: custom_type)" in result

    def test_formats_multiple_errors(self) -> None:
        """Should format multiple errors separated by newlines."""
        mock_error = self._create_mock_validation_error(
            [
                {"loc": ("field1",), "msg": "error1", "type": "missing"},
                {"loc": ("field2",), "msg": "error2", "type": "missing"},
            ]
        )

        result = format_pydantic_errors(mock_error)
        lines = result.split("\n")
        assert len(lines) == 2


class TestValidateApiAuth:
    """Tests for validate_api_auth function."""

    def test_raises_for_empty_auth(self) -> None:
        """Should raise ValueError for empty api_auth."""
        with pytest.raises(ValueError, match="'api_auth' section is missing"):
            validate_api_auth({})

    def test_raises_for_missing_discogs_token(self) -> None:
        """Should raise ValueError when discogs_token is missing."""
        api_auth: dict[str, Any] = {
            "contact_email": "test@example.com",
        }

        with pytest.raises(ValueError, match="DISCOGS_TOKEN"):
            validate_api_auth(api_auth)

    def test_raises_for_missing_contact_email(self) -> None:
        """Should raise ValueError when contact_email is missing."""
        api_auth: dict[str, Any] = {
            "discogs_token": "token123",
        }

        with pytest.raises(ValueError, match="CONTACT_EMAIL"):
            validate_api_auth(api_auth)

    def test_accepts_valid_complete_auth(self) -> None:
        """Should not raise for complete valid auth."""
        api_auth: dict[str, Any] = {
            "discogs_token": "token123",
            "contact_email": "test@example.com",
        }

        # Should not raise
        validate_api_auth(api_auth)
