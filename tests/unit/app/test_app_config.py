"""Unit tests for app configuration manager."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.app_config import Config
from tests.factories import create_test_app_config as _make_test_app_config


class TestConfigInit:
    """Tests for Config initialization."""

    def test_init_with_explicit_path(self) -> None:
        """Config should use provided path directly."""
        config = Config(config_path="/path/to/config.yaml")
        assert config.config_path == "/path/to/config.yaml"
        assert config._app_config is None

    def test_init_with_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Config should use CONFIG_PATH env var when no path provided."""
        monkeypatch.setenv("CONFIG_PATH", "/env/path/config.yaml")
        config = Config()
        assert config.config_path == "/env/path/config.yaml"

    def test_init_finds_existing_config_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Config should find existing config file from defaults."""
        self._setup_config_file(monkeypatch, tmp_path, "config.yaml")

    def test_init_falls_back_to_my_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Config should fall back to my-config.yaml if config.yaml doesn't exist."""
        self._setup_config_file(monkeypatch, tmp_path, "my-config.yaml")

    def test_prefers_my_config_over_config_when_both_exist(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When both config.yaml and my-config.yaml exist, prefer my-config.yaml."""
        monkeypatch.delenv("CONFIG_PATH", raising=False)
        monkeypatch.chdir(tmp_path)

        # Create both config files
        (tmp_path / "config.yaml").write_text("source: template")
        (tmp_path / "my-config.yaml").write_text("source: custom")

        with patch("app.app_config.load_dotenv", return_value=None):
            config = Config()

        # my-config.yaml should take precedence (user config over template)
        assert config.config_path == "my-config.yaml"

    @staticmethod
    def _setup_config_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, config_filename: str) -> None:
        """Set up a config file in tmp_path and verify Config finds it."""
        monkeypatch.delenv("CONFIG_PATH", raising=False)
        monkeypatch.chdir(tmp_path)
        config_file = tmp_path / config_filename
        config_file.write_text("key: value")
        # Patch load_dotenv to prevent .env file from re-setting CONFIG_PATH
        with patch("app.app_config.load_dotenv", return_value=None):
            config = Config()
        assert config.config_path == config_filename

    def test_init_raises_when_no_config_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Config should raise FileNotFoundError when no config file exists."""
        monkeypatch.delenv("CONFIG_PATH", raising=False)
        monkeypatch.chdir(tmp_path)

        # Patch load_dotenv to prevent .env file from setting CONFIG_PATH
        with (
            patch("app.app_config.load_dotenv", return_value=None),
            pytest.raises(FileNotFoundError, match="No configuration file found"),
        ):
            Config()


class TestConfigLoad:
    """Tests for Config.load() method."""

    def test_load_returns_app_config(self) -> None:
        """Config.load should return a validated AppConfig instance."""
        from core.models.track_models import AppConfig

        mock_app_config = _make_test_app_config()

        with patch("app.app_config.load_yaml_config", return_value=mock_app_config):
            config = Config("/fake/config.yaml")
            result = config.load()

        assert isinstance(result, AppConfig)
        assert config._app_config is result

    def test_load_caches_result(self) -> None:
        """Config should only load file once."""
        mock_app_config = _make_test_app_config()

        with patch("app.app_config.load_yaml_config", return_value=mock_app_config) as mock_load:
            config = Config("/fake/config.yaml")
            data1 = config.load()
            data2 = config.load()

        assert data1 is data2
        mock_load.assert_called_once()

    def test_load_stores_app_config(self) -> None:
        """load() should store AppConfig in _app_config."""
        from core.models.track_models import AppConfig

        mock_app_config = _make_test_app_config()

        with patch("app.app_config.load_yaml_config", return_value=mock_app_config):
            config = Config("/fake/config.yaml")
            result = config.load()

        assert config._app_config is result
        assert isinstance(config._app_config, AppConfig)

    def test_load_raises_on_invalid_path(self) -> None:
        """Config should raise RuntimeError for load failures."""
        with patch("app.app_config.load_yaml_config", side_effect=FileNotFoundError("not found")):
            config = Config("/nonexistent/path/config.yaml")

            with pytest.raises(RuntimeError, match="Failed to load configuration"):
                config.load()


class TestConfigResolvedPath:
    """Tests for resolved_path property."""

    def test_resolved_path_returns_absolute(self, tmp_path: Path) -> None:
        """resolved_path should return absolute path."""
        # Create a real file in tmp_path for resolve to work
        config_file = tmp_path / "config.yaml"
        config_file.write_text("key: value")

        mock_app_config = _make_test_app_config()
        with patch("app.app_config.load_yaml_config", return_value=mock_app_config):
            config = Config(str(config_file))
            resolved = config.resolved_path

        assert Path(resolved).is_absolute()

    def test_resolved_path_auto_loads(self) -> None:
        """resolved_path should auto-load config."""
        mock_app_config = _make_test_app_config()

        with patch("app.app_config.load_yaml_config", return_value=mock_app_config):
            config = Config("/fake/config.yaml")
            assert config._app_config is None

            _ = config.resolved_path
            assert config._app_config is not None

    def test_resolve_config_path_fallback_on_error(self) -> None:
        """_resolve_config_path should fallback to absolute path on resolve errors."""
        config = Config("/some/path/config.yaml")
        # _resolve_config_path handles OSError gracefully
        result = config._resolve_config_path()
        assert "config.yaml" in result


class TestLegacyTestArtistsMigration:
    """Tests for AppConfig.migrate_legacy_test_artists validator."""

    def test_migrates_top_level_to_development(self) -> None:
        """Top-level test_artists should migrate and emit deprecation warning."""
        with pytest.warns(DeprecationWarning, match="deprecated"):
            app_config = _make_test_app_config(
                test_artists=["Metallica", "Slayer"],
                development={"test_artists": []},
            )
        assert app_config.development.test_artists == ["Metallica", "Slayer"]

    def test_no_migration_when_development_has_values(self) -> None:
        """When both are set, top-level is ignored with a warning."""
        with pytest.warns(DeprecationWarning, match="ignored"):
            app_config = _make_test_app_config(
                test_artists=["Metallica"],
                development={"test_artists": ["Iron Maiden"]},
            )
        assert app_config.development.test_artists == ["Iron Maiden"]

    def test_no_migration_when_top_level_is_empty(self) -> None:
        """When top-level test_artists is empty, nothing changes."""
        app_config = _make_test_app_config(
            test_artists=[],
            development={"test_artists": []},
        )
        assert app_config.development.test_artists == []

    def test_default_no_top_level_key(self) -> None:
        """When top-level test_artists is not provided, development keeps its value."""
        app_config = _make_test_app_config(
            development={"test_artists": ["Opeth"]},
        )
        assert app_config.development.test_artists == ["Opeth"]
