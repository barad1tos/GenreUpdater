# utils/logger.py
"""Enhanced Logger Module using QueueHandler for non-blocking file IO, with RichHandler for improved console output.

Provides a comprehensive logging system with detailed tracking and visual formatting. Features:

1.  **Run Tracking:** Adds headers/footers with timestamps and duration for script executions (`RunHandler`).
2.  **Log Rotation by Runs:** Keeps only the most recent N runs in log files (`RunHandler.trim_log_to_max_runs`).
3.  **Rich Console Output:** Uses `rich.logging.RichHandler` for color coding, formatting, and improved readability in the terminal.
4.  **Path Aliases:** Shortens file paths in log messages to readable aliases like `$SCRIPTS`, `$LOGS`, `$MUSIC_LIB`, `~` (`shorten_path`).
5.  **Multiple Loggers:** Configures distinct loggers for console output, main application/error logging, and analytics (`get_loggers`).
6.  **Non-Blocking File Logging:** Employs `QueueHandler` and `QueueListener` to prevent file I/O from blocking the main application thread.
7.  **Visual Indicators:** Uses emojis (噫, 潤) and separators for improved readability in log files and console.
8.  **Compact Formatting:** Provides a `CompactFormatter` (primarily for files) and configures `RichHandler` for a compact console view.
9.  **HTML Report Path:** Includes a helper function (`get_html_report_path`) to determine paths for analytics reports.
10. **Configuration Driven:** Relies on a configuration dictionary for paths, log levels, and other settings.
"""

from __future__ import annotations

import contextlib

# ---------------------------------------------------------------------------
# Golden Rule of Logging
# ---------------------------------------------------------------------------
# All runtime and debugging information **must** go through a configured
# ``logging.Logger`` instance.  Using ``print()`` for operational output leads
# to inconsistent formatting and missing log levels.  ``print()`` should only be
# used for the final end-user result that is not considered part of the logs.
# ---------------------------------------------------------------------------
import logging
import os
import queue
import re
import sys
import syslog
import time
import traceback
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from logging.handlers import QueueHandler, QueueListener
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from rich.console import Console
from rich.logging import RichHandler

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from rich.status import Status

    from core.models.track_models import AppConfig

# Explicit exports
__all__ = [
    "LEVEL_ABBREV",
    "CompactFormatter",
    "LogFormat",
    "Loggable",
    "LoggerFilter",
    "RunHandler",
    "RunTrackingHandler",
    "SafeQueueListener",
    "build_config_alias_map",
    "convert_path_value_to_string",
    "create_console_logger",
    "create_fallback_loggers",
    "ensure_directory",
    "get_full_log_path",
    "get_html_report_path",
    "get_log_file_paths",
    "get_log_levels_from_config",
    "get_loggers",
    "get_path_from_config",
    "get_shared_console",
    "setup_queue_logging",
    "shorten_path",
    "spinner",
    "try_config_alias_replacement",
    "try_home_directory_replacement",
]

# Module-level shared console container (avoids global statement)
_console_holder: dict[str, Console] = {}


def get_shared_console() -> Console:
    """Get or create the shared Rich console instance.

    All Rich output (logging, spinners, progress bars) should use this
    single Console instance to prevent output interleaving issues.

    Returns:
        The shared Console instance.

    """
    if "console" not in _console_holder:
        _console_holder["console"] = Console()
    return _console_holder["console"]


class SafeQueueListener(QueueListener):
    """A QueueListener wrapper that safely handles stop() calls."""

    def stop(self) -> None:
        """Stop the listener thread safely, handling cases where _thread is None."""
        try:
            if hasattr(self, "_thread") and self._thread is not None:
                super().stop()
            # If _thread is None or doesn't exist, the listener is already stopped
        except (AttributeError, RuntimeError, TypeError) as e:
            # Log the error if possible, but don't crash
            print(f"Warning: Error stopping QueueListener: {e}", file=sys.stderr)


# ANSI escape codes for colors - used by CompactFormatter (primarily for files now)
# RichHandler handles console colors automatically
RESET = "\033[0m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
GRAY = "\033[90m"
BOLD = "\033[1m"

# Level abbreviations used by CompactFormatter (primarily for file logs)
LEVEL_ABBREV = {
    "DEBUG": "D",
    "INFO": "I",
    "WARNING": "W",
    "ERROR": "E",
    "CRITICAL": "C",
}


