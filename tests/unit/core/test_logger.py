"""Unit tests for logger module."""

from __future__ import annotations

import logging
from pathlib import Path as PathLib
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

from core.logger import (
    LEVEL_ABBREV,
    CompactFormatter,
    LogFormat,
    Loggable,
    LoggerFilter,
    RunHandler,
    RunTrackingHandler,
    SafeQueueListener,
    build_config_alias_map,
    convert_path_value_to_string,
    create_console_logger,
    create_fallback_loggers,
    ensure_directory,
    get_full_log_path,
    get_html_report_path,
    get_log_file_paths,
    get_log_levels_from_config,
    get_loggers,
    get_path_from_config,
    setup_queue_logging,
    shorten_path,
    spinner,
    try_config_alias_replacement,
    try_home_directory_replacement,
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

        with patch("core.logger.Path.mkdir", side_effect=OSError("Permission denied")):
            ensure_directory(str(tmp_path / "test"), error_logger)

        error_logger.exception.assert_called_once()


class TestConvertPathValueToString:
    """Tests for convert_path_value_to_string function."""

    def test_returns_string_unchanged(self) -> None:
        """Should return string value unchanged."""
        result = convert_path_value_to_string("/path/to/file", "default", None)
        assert result == "/path/to/file"

    def test_returns_default_for_none(self) -> None:
        """Should return default when value is None."""
        result = convert_path_value_to_string(None, "default.log", None)
        assert result == "default.log"

    def test_converts_int_to_string(self) -> None:
        """Should convert integer to string."""
        result = convert_path_value_to_string(123, "default", None)
        assert result == "123"


class TestGetPathFromConfig:
    """Tests for get_path_from_config function."""

    def test_returns_default_when_no_logging_section(self) -> None:
        """Should return default when logging section is missing."""
        config: dict[str, Any] = {}
        result = get_path_from_config(config, "log_file", "default.log", None)
        assert result == "default.log"

    def test_returns_default_when_logging_not_dict(self) -> None:
        """Should return default when logging is not a dict."""
        config: dict[str, Any] = {"logging": "invalid"}
        result = get_path_from_config(config, "log_file", "default.log", None)
        assert result == "default.log"

    def test_returns_value_from_config(self) -> None:
        """Should return value from logging config."""
        config: dict[str, Any] = {"logging": {"log_file": "custom.log"}}
        result = get_path_from_config(config, "log_file", "default.log", None)
        assert result == "custom.log"

    def test_returns_default_when_key_not_in_logging(self) -> None:
        """Should return default when key is not in logging section."""
        config: dict[str, Any] = {"logging": {"other_key": "value"}}
        result = get_path_from_config(config, "missing_key", "default.log", None)
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
    """Tests for build_config_alias_map function."""

    def test_returns_empty_for_non_dict_config(self) -> None:
        """Should return empty list for non-dict config."""
        result = build_config_alias_map(None)
        assert result == []

    def test_builds_alias_map_from_config(self, tmp_path: Path) -> None:
        """Should build alias map from config directories."""
        config: dict[str, Any] = {
            "apple_scripts_dir": str(tmp_path / "scripts"),
            "logs_base_dir": str(tmp_path / "logs"),
            "music_library_path": str(tmp_path / "music"),
        }

        result = build_config_alias_map(config)

        assert len(result) == 3
        assert ("$SCRIPTS" in result[0][1]) or ("$LOGS" in result[1][1])


class TestTryConfigAliasReplacement:
    """Tests for try_config_alias_replacement function."""

    def test_replaces_path_with_alias(self, tmp_path: Path) -> None:
        """Should replace matching path with alias."""
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        config: dict[str, Any] = {"apple_scripts_dir": str(scripts_dir)}

        result = try_config_alias_replacement(str(scripts_dir / "test.scpt"), config)

        assert result is not None
        assert "$SCRIPTS" in result

    def test_returns_none_for_non_matching_path(self) -> None:
        """Should return None when path doesn't match any alias."""
        config: dict[str, Any] = {"apple_scripts_dir": "/some/path"}
        result = try_config_alias_replacement("/other/path/file.txt", config)
        assert result is None


class TestTryHomeDirectoryReplacement:
    """Tests for try_home_directory_replacement function."""

    def test_replaces_home_with_tilde(self) -> None:
        """Should replace home directory with ~."""
        home_path = str(PathLib.home() / "test" / "file.txt")
        result = try_home_directory_replacement(home_path, None)

        assert result is not None
        assert result.startswith("~")

    def test_returns_tilde_for_exact_home(self) -> None:
        """Should return ~ for exact home directory path."""
        home_dir = str(PathLib.home())
        result = try_home_directory_replacement(home_dir, None)

        assert result == "~"

    def test_returns_none_for_non_home_path(self) -> None:
        """Should return None when path is not under home."""
        result = try_home_directory_replacement("/usr/local/bin", None)
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

    @staticmethod
    def _create_log_record(name: str) -> logging.LogRecord:
        """Create a log record with the given logger name."""
        return logging.LogRecord(
            name=name,
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=(),
            exc_info=None,
        )

    def test_allows_exact_match(self) -> None:
        """Should allow records from exact logger match."""
        self._assert_filter_result("my_logger", "my_logger", expected=True)

    def test_allows_child_logger(self) -> None:
        """Should allow records from child loggers."""
        self._assert_filter_result("parent", "parent.child", expected=True)

    def test_rejects_non_matching_logger(self) -> None:
        """Should reject records from non-matching loggers."""
        self._assert_filter_result("allowed_logger", "other_logger", expected=False)

    def _assert_filter_result(
        self,
        allowed_logger: str,
        record_logger: str,
        *,
        expected: bool,
    ) -> None:
        """Assert that filter returns expected result for given loggers."""
        filter_obj = LoggerFilter([allowed_logger])
        record = self._create_log_record(record_logger)
        assert filter_obj.filter(record) is expected

    def test_allows_multiple_loggers(self) -> None:
        """Should allow records from any of multiple allowed loggers."""
        filter_obj = LoggerFilter(["logger_a", "logger_b"])

        record_a = self._create_log_record("logger_a")
        record_b = self._create_log_record("logger_b")

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
        handler = RunHandler()
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
        assert formatter._fmt is not None
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
    """Tests for get_log_levels_from_config function."""

    def test_returns_default_levels_for_empty_config(self) -> None:
        """Should return default levels for empty config."""
        config: dict[str, Any] = {}
        levels = get_log_levels_from_config(config)

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

        levels = get_log_levels_from_config(config)

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

        levels = get_log_levels_from_config(config)

        assert levels["console"] == logging.DEBUG
        assert levels["main_file"] == logging.ERROR


class TestGetLogFilePaths:
    """Tests for get_log_file_paths function."""

    def test_returns_all_file_paths(self, tmp_path: Path) -> None:
        """Should return all log file paths."""
        config: dict[str, Any] = {
            "logs_base_dir": str(tmp_path),
            "logging": {},
        }

        paths = get_log_file_paths(config)

        assert "main" in paths
        assert "analytics" in paths
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


class TestSafeQueueListener:
    """Tests for SafeQueueListener class."""

    def test_stop_when_thread_exists(self) -> None:
        """Should stop normally when thread exists."""
        import queue

        q: queue.Queue[logging.LogRecord] = queue.Queue()
        handler = logging.NullHandler()
        listener = SafeQueueListener(q, handler)
        listener.start()

        listener.stop()

        assert not listener._thread or not listener._thread.is_alive()

    def test_stop_when_thread_is_none(self) -> None:
        """Should not crash when _thread is None."""
        import queue

        q: queue.Queue[logging.LogRecord] = queue.Queue()
        handler = logging.NullHandler()
        listener = SafeQueueListener(q, handler)
        listener._thread = None

        listener.stop()

    def test_stop_when_no_thread_attribute(self) -> None:
        """Should not crash when _thread attribute doesn't exist."""
        import queue

        q: queue.Queue[logging.LogRecord] = queue.Queue()
        handler = logging.NullHandler()
        listener = SafeQueueListener(q, handler)
        if hasattr(listener, "_thread"):
            delattr(listener, "_thread")

        listener.stop()

    def test_stop_handles_runtime_error(self) -> None:
        """Should handle RuntimeError during stop."""
        import queue

        q: queue.Queue[logging.LogRecord] = queue.Queue()
        handler = logging.NullHandler()
        listener = SafeQueueListener(q, handler)
        listener.start()

        with patch.object(
            SafeQueueListener.__bases__[0],
            "stop",
            side_effect=RuntimeError("Thread error"),
        ):
            listener.stop()


class TestLogFormat:
    """Tests for LogFormat class."""

    def test_entity_formats_with_yellow(self) -> None:
        """Should format entity with yellow markup."""
        result = LogFormat.entity("CacheService")
        assert result == "[yellow]CacheService[/yellow]"

    def test_file_formats_with_cyan(self) -> None:
        """Should format file with cyan markup."""
        result = LogFormat.file("cache.json")
        assert result == "[cyan]cache.json[/cyan]"

    def test_number_formats_with_bright_white(self) -> None:
        """Should format number with bright_white markup."""
        result = LogFormat.number(100)
        assert result == "[bright_white]100[/bright_white]"

    def test_number_formats_float(self) -> None:
        """Should format float number."""
        result = LogFormat.number(3.14)
        assert result == "[bright_white]3.14[/bright_white]"

    def test_success_formats_with_green(self) -> None:
        """Should format success with green markup."""
        result = LogFormat.success("OK")
        assert result == "[green]OK[/green]"

    def test_error_formats_with_red(self) -> None:
        """Should format error with red markup."""
        result = LogFormat.error("FAILED")
        assert result == "[red]FAILED[/red]"

    def test_warning_formats_with_yellow(self) -> None:
        """Should format warning with yellow markup."""
        result = LogFormat.warning("WARN")
        assert result == "[yellow]WARN[/yellow]"

    def test_duration_formats_with_dim(self) -> None:
        """Should format duration with dim markup and 1 decimal."""
        result = LogFormat.duration(3.567)
        assert result == "[dim]3.6s[/dim]"

    def test_duration_formats_whole_number(self) -> None:
        """Should format whole number duration."""
        result = LogFormat.duration(10.0)
        assert result == "[dim]10.0s[/dim]"

    def test_label_formats_with_bold(self) -> None:
        """Should format label with bold markup."""
        result = LogFormat.label("Status")
        assert result == "[bold]Status[/bold]"

    def test_dim_formats_with_dim(self) -> None:
        """Should format text with dim markup."""
        result = LogFormat.dim("secondary info")
        assert result == "[dim]secondary info[/dim]"


class TestSpinner:
    """Tests for spinner async context manager."""

    @pytest.mark.asyncio
    async def test_yields_status_object(self) -> None:
        """Should yield a Status object."""
        from rich.status import Status

        async with spinner("Loading...") as status:
            assert isinstance(status, Status)

    @pytest.mark.asyncio
    async def test_uses_provided_console(self) -> None:
        """Should use provided console."""
        from rich.console import Console

        console = Console(force_terminal=True)

        async with spinner("Loading...", console=console) as status:
            assert status is not None

    @pytest.mark.asyncio
    async def test_creates_console_if_not_provided(self) -> None:
        """Should create console if not provided."""
        async with spinner("Loading...") as status:
            assert status is not None

    @pytest.mark.asyncio
    async def test_message_is_displayed(self) -> None:
        """Should display the provided message."""
        from io import StringIO

        from rich.console import Console

        output = StringIO()
        console = Console(file=output, force_terminal=True)

        async with spinner("Test message", console=console):
            pass


class TestRunTrackingHandler:
    """Tests for RunTrackingHandler class."""

    def test_init_creates_handler(self, tmp_path: Path) -> None:
        """Should create handler with file."""
        log_file = tmp_path / "test.log"
        handler = RunTrackingHandler(str(log_file))
        handler.close()

        assert log_file.exists()

    def test_init_stores_run_handler(self, tmp_path: Path) -> None:
        """Should store run_handler reference."""
        log_file = tmp_path / "test.log"
        run_handler = RunHandler(max_runs=5)

        handler = RunTrackingHandler(str(log_file), run_handler=run_handler)
        handler.close()

        assert handler.run_handler is run_handler

    def test_emit_writes_header_before_first_record(self, tmp_path: Path) -> None:
        """Should write header before first log record."""
        log_file = tmp_path / "test.log"
        run_handler = RunHandler()
        handler = RunTrackingHandler(str(log_file), run_handler=run_handler)
        handler.setFormatter(logging.Formatter("%(message)s"))

        content = self._emit_record_and_verify_footer("Test message", handler, log_file, "NEW RUN")
        assert "Test message" in content

    def test_emit_writes_header_only_once(self, tmp_path: Path) -> None:
        """Should write header only before first record."""
        log_file = tmp_path / "test.log"
        run_handler = RunHandler()
        handler = RunTrackingHandler(str(log_file), run_handler=run_handler)
        handler.setFormatter(logging.Formatter("%(message)s"))

        for i in range(3):
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg=f"Message {i}",
                args=(),
                exc_info=None,
            )
            handler.emit(record)

        handler.close()

        content = log_file.read_text()
        assert content.count("NEW RUN") == 1

    def test_close_writes_footer(self, tmp_path: Path) -> None:
        """Should write footer on close."""
        log_file = tmp_path / "test.log"
        run_handler = RunHandler()
        handler = RunTrackingHandler(str(log_file), run_handler=run_handler)
        handler.setFormatter(logging.Formatter("%(message)s"))

        self._emit_record_and_verify_footer("Test", handler, log_file, "END RUN")

    def test_close_prevents_double_close(self, tmp_path: Path) -> None:
        """Should not crash on double close."""
        log_file = tmp_path / "test.log"
        handler = RunTrackingHandler(str(log_file))

        handler.close()
        handler.close()

    def test_emit_without_run_handler(self, tmp_path: Path) -> None:
        """Should emit records without run handler."""
        log_file = tmp_path / "test.log"
        handler = RunTrackingHandler(str(log_file))
        handler.setFormatter(logging.Formatter("%(message)s"))

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        handler.emit(record)
        handler.close()

        content = log_file.read_text()
        assert "Test message" in content
        assert "NEW RUN" not in content

    def test_close_trims_log_file(self, tmp_path: Path) -> None:
        """Should trim log file on close when max_runs > 0."""
        log_file = tmp_path / "test.log"
        run_handler = RunHandler(max_runs=2)
        handler = RunTrackingHandler(str(log_file), run_handler=run_handler)
        handler.setFormatter(logging.Formatter("%(message)s"))

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Test",
            args=(),
            exc_info=None,
        )
        handler.emit(record)
        handler.close()

    @staticmethod
    def _emit_record_and_verify_footer(
        msg: str,
        handler: RunTrackingHandler,
        log_file: PathLib,
        expected_footer: str,
    ) -> str:
        """Emit a log record and verify the footer is written on close.

        Returns the log file content for additional assertions.
        """
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=msg,
            args=(),
            exc_info=None,
        )
        handler.emit(record)
        handler.close()
        result = log_file.read_text()
        assert expected_footer in result
        return result

    def test_emit_handles_header_write_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Should handle OSError when writing header."""
        log_file = tmp_path / "test.log"
        run_handler = RunHandler()
        handler = RunTrackingHandler(str(log_file), run_handler=run_handler)
        handler.setFormatter(logging.Formatter("%(message)s"))
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Test",
            args=(),
            exc_info=None,
        )
        with patch.object(handler.stream, "write", side_effect=OSError("Write failed")):
            handler.emit(record)
        assert handler._header_failed is True
        captured = capsys.readouterr()
        assert "Failed to write log header" in captured.err
        handler.close()

    def test_close_handles_close_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Should handle error when closing stream."""
        log_file = tmp_path / "test.log"
        handler = RunTrackingHandler(str(log_file))

        with patch.object(logging.FileHandler, "close", side_effect=OSError("Close failed")):
            handler.close()

        captured = capsys.readouterr()
        assert "Error closing file stream" in captured.err

    def test_close_handles_footer_write_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Should handle error when writing footer."""
        log_file = tmp_path / "test.log"
        run_handler = RunHandler()
        handler = RunTrackingHandler(str(log_file), run_handler=run_handler)
        handler.setFormatter(logging.Formatter("%(message)s"))
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Test",
            args=(),
            exc_info=None,
        )
        handler.emit(record)
        with patch.object(handler.stream, "write", side_effect=OSError("Write failed")):
            handler.close()
        captured = capsys.readouterr()
        assert "Failed to write log footer" in captured.err

    def test_close_handles_trim_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Should handle error when trimming log file."""
        log_file = tmp_path / "test.log"
        run_handler = RunHandler()
        handler = RunTrackingHandler(str(log_file), run_handler=run_handler)

        with patch.object(run_handler, "trim_log_to_max_runs", side_effect=OSError("Trim failed")):
            handler.close()

        captured = capsys.readouterr()
        assert "Error trimming log file" in captured.err


