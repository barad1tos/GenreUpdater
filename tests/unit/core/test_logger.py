"""Unit tests for logger module."""

from __future__ import annotations

import logging
from pathlib import Path as PathLib
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

from src.core.logger import (
    LEVEL_ABBREV,
    CompactFormatter,
    Loggable,
    LoggerFilter,
    RunHandler,
    _build_config_alias_map,
    _convert_path_value_to_string,
    _get_log_file_paths,
    _get_log_levels_from_config,
    _get_path_from_config,
    _try_config_alias_replacement,
    _try_home_directory_replacement,
    ensure_directory,
    get_full_log_path,
    get_html_report_path,
    shorten_path,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestEnsureDirectory:
    """Tests for ensure_directory function."""

    def test_creates_directory_if_not_exists(self, tmp_path: Path) -> None:
        """Should create directory if it doesn't exist."""
        new_dir = tmp_path / "new_directory"
        assert not new_dir.exists()

        ensure_directory(str(new_dir))

        assert new_dir.exists()

    def test_does_nothing_if_directory_exists(self, tmp_path: Path) -> None:
        """Should not fail if directory already exists."""
        ensure_directory(str(tmp_path))
        assert tmp_path.exists()

    def test_creates_nested_directories(self, tmp_path: Path) -> None:
        """Should create nested directories."""
        nested = tmp_path / "a" / "b" / "c"

        ensure_directory(str(nested))

        assert nested.exists()

    def test_handles_empty_path(self) -> None:
        """Should handle empty path without error."""
        ensure_directory("")

    def test_logs_error_on_permission_failure(self, tmp_path: Path) -> None:
        """Should log error when directory creation fails."""
        error_logger = MagicMock(spec=logging.Logger)

        with patch("src.core.logger.Path.mkdir", side_effect=OSError("Permission denied")):
            ensure_directory(str(tmp_path / "test"), error_logger)

        error_logger.exception.assert_called_once()


class TestConvertPathValueToString:
    """Tests for _convert_path_value_to_string function."""

    def test_returns_string_unchanged(self) -> None:
        """Should return string value unchanged."""
        result = _convert_path_value_to_string("/path/to/file", "default", None)
        assert result == "/path/to/file"

    def test_returns_default_for_none(self) -> None:
        """Should return default when value is None."""
        result = _convert_path_value_to_string(None, "default.log", None)
        assert result == "default.log"

    def test_converts_int_to_string(self) -> None:
        """Should convert integer to string."""
        result = _convert_path_value_to_string(123, "default", None)
        assert result == "123"


class TestGetPathFromConfig:
    """Tests for _get_path_from_config function."""

    def test_returns_default_when_no_logging_section(self) -> None:
        """Should return default when logging section is missing."""
        config: dict[str, Any] = {}
        result = _get_path_from_config(config, "log_file", "default.log", None)
        assert result == "default.log"

    def test_returns_default_when_logging_not_dict(self) -> None:
        """Should return default when logging is not a dict."""
        config: dict[str, Any] = {"logging": "invalid"}
        result = _get_path_from_config(config, "log_file", "default.log", None)
        assert result == "default.log"

    def test_returns_value_from_config(self) -> None:
        """Should return value from logging config."""
        config: dict[str, Any] = {"logging": {"log_file": "custom.log"}}
        result = _get_path_from_config(config, "log_file", "default.log", None)
        assert result == "custom.log"

    def test_returns_default_when_key_not_in_logging(self) -> None:
        """Should return default when key is not in logging section."""
        config: dict[str, Any] = {"logging": {"other_key": "value"}}
        result = _get_path_from_config(config, "missing_key", "default.log", None)
        assert result == "default.log"


class TestGetFullLogPath:
    """Tests for get_full_log_path function."""

    def test_builds_full_path(self, tmp_path: Path) -> None:
        """Should build full path from base dir and relative path."""
        config: dict[str, Any] = {
            "logs_base_dir": str(tmp_path),
            "logging": {"main_log": "logs/main.log"},
        }

        result = get_full_log_path(config, "main_log", "default.log")

        assert result == str(tmp_path / "logs" / "main.log")

    def test_uses_default_when_config_is_none(self) -> None:
        """Should use default path when config is None."""
        result = get_full_log_path(None, "key", "default.log")
        assert result == "default.log"

    def test_creates_necessary_directories(self, tmp_path: Path) -> None:
        """Should create parent directories."""
        config: dict[str, Any] = {
            "logs_base_dir": str(tmp_path),
            "logging": {"main_log": "deep/nested/main.log"},
        }

        result = get_full_log_path(config, "main_log", "default.log")

        assert (tmp_path / "deep" / "nested").exists()
        assert result == str(tmp_path / "deep" / "nested" / "main.log")


class TestBuildConfigAliasMap:
    """Tests for _build_config_alias_map function."""

    def test_returns_empty_for_non_dict_config(self) -> None:
        """Should return empty list for non-dict config."""
        result = _build_config_alias_map(None)
        assert result == []

    def test_builds_alias_map_from_config(self, tmp_path: Path) -> None:
        """Should build alias map from config directories."""
        config: dict[str, Any] = {
            "apple_scripts_dir": str(tmp_path / "scripts"),
            "logs_base_dir": str(tmp_path / "logs"),
            "music_library_path": str(tmp_path / "music"),
        }

        result = _build_config_alias_map(config)

        assert len(result) == 3
        assert ("$SCRIPTS" in result[0][1]) or ("$LOGS" in result[1][1])


class TestTryConfigAliasReplacement:
    """Tests for _try_config_alias_replacement function."""

    def test_replaces_path_with_alias(self, tmp_path: Path) -> None:
        """Should replace matching path with alias."""
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        config: dict[str, Any] = {"apple_scripts_dir": str(scripts_dir)}

        result = _try_config_alias_replacement(str(scripts_dir / "test.scpt"), config)

        assert result is not None
        assert "$SCRIPTS" in result

    def test_returns_none_for_non_matching_path(self) -> None:
        """Should return None when path doesn't match any alias."""
        config: dict[str, Any] = {"apple_scripts_dir": "/some/path"}
        result = _try_config_alias_replacement("/other/path/file.txt", config)
        assert result is None


class TestTryHomeDirectoryReplacement:
    """Tests for _try_home_directory_replacement function."""

    def test_replaces_home_with_tilde(self) -> None:
        """Should replace home directory with ~."""
        home_path = str(PathLib.home() / "test" / "file.txt")
        result = _try_home_directory_replacement(home_path, None)

        assert result is not None
        assert result.startswith("~")

    def test_returns_tilde_for_exact_home(self) -> None:
        """Should return ~ for exact home directory path."""
        home_dir = str(PathLib.home())
        result = _try_home_directory_replacement(home_dir, None)

        assert result == "~"

    def test_returns_none_for_non_home_path(self) -> None:
        """Should return None when path is not under home."""
        result = _try_home_directory_replacement("/usr/local/bin", None)
        assert result is None


class TestShortenPath:
    """Tests for shorten_path function."""

    def test_returns_empty_for_empty_path(self) -> None:
        """Should return empty string for empty path."""
        assert shorten_path("") == ""

    def test_shortens_home_directory(self) -> None:
        """Should shorten home directory to ~."""
        home_path = str(PathLib.home() / "test")
        result = shorten_path(home_path)

        assert result.startswith("~")

    def test_shortens_config_paths(self, tmp_path: Path) -> None:
        """Should shorten paths matching config directories."""
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        config: dict[str, Any] = {"apple_scripts_dir": str(scripts_dir)}

        result = shorten_path(str(scripts_dir / "test.scpt"), config)

        assert "$SCRIPTS" in result

    def test_returns_filename_for_absolute_path(self) -> None:
        """Should return filename for unmatched absolute paths."""
        result = shorten_path("/usr/local/bin/python")
        assert result == "python"


class TestLoggerFilter:
    """Tests for LoggerFilter class."""

    def test_allows_exact_match(self) -> None:
        """Should allow records from exact logger match."""
        filter_obj = LoggerFilter(["my_logger"])
        record = logging.LogRecord(
            name="my_logger",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=(),
            exc_info=None,
        )

        assert filter_obj.filter(record) is True

    def test_allows_child_logger(self) -> None:
        """Should allow records from child loggers."""
        filter_obj = LoggerFilter(["parent"])
        record = logging.LogRecord(
            name="parent.child",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=(),
            exc_info=None,
        )

        assert filter_obj.filter(record) is True

    def test_rejects_non_matching_logger(self) -> None:
        """Should reject records from non-matching loggers."""
        filter_obj = LoggerFilter(["allowed_logger"])
        record = logging.LogRecord(
            name="other_logger",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=(),
            exc_info=None,
        )

        assert filter_obj.filter(record) is False

    def test_allows_multiple_loggers(self) -> None:
        """Should allow records from any of multiple allowed loggers."""
        filter_obj = LoggerFilter(["logger_a", "logger_b"])

        record_a = logging.LogRecord(
            name="logger_a",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=(),
            exc_info=None,
        )
        record_b = logging.LogRecord(
            name="logger_b",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=(),
            exc_info=None,
        )

        assert filter_obj.filter(record_a) is True
        assert filter_obj.filter(record_b) is True


class TestRunHandler:
    """Tests for RunHandler class."""

    def test_init_sets_max_runs(self) -> None:
        """Should set max_runs on initialization."""
        handler = RunHandler(max_runs=5)
        assert handler.max_runs == 5

    def test_init_creates_run_id(self) -> None:
        """Should create run_id on initialization."""
        handler = RunHandler()
        assert handler.current_run_id is not None
        assert len(handler.current_run_id) > 0

    def test_format_run_header_includes_new_run(self) -> None:
        """Should include NEW RUN in header."""
        header = RunHandler.format_run_header("test_logger")
        assert "NEW RUN" in header
        assert "test_logger" in header

    def test_format_run_footer_includes_end_run(self) -> None:
        """Should include END RUN in footer."""
        handler = RunHandler()
        footer = handler.format_run_footer("test_logger")
        assert "END RUN" in footer
        assert "test_logger" in footer
        assert "time:" in footer.lower()

    def test_trim_log_to_max_runs_does_nothing_when_no_file(self, tmp_path: Path) -> None:
        """Should do nothing when file doesn't exist."""
        handler = RunHandler(max_runs=3)
        handler.trim_log_to_max_runs(str(tmp_path / "nonexistent.log"))


class TestCompactFormatter:
    """Tests for CompactFormatter class."""

    def test_init_with_default_format(self) -> None:
        """Should use default format when none provided."""
        formatter = CompactFormatter()
        assert formatter._fmt is not None

    def test_init_with_custom_format(self) -> None:
        """Should use custom format when provided."""
        custom_fmt = "%(message)s"
        formatter = CompactFormatter(fmt=custom_fmt)
        assert custom_fmt in formatter._fmt

    def test_format_abbreviates_level(self) -> None:
        """Should abbreviate level names."""
        formatter = CompactFormatter(fmt="%(levelname)s - %(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/path/file.py",
            lineno=10,
            msg="test message",
            args=(),
            exc_info=None,
        )

        result = formatter.format(record)

        assert LEVEL_ABBREV["INFO"] in result

    def test_format_shortens_pathname(self) -> None:
        """Should shorten pathname in log output."""
        formatter = CompactFormatter(fmt="%(short_pathname)s - %(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="/very/long/path/to/file.py",
            lineno=10,
            msg="test message",
            args=(),
            exc_info=None,
        )

        result = formatter.format(record)

        assert "file.py" in result


class TestLoggable:
    """Tests for Loggable class."""

    def test_stores_loggers(self) -> None:
        """Should store console and error loggers."""
        console_logger = logging.getLogger("console")
        error_logger = logging.getLogger("error")

        loggable = Loggable(console_logger, error_logger)

        assert loggable.console_logger is console_logger
        assert loggable.error_logger is error_logger


class TestGetLogLevelsFromConfig:
    """Tests for _get_log_levels_from_config function."""

    def test_returns_default_levels_for_empty_config(self) -> None:
        """Should return default levels for empty config."""
        config: dict[str, Any] = {}
        levels = _get_log_levels_from_config(config)

        assert levels["console"] == logging.INFO
        assert levels["main_file"] == logging.INFO

    def test_parses_string_levels(self) -> None:
        """Should parse string level names."""
        config: dict[str, Any] = {
            "logging": {
                "levels": {
                    "console": "DEBUG",
                    "main_file": "WARNING",
                }
            }
        }

        levels = _get_log_levels_from_config(config)

        assert levels["console"] == logging.DEBUG
        assert levels["main_file"] == logging.WARNING

    def test_handles_case_insensitive_levels(self) -> None:
        """Should handle level names case-insensitively."""
        config: dict[str, Any] = {
            "logging": {
                "levels": {
                    "console": "debug",
                    "main_file": "ERROR",
                }
            }
        }

        levels = _get_log_levels_from_config(config)

        assert levels["console"] == logging.DEBUG
        assert levels["main_file"] == logging.ERROR


class TestGetLogFilePaths:
    """Tests for _get_log_file_paths function."""

    def test_returns_all_file_paths(self, tmp_path: Path) -> None:
        """Should return all log file paths."""
        config: dict[str, Any] = {
            "logs_base_dir": str(tmp_path),
            "logging": {},
        }

        paths = _get_log_file_paths(config)

        assert "main" in paths
        assert "analytics" in paths
        assert "year_changes" in paths
        assert "db_verify" in paths


class TestGetHtmlReportPath:
    """Tests for get_html_report_path function."""

    def test_returns_incremental_path_by_default(self, tmp_path: Path) -> None:
        """Should return incremental report path by default."""
        config: dict[str, Any] = {"logs_base_dir": str(tmp_path)}

        result = get_html_report_path(config)

        assert "analytics_incremental.html" in result

    def test_returns_full_path_in_force_mode(self, tmp_path: Path) -> None:
        """Should return full report path in force mode."""
        config: dict[str, Any] = {"logs_base_dir": str(tmp_path)}

        result = get_html_report_path(config, force_mode=True)

        assert "analytics_full.html" in result

    def test_handles_invalid_config(self) -> None:
        """Should handle invalid config gracefully."""
        result = get_html_report_path(None)
        assert "analytics" in result

    def test_creates_analytics_directory(self, tmp_path: Path) -> None:
        """Should create analytics directory."""
        config: dict[str, Any] = {"logs_base_dir": str(tmp_path)}

        get_html_report_path(config)

        assert (tmp_path / "analytics").exists()