class LogFormat:
    """Rich markup helpers for consistent log formatting.

    Provides IDE-like syntax highlighting for log messages using Rich markup.
    Use these methods to wrap values that should be highlighted in console output.

    Example:
        from core.logger import LogFormat as LF
        logger.info("Initializing %s...", LF.entity("CacheService"))
        logger.info("Loaded %s entries from %s", LF.number(100), LF.file("cache.json"))
    """

    @staticmethod
    def entity(name: str) -> str:
        """Format entity/class name with IDE-like highlighting (yellow).

        Args:
            name: Class, service, or component name

        Returns:
            Rich-formatted string with yellow highlighting
        """
        return f"[yellow]{name}[/yellow]"

    @staticmethod
    def file(name: str) -> str:
        """Format filename with cyan highlighting.

        Args:
            name: Filename or path

        Returns:
            Rich-formatted string with cyan highlighting
        """
        return f"[cyan]{name}[/cyan]"

    @staticmethod
    def number(value: float) -> str:
        """Format numbers with bright white highlighting.

        Args:
            value: Numeric value to format (int or float, since int is a subtype)

        Returns:
            Rich-formatted string with bright white highlighting
        """
        return f"[bright_white]{value}[/bright_white]"

    @staticmethod
    def success(text: str) -> str:
        """Format success status with green highlighting.

        Args:
            text: Success message or status

        Returns:
            Rich-formatted string with green highlighting
        """
        return f"[green]{text}[/green]"

    @staticmethod
    def error(text: str) -> str:
        """Format error status with red highlighting.

        Args:
            text: Error message or status

        Returns:
            Rich-formatted string with red highlighting
        """
        return f"[red]{text}[/red]"

    @staticmethod
    def warning(text: str) -> str:
        """Format warning status with yellow highlighting.

        Args:
            text: Warning message or status

        Returns:
            Rich-formatted string with yellow highlighting
        """
        return f"[yellow]{text}[/yellow]"

    @staticmethod
    def duration(seconds: float) -> str:
        """Format duration with dim styling.

        Args:
            seconds: Duration in seconds

        Returns:
            Rich-formatted string with duration
        """
        return f"[dim]{seconds:.1f}s[/dim]"

    @staticmethod
    def label(text: str) -> str:
        """Format label/key with bold styling.

        Args:
            text: Label text

        Returns:
            Rich-formatted string with bold styling
        """
        return f"[bold]{text}[/bold]"

    @staticmethod
    def dim(text: str) -> str:
        """Format text with dim styling for secondary info.

        Args:
            text: Secondary text

        Returns:
            Rich-formatted string with dim styling
        """
        return f"[dim]{text}[/dim]"


@asynccontextmanager
async def spinner(message: str, console: Console | None = None) -> AsyncGenerator[Status]:
    """Async context manager for indeterminate spinner during long operations.

    Use this for operations where progress cannot be tracked (e.g., AppleScript
    calls that return all data at once). The spinner provides visual feedback
    that the application is working.

    Args:
        message: Message to display next to the spinner
        console: Optional Rich Console instance (creates new one if not provided)

    Yields:
        Rich Status object that can be used to update the message

    Example:
        async with spinner("Fetching all track IDs from Music.app..."):
            result = await run_applescript(...)
    """
    _console = console or get_shared_console()
    with _console.status(f"[cyan]{message}[/cyan]") as status:
        yield status


class LoggerFilter:
    """Filter that only allows records from specific logger names."""

    def __init__(self, allowed_loggers: list[str]) -> None:
        """Initialize filter with allowed logger names.

        Args:
            allowed_loggers: List of logger names that should pass the filter.
                Child loggers (e.g., "main_logger.child") also pass if parent is allowed.

        """
        self.allowed_loggers = set(allowed_loggers)

    def filter(self, record: logging.LogRecord) -> bool:
        """Filter log records based on logger name.

        Args:
            record: The log record to filter.

        Returns:
            True if record's logger name matches or is child of allowed loggers.

        """
        return any(record.name == logger or record.name.startswith(f"{logger}.") for logger in self.allowed_loggers)


