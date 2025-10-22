"""AppleScript Client Module.

This module provides an abstraction for executing AppleScript commands asynchronously.
It centralizes the logic for interacting with AppleScript via the `osascript` command,
handles errors, applies concurrency limits via semaphore-based control, and ensures non-blocking execution.

The module supports both executing AppleScript files and inline AppleScript code.
"""

import asyncio
import logging
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from src.shared.data.protocols import AppleScriptClientProtocol
from src.shared.monitoring import Analytics

RESULT_PREVIEW_LEN = 50  # characters shown when previewing small script results
LOG_PREVIEW_LEN = 200  # characters shown when previewing long outputs/stderr
DANGEROUS_ARG_CHARS = [";", "&", "|", "`", "$", ">", "<", "!"]

# Security constants for AppleScript sanitization
APPLESCRIPT_RESERVED_WORDS = {
    "do shell script",
    "system events",
    "system attribute",
    "load script",
    "store script",
    "tell application finder",
    "delete",
    "move",
    "create",
    "set value",
    "keystroke",
    "key code",
    "choose file",
    "choose folder",
    "display dialog",
    "display notification",
    "open location",
}

DANGEROUS_APPLESCRIPT_PATTERNS = [
    r"do\s+shell\s+script",  # Shell execution
    r"tell\s+application\s+\"Finder\"",  # File system access
    r"tell\s+application\s+\"System\s+Events\"",  # System manipulation
    r"load\s+script",  # External script loading
    r"store\s+script",  # Script writing
    r"choose\s+file",  # File system browsing
    r"choose\s+folder",  # Directory browsing
    r"open\s+location",  # URL/file opening
    r"keystroke|key\s+code",  # Keyboard input simulation
    r"system\s+attribute",  # System information access
]

# Security limits
MAX_TRACK_ID_LENGTH = 100
MAX_SCRIPT_SIZE = 10000  # 10KB limit for script size

# Thresholds for using temporary file execution
COMPLEX_SCRIPT_THRESHOLD = 2000  # Use temp file for scripts > 2KB
COMPLEX_PATTERN_COUNT = 5  # Use a temp file if the script has > 5 complex patterns
MAX_TELL_BLOCKS = 3  # Use the temp file if the script has > 3 tell blocks


class AppleScriptSanitizationError(Exception):
    """Exception raised when AppleScript code fails security validation."""

    def __init__(self, message: str, dangerous_pattern: str | None = None) -> None:
        """Initialize the sanitization error.

        Args:
            message: Error message describing the security violation
            dangerous_pattern: The specific pattern that triggered the error

        """
        super().__init__(message)
        self.dangerous_pattern = dangerous_pattern