class TestRunHandlerTrim:
    """Additional tests for RunHandler trim functionality."""

    def test_trim_log_to_max_runs_trims_old_runs(self, tmp_path: Path) -> None:
        """Should trim log file to max runs."""
        log_file = tmp_path / "test.log"
        run_handler = RunHandler(max_runs=2)

        separator = "=" * 80
        content = (
            f"{separator}\n"
            "\ue05e NEW RUN: run1 - 2024-01-01\n"
            f"{separator}\n"
            "Log entry 1\n"
            f"{separator}\n"
            "\ue05e NEW RUN: run2 - 2024-01-02\n"
            f"{separator}\n"
            "Log entry 2\n"
            f"{separator}\n"
            "\ue05e NEW RUN: run3 - 2024-01-03\n"
            f"{separator}\n"
            "Log entry 3\n"
        )
        log_file.write_text(content)

        run_handler.trim_log_to_max_runs(str(log_file))

        trimmed_content = log_file.read_text()
        assert "run1" not in trimmed_content
        assert "run2" in trimmed_content
        assert "run3" in trimmed_content

    def test_trim_log_handles_oserror(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Should handle OSError during trimming."""
        log_file = tmp_path / "test.log"
        run_handler = RunHandler(max_runs=1)

        separator = "=" * 80
        content = f"{separator}\n\ue05e NEW RUN: run1 - 2024-01-01\n{separator}\n\ue05e NEW RUN: run2 - 2024-01-02\n"
        log_file.write_text(content)

        with patch("core.logger.Path.open", side_effect=OSError("Permission denied")):
            run_handler.trim_log_to_max_runs(str(log_file))

        captured = capsys.readouterr()
        assert "Error trimming log file" in captured.err


class TestCreateConsoleLogger:
    """Tests for create_console_logger function."""

    def test_creates_logger_with_rich_handler(self) -> None:
        """Should create logger with RichHandler."""
        from rich.logging import RichHandler

        levels = {"console": logging.INFO}

        logger = create_console_logger(levels)

        assert logger.name == "console_logger"
        assert any(isinstance(h, RichHandler) for h in logger.handlers)

    def test_sets_correct_level(self) -> None:
        """Should set correct log level."""
        levels = {"console": logging.DEBUG}

        _logger_name = f"console_logger_{id(object())}"
        with patch("core.logger.logging.getLogger") as mock_get_logger:
            mock_logger = MagicMock()
            mock_logger.handlers = []
            mock_get_logger.return_value = mock_logger

            create_console_logger(levels)

            mock_logger.setLevel.assert_called_with(logging.DEBUG)

    def test_does_not_add_handler_twice(self) -> None:
        """Should not add handler if already exists."""
        from rich.logging import RichHandler

        levels = {"console": logging.INFO}

        logger = create_console_logger(levels)
        handler_count = len([h for h in logger.handlers if isinstance(h, RichHandler)])

        create_console_logger(levels)

        new_handler_count = len([h for h in logger.handlers if isinstance(h, RichHandler)])
        assert new_handler_count == handler_count


class TestSetupQueueLogging:
    """Tests for setup_queue_logging function."""

    def test_returns_all_loggers(self, tmp_path: Path) -> None:
        """Should return all loggers and listener."""
        config: dict[str, Any] = {
            "logs_base_dir": str(tmp_path),
            "logging": {"max_runs": 2},
        }
        levels = {
            "console": logging.INFO,
            "main_file": logging.DEBUG,
            "analytics_file": logging.INFO,
        }
        log_files = {
            "main": str(tmp_path / "main.log"),
            "analytics": str(tmp_path / "analytics.log"),
            "db_verify": str(tmp_path / "db_verify.log"),
        }

        result = setup_queue_logging(config, levels, log_files)
        main_logger, error_logger, analytics_logger, db_verify_logger, listener = result

        assert main_logger is not None
        assert error_logger is not None
        assert analytics_logger is not None
        assert db_verify_logger is not None
        assert isinstance(listener, SafeQueueListener)

        listener.stop()

    def test_creates_log_files(self, tmp_path: Path) -> None:
        """Should create log file directories."""
        config: dict[str, Any] = {
            "logs_base_dir": str(tmp_path),
            "logging": {},
        }
        levels = {
            "console": logging.INFO,
            "main_file": logging.DEBUG,
            "analytics_file": logging.INFO,
        }
        log_files = {
            "main": str(tmp_path / "logs" / "main.log"),
            "analytics": str(tmp_path / "logs" / "analytics.log"),
            "db_verify": str(tmp_path / "logs" / "db_verify.log"),
        }

        _, _, _, _, listener = setup_queue_logging(config, levels, log_files)
        listener.stop()

        assert (tmp_path / "logs").exists()


class TestGetLoggers:
    """Tests for get_loggers function."""

    def test_returns_tuple_of_loggers(self, tmp_path: Path) -> None:
        """Should return tuple of loggers."""
        config: dict[str, Any] = {
            "logs_base_dir": str(tmp_path),
            "logging": {
                "main_log": "main.log",
                "analytics_log": "analytics.log",
                "db_verify_log": "db_verify.log",
            },
        }

        result = get_loggers(config)
        console, error, analytics, db_verify, listener = result

        assert console is not None
        assert error is not None
        assert analytics is not None
        assert db_verify is not None

        if listener:
            listener.stop()

    def test_returns_fallback_on_error(self) -> None:
        """Should return fallback loggers on error."""
        with patch(
            "core.logger.get_log_levels_from_config",
            side_effect=ValueError("Config error"),
        ):
            result = get_loggers({})

        console, _error, _analytics, _db_verify, listener = result
        assert listener is None
        assert console.name == "console_fallback"


class TestCreateFallbackLoggers:
    """Tests for create_fallback_loggers function."""

    def test_returns_four_loggers_and_none_listener(self) -> None:
        """Should return four fallback loggers and None listener."""
        result = create_fallback_loggers(ValueError("Test error"))
        console, error, analytics, db_verify, listener = result

        assert console.name == "console_fallback"
        assert error.name == "error_fallback"
        assert analytics.name == "analytics_fallback"
        assert db_verify.name == "db_verify_fallback"
        assert listener is None

    def test_adds_stream_handlers(self) -> None:
        """Should add stream handlers to fallback loggers."""
        console, error, analytics, db_verify, _ = create_fallback_loggers(RuntimeError("Setup failed"))

        assert len(console.handlers) > 0
        assert len(error.handlers) > 0
        assert len(analytics.handlers) > 0
        assert len(db_verify.handlers) > 0

    def test_logs_error_message(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Should log error message to stderr."""
        create_fallback_loggers(TypeError("Type mismatch"))

        captured = capsys.readouterr()
        assert "FATAL ERROR" in captured.err
        assert "Type mismatch" in captured.err


class TestAppConfigPath:
    """Tests that logger functions accept AppConfig alongside dict."""

    @staticmethod
    def _make_app_config(tmp_path: PathLib) -> Any:
        """Create an AppConfig with tmp_path as logs_base_dir."""
        from tests.factories import create_test_app_config

        return create_test_app_config(
            music_library_path=str(tmp_path / "library"),
            apple_scripts_dir=str(tmp_path / "scripts"),
            logs_base_dir=str(tmp_path / "logs"),
            logging={
                "max_runs": 3,
                "main_log_file": "main.log",
                "analytics_log_file": "analytics.log",
                "csv_output_file": "output.csv",
                "changes_report_file": "changes.json",
                "dry_run_report_file": "dryrun.json",
                "last_incremental_run_file": "lastrun.json",
                "pending_verification_file": "pending.json",
                "last_db_verify_log": "dbverify.log",
                "levels": {"console": "DEBUG", "main_file": "DEBUG", "analytics_file": "INFO"},
            },
        )

    def test_get_full_log_path_with_app_config(self, tmp_path: PathLib) -> None:
        """get_full_log_path should resolve paths from AppConfig."""
        app_config = self._make_app_config(tmp_path)
        result = get_full_log_path(app_config, "main_log_file", "default.log")

        expected_base = str(tmp_path / "logs")
        assert result.startswith(expected_base)
        assert "main.log" in result

    def test_get_html_report_path_with_app_config(self, tmp_path: PathLib) -> None:
        """get_html_report_path should resolve path from AppConfig."""
        app_config = self._make_app_config(tmp_path)
        result = get_html_report_path(app_config)

        assert "analytics_incremental.html" in result
        expected_analytics_dir = tmp_path / "logs" / "analytics"
        assert expected_analytics_dir.exists()

    def test_get_log_levels_from_config_with_app_config(self, tmp_path: PathLib) -> None:
        """get_log_levels_from_config should parse levels from AppConfig."""
        app_config = self._make_app_config(tmp_path)
        levels = get_log_levels_from_config(app_config)

        assert levels["console"] == logging.DEBUG
        assert levels["main_file"] == logging.DEBUG
        assert levels["analytics_file"] == logging.INFO