class RunHandler:
    """Handles tracking of script runs, adding separators between runs, and limiting logs to max number of runs."""

    def __init__(self, max_runs: int = 3) -> None:
        """Initialize the RunHandler for log run tracking.

        Args:
            max_runs: Maximum number of runs to keep in log files.
                Older runs are trimmed during log cleanup.

        """
        self.max_runs = max_runs
        self.current_run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        self.run_start_time = time.monotonic()  # Use monotonic for duration

    @staticmethod
    def format_run_header(logger_name: str) -> str:
        """Create a formatted header for a new run.

        Args:
            logger_name: Name of the logger starting the run.

        Returns:
            Formatted header string with timestamp and visual separators.

        """
        # Use local time for now.
        now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        # Use ANSI colors for file headers for readability in terminals that support them
        return f"\n\n{BLUE}{'=' * 80}{RESET}\n\ue05e NEW RUN: {logger_name} - {now_str}\n{BLUE}{'=' * 80}{RESET}\n\n"

    def format_run_footer(self, logger_name: str) -> str:
        """Create a formatted footer for the end of a run.

        Args:
            logger_name: Name of the logger ending the run.

        Returns:
            Formatted footer string with elapsed time and visual separators.

        """
        elapsed = time.monotonic() - self.run_start_time
        # Use ANSI colors for file footers
        return f"\n\n{BLUE}{'=' * 80}{RESET}\n\ue05e END RUN: {logger_name} - Total time: {elapsed:.2f}s\n{BLUE}{'=' * 80}{RESET}\n\n"

    def trim_log_to_max_runs(self, log_file: str) -> None:
        """Trims a log file to contain only the most recent N runs, identified by run headers."""
        if not Path(log_file).exists() or self.max_runs <= 0:
            return

        try:
            # Use text mode with utf-8 and ignore errors for simplicity.
            with Path(log_file).open(encoding="utf-8", errors="ignore") as f:
                # Read lines efficiently, especially for potentially large files
                lines = f.readlines()

            # Find indices of run headers
            # Look for the separator line followed by the " NEW RUN:" line
            header_indices = [
                i
                for i, line in enumerate(lines)
                # Check for the separator line (handle potential color codes)
                if re.match(r"^(\x1b\[\d+m)?={80}(\x1b\[0m)?$", line.strip())
                and i + 1 < len(lines)
                # Check for the run header line (handle potential color codes and emoji)
                and re.match(r"^(\x1b\[\d+m)?\ue05e NEW RUN:", lines[i + 1].strip())
            ]

            if len(header_indices) <= self.max_runs:
                return  # No trimming needed

            # Keep only the last 'max_runs' sections
            start_line_index = header_indices[-self.max_runs]  # Index of the oldest header to keep

            # Use atomic write pattern to avoid data loss if writing fails
            temp_log_file = f"{log_file}.tmp"
            with Path(temp_log_file).open("w", encoding="utf-8") as f:
                f.writelines(lines[start_line_index:])

            # Replace original file with temporary file
            Path(temp_log_file).replace(log_file)

        except OSError as e:
            # Use print for errors during logging setup/teardown as logger might not be available
            print(f"Error trimming log file {log_file}: {e}", file=sys.stderr)
        except (ValueError, TypeError, AttributeError) as e:
            # Catch unexpected errors during log trimming (non-filesystem related)
            print(f"Unexpected error trimming log file {log_file}: {e}", file=sys.stderr)


# Added optional error_logger argument for logging within the utility
def ensure_directory(path: str, error_logger: logging.Logger | None = None) -> None:
    """Ensure that the given directory path exists, creating it if necessary.

    Handles potential race conditions during creation. Logs errors using the provided logger.
    """
    try:
        # Check if the path is not empty and doesn't exist before trying to create
        if path and not Path(path).exists():
            Path(path).mkdir(parents=True, exist_ok=True)
    except OSError as e:
        # Handle potential errors during directory creation (e.g., permission issues)
        if error_logger:
            error_logger.exception("Error creating directory %s", path)
        else:
            print(f"ERROR: Error creating directory {path}: {e}", file=sys.stderr)


def convert_path_value_to_string(path_value: str | int | None, default: str, error_logger: logging.Logger | None) -> str:
    """Convert a config path value to string with fallback and error handling.

    Handles various input types from YAML config and provides safe conversion
    with logging on failure.

    Args:
        path_value: The path value from config (str, int, or None).
        default: Default string to return if conversion fails.
        error_logger: Optional logger for error reporting.

    Returns:
        String representation of path_value, or default on failure.

    """
    if path_value is None:
        return default
    if isinstance(path_value, str):
        return path_value
    try:
        return str(path_value)
    except (TypeError, ValueError):
        if error_logger is not None:
            error_logger.exception("Failed to convert path value to string")
        return default