class AppleScriptSanitizer:
    """Security-focused AppleScript code sanitizer and validator.

    This class provides methods to sanitize and validate AppleScript code
    to prevent command injection and other security vulnerabilities.
    Implements defense-in-depth through multiple validation layers.
    """

    def __init__(self, logger: logging.Logger | None = None) -> None:
        """Initialize the AppleScript sanitizer.

        Args:
            logger: Optional logger instance for security event logging

        """
        self.logger = logger or logging.getLogger(__name__)
        self._compiled_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in DANGEROUS_APPLESCRIPT_PATTERNS]

    def sanitize_string(self, value: Any) -> str:
        """Sanitize a string value for safe use in AppleScript.

        Escapes special characters that could be used for injection attacks.

        Args:
            value: The string value to sanitize

        Returns:
            str: The sanitized string safe for AppleScript execution

        Raises:
            ValueError: If the input value is None or not a string

        """
        if value is None:
            msg = "Cannot sanitize None value"
            raise ValueError(msg)

        if not isinstance(value, str):
            msg = f"Expected string, got {type(value).__name__}"
            raise TypeError(msg)

        # Escape backslashes first to prevent double-escaping
        sanitized = value.replace("\\", "\\\\")

        # Escape double quotes for AppleScript string literals
        sanitized = sanitized.replace('"', '\\"')

        # Escape single quotes
        sanitized = sanitized.replace("'", "\\'")

        # Log any escaping changes for audit purposes
        if value != sanitized:
            self.logger.debug(
                "Escaped AppleScript string: %d characters processed, %d changes made",
                len(value),
                len([c for c, s in zip(value, sanitized, strict=False) if c != s]),
            )

        return sanitized

    def validate_track_id(self, track_id: str) -> bool:
        """Validate a track ID for safe use in AppleScript.

        Args:
            track_id: The track ID to validate

        Returns:
            bool: True if the track ID is safe to use

        """
        # Track IDs should be numeric or alphanumeric
        if not re.match(r"^[a-zA-Z0-9\-_]+$", track_id):
            self.logger.warning("Invalid track ID format: %s", track_id)
            return False

        # Reasonable length limits
        if len(track_id) > MAX_TRACK_ID_LENGTH:
            self.logger.warning("Track ID too long: %d characters", len(track_id))
            return False

        # Check for suspicious patterns
        if any(char in track_id for char in DANGEROUS_ARG_CHARS):
            self.logger.warning("Dangerous characters in track ID: %s", track_id)
            return False

        return True

    def validate_script_code(self, script_code: str | None, allow_music_app: bool = True) -> None:
        """Validate AppleScript code for security vulnerabilities.

        Args:
            script_code: The AppleScript code to validate
            allow_music_app: Whether to allow Music.app operations (default: True)

        Raises:
            AppleScriptSanitizationError: If dangerous patterns are detected
            ValueError: If script_code is invalid

        """
        if not script_code:
            msg = "Script code must be a non-empty string"
            raise ValueError(msg)

        # Normalize the code for pattern matching
        normalized_code = re.sub(r"\s+", " ", script_code.lower().strip())

        # Check for dangerous patterns
        for pattern in self._compiled_patterns:
            if match := pattern.search(normalized_code):
                dangerous_text = match.group()
                error_msg = f"Dangerous AppleScript pattern detected: '{dangerous_text}'"
                self.logger.error("Security violation: %s in code: %s", error_msg, script_code[:100])
                raise AppleScriptSanitizationError(error_msg, dangerous_text)

        # Check for reserved words that could be dangerous
        for reserved_word in APPLESCRIPT_RESERVED_WORDS:
            if reserved_word.lower() in normalized_code:
                # Allow Music.app specific operations if permitted
                if allow_music_app and "music" in reserved_word.lower():
                    continue

                error_msg = f"Restricted AppleScript command detected: '{reserved_word}'"
                self.logger.error("Security violation: %s", error_msg)
                raise AppleScriptSanitizationError(error_msg, reserved_word)

        # Additional validation for script length (prevent DoS)
        if len(script_code) > MAX_SCRIPT_SIZE:
            error_msg = f"AppleScript code too large: {len(script_code)} characters"
            self.logger.error("Security violation: %s", error_msg)
            raise AppleScriptSanitizationError(error_msg)

        self.logger.debug(
            "AppleScript code passed security validation: %d characters",
            len(script_code),
        )

    def create_safe_command(self, script_code: str, arguments: list[str] | None = None) -> list[str]:
        """Create a safe osascript command with validated inputs.

        Args:
            script_code: The AppleScript code to execute
            arguments: Optional arguments to pass to the script

        Returns:
            list[str]: Safe command list for subprocess execution

        Raises:
            AppleScriptSanitizationError: If validation fails
            ValueError: If inputs are invalid

        """
        # Validate the script code first
        self.validate_script_code(script_code)

        # Start with base command
        cmd = ["osascript", "-e", script_code]

        # Add arguments if provided
        if arguments:
            sanitized_args: list[str] = []
            for arg in arguments:
                # Validate and sanitize each argument
                if any(char in arg for char in DANGEROUS_ARG_CHARS):
                    msg = f"Dangerous characters in argument: {arg}"
                    raise AppleScriptSanitizationError(msg, arg)

                sanitized_args.append(self.sanitize_string(arg))

            cmd.extend(sanitized_args)

        self.logger.debug("Created safe AppleScript command with %d components", len(cmd))
        return cmd


