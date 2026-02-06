"""AppleScript Client Module.

This module provides an abstraction for executing AppleScript commands asynchronously.
It centralizes the logic for interacting with AppleScript via the `osascript` command,
handles errors, applies concurrency limits via semaphore-based control, and ensures non-blocking execution.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.logger import LogFormat, spinner
from core.models.protocols import AppleScriptClientProtocol
from core.tracks.track_delta import FIELD_SEPARATOR, LINE_SEPARATOR
from metrics import Analytics
from services.apple.applescript_executor import AppleScriptExecutor
from services.apple.file_validator import AppleScriptFileValidator
from services.apple.rate_limiter import EnhancedRateLimiter
from services.apple.sanitizer import AppleScriptSanitizer
from services.apple.scripts import (
    FETCH_TRACK_IDS,
    FETCH_TRACKS,
    FETCH_TRACKS_BY_IDS,
    UPDATE_PROPERTY,
)

if TYPE_CHECKING:
    from core.retry_handler import DatabaseRetryHandler

# Logging constants
RESULT_PREVIEW_LENGTH = 50  # characters shown when previewing small script results
LOG_PREVIEW_LENGTH = 200  # characters shown when previewing long outputs/stderr

# AppleScript output markers
NO_TRACKS_FOUND = "NO_TRACKS_FOUND"


# noinspection PyUnboundLocalVariable
class AppleScriptClient(AppleScriptClientProtocol):
    """A client to run AppleScript commands asynchronously using the osascript command.

    Semaphore initialization is done in the async initialize method.

    Attributes:
        config (dict): Configuration dictionary loaded from config.yaml or my-config.yaml.
        apple_scripts_dir (str): Directory containing AppleScript files.
        console_logger (logging.Logger): Logger for console output.
        error_logger (logging.Logger): Logger for error output.
        semaphore (Optional[asyncio.Semaphore]): Semaphore to limit concurrent AppleScript executions (initialized asynchronously).

    """

    def __init__(
        self,
        config: dict[str, Any],
        analytics: Analytics,
        console_logger: logging.Logger | None = None,
        error_logger: logging.Logger | None = None,
        retry_handler: DatabaseRetryHandler | None = None,
    ) -> None:
        """Initialize the AppleScript client."""
        self.config = config
        self.analytics = analytics
        self.console_logger = console_logger if console_logger is not None else logging.getLogger(__name__)
        self.error_logger = error_logger if error_logger is not None else self.console_logger

        self.apple_scripts_dir = config.get("apple_scripts_dir")
        if not self.apple_scripts_dir:
            # Log critical error but don't raise here in __init__, let the initialize method handle it
            self.error_logger.critical("Configuration error: 'apple_scripts_dir' key is missing.")

        # Semaphore and rate limiter are initialized in the async initialize method
        self.semaphore: asyncio.Semaphore | None = None
        self.rate_limiter: EnhancedRateLimiter | None = None

        # Initialize the security sanitizer
        self.sanitizer = AppleScriptSanitizer(self.console_logger)

        # Initialize the file validator
        self.file_validator = AppleScriptFileValidator(
            self.apple_scripts_dir,
            self.error_logger,
            self.console_logger,
        )

        # Initialize the executor (semaphore will be set in initialize())
        self.executor = AppleScriptExecutor(
            semaphore=None,
            apple_scripts_directory=self.apple_scripts_dir,
            console_logger=self.console_logger,
            error_logger=self.error_logger,
            retry_handler=retry_handler,
        )

    async def initialize(self) -> None:
        """Asynchronously initializes the AppleScriptClient by creating the semaphore.

        Must be called within an active event loop.
        """
        if self.apple_scripts_dir is None:
            error_msg = "AppleScript directory is not set"
            self.error_logger.critical(error_msg)
            raise ValueError(error_msg)

        self.console_logger.debug("AppleScript directory: %s", self.apple_scripts_dir)

        # Check if the directory exists and is accessible
        if not Path(self.apple_scripts_dir).is_dir():
            error_msg = f"AppleScript directory not accessible: {self.apple_scripts_dir}"
            self.error_logger.critical(error_msg)
            raise FileNotFoundError(error_msg)

        # Count and validate scripts
        try:
            scripts_path = Path(self.apple_scripts_dir)
            scripts: list[str] = [f.name for f in scripts_path.iterdir() if f.name.endswith((".applescript", ".scpt"))]

            # Check for required scripts
            required_scripts: list[str] = [
                UPDATE_PROPERTY,
                FETCH_TRACKS,
            ]
            if missing_scripts := [script for script in required_scripts if not (Path(self.apple_scripts_dir) / script).exists()]:
                self.error_logger.warning("Missing required AppleScripts: %s", "', '".join(missing_scripts))

        except OSError as e:
            self.console_logger.warning("Could not list AppleScript directory: %s", e)
            scripts = []

        if self.semaphore is None:
            try:
                concurrent_limit = self.config.get("apple_script_concurrency", 5)
                if concurrent_limit <= 0:
                    error_msg = f"Invalid concurrency limit: {concurrent_limit}. Must be positive."
                    self.error_logger.critical(error_msg)
                    raise ValueError(error_msg)

                # Check if rate limiting is enabled (provides better throughput stability)
                rate_limit_config = self.config.get("apple_script_rate_limit", {})
                if rate_limit_config.get("enabled", False):
                    # Use enhanced rate limiter (rate limiting + concurrency control)
                    requests_per_window = rate_limit_config.get("requests_per_window", 10)
                    window_size = rate_limit_config.get("window_size_seconds", 1.0)

                    self.rate_limiter = EnhancedRateLimiter(
                        requests_per_window=requests_per_window,
                        window_size=window_size,
                        max_concurrent=concurrent_limit,
                        logger=self.console_logger,
                    )
                    await self.rate_limiter.initialize()
                    self.executor.update_rate_limiter(self.rate_limiter)
                    self.console_logger.info(
                        "%s initialized (%d scripts, concurrency: %d, rate: %d/%ss)",
                        LogFormat.entity("AppleScriptClient"),
                        len(scripts),
                        concurrent_limit,
                        requests_per_window,
                        window_size,
                    )
                else:
                    # Use semaphore-only concurrency control (legacy behavior)
                    self.console_logger.debug("Creating semaphore with concurrency limit: %d", concurrent_limit)
                    self.semaphore = asyncio.Semaphore(concurrent_limit)
                    self.executor.update_semaphore(self.semaphore)
                    self.console_logger.info(
                        "%s initialized (%d scripts, concurrency: %d)",
                        LogFormat.entity("AppleScriptClient"),
                        len(scripts),
                        concurrent_limit,
                    )
            except (ValueError, TypeError, RuntimeError, asyncio.InvalidStateError) as e:
                self.error_logger.exception("Error initializing AppleScriptClient: %s", e)
                raise
        else:
            self.console_logger.debug("Semaphore already initialized")

    @staticmethod
    def _build_command_with_args(script_path: str, arguments: list[str] | None) -> list[str] | None:
        """Build osascript command with validated arguments.

        Args:
            script_path: Path to the script file
            arguments: Optional list of arguments

        Returns:
            list[str] | None: Command list if valid, None if validation fails

        """
        cmd = ["osascript", script_path]

        if arguments:
            # Shell metacharacters are safe - we use create_subprocess_exec (no shell)
            cmd.extend(arguments)

        return cmd

    def _log_script_result(self, result: str | None) -> None:
        """Log script execution result.

        Args:
            result: Script execution result

        """
        if result is not None:
            self.console_logger.debug("AppleScript execution completed. Result length: %d characters", len(result))
            if result.strip():
                self.console_logger.debug("Script output: %s%s", result[:LOG_PREVIEW_LENGTH], "..." if len(result) > LOG_PREVIEW_LENGTH else "")
        else:
            self.console_logger.warning("AppleScript execution returned None")

    @Analytics.track_instance_method("applescript_run_script")
    async def run_script(
        self,
        script_name: str,
        arguments: list[str] | None = None,
        timeout: float | None = None,
        context_artist: str | None = None,
        context_album: str | None = None,
        context_track: str | None = None,
        label: str | None = None,
    ) -> str | None:
        """Execute an AppleScript file and return its output.

        Requires initialize() to be called first to set up the AppleScript
        directory path.

        Args:
            script_name: Name of the AppleScript file to execute.
            arguments: Optional list of arguments to pass to the script.
            timeout: Optional timeout in seconds. Uses default from config if not specified.
            context_artist: Artist name for contextual logging.
            context_album: Album name for contextual logging.
            context_track: Track name for contextual logging.
            label: Custom label for logging (defaults to script_name).

        Returns:
            The stdout output from the AppleScript execution, or None if an error occurred.

        Raises:
            TimeoutError: If script execution times out.
            OSError: If script execution fails.

        """
        self.console_logger.debug("run_script called: script='%s'", script_name)

        if self.apple_scripts_dir is None:
            error_msg = "AppleScript directory is not set. Cannot run script."
            self.error_logger.error(error_msg)
            return None

        script_path = str(Path(self.apple_scripts_dir) / script_name)
        self.console_logger.debug("Script path: %s", script_path)

        # Validate the script path is within the allowed directory
        if not self.file_validator.validate_script_path(script_path):
            self.error_logger.error("Invalid script path (security check failed): %s", script_path)
            return None

        # Validate file access
        if not self.file_validator.validate_script_file_access(script_path):
            return None

        # Build command with validated arguments
        cmd = self._build_command_with_args(script_path, arguments)
        if cmd is None:
            return None

        # Convert timeout to float, using configured default if not specified
        if timeout is None:
            timeout = self.config.get("applescript_timeouts", {}).get("default") or self.config.get("applescript_timeout_seconds", 3600)
        timeout_float = float(timeout) if timeout is not None else 3600.0

        # Build contextual information
        context_parts: list[str] = []
        if context_artist:
            context_parts.append(f"Artist: {context_artist}")
        if context_album:
            context_parts.append(f"Album: {context_album}")
        if context_track:
            context_parts.append(f"Track: {context_track}")

        if context_parts:
            context_str = f" ({' | '.join(context_parts)})"
            self.console_logger.debug(
                "Executing AppleScript: %s%s [timeout: %ss]",
                script_name,
                context_str,
                timeout_float,
            )
        else:
            self.console_logger.debug(
                "Executing AppleScript: %s [timeout: %ss]",
                script_name,
                timeout_float,
            )

        try:
            result = await self.executor.run_osascript(cmd, label or script_name, timeout_float)
            self._log_script_result(result)
            return result
        except TimeoutError:
            error_msg = f"AppleScript execution timed out after {timeout_float} seconds"
            self.error_logger.exception(error_msg)
            raise
        except (OSError, subprocess.SubprocessError, asyncio.CancelledError) as e:
            error_msg = f"Error in AppleScript execution: {e}"
            self.error_logger.exception(error_msg)
            raise

    @Analytics.track_instance_method("applescript_fetch_by_ids")
    async def fetch_tracks_by_ids(
        self,
        track_ids: list[str],
        batch_size: int = 1000,
        timeout: float | None = None,
    ) -> list[dict[str, str]]:
        """Fetch tracks by their IDs using fetch_tracks_by_ids.applescript.

        Args:
            track_ids: List of track IDs to fetch
            batch_size: Maximum number of IDs per batch (default: 1000)
            timeout: Timeout in seconds for script execution

        Returns:
            List of track dictionaries with metadata

        """
        if not track_ids:
            return []

        if batch_size <= 0:
            msg = f"Invalid batch_size: {batch_size}, must be positive"
            raise ValueError(msg)

        if timeout is None:
            timeout = self.config.get("applescript_timeouts", {}).get("default") or self.config.get("applescript_timeout_seconds", 3600)

        timeout_float = float(timeout) if timeout is not None else 3600.0

        all_tracks: list[dict[str, str]] = []
        total_batches = (len(track_ids) + batch_size - 1) // batch_size

        # Use analytics batch_mode to suppress per-call console logging
        async with self.analytics.batch_mode("Fetching tracks by ID...") as status:
            for i in range(0, len(track_ids), batch_size):
                batch = track_ids[i : i + batch_size]
                ids_csv = ",".join(batch)
                batch_num = i // batch_size + 1

                status.update(f"[cyan]Fetching tracks by ID... ({batch_num}/{total_batches})[/cyan]")

                batch_label = f"{FETCH_TRACKS_BY_IDS} [{batch_num}/{total_batches}]"

                raw_output = await self.run_script(
                    FETCH_TRACKS_BY_IDS,
                    [ids_csv],
                    timeout=timeout_float,
                    label=batch_label,
                )

                if not raw_output or raw_output == NO_TRACKS_FOUND:
                    continue

                # Parse output using same format as fetch_tracks.applescript
                batch_tracks = self._parse_track_output(raw_output)
                all_tracks.extend(batch_tracks)

        self.console_logger.info("Fetched %d tracks (requested: %d)", len(all_tracks), len(track_ids))
        return all_tracks

    @Analytics.track_instance_method("applescript_fetch_all_ids")
    async def fetch_all_track_ids(self, timeout: float | None = None) -> list[str]:
        """Fetch just track IDs from Music.app (lightweight operation).

        This is used by Smart Delta to detect new/removed tracks without
        fetching full metadata. Much faster than fetching all track data.

        Args:
            timeout: Timeout in seconds for script execution

        Returns:
            List of track ID strings

        """
        if timeout is None:
            timeout = self.config.get("applescript_timeouts", {}).get("default") or self.config.get("applescript_timeout_seconds", 600)

        timeout_float = float(timeout) if timeout is not None else 600.0

        async with spinner("Fetching all track IDs from Music.app..."):
            raw_output = await self.run_script(
                FETCH_TRACK_IDS,
                timeout=timeout_float,
            )

        if not raw_output:
            self.console_logger.warning("No track IDs returned from Music.app")
            return []

        # Check for error from AppleScript
        if raw_output.startswith("ERROR:"):
            self.error_logger.error("AppleScript error: %s", raw_output[6:])
            return []

        # Parse comma-separated IDs
        track_ids = [id_str.strip() for id_str in raw_output.split(",") if id_str.strip()]

        self.console_logger.info("Fetched %d track IDs", len(track_ids))
        return track_ids

    @staticmethod
    def _parse_track_output(raw_output: str) -> list[dict[str, str]]:
        """Parse AppleScript track output into track dictionaries.

        Args:
            raw_output: Raw AppleScript output with ASCII 30/29 separators

        Returns:
            List of track dictionaries

        """
        tracks: list[dict[str, str]] = []

        # Split by line separator
        lines = raw_output.split(LINE_SEPARATOR)

        for line in lines:
            if not line.strip():
                continue

            fields = line.split(FIELD_SEPARATOR)

            # Expected fields: id, name, artist, album_artist, album, genre, date_added,
            # track_status, year, release_year, "" (empty placeholder, ignored)
            # Note: year_set_by_mgu is a tracking field managed by year_batch.py,
            # NOT from AppleScript. The last field is always empty.
            if len(fields) >= 10:
                track = {
                    "id": fields[0],
                    "name": fields[1],
                    "artist": fields[2],
                    "album_artist": fields[3],
                    "album": fields[4],
                    "genre": fields[5],
                    "date_added": fields[6],
                    "track_status": fields[7],
                    "year": fields[8],
                    "release_year": fields[9],
                    # fields[10] is empty placeholder - intentionally not exposed
                }
                tracks.append(track)
            else:
                logging.warning(
                    "Skipping line with insufficient fields (%d < 10): %s",
                    len(fields),
                    line[:100] if len(line) > 100 else line,
                )

        return tracks