def get_path_from_config(config: AppConfig, key: str, default: str, error_logger: logging.Logger | None) -> str:
    """Extract relative path from the logging config section.

    Safely retrieves a path value from the typed logging configuration
    using attribute access.

    Args:
        config: Typed application configuration.
        key: Attribute name in ``LoggingConfig`` (e.g. ``"main_log_file"``).
        default: Default path if attribute is missing or invalid.
        error_logger: Optional logger for error reporting.

    Returns:
        Path string from config, or default if not found/invalid.

    """
    value = getattr(config.logging, key, None)
    if value is None:
        return default

    return convert_path_value_to_string(value, default, error_logger)


def get_full_log_path(
    config: AppConfig | None,
    key: str,
    default: str,
    error_logger: logging.Logger | None = None,
) -> str:
    """Return the full log path by joining the base logs directory with the relative path.

    Ensures the base directory and the log file's directory exist.
    """
    logs_base_dir = ""
    relative_path = default

    if config is not None:
        logs_base_dir = config.logs_base_dir
        relative_path = get_path_from_config(config, key, default, error_logger)
    elif error_logger is not None:
        error_logger.error("Invalid config passed to get_full_log_path.")

    # Ensure the base directory exists
    ensure_directory(logs_base_dir, error_logger)

    # Join base directory and relative path
    full_path = str(Path(logs_base_dir) / relative_path)

    if log_dir := str(Path(full_path).parent):
        ensure_directory(log_dir, error_logger)

    return full_path


def build_config_alias_map(config: AppConfig | None) -> list[tuple[str, str]]:
    """Build alias mapping from config directories for path shortening.

    Creates ordered list of (directory, alias) tuples used by shorten_path()
    to replace long paths with readable aliases like $SCRIPTS, $LOGS.

    Args:
        config: Typed application configuration.

    Returns:
        List of (resolved_path, alias) tuples ordered by replacement priority.
        Returns empty list if config is None.

    """
    if config is None:
        return []

    scripts_dir = str(Path(config.apple_scripts_dir).resolve())
    logs_dir = str(Path(config.logs_base_dir).resolve())
    music_lib = str(Path(config.music_library_path).resolve())

    # Order matters — first matched prefix wins
    return [
        (scripts_dir, "$SCRIPTS"),
        (logs_dir, "$LOGS"),
        (music_lib, "$MUSIC_LIB"),
    ]


def try_config_alias_replacement(norm_path: str, config: AppConfig | None) -> str | None:
    """Attempt to replace path with config-based aliases.

    Checks if the normalized path starts with any configured directory
    and returns the aliased version if found.

    Args:
        norm_path: Normalized (os.path.normpath) absolute path.
        config: Typed application configuration.

    Returns:
        Aliased path string (e.g., "$SCRIPTS/fetch.scpt") if match found,
        None otherwise.

    """
    alias_map = build_config_alias_map(config)

    for base_dir, alias in alias_map:
        if base_dir and norm_path.startswith(base_dir):
            relative = str(Path(norm_path).relative_to(base_dir))
            return alias if relative == "." else f"{alias}{os.sep}{relative}"

    return None


def try_home_directory_replacement(norm_path: str, error_logger: logging.Logger | None) -> str | None:
    """Attempt to replace path with home directory shortcut (~).

    Safely resolves home directory and replaces it with ~ for readability.
    Handles OSError if home directory cannot be determined.

    Args:
        norm_path: Normalized absolute path to process.
        error_logger: Optional logger for warning on home resolution failure.

    Returns:
        Path with home directory replaced by ~, or None if no replacement made.

    """
    try:
        home_dir = str(Path.home())
        if norm_path.startswith(home_dir):
            return "~" if norm_path == home_dir else norm_path.replace(home_dir, "~", 1)
    except (OSError, AttributeError, KeyError) as exc:
        msg = f"shorten_path: failed to resolve home dir - {exc}"
        if error_logger:
            error_logger.warning(msg)
        else:
            print(f"WARNING: {msg}", file=sys.stderr)

    return None


def shorten_path(
    path: str,
    config: AppConfig | None = None,
    error_logger: logging.Logger | None = None,
) -> str:
    """Return a shortened, human-friendly representation of *path*.

    Priority of replacements (first match wins):
    1. User-defined directories from *config* → ``$SCRIPTS``, ``$LOGS``, ``$MUSIC_LIB``.
    2. Current user's home directory → ``~``.
    3. Any other absolute path collapses to its file name.

    The function never raises and always returns *some* string, falling back to the
    original *path* on errors. Internal issues are routed to *error_logger* or
    ``stderr`` if the logger is unavailable.
    """
    # Fast-fail on falsy or non-string input
    if not path:
        return path or ""

    norm_path = os.path.normpath(path)

    if config_result := try_config_alias_replacement(norm_path, config):
        return config_result

    if home_result := try_home_directory_replacement(norm_path, error_logger):
        return home_result

    # 3. Fallbacks
    if Path(norm_path).is_absolute() and Path(norm_path).parent.name:
        return Path(norm_path).name

    return norm_path


