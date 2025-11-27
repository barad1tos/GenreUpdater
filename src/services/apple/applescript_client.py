"""AppleScript Client Module.

This module provides an abstraction for executing AppleScript commands asynchronously.
It centralizes the logic for interacting with AppleScript via the `osascript` command,
handles errors, applies concurrency limits via semaphore-based control, and ensures non-blocking execution.

The module supports both executing AppleScript files and inline AppleScript code.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from src.core.models.protocols import AppleScriptClientProtocol
from src.metrics import Analytics
from src.services.apple.applescript_executor import AppleScriptExecutor
from src.services.apple.file_validator import AppleScriptFileValidator
from src.services.apple.sanitizer import (
    AppleScriptSanitizationError,
    AppleScriptSanitizer,
    DANGEROUS_ARGUMENT_CHARACTERS,
)

# Logging constants
RESULT_PREVIEW_LENGTH = 50  # characters shown when previewing small script results
LOG_PREVIEW_LENGTH = 200  # characters shown when previewing long outputs/stderr


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

        # Semaphore is initialized in the async initialize method
        self.semaphore: asyncio.Semaphore | None = None  # Initialize as None

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
        )

    async def initialize(self) -> None:
        """Asynchronously initializes the AppleScriptClient by creating the semaphore.

        Must be called within an active event loop.
        """
        self.console_logger.info("🔧 Starting AppleScriptClient initialization...")

        if self.apple_scripts_dir is None:
            error_msg = "❌ AppleScript directory is not set. Cannot initialize client."
            self.error_logger.critical(error_msg)
            raise ValueError(error_msg)

        self.console_logger.debug("📁 AppleScript directory: %s", self.apple_scripts_dir)

        # Check if the directory exists and is accessible
        if not Path(self.apple_scripts_dir).is_dir():
            error_msg = f"❌ AppleScript directory does not exist or is not accessible: {self.apple_scripts_dir}"
            self.error_logger.critical(error_msg)
            raise FileNotFoundError(error_msg)

        # List scripts in the directory for debugging
        try:
            # Use pathlib.Path.iterdir() for better path handling
            scripts_path = Path(self.apple_scripts_dir)
            scripts: list[str] = [f.name for f in scripts_path.iterdir() if f.name.endswith((".applescript", ".scpt"))]
            msg = f"📜 Found {len(scripts)} AppleScript files: {', '.join(scripts) if scripts else 'None'}"
            self.console_logger.info(msg)

            # Check for required scripts
            required_scripts: list[str] = [
                "update_property.applescript",
                "fetch_tracks.scpt",
            ]
            if missing_scripts := [script for script in required_scripts if not (Path(self.apple_scripts_dir) / script).exists()]:
                self.error_logger.warning("⚠️ Missing required AppleScripts: %s", "', '".join(missing_scripts))

        except OSError as e:
            self.console_logger.warning("⚠️ Could not list AppleScript directory: %s", e)

        if self.semaphore is None:
            try:
                concurrent_limit = self.config.get("apple_script_concurrency", 5)
                if concurrent_limit <= 0:
                    error_msg = f"❌ Invalid concurrency limit: {concurrent_limit}. Must be a positive integer."
                    self.error_logger.critical(error_msg)
                    raise ValueError(error_msg)

                self.console_logger.debug("🔒 Creating semaphore with concurrency limit: %d", concurrent_limit)
                self.semaphore = asyncio.Semaphore(concurrent_limit)
                self.executor.update_semaphore(self.semaphore)
                self.console_logger.info("✅ AppleScriptClient semaphore initialized with concurrency: %d", concurrent_limit)
            except (ValueError, TypeError, RuntimeError, asyncio.InvalidStateError) as e:
                self.error_logger.exception("❌ Error initializing AppleScriptClient semaphore: %s", e)
                raise
        else:
            self.console_logger.debug("INFO: Semaphore already initialized")

        self.console_logger.info("✨ AppleScriptClient initialization complete")

    def _build_command_with_args(self, script_path: str, arguments: list[str] | None) -> list[str] | None:
        """Build osascript command with validated arguments.

        Args:
            script_path: Path to the script file
            arguments: Optional list of arguments

        Returns:
            list[str] | None: Command list if valid, None if validation fails

        """
        cmd = ["osascript", script_path]

        if arguments:
            safe_args: list[str] = []
            for arg in arguments:
                # Basic safety check for potentially dangerous characters
                if any(c in arg for c in DANGEROUS_ARGUMENT_CHARACTERS):
                    self.error_logger.error("❌ Potentially dangerous characters in argument: %s", arg)
                    return None
                safe_args.append(arg)
            cmd.extend(safe_args)

        return cmd

    def _log_script_result(self, result: str | None) -> None:
        """Log script execution result.

        Args:
            result: Script execution result

        """
        if result is not None:
            self.console_logger.debug("✅ AppleScript execution completed successfully. Result length: %d characters", len(result))
            if result.strip():
                self.console_logger.debug("📝 Script output: %s%s", result[:LOG_PREVIEW_LENGTH], "..." if len(result) > LOG_PREVIEW_LENGTH else "")
        else:
            self.console_logger.warning("⚠️ AppleScript execution returned None")

    @Analytics.track_instance_method("applescript_run_script")
    async def run_script(
        self,
        script_name: str,
        arguments: list[str] | None = None,
        timeout: float | None = None,
        context_artist: str | None = None,
        context_album: str | None = None,
        context_track: str | None = None,
    ) -> str | None:
        """Execute an AppleScript asynchronously and return its output.

        Requires initialize() to have been called.

        :param script_name: The name of the AppleScript file to execute.
        :param arguments: List of arguments to pass to the script.
        :param timeout: Timeout in seconds for script execution
        :param context_artist: Artist name for contextual logging (optional)
        :param context_album: Album name for contextual logging (optional)
        :param context_track: Track name for contextual logging (optional)
        :return: The output of the script, or None if an error occurred
        """
        self.console_logger.debug("🔧 run_script called with script_name='%s', arguments=%s", script_name, arguments)

        if self.apple_scripts_dir is None:
            error_msg = "❌ AppleScript directory is not set. Cannot run script."
            self.error_logger.error(error_msg)
            return None

        script_path = str(Path(self.apple_scripts_dir) / script_name)
        self.console_logger.debug("📜 Resolved script path: %s", script_path)

        # Validate the script path is within the allowed directory
        if not self.file_validator.validate_script_path(script_path):
            self.error_logger.error("❌ Invalid script path (security check failed): %s", script_path)
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

        # Log the command with contextual information when available
        args_str = " ".join([script_name] + (arguments or []))

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
            self.console_logger.info(
                "🚀 Executing AppleScript: %s (%s)%s [timeout: %ss]",
                script_name,
                args_str,
                context_str,
                timeout_float,
            )
        else:
            self.console_logger.info(
                "🚀 Executing AppleScript: %s (%s) [timeout: %ss]",
                script_name,
                args_str,
                timeout_float,
            )

        try:
            result = await self.executor.run_osascript(cmd, script_name, timeout_float)
            self._log_script_result(result)
            return result
        except TimeoutError:
            error_msg = f"⌛ AppleScript execution timed out after {timeout_float} seconds"
            self.error_logger.exception(error_msg)
            raise
        except (OSError, subprocess.SubprocessError, asyncio.CancelledError) as e:
            error_msg = f"❌ Error in AppleScript execution: {e}"
            self.error_logger.exception(error_msg)
            raise

    @Analytics.track_instance_method("applescript_run_code")
    async def run_script_code(
        self,
        script_code: str,
        arguments: list[str] | None = None,
        timeout: float | None = None,
    ) -> str | None:
        """Execute an AppleScript code asynchronously and return its output.

        Requires initialize() to have been called.
        For large or complex scripts, use temporary file execution for reliability.

        :param script_code: The AppleScript code to execute.
        :param arguments: List of arguments to pass to the script.
        :param timeout: Timeout in seconds for script execution
        :return: The output of the script, or None if an error occurred
        """
        if not script_code.strip():
            self.error_logger.error("No script code provided.")
            return None

        if timeout is None:
            # Use default timeout from applescript_timeouts config, with fallback to applescript_timeout_seconds
            timeout = self.config.get("applescript_timeouts", {}).get("default") or self.config.get("applescript_timeout_seconds", 3600)

        # Ensure timeout is a float, using configured default if needed
        timeout_float = (
            float(timeout)
            if timeout is not None
            else float(self.config.get("applescript_timeouts", {}).get("default") or self.config.get("applescript_timeout_seconds", 3600))
        )

        if self.executor.should_use_temp_file(script_code):
            # Validate the script before writing to the temp file
            try:
                self.sanitizer.validate_script_code(script_code)
                self.console_logger.debug("✅ AppleScript code passed security validation (temp file mode)")
            except AppleScriptSanitizationError as e:
                self.error_logger.exception("🔒 Security violation in AppleScript code: %s", e)
                self.error_logger.exception("🚫 Blocked potentially dangerous script: %s", script_code[:200])
                return None

            code_preview = self._format_script_preview(script_code)
            self.console_logger.info(
                "▷ tempfile-script (%dB) [t:%ss] %s",
                len(script_code.encode()),
                timeout_float,
                code_preview,
            )

            return await self.executor.run_via_temp_file(script_code, arguments, timeout_float)
        # Use traditional -e flag execution for simple scripts
        try:
            cmd = self.sanitizer.create_safe_command(script_code, arguments)
            self.console_logger.debug("✅ AppleScript code passed security validation (inline mode)")
        except AppleScriptSanitizationError as e:
            self.error_logger.exception("🔒 Security violation in AppleScript code: %s", e)
            self.error_logger.exception("🚫 Blocked potentially dangerous script: %s", script_code[:200])
            return None
        except (ValueError, TypeError, AttributeError) as e:
            self.error_logger.exception("❌ Error during script sanitization: %s", e)
            return None

        code_preview = self._format_script_preview(script_code)
        self.console_logger.info(
            "▷ inline-script (%dB) [t:%ss] %s",
            len(script_code.encode()),
            timeout_float,
            code_preview,
        )

        return await self.executor.run_osascript(cmd, "inline-script", timeout_float)

    @Analytics.track_instance_method("applescript_fetch_by_ids")
    async def fetch_tracks_by_ids(
        self,
        track_ids: list[str],
        batch_size: int = 1000,
        timeout: float | None = None,
    ) -> list[dict[str, str]]:
        """Fetch tracks by their IDs using fetch_tracks_by_ids.scpt.

        Args:
            track_ids: List of track IDs to fetch
            batch_size: Maximum number of IDs per batch (default: 1000)
            timeout: Timeout in seconds for script execution

        Returns:
            List of track dictionaries with metadata

        """
        if not track_ids:
            return []

        if timeout is None:
            timeout = self.config.get("applescript_timeouts", {}).get("default") or self.config.get("applescript_timeout_seconds", 3600)

        timeout_float = float(timeout) if timeout is not None else 3600.0

        all_tracks: list[dict[str, str]] = []

        # Process in batches to avoid command-line length limits
        for i in range(0, len(track_ids), batch_size):
            batch = track_ids[i : i + batch_size]
            ids_csv = ",".join(batch)

            self.console_logger.info(
                "🔍 Fetching %d tracks by ID (batch %d-%d of %d)",
                len(batch),
                i + 1,
                min(i + batch_size, len(track_ids)),
                len(track_ids),
            )

            raw_output = await self.run_script(
                "fetch_tracks_by_ids.scpt",
                [ids_csv],
                timeout=timeout_float,
            )

            if not raw_output or raw_output == "NO_TRACKS_FOUND":
                continue

            # Parse output using same format as fetch_tracks.scpt
            batch_tracks = self._parse_track_output(raw_output)
            all_tracks.extend(batch_tracks)

        self.console_logger.info("✓ Fetched %d tracks by ID (requested: %d)", len(all_tracks), len(track_ids))
        return all_tracks

    @staticmethod
    def _parse_track_output(raw_output: str) -> list[dict[str, str]]:
        """Parse AppleScript track output into track dictionaries.

        Args:
            raw_output: Raw AppleScript output with ASCII 30/29 separators

        Returns:
            List of track dictionaries

        """
        field_separator = "\x1e"  # ASCII 30
        line_separator = "\x1d"  # ASCII 29

        tracks: list[dict[str, str]] = []

        # Split by line separator
        lines = raw_output.split(line_separator)

        for line in lines:
            if not line.strip():
                continue

            fields = line.split(field_separator)

            # Expected fields: id, name, artist, album_artist, album, genre, date_added,
            # track_status, year, release_year, new_year
            if len(fields) >= 11:
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
                    "new_year": fields[10],
                }
                tracks.append(track)

        return tracks

    def _format_script_preview(self, script_code: str) -> str:
        """Format AppleScript code for log output, showing only essential parts.

        Args:
            script_code: The AppleScript code to format.

        Returns:
            str: Formatted string with essential parts of the script.

        """
        try:
            # Normalize whitespace
            normalized_code = re.sub(r"\s+", " ", script_code.replace("\n", " ").replace("\r", " ")).strip()

            # Find the "tell application" pattern
            if tell_match := re.search(r'tell application\s+["\'](.*?)["\']', normalized_code, re.IGNORECASE):
                app_name = tell_match[1]
                # Include the first part of the command for better context
                command_preview = f"{normalized_code[tell_match.end() :].strip()[:30]}..."
                return f'tell application "{app_name}" {command_preview}'

            # Fallback if the pattern is not found
            preview_length = 100  # Default preview length
            return f"{normalized_code[:preview_length]}..." if len(normalized_code) > preview_length else normalized_code
        except (AttributeError, IndexError, TypeError, ValueError, re.error) as e:
            self.error_logger.exception("Error formatting script preview: %s", e)
            return "[Script preview error]"
