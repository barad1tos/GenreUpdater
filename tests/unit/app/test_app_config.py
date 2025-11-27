"""Unit tests for app configuration manager."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from src.app.app_config import Config, DEFAULT_CONFIG_FILES


class TestConfigInit:
    """Tests for Config initialization."""

    def test_init_with_explicit_path(self) -> None:
        """Config should use provided path directly."""
        config = Config(config_path="/path/to/config.yaml")
        assert config.config_path == "/path/to/config.yaml"
        assert config._loaded is False

    def test_init_with_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Config should use CONFIG_PATH env var when no path provided."""
        monkeypatch.setenv("CONFIG_PATH", "/env/path/config.yaml")
        config = Config()
        assert config.config_path == "/env/path/config.yaml"

    def test_init_finds_existing_config_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Config should find existing config file from defaults."""
        self._setup_config_file(
            monkeypatch, tmp_path, "config.yaml"
        )

    def test_init_falls_back_to_my_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Config should fall back to my-config.yaml if config.yaml doesn't exist."""
        self._setup_config_file(
            monkeypatch, tmp_path, "my-config.yaml"
        )

    @staticmethod
    def _setup_config_file(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, config_filename: str
    ) -> None:
        """Set up a config file in tmp_path and verify Config finds it."""
        monkeypatch.delenv("CONFIG_PATH", raising=False)
        monkeypatch.chdir(tmp_path)
        config_file = tmp_path / config_filename
        config_file.write_text("key: value")
        config = Config()
        assert config.config_path == config_filename

    def test_init_warns_when_no_config_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Config should warn when no config file exists."""
        monkeypatch.delenv("CONFIG_PATH", raising=False)
        monkeypatch.chdir(tmp_path)

        with caplog.at_level(logging.WARNING):
            config = Config()

        assert config.config_path == DEFAULT_CONFIG_FILES[0]
        assert "No configuration file found" in caplog.text


class TestConfigLoad:
    """Tests for Config.load() method."""

    def test_load_valid_yaml(self) -> None:
        """Config should load valid YAML file."""
        mock_config = {"database": {"host": "localhost", "port": 5432}}

        with patch("src.app.app_config.load_yaml_config", return_value=mock_config):
            config = Config("/fake/config.yaml")
            data = config.load()

        assert data["database"]["host"] == "localhost"
        assert data["database"]["port"] == 5432
        assert config._loaded is True

    def test_load_caches_result(self) -> None:
        """Config should only load file once."""
        mock_config = {"key": "value"}

        with patch("src.app.app_config.load_yaml_config", return_value=mock_config) as mock_load:
            config = Config("/fake/config.yaml")
            data1 = config.load()
            data2 = config.load()

        assert data1 is data2
        mock_load.assert_called_once()

    def test_load_raises_on_invalid_path(self) -> None:
        """Config should raise RuntimeError for load failures."""
        with patch("src.app.app_config.load_yaml_config", side_effect=FileNotFoundError("not found")):
            config = Config("/nonexistent/path/config.yaml")

            with pytest.raises(RuntimeError, match="Failed to load configuration"):
                config.load()


class TestConfigGet:
    """Tests for Config.get() method."""

    @pytest.fixture
    def loaded_config(self) -> Config:
        """Create a loaded config for testing."""
        mock_config = {
            "database": {"host": "localhost", "port": 5432, "nested": {"value": "deep"}},
            "features": {"enabled": True},
            "count": 42,
        }
        with patch("src.app.app_config.load_yaml_config", return_value=mock_config):
            config = Config("/fake/config.yaml")
            config.load()
        return config

    def test_get_simple_key(self, loaded_config: Config) -> None:
        """Get should retrieve simple keys."""
        assert loaded_config.get("count") == 42

    def test_get_nested_key_with_dot_notation(self, loaded_config: Config) -> None:
        """Get should support dot notation for nested keys."""
        assert loaded_config.get("database.host") == "localhost"
        assert loaded_config.get("database.port") == 5432
        assert loaded_config.get("database.nested.value") == "deep"

    def test_get_returns_default_for_missing_key(self, loaded_config: Config) -> None:
        """Get should return default for missing keys."""
        assert loaded_config.get("nonexistent") is None
        assert loaded_config.get("nonexistent", "default") == "default"
        assert loaded_config.get("database.missing", 123) == 123

    def test_get_auto_loads_config(self) -> None:
        """Get should auto-load config if not loaded."""
        mock_config = {"key": "auto_loaded"}

        with patch("src.app.app_config.load_yaml_config", return_value=mock_config):
            config = Config("/fake/config.yaml")
            assert config._loaded is False

            value = config.get("key")
            assert value == "auto_loaded"
            assert config._loaded is True


class TestConfigTypedGetters:
    """Tests for typed getter methods."""

    @pytest.fixture
    def config_with_types(self) -> Config:
        """Create config with various types."""
        mock_config = {
            "string_val": "hello",
            "int_val": 42,
            "float_val": 3.14,
            "bool_true": True,
            "bool_false": False,
            "bool_yes": "yes",
            "bool_no": "no",
            "bool_on": "on",
            "bool_off": "off",
            "bool_one": "1",
            "bool_zero": "0",
            "list_val": ["item1", "item2"],
            "dict_val": {"key1": "val1", "key2": "val2"},
            "path_val": "~/documents/file.txt",
            "path_with_env": "$HOME/config",
        }
        with patch("src.app.app_config.load_yaml_config", return_value=mock_config):
            config = Config("/fake/config.yaml")
            config.load()
        return config

    def test_get_bool_true_values(self, config_with_types: Config) -> None:
        """get_bool should handle various true representations."""
        assert config_with_types.get_bool("bool_true") is True
        assert config_with_types.get_bool("bool_yes") is True
        assert config_with_types.get_bool("bool_on") is True
        assert config_with_types.get_bool("bool_one") is True

    def test_get_bool_false_values(self, config_with_types: Config) -> None:
        """get_bool should handle various false representations."""
        assert config_with_types.get_bool("bool_false") is False
        assert config_with_types.get_bool("bool_no") is False
        assert config_with_types.get_bool("bool_off") is False
        assert config_with_types.get_bool("bool_zero") is False

    def test_get_bool_default(self, config_with_types: Config) -> None:
        """get_bool should return default for missing keys."""
        assert config_with_types.get_bool("nonexistent") is False
        assert config_with_types.get_bool("nonexistent", True) is True

    def test_get_int(self, config_with_types: Config) -> None:
        """get_int should parse integer values."""
        assert config_with_types.get_int("int_val") == 42
        assert config_with_types.get_int("nonexistent") == 0
        assert config_with_types.get_int("nonexistent", 99) == 99
        assert config_with_types.get_int("string_val", 10) == 10  # Invalid, returns default

    def test_get_float(self, config_with_types: Config) -> None:
        """get_float should parse float values."""
        assert config_with_types.get_float("float_val") == pytest.approx(3.14)
        assert config_with_types.get_float("int_val") == pytest.approx(42.0)
        assert config_with_types.get_float("nonexistent") == pytest.approx(0.0)
        assert config_with_types.get_float("nonexistent", 1.5) == pytest.approx(1.5)

    def test_get_list(self, config_with_types: Config) -> None:
        """get_list should return list values."""
        result = config_with_types.get_list("list_val")
        assert result == ["item1", "item2"]
        assert config_with_types.get_list("nonexistent") == []
        assert config_with_types.get_list("nonexistent", ["default"]) == ["default"]
        assert config_with_types.get_list("string_val") == []  # Invalid type, returns default

    def test_get_dict(self, config_with_types: Config) -> None:
        """get_dict should return dict values."""
        result = config_with_types.get_dict("dict_val")
        assert result == {"key1": "val1", "key2": "val2"}
        assert config_with_types.get_dict("nonexistent") == {}
        assert config_with_types.get_dict("nonexistent", {"default": "val"}) == {"default": "val"}

    def test_get_path(self, config_with_types: Config) -> None:
        """get_path should return expanded Path objects."""
        result = config_with_types.get_path("path_val")
        assert isinstance(result, Path)
        assert "~" not in str(result)  # Should be expanded
        assert result.name == "file.txt"

    def test_get_path_expands_env_vars(self, config_with_types: Config, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_path should expand environment variables."""
        monkeypatch.setenv("HOME", "/home/testuser")
        result = config_with_types.get_path("path_with_env")
        assert "/home/testuser" in str(result)


class TestConfigResolvedPath:
    """Tests for resolved_path property."""

    def test_resolved_path_returns_absolute(self, tmp_path: Path) -> None:
        """resolved_path should return absolute path."""
        # Create a real file in tmp_path for resolve to work
        config_file = tmp_path / "config.yaml"
        config_file.write_text("key: value")

        mock_config = {"key": "value"}
        with patch("src.app.app_config.load_yaml_config", return_value=mock_config):
            config = Config(str(config_file))
            resolved = config.resolved_path

        assert Path(resolved).is_absolute()

    def test_resolved_path_auto_loads(self) -> None:
        """resolved_path should auto-load config."""
        mock_config = {"key": "value"}

        with patch("src.app.app_config.load_yaml_config", return_value=mock_config):
            config = Config("/fake/config.yaml")
            assert config._loaded is False

            _ = config.resolved_path
            assert config._loaded is True

    def test_resolve_config_path_fallback_on_error(self) -> None:
        """_resolve_config_path should fallback to absolute path on resolve errors."""
        config = Config("/some/path/config.yaml")
        # _resolve_config_path handles OSError gracefully
        result = config._resolve_config_path()
        assert "config.yaml" in result