class CompactFormatter(logging.Formatter):
    """Custom log formatter for compact, readable output, primarily for file logs.

    Features: Level abbreviation, short timestamps, and precise path shortening
    applied to specific log record fields.
    RichHandler is used for console output.
    """

    _FormatStyleType = Literal["%", "{", "$"]  # Define type alias for style parameter

    def __init__(
        self,
        fmt: str | None = None,
        datefmt: str = "%H:%M:%S",
        style: Literal["%", "{", "$"] = "%",
        *,
        include_separator: bool = False,
        config: AppConfig | None = None,
    ) -> None:
        """Initialize the CompactFormatter."""
        # Define a default format string that includes placeholders for shortened paths
        # We will populate 'short_pathname' and 'short_filename' in the format method
        # Default format including time, level, logger name, shortened path/filename, and message
        # Example format string using custom attributes: %(short_pathname)s:%(lineno)d
        default_fmt = "%(asctime)s %(levelname)s [%(name)s] %(short_pathname)s:%(lineno)d - %(message)s"

        # Use the provided format string if available, otherwise use the default
        used_fmt = fmt if fmt is not None else default_fmt

        super().__init__(used_fmt, datefmt, style)
        self.include_separator = include_separator
        # Separator line uses ANSI colors for file logs
        self.separator_line = f"\n{BLUE}{'-' * 60}{RESET}"
        self.config = config

    def format(self, record: logging.LogRecord) -> str:
        """Format the LogRecord into a compact string, applying path shortening to specific attributes before standard formatting."""
        # Store original level name for abbreviation lookup
        original_levelname = record.levelname

        # --- Apply Path Shortening to Specific Attributes ---
        # Apply shorten_path to the original pathname and filename attributes
        # and store them in new attributes on the record object.
        # Ensure the record has the necessary attributes before trying to shorten
        if hasattr(record, "pathname"):
            # Apply shorten_path and store in a new attribute
            record.short_pathname = shorten_path(record.pathname, self.config)
        else:
            # If pathname is missing, use the original value (or provide a placeholder)
            record.short_pathname = getattr(
                record,
                "pathname",
                "N/A",
            )  # Use 'N/A' if attribute doesn't exist

        if hasattr(record, "filename"):
            # Apply shorten_path and store in a new attribute
            record.short_filename = shorten_path(record.filename, self.config)
        else:
            # If filename is missing, use the original value (or provide a placeholder)
            record.short_filename = getattr(
                record,
                "filename",
                "N/A",
            )  # Use 'N/A' if attribute doesn't exist

        # You could add other attributes here if they might contain paths, e.g., record.module

        # --- Abbreviate Level Name ---
        # Abbreviate level name for display in the formatted message
        # This modifies the record's levelname attribute temporarily for formatting
        record.levelname = LEVEL_ABBREV.get(
            original_levelname,
            original_levelname[:1],
        )  # Fallback to first letter

        # --- Perform Standard Formatting ---
        # Let the parent class handle the core formatting using the (potentially modified) record attributes
        try:
            # The format string defined in __init__ or passed as fmt will now use
            # %(short_pathname)s, %(short_filename)s, etc.
            formatted = super().format(record)
        except (ValueError, TypeError, KeyError, AttributeError) as format_error:
            # If the formatting fails, log the error and return a basic representation.
            # Use print for logging errors within the formatter as logger might be unavailable
            print(
                f"CRITICAL LOGGING ERROR during format: {format_error}",
                file=sys.stderr,
            )
            # Restore original level name before returning basic info
            record.levelname = original_levelname
            # Attempt to format a basic message to provide some info
            try:
                # Use original attributes here as custom ones might not be set if error happened early
                basic_msg = f"{getattr(record, 'asctime', 'N/A')} {original_levelname} [{getattr(record, 'name', 'N/A')}] {record.getMessage()}"
            except (ValueError, TypeError, AttributeError) as critical_error:
                # Log to stderr as last resort since regular logging failed
                error_msg = f"CRITICAL LOGGING ERROR: {critical_error}"
                print(error_msg, file=sys.stderr)
                # Also try to log using a different mechanism if available
                with contextlib.suppress(OSError, AttributeError):
                    syslog.syslog(syslog.LOG_CRIT, error_msg)
                return f"CRITICAL FORMATTING ERROR: {error_msg}"
            return f"FORMATTING ERROR: {basic_msg}"

        # --- Restore Original Level Name ---
        # Restore original levelname on the record object after formatting is done,
        # This is important if the record object is reused or inspected elsewhere by other handlers/formatters
        record.levelname = original_levelname

        # --- Remove Custom Attributes ---
        # Clean up the custom attributes we added to the record
        # This prevents them from potentially interfering with other handlers/formatters
        if hasattr(record, "short_pathname"):
            delattr(record, "short_pathname")
        if hasattr(record, "short_filename"):
            delattr(record, "short_filename")
        if hasattr(record, "short_module"):
            delattr(record, "short_module")

        # Add separator line if requested (for file logs)
        # This logic remains the same
        if getattr(record, "section_end", False) and self.include_separator:
            formatted += self.separator_line

        return formatted