class EnhancedRateLimiter:
    """Advanced rate limiter using a moving window approach."""

    def __init__(
        self,
        requests_per_window: int,
        window_size: float,
        max_concurrent: int = 3,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the rate limiter."""
        if requests_per_window <= 0:
            msg = "requests_per_window must be a positive integer"
            raise ValueError(msg)
        if window_size <= 0:
            msg = "window_size must be a positive number"
            raise ValueError(msg)
        if max_concurrent <= 0:
            msg = "max_concurrent must be a positive integer"
            raise ValueError(msg)

        self.requests_per_window = requests_per_window
        self.window_size = window_size
        self.request_timestamps: list[float] = []
        self.semaphore: asyncio.Semaphore | None = None
        self.max_concurrent = max_concurrent
        self.logger = logger or logging.getLogger(__name__)
        self.total_requests: int = 0
        self.total_wait_time: float = 0.0

    async def initialize(self) -> None:
        """Initialize the rate limiter."""
        if self.semaphore is None:
            try:
                self.semaphore = asyncio.Semaphore(self.max_concurrent)
                self.logger.debug(f"RateLimiter initialized with max_concurrent: {self.max_concurrent}")
                # Yield control to event loop to make this properly async
                await asyncio.sleep(0)
            except (ValueError, TypeError, RuntimeError, asyncio.InvalidStateError) as e:
                self.logger.exception("Error initializing RateLimiter semaphore: %s", e)
                raise

    async def acquire(self) -> float:
        """Acquire permission to make a request, waiting if necessary due to rate limits or concurrency limits."""
        if self.semaphore is None:
            msg = "RateLimiter not initialized"
            raise RuntimeError(msg)
        rate_limit_wait_time = await self._wait_if_needed()
        self.total_requests += 1
        self.total_wait_time += rate_limit_wait_time
        await self.semaphore.acquire()
        return rate_limit_wait_time

    def release(self) -> None:
        """Release the semaphore, allowing another request to proceed."""
        if self.semaphore is None:
            return
        self.semaphore.release()

    async def _wait_if_needed(self) -> float:
        now = time.monotonic()
        while self.request_timestamps and now - self.request_timestamps[0] > self.window_size:
            self.request_timestamps.pop(0)
        if len(self.request_timestamps) >= self.requests_per_window:
            oldest_timestamp = self.request_timestamps[0]
            wait_duration = (oldest_timestamp + self.window_size) - now
            if wait_duration > 0:
                self.logger.debug(f"Rate limit reached. Waiting {wait_duration:.3f}s")
                await asyncio.sleep(wait_duration)
                return wait_duration + await self._wait_if_needed()
        self.request_timestamps.append(time.monotonic())
        return 0.0

    def get_stats(self) -> dict[str, Any]:
        """Get statistics about rate limiter usage."""
        now = time.monotonic()
        self.request_timestamps = [ts for ts in self.request_timestamps if now - ts <= self.window_size]
        return {
            "total_requests": self.total_requests,
            "total_wait_time": self.total_wait_time,
            "avg_wait_time": self.total_wait_time / max(1, self.total_requests),
            "current_window_usage": len(self.request_timestamps),
            "max_requests_per_window": self.requests_per_window,
        }


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
        analytics: "Analytics",
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
                self.console_logger.info("✅ AppleScriptClient semaphore initialized with concurrency: %d", concurrent_limit)
            except (ValueError, TypeError, RuntimeError, asyncio.InvalidStateError) as e:
                self.error_logger.exception("❌ Error initializing AppleScriptClient semaphore: %s", e)
                raise
        else:
            self.console_logger.debug("INFO: Semaphore already initialized")

        self.console_logger.info("✨ AppleScriptClient initialization complete")

    def _validate_script_path(self, script_path: str) -> bool:
        """Validate that the script path is safe to execute.

        :param script_path: Path to the script to validate
        :return: True if the path is safe, False otherwise
        """
        try:
            if not script_path or not self.apple_scripts_dir:
                return False

            # Resolve the path to prevent directory traversal
            resolved_path = Path(script_path).resolve()
            scripts_dir = Path(self.apple_scripts_dir).resolve()

            # Ensure the path is within the allowed directory (safe from path traversal)
            try:
                resolved_path.relative_to(scripts_dir)
            except ValueError:
                self.error_logger.exception("Script path is outside allowed directory: %s", script_path)
                return False

            # Check for suspicious patterns
            if any(part.startswith((".", "~")) or part == ".." for part in Path(script_path).parts):
                self.error_logger.error("Suspicious script path: %s", script_path)
                return False

            return True

        except (ValueError, TypeError) as e:
            self.error_logger.exception("Invalid script path %s: %s", script_path, e)
            return False

    @staticmethod
    def _write_temp_file_sync(file_path: str, content: str) -> None:
        """Write content to a temporary file synchronously."""
        with Path(file_path).open("w", encoding="utf-8") as f:
            f.write(content)
            f.flush()

    def _validate_script_file_access(self, script_path: str) -> bool:
        """Validate script file exists and is accessible.

        Args:
            script_path: Path to the script file

        Returns:
            bool: True if the file is valid and accessible

        """
        script_file = Path(script_path)

        # Reject symlinks to prevent path traversal attacks
        if script_file.is_symlink():
            self.error_logger.error("❌ Symlinks not allowed: %s", script_path)
            return False

        # Check if file exists (without following symlinks)
        if not script_file.is_file():
            self.error_logger.error("❌ AppleScript file does not exist: %s", script_path)

            # List directory contents for debugging
            try:
                if self.apple_scripts_dir:
                    dir_contents = [f.name for f in Path(self.apple_scripts_dir).iterdir()]
                    self.console_logger.debug("📂 Directory contents: %s", dir_contents)
            except OSError as e:
                self.console_logger.exception("⚠️ Could not list directory contents: %s", e)
            return False

        # Check if the file is readable
        if not os.access(script_path, os.R_OK):
            self.error_logger.error("❌ AppleScript file is not readable: %s", script_path)
            return False

        return True

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
                if any(c in arg for c in DANGEROUS_ARG_CHARS):
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
                self.console_logger.debug("📝 Script output: %s%s", result[:LOG_PREVIEW_LEN], "..." if len(result) > LOG_PREVIEW_LEN else "")
        else:
            self.console_logger.warning("⚠️ AppleScript execution returned None")

    def _log_script_success(self, label: str, script_result: str, elapsed: float) -> None:
        """Log successful script execution with appropriate formatting.

        Args:
            label: Script label for logging
            script_result: Script output
            elapsed: Execution time in seconds

        """
        if label == "fetch_tracks.scpt":
            # Count tracks by counting line separators (ASCII 29)
            track_count = script_result.count("\x1d")
            size_bytes = len(script_result.encode())
            size_kb = size_bytes / 1024

            self.console_logger.info(
                "◁ %s: %d tracks (%.1fKB, %.1fs)",
                label,
                track_count,
                size_kb,
                elapsed,
            )
        else:
            # Create a preview for logging - this can be stripped
            preview_text = script_result.strip()
            preview = f"{preview_text[:RESULT_PREVIEW_LEN]}..." if len(preview_text) > RESULT_PREVIEW_LEN else preview_text
            # Log at appropriate level based on result content
            log_level = self.console_logger.debug if "No Change" in preview else self.console_logger.info
            log_level(
                "◁ %s (%dB, %.1fs) %s",
                label,
                len(script_result.encode()),
                elapsed,
                preview,
            )

    async def _handle_subprocess_execution(self, cmd: list[str], label: str, timeout: float) -> str | None:
        """Handle subprocess execution with timeout and error handling.

        Args:
            cmd: Command to execute as a list of strings
            label: Label for logging
            timeout: Timeout in seconds

        Returns:
            str | None: Command output if successful, None otherwise

        """
        try:
            start_time = time.time()
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                elapsed = time.time() - start_time

                # Process stderr if present
                if stderr:
                    stderr_text = stderr.decode().strip()
                    self.console_logger.warning("◁ %s stderr: %s", label, stderr_text[:LOG_PREVIEW_LEN])

                # Handle process completion
                if proc.returncode == 0:
                    # Don't strip() here as it removes special separator characters
                    script_result: str = stdout.decode()
                    self._log_script_success(label, script_result, elapsed)
                    return script_result

                self.error_logger.error(
                    "◁ %s failed with return code %s: %s",
                    label,
                    proc.returncode,
                    stderr.decode().strip() if stderr else "",
                )
                return None

            except TimeoutError:
                self.error_logger.exception("⊗ %s timeout: %ss exceeded", label, timeout)
                return None

            except (subprocess.SubprocessError, OSError) as e:
                self.error_logger.exception("⊗ %s error during execution: %s", label, e)
                return None

            except asyncio.CancelledError:
                self.console_logger.info("⊗ %s cancelled", label)
                raise

            except (UnicodeDecodeError, MemoryError, RuntimeError) as e:
                self.error_logger.exception("⊗ %s unexpected error during communicate/wait: %s", label, e)
                return None

            finally:
                await self._cleanup_process(proc, label)

        except OSError as e:
            self.error_logger.exception("⊗ %s subprocess error: %s", label, e)
            return None

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
        if not self._validate_script_path(script_path):
            self.error_logger.error("❌ Invalid script path (security check failed): %s", script_path)
            return None

        # Validate file access
        if not self._validate_script_file_access(script_path):
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
            result = await self._run_osascript(cmd, script_name, timeout_float)
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

        if self._should_use_temp_file(script_code):
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

            return await self._run_via_temp_file(script_code, arguments, timeout_float)
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

        return await self._run_osascript(cmd, "inline-script", timeout_float)

    async def _run_osascript(
        self,
        cmd: list[str],
        label: str,
        timeout: float,
    ) -> str | None:
        """Run an osascript command and return output.

        Args:
            cmd: Command to execute as a list of strings
            label: Label for logging
            timeout: Timeout in seconds

        Returns:
            str: Command output if successful, None otherwise

        """
        if self.semaphore is None:
            self.error_logger.error("AppleScriptClient semaphore not initialized. Call initialize() first.")
            return None

        async with self.semaphore:
            return await self._handle_subprocess_execution(cmd, label, timeout)

    async def _cleanup_process(self, proc: "asyncio.subprocess.Process", label: str) -> None:
        """Clean up process resources.

        Args:
            proc: Process to clean up
            label: Label for logging

        """
        try:
            # Wait briefly for process to exit naturally
            await asyncio.wait_for(proc.wait(), timeout=0.5)
            self.console_logger.debug("Process for %s exited naturally and cleaned up", label)
        except TimeoutError:
            # If still running, kill and wait for cleanup
            try:
                proc.kill()
                await asyncio.wait_for(proc.wait(), timeout=5)
                self.console_logger.debug("Process for %s killed and cleaned up", label)
            except (TimeoutError, ProcessLookupError) as e:
                self.console_logger.warning(
                    "Could not kill or wait for process %s during cleanup: %s",
                    label,
                    str(e),
                )

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

    def _should_use_temp_file(self, script_code: str) -> bool:
        """Determine if a script should be executed via a temporary file.

        Large or complex scripts benefit from temporary file execution to avoid
        shell escaping issues and command-line length limitations.

        Args:
            script_code: The AppleScript code to evaluate

        Returns:
            bool: True if the script should use temporary file execution

        """
        # Check script size
        script_size = len(script_code.encode())
        if script_size > COMPLEX_SCRIPT_THRESHOLD:
            self.console_logger.debug("Script size %d exceeds threshold %d, using temp file", script_size, COMPLEX_SCRIPT_THRESHOLD)
            return True

        # Check for complex patterns that might cause escaping issues
        complex_patterns = [
            r"\"",  # Escaped quotes
            r"\\",  # Escaped backslashes
            r"\n",  # Newlines in strings
            r"\t",  # Tabs
            r"[^\x20-\x7E]",  # Non-ASCII characters
            r"(?s).{100,}",  # Very long single lines
        ]

        pattern_count = 0
        for pattern in complex_patterns:
            if re.search(pattern, script_code):
                pattern_count += 1
                if pattern_count > COMPLEX_PATTERN_COUNT:
                    self.console_logger.debug("Script has %d complex patterns, using temp file", pattern_count)
                    return True

        # Check for multiple nested tell blocks (complex structure)
        tell_count = len(re.findall(r"tell\s+application", script_code, re.IGNORECASE))
        if tell_count > MAX_TELL_BLOCKS:
            self.console_logger.debug("Script has %d tell blocks, using temp file", tell_count)
            return True

        return False

    async def _run_via_temp_file(
        self,
        script_code: str,
        arguments: list[str] | None,
        timeout: float,
    ) -> str | None:
        """Execute AppleScript via a temporary file.

        This method writes the script to a temporary file and executes it,
        which is more reliable for complex scripts than using the -e flag.

        Args:
            script_code: The AppleScript code to execute
            arguments: Optional arguments to pass to the script
            timeout: Timeout in seconds for script execution

        Returns:
            str: The output of the script, or None if an error occurred

        """
        temp_file_path: str | None = None
        try:
            # Check if apple_scripts_dir is set
            if self.apple_scripts_dir is None:
                self.error_logger.error("❌ AppleScript directory is not set. Cannot create temporary file.")
                return None

            # Create a temporary file with .applescript extension using an async-friendly approach
            temp_filename = f"temp_script_{uuid.uuid4().hex}.applescript"
            temp_file_path = str(Path(self.apple_scripts_dir) / temp_filename)
            # Write the script content asynchronously using an executor
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                AppleScriptClient._write_temp_file_sync,
                temp_file_path,
                script_code,
            )

            self.console_logger.debug("Created temporary script file: %s", Path(temp_file_path).name)

            # Build command with the temp file
            cmd = ["osascript", temp_file_path]
            if arguments:
                # Validate arguments
                for arg in arguments:
                    if any(c in arg for c in DANGEROUS_ARG_CHARS):
                        self.error_logger.error("Potentially dangerous characters in argument: %s", arg)
                        return None
                cmd.extend(arguments)

            return await self._run_osascript(
                cmd,
                f"tempfile-script ({Path(temp_file_path).name})",
                timeout,
            )
        except OSError as e:
            self.error_logger.exception("Error creating temporary file: %s", e)
            return None
        except (ValueError, TypeError, subprocess.SubprocessError) as e:
            self.error_logger.exception("Error in temp file execution: %s", e)
            return None
        finally:
            # Clean up temporary file
            if temp_file_path:
                try:
                    Path(temp_file_path).unlink()
                    self.console_logger.debug("Cleaned up temporary file: %s", Path(temp_file_path).name)
                except OSError as e:
                    self.console_logger.warning("Could not delete temporary file %s: %s", temp_file_path, e)
