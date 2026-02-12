"""Unit tests for app configuration manager."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from app.app_config import Config
from core.models.track_models import AppConfig


def _make_test_app_config(**overrides: Any) -> AppConfig:
    """Create a minimal valid AppConfig for test mocking."""
    base: dict[str, Any] = {
        "music_library_path": "/tmp/test-library",
        "apple_scripts_dir": "/tmp/test-scripts",
        "logs_base_dir": "/tmp/test-logs",
        "python_settings": {"prevent_bytecode": True},
        "apple_script_concurrency": 2,
        "applescript_timeout_seconds": 60,
        "max_retries": 3,
        "retry_delay_seconds": 1.0,
        "incremental_interval_minutes": 15,
        "cache_ttl_seconds": 1200,
        "cleaning": {"remaster_keywords": ["remaster"], "album_suffixes_to_remove": []},
        "exceptions": {"track_cleaning": []},
        "database_verification": {"auto_verify_days": 7, "batch_size": 10},
        "development": {"test_artists": []},
        "logging": {
            "max_runs": 3,
            "main_log_file": "test.log",
            "analytics_log_file": "analytics.log",
            "csv_output_file": "output.csv",
            "changes_report_file": "changes.json",
            "dry_run_report_file": "dryrun.json",
            "last_incremental_run_file": "lastrun.json",
            "pending_verification_file": "pending.json",
            "last_db_verify_log": "dbverify.log",
            "levels": {"console": "INFO", "main_file": "INFO", "analytics_file": "INFO"},
        },
        "analytics": {
            "duration_thresholds": {"short_max": 2, "medium_max": 5, "long_max": 10},
            "max_events": 10000,
            "compact_time": False,
        },
        "genre_update": {"batch_size": 50, "concurrent_limit": 5},
        "year_retrieval": {
            "enabled": False,
            "preferred_api": "musicbrainz",
            "api_auth": {
                "discogs_token": "test-token",
                "musicbrainz_app_name": "TestApp/1.0",
                "contact_email": "test@example.com",
            },
            "rate_limits": {
                "discogs_requests_per_minute": 25,
                "musicbrainz_requests_per_second": 1,
                "concurrent_api_calls": 3,
            },
            "processing": {
                "batch_size": 10,
                "delay_between_batches": 60,
                "adaptive_delay": False,
                "cache_ttl_days": 30,
                "pending_verification_interval_days": 30,
            },
            "logic": {
                "min_valid_year": 1900,
                "definitive_score_threshold": 85,
                "definitive_score_diff": 15,
                "preferred_countries": [],
                "major_market_codes": [],
            },
            "reissue_detection": {"reissue_keywords": []},
            "scoring": {
                "base_score": 0,
                "artist_exact_match_bonus": 0,
                "album_exact_match_bonus": 0,
                "perfect_match_bonus": 0,
                "album_variation_bonus": 0,
                "album_substring_penalty": 0,
                "album_unrelated_penalty": 0,
                "mb_release_group_match_bonus": 0,
                "type_album_bonus": 0,
                "type_ep_single_penalty": 0,
                "type_compilation_live_penalty": 0,
                "status_official_bonus": 0,
                "status_bootleg_penalty": 0,
                "status_promo_penalty": 0,
                "reissue_penalty": 0,
                "year_diff_penalty_scale": 0,
                "year_diff_max_penalty": 0,
                "year_before_start_penalty": 0,
                "year_after_end_penalty": 0,
                "year_near_start_bonus": 0,
                "country_artist_match_bonus": 0,
                "country_major_market_bonus": 0,
                "source_mb_bonus": 0,
                "source_discogs_bonus": 0,
            },
        },
    }
    return AppConfig(**{**base, **overrides})


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
        assert config._loaded is True
        # Verify internal dict is populated for accessor methods
        assert isinstance(config._config, dict)

    def test_load_caches_result(self) -> None:
        """Config should only load file once."""
        mock_app_config = _make_test_app_config()

        with patch("app.app_config.load_yaml_config", return_value=mock_app_config) as mock_load:
            config = Config("/fake/config.yaml")
            data1 = config.load()
            data2 = config.load()

        assert data1 is data2
        mock_load.assert_called_once()

    def test_load_raises_on_invalid_path(self) -> None:
        """Config should raise RuntimeError for load failures."""
        with patch("app.app_config.load_yaml_config", side_effect=FileNotFoundError("not found")):
            config = Config("/nonexistent/path/config.yaml")

            with pytest.raises(RuntimeError, match="Failed to load configuration"):
                config.load()


class TestConfigGet:
    """Tests for Config.get() method with dict-based accessor."""

    @pytest.fixture
    def loaded_config(self) -> Config:
        """Create a loaded config with custom dict data for accessor testing.

        Bypasses load() and sets ``_config`` directly to test accessor methods
        with arbitrary keys that don't match the AppConfig schema.
        """
        config = Config("/fake/config.yaml")
        config._config = {
            "database": {"host": "localhost", "port": 5432, "nested": {"value": "deep"}},
            "features": {"enabled": True},
            "count": 42,
        }
        config._loaded = True
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
        mock_app_config = _make_test_app_config()

        with patch("app.app_config.load_yaml_config", return_value=mock_app_config):
            config = Config("/fake/config.yaml")
            assert config._loaded is False

            # Access a key that exists in AppConfig model_dump()
            value = config.get("apple_script_concurrency")
            assert value is not None
            assert config._loaded is True


class TestConfigTypedGetters:
    """Tests for typed getter methods."""

    @pytest.fixture
    def config_with_types(self) -> Config:
        """Create config with various types for accessor testing.

        Sets ``_config`` directly to test accessors with arbitrary keys.
        """
        config = Config("/fake/config.yaml")
        config._config = {
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
        config._loaded = True
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
            assert config._loaded is False

            _ = config.resolved_path
            assert config._loaded is True

    def test_resolve_config_path_fallback_on_error(self) -> None:
        """_resolve_config_path should fallback to absolute path on resolve errors."""
        config = Config("/some/path/config.yaml")
        # _resolve_config_path handles OSError gracefully
        result = config._resolve_config_path()
        assert "config.yaml" in result