class RunTrackingHandler(logging.FileHandler):
    """A file handler that adds run separation headers/footers and trims the log to a maximum number of runs based on headers."""

    def __init__(
        self,
        filename: str,
        *,
        mode: str = "a",
        encoding: str | None = "utf-8",
        delay: bool = False,
        run_handler: RunHandler | None = None,
    ) -> None:
        """Initialize the handler."""
        # Ensure directory exists before initializing FileHandler
        # Pass logger to ensure_directory
        # Cannot pass error_logger here easily before loggers are fully setup, use print fallback in ensure_directory
        ensure_directory(str(Path(filename).parent))
        super().__init__(filename, mode, encoding, delay)
        self.run_handler: RunHandler | None = run_handler
        self._header_written: bool = False  # Track if header for the *current* run has been written
        self._header_failed: bool = False  # Track if header writing failed
        self._closed: bool = False  # Track if handler is closed

    def emit(self, record: logging.LogRecord) -> None:
        """Emit a log record with run tracking.

        Writes the run header before the first record of a new run.
        Handles header write failures gracefully.

        Args:
            record: The log record to emit.

        """
        if self.run_handler and not self._header_written:
            try:
                # FIX: Add a check to ensure the stream is open before writing
                if self.stream and hasattr(self.stream, "write"):
                    header = self.run_handler.format_run_header(record.name)
                    # Use self.stream.write for direct writing to the file
                    self.stream.write(header)
                    # Ensure the header is immediately written to the file
                    self.flush()
                    self._header_written = True
            except OSError as header_error:
                # Log specific error details for debugging
                error_msg = f"Failed to write log header: {header_error}"
                print(error_msg, file=sys.stderr)

                # Still use standard error handling
                self.handleError(record)

                # Mark header as failed to prevent retry loops
                self._header_failed = True

        # Emit the actual record using the parent class method, also with a check
        if self.stream:
            super().emit(record)

    def close(self) -> None:
        """Close the handler with run cleanup.

        Writes the run footer, closes the file stream, and trims the log
        to maintain max_runs limit. Handles errors at each stage.

        """
        # Check if already closed to prevent recursion or multiple writes
        # Use a flag to prevent multiple close calls
        if getattr(self, "_closed", False):
            return

        # Set closed flag early
        self._closed = True

        try:
            if self.run_handler and self.stream and hasattr(self.stream, "write"):
                footer = self.run_handler.format_run_footer("Logger")
                # Use self.stream.write for direct writing to the file
                self.stream.write(footer)
                self.flush()  # Ensure footer is written

        except (OSError, AttributeError) as e:
            # Log the error to stderr as loggers might be shutting down
            print(
                f"ERROR: Failed to write log footer or flush stream for {self.baseFilename}: {e}",
                file=sys.stderr,
            )

        finally:
            # Close the stream using parent method *before* trimming
            try:
                super().close()
            except OSError as close_err:
                print(
                    f"Error closing file stream for {self.baseFilename}: {close_err}",
                    file=sys.stderr,
                )

            # Trim log file *after* closing the stream
            if self.run_handler and hasattr(self.run_handler, "max_runs") and self.run_handler.max_runs > 0:
                try:
                    self.run_handler.trim_log_to_max_runs(self.baseFilename)
                except (OSError, ValueError, AttributeError) as trim_e:
                    print(
                        f"Error trimming log file {self.baseFilename} after close: {trim_e}",
                        file=sys.stderr,
                    )


# Convenience base class for access to configured loggers
class Loggable:
    """Mixin providing ``console_logger`` and ``error_logger`` attributes."""

    def __init__(self, console_logger: logging.Logger, error_logger: logging.Logger) -> None:
        """Store the provided loggers."""
        self.console_logger = console_logger
        self.error_logger = error_logger


def get_log_levels_from_config(config: AppConfig) -> dict[str, int]:
    """Extract log levels from config for different logger targets.

    Reads the ``logging.levels`` section and converts string level
    names to ``logging`` module constants.

    Args:
        config: Typed application configuration.

    Returns:
        Dictionary mapping target names to logging level constants:
        ``"console"``, ``"main_file"``, ``"analytics_file"``.

    """
    levels_config = config.logging.levels

    log_levels: dict[str, int] = {
        "CRITICAL": logging.CRITICAL,
        "FATAL": logging.FATAL,
        "ERROR": logging.ERROR,
        "WARNING": logging.WARNING,
        "WARN": logging.WARNING,
        "INFO": logging.INFO,
        "DEBUG": logging.DEBUG,
        "NOTSET": logging.NOTSET,
    }

    def get_level_from_config(level_name: str | int | None, default_level: int = logging.INFO) -> int:
        """Get logging level constant from a string name, with fallback."""
        if not level_name:
            return default_level
        if isinstance(level_name, int):
            return level_name if level_name in log_levels.values() else default_level
        level_upper = str(level_name).upper()
        return log_levels.get(level_upper, default_level)

    return {
        "console": get_level_from_config(levels_config.console),
        "main_file": get_level_from_config(levels_config.main_file),
        "analytics_file": get_level_from_config(levels_config.analytics_file),
    }


def get_log_file_paths(config: AppConfig) -> dict[str, str]:
    """Get all log file paths from config with defaults.

    Resolves full paths for each log file by combining ``logs_base_dir``
    with relative paths from the logging config section.

    Args:
        config: Typed application configuration.

    Returns:
        Dictionary mapping log type to absolute file path:
        ``"main"``, ``"analytics"``, ``"db_verify"``.

    """
    return {
        "main": get_full_log_path(config, "main_log_file", "main/main.log"),
        "analytics": get_full_log_path(config, "analytics_log_file", "analytics/analytics.log"),
        "db_verify": get_full_log_path(config, "last_db_verify_log", "main/last_db_verify.log"),
    }


def create_console_logger(levels: dict[str, int]) -> logging.Logger:
    """Create and configure the console logger with RichHandler.

    Sets up a logger named "console_logger" with Rich formatting for
    colorized, readable console output. Only adds handler if not already present.

    Args:
        levels: Dictionary with "console" key containing logging level constant.

    Returns:
        Configured console logger instance with RichHandler attached.

    """
    console_logger = logging.getLogger("console_logger")
    if not console_logger.handlers:
        ch = RichHandler(
            level=levels["console"],
            console=get_shared_console(),
            show_path=False,
            enable_link_path=False,
            log_time_format="%H:%M:%S",
            markup=True,
        )
        ch.setLevel(levels["console"])
        console_logger.addHandler(ch)
        console_logger.setLevel(levels["console"])
        console_logger.propagate = False
    return console_logger


def setup_queue_logging(
    config: AppConfig, levels: dict[str, int], log_files: dict[str, str]
) -> tuple[logging.Logger, logging.Logger, logging.Logger, logging.Logger, SafeQueueListener]:
    """Set up queue-based file logging with all handlers."""
    max_runs = config.logging.max_runs
    run_handler = RunHandler(max_runs)

    file_formatter = CompactFormatter(
        "%(asctime)s %(levelname)s [%(name)s] %(short_pathname)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        config=config,
    )

    log_queue: queue.Queue[logging.LogRecord] = queue.Queue(-1)

    # Create file handlers with filters
    main_handler = RunTrackingHandler(log_files["main"], run_handler=run_handler)
    main_handler.setFormatter(file_formatter)
    main_handler.setLevel(levels["main_file"])
    main_handler.addFilter(LoggerFilter(["main_logger", "error_logger", "config"]))

    analytics_handler = RunTrackingHandler(log_files["analytics"], run_handler=run_handler)
    analytics_handler.setFormatter(file_formatter)
    analytics_handler.setLevel(levels["analytics_file"])
    analytics_handler.addFilter(LoggerFilter(["analytics_logger"]))

    db_verify_handler = RunTrackingHandler(log_files["db_verify"], run_handler=run_handler)
    db_verify_handler.setFormatter(file_formatter)
    db_verify_handler.setLevel(levels["main_file"])
    db_verify_handler.addFilter(LoggerFilter(["db_verify"]))

    file_handlers: list[logging.Handler] = [main_handler, analytics_handler, db_verify_handler]

    listener = SafeQueueListener(log_queue, *file_handlers, respect_handler_level=True)
    listener.start()
    print("QueueListener started.", file=sys.stderr)

    queue_handler = QueueHandler(log_queue)

    def setup_logger(logger_name: str, log_level: int) -> logging.Logger:
        """Set up a logger with the given name and level using the queue handler."""
        logger = logging.getLogger(logger_name)
        if not logger.handlers:
            logger.addHandler(queue_handler)
            logger.setLevel(log_level)
            logger.propagate = False
        return logger

    main_logger = setup_logger("main_logger", levels["main_file"])
    error_logger = setup_logger("error_logger", levels["main_file"])
    analytics_logger = setup_logger("analytics_logger", levels["analytics_file"])
    db_verify_logger = setup_logger("db_verify", levels["main_file"])
    setup_logger("config", levels["main_file"])

    return main_logger, error_logger, analytics_logger, db_verify_logger, listener


def get_loggers(
    config: AppConfig,
) -> tuple[logging.Logger, logging.Logger, logging.Logger, logging.Logger, SafeQueueListener | None]:
    """Create and return loggers with QueueHandler for non-blocking file logging.

    Sets up multiple loggers with different purposes and a shared queue listener
    for efficient file I/O. Uses RichHandler for colorized console output.

    Note:
        This function never raises exceptions. On setup failure, it returns
        fallback loggers with basic StreamHandler configuration.

    Args:
        config: Typed application configuration.

    Returns:
        Tuple of (console_logger, error_logger, analytics_logger,
        db_verify_logger, listener). Listener is ``None`` on failure.

    """
    try:
        levels = get_log_levels_from_config(config)
        log_files = get_log_file_paths(config)
        console_logger = create_console_logger(levels)

        _, error_logger, analytics_logger, db_verify_logger, listener = setup_queue_logging(config, levels, log_files)

    except (ImportError, OSError, ValueError, AttributeError, TypeError) as e:
        return create_fallback_loggers(e)

    console_logger.debug("Logging setup with QueueListener and RichHandler complete.")
    return console_logger, error_logger, analytics_logger, db_verify_logger, listener


def create_fallback_loggers(
    e: Exception,
) -> tuple[logging.Logger, logging.Logger, logging.Logger, logging.Logger, None]:
    """Create fallback loggers when main logger setup fails.

    Configures basic logging as a safety net when QueueListener or RichHandler
    initialization fails. Logs the original error and returns simple StreamHandler
    loggers.

    Args:
        e: The exception that caused main logger setup to fail.

    Returns:
        Tuple of (console_logger, error_logger, analytics_logger, db_verify_logger, None).
        The listener is None since queue-based logging failed.

    """
    print(
        f"FATAL ERROR: Failed to configure custom logging with QueueListener and RichHandler: {e}",
        file=sys.stderr,
    )
    traceback.print_exc(file=sys.stderr)

    # Configure basic logging as a fallback
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    # Log the error using the fallback basic config
    logging.critical("Fallback basic logging configured due to error: %s", e)
    # Return basic loggers and None for listener
    console_fallback = logging.getLogger("console_fallback")
    error_fallback = logging.getLogger("error_fallback")
    analytics_fallback = logging.getLogger("analytics_fallback")
    db_verify_fallback = logging.getLogger("db_verify_fallback")
    # Ensure fallback loggers have at least one handler to output messages
    if not console_fallback.handlers:
        console_fallback.addHandler(logging.StreamHandler(sys.stdout))
    if not error_fallback.handlers:
        error_fallback.addHandler(logging.StreamHandler(sys.stderr))
    if not analytics_fallback.handlers:
        analytics_fallback.addHandler(logging.StreamHandler(sys.stdout))
    if not db_verify_fallback.handlers:
        db_verify_fallback.addHandler(logging.StreamHandler(sys.stdout))

    return console_fallback, error_fallback, analytics_fallback, db_verify_fallback, None


def get_html_report_path(config: AppConfig | None, force_mode: bool = False) -> str:
    """Get the path for HTML analytics report based on run mode."""
    if config is None:
        print("ERROR: Invalid config passed to get_html_report_path.", file=sys.stderr)
        logs_base_dir = ""
    else:
        logs_base_dir = config.logs_base_dir

    analytics_dir = str(Path(logs_base_dir) / "analytics")
    ensure_directory(analytics_dir)

    report_filename = "analytics_full.html" if force_mode else "analytics_incremental.html"
    return str(Path(analytics_dir) / report_filename)
