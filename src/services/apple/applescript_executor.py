"""AppleScript subprocess execution module.

This module handles the low-level subprocess execution for AppleScript
commands, including timeout handling and process cleanup.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
import asyncio.subprocess
from typing import TYPE_CHECKING

from core.tracks.track_delta import FIELD_SEPARATOR, LINE_SEPARATOR

if TYPE_CHECKING:
    import logging

    from core.retry_handler import DatabaseRetryHandler
    from services.apple.rate_limiter import EnhancedRateLimiter


# Constants for script execution
RESULT_PREVIEW_LENGTH = 50  # characters shown when previewing small script results
LOG_PREVIEW_LENGTH = 200  # characters shown when previewing long outputs/stderr


class AppleScriptExecutionError(OSError):
    """Exception raised when AppleScript execution fails.

    This exception is used to signal transient errors that may be retried
    by the DatabaseRetryHandler. It extends OSError to leverage the retry
    handler's transient error detection based on errno codes.
    """

    def __init__(self, message: str, label: str, errno_code: int | None = None) -> None:
        """Initialize the execution error.

        Args:
            message: Error description
            label: Script label for context
            errno_code: Optional errno code for transient error detection
        """
        super().__init__(errno_code, message)
        self.label = label


class AppleScriptExecutor:
    """Handles subprocess execution for AppleScript commands.

    This class manages the execution lifecycle including:
    - Running osascript subprocesses
    - Handling timeouts and cancellation
    - Process cleanup
    - Temporary file execution for complex scripts
    """

    def __init__(
        self,
        semaphore: asyncio.Semaphore | None,
        apple_scripts_directory: str | None,
        console_logger: logging.Logger,
        error_logger: logging.Logger,
        retry_handler: DatabaseRetryHandler | None = None,
        rate_limiter: EnhancedRateLimiter | None = None,
    ) -> None:
        """Initialize the executor.

        Args:
            semaphore: Semaphore for concurrency control (can be None initially)
            apple_scripts_directory: Directory for temporary script files
            console_logger: Logger for debug/info messages
            error_logger: Logger for error messages
            retry_handler: Optional retry handler for transient error recovery
            rate_limiter: Optional rate limiter for enhanced throughput control
        """
        self.semaphore = semaphore
        self.apple_scripts_directory = apple_scripts_directory
        self.console_logger = console_logger
        self.error_logger = error_logger
        self.retry_handler = retry_handler
        self.rate_limiter = rate_limiter

    def update_semaphore(self, semaphore: asyncio.Semaphore) -> None:
        """Update the semaphore after async initialization.

        Args:
            semaphore: The initialized semaphore
        """
        self.semaphore = semaphore

    def update_rate_limiter(self, rate_limiter: EnhancedRateLimiter) -> None:
        """Update the rate limiter after async initialization.

        When a rate limiter is set, it takes precedence over the semaphore
        for concurrency control, providing both rate limiting and concurrency.

        Args:
            rate_limiter: The initialized rate limiter
        """
        self.rate_limiter = rate_limiter

    def log_script_success(self, label: str, script_result: str, elapsed: float) -> None:
        """Log successful script execution with appropriate formatting.

        Args:
            label: Script label for logging
            script_result: Script output
            elapsed: Execution time in seconds
        """
        # Skip verbose logging for update_property - higher-level logs are more informative
        if label.startswith("update_property"):
            self.console_logger.debug("◁ %s completed in %.1fs", label, elapsed)
            return

        if label.startswith(("fetch_tracks.applescript", "fetch_tracks_by_ids.applescript")):
            # Count tracks by counting line separators (ASCII 29)
            track_count = script_result.count(LINE_SEPARATOR)
            size_kb = len(script_result.encode()) / 1024

            self.console_logger.info(
                "◁ %s: %d tracks (%.1fKB, %.1fs)",
                label,
                track_count,
                size_kb,
                elapsed,
            )
        elif label.startswith("fetch_track_ids.applescript"):
            # Just show count of IDs fetched - no preview needed
            id_count = script_result.count(",") + 1 if script_result.strip() else 0
            size_kb = len(script_result.encode()) / 1024
            self.console_logger.info(
                "◁ %s: %d IDs (%.1fKB, %.1fs)",
                label,
                id_count,
                size_kb,
                elapsed,
            )
        elif LINE_SEPARATOR in script_result or FIELD_SEPARATOR in script_result:
            # Other scripts with field/record separators - show count only
            record_count = script_result.count(LINE_SEPARATOR) or script_result.count(FIELD_SEPARATOR)
            size_kb = len(script_result.encode()) / 1024
            self.console_logger.info(
                "◁ %s: %d records (%.1fKB, %.1fs)",
                label,
                record_count,
                size_kb,
                elapsed,
            )
        else:
            # Create a preview for logging - this can be stripped
            preview_text = script_result.strip()
            preview = f"{preview_text[:RESULT_PREVIEW_LENGTH]}..." if len(preview_text) > RESULT_PREVIEW_LENGTH else preview_text
            # Log at appropriate level based on result content
            log_level = self.console_logger.debug if "No Change" in preview else self.console_logger.info
            log_level(
                "◁ %s (%dB, %.1fs) %s",
                label,
                len(script_result.encode()),
                elapsed,
                preview,
            )

    async def cleanup_process(self, proc: asyncio.subprocess.Process, label: str) -> None:
        """Clean up process resources.

        Args:
            proc: Process to clean up
            label: Label for logging
        """
        try:
            # Wait briefly for process to exit naturally
            async with asyncio.timeout(0.5):
                await proc.wait()
            self.console_logger.debug("Process for %s exited naturally and cleaned up", label)
        except TimeoutError:
            # If still running, kill and wait for cleanup
            try:
                proc.kill()
                async with asyncio.timeout(5):
                    await proc.wait()
                self.console_logger.debug("Process for %s killed and cleaned up", label)
            except (TimeoutError, ProcessLookupError) as e:
                self.console_logger.warning(
                    "Could not kill or wait for process %s during cleanup: %s",
                    label,
                    str(e),
                )

    async def handle_subprocess_execution(
        self,
        cmd: list[str],
        label: str,
        timeout_seconds: float,
    ) -> str | None:
        """Handle subprocess execution with timeout, error handling, and optional retry.

        If a retry_handler is configured, transient errors will be automatically
        retried with exponential backoff.

        Args:
            cmd: Command to execute as a list of strings
            label: Label for logging
            timeout_seconds: Timeout in seconds

        Returns:
            Command output if successful, None otherwise
        """
        try:
            if not self.retry_handler:
                return await self._execute_subprocess(cmd, label, timeout_seconds)
            return await self.retry_handler.execute_with_retry(
                lambda: self._execute_subprocess(cmd, label, timeout_seconds),
                f"applescript:{label}",
            )
        except OSError:
            # All retries exhausted, return None for backward compatibility
            return None

    async def _execute_subprocess(
        self,
        cmd: list[str],
        label: str,
        timeout_seconds: float,
    ) -> str:
        """Execute subprocess and return result or raise exception.

        This internal method raises exceptions on failure to allow the retry
        handler to catch and retry transient errors.

        Args:
            cmd: Command to execute as a list of strings
            label: Label for logging
            timeout_seconds: Timeout in seconds

        Returns:
            Command output if successful

        Raises:
            AppleScriptExecutionError: On execution failure (may be transient)
            asyncio.CancelledError: If operation was canceled
        """
        try:
            start_time = time.time()
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                async with asyncio.timeout(timeout_seconds):
                    stdout, stderr = await proc.communicate()
                elapsed = time.time() - start_time

                # Process stderr if present
                if stderr:
                    stderr_text = stderr.decode().strip()
                    self.console_logger.warning("◁ %s stderr: %s", label, stderr_text[:LOG_PREVIEW_LENGTH])

                # Handle process completion
                if proc.returncode == 0:
                    # Don't strip() here as it removes special separator characters
                    script_result: str = stdout.decode()
                    self.log_script_success(label, script_result, elapsed)
                    return script_result

                # Non-zero return code - raise for potential retry
                error_msg = stderr.decode().strip() if stderr else f"return code {proc.returncode}"
                self.error_logger.error("◁ %s failed with return code %s: %s", label, proc.returncode, error_msg)
                # Use errno 61 (connection refused on macOS) to signal transient error
                raise AppleScriptExecutionError(error_msg, label, errno_code=61)

            except TimeoutError as e:
                self.error_logger.exception("⊗ %s timeout: %ss exceeded", label, timeout_seconds)
                # Timeout is transient - use errno 110 (connection timed out)
                timeout_msg = f"timeout after {timeout_seconds}s"
                raise AppleScriptExecutionError(timeout_msg, label, errno_code=110) from e

            except (subprocess.SubprocessError, OSError) as e:
                self.error_logger.exception("⊗ %s error during execution: %s", label, e)
                # Re-raise OSError directly for retry handler's is_transient_error
                raise

            except asyncio.CancelledError:
                self.console_logger.info("⊗ %s cancelled", label)
                raise

            except (UnicodeDecodeError, MemoryError, RuntimeError) as e:
                self.error_logger.exception("⊗ %s unexpected error during communicate/wait: %s", label, e)
                # These are not transient - raise without errno
                raise AppleScriptExecutionError(str(e), label) from e

            finally:
                await self.cleanup_process(proc, label)

        except OSError as e:
            self.error_logger.exception("⊗ %s subprocess error: %s", label, e)
            # Re-raise for retry handler
            raise

    async def run_osascript(
        self,
        cmd: list[str],
        label: str,
        timeout_seconds: float,
    ) -> str | None:
        """Run an osascript command and return output.

        Uses rate limiter if configured (provides both rate limiting and concurrency),
        otherwise falls back to semaphore-only concurrency control.

        Args:
            cmd: Command to execute as a list of strings
            label: Label for logging
            timeout_seconds: Timeout in seconds

        Returns:
            Command output if successful, None otherwise
        """
        # Use rate limiter if available (provides both rate limiting + concurrency)
        if self.rate_limiter is not None:
            try:
                await self.rate_limiter.acquire()
                return await self.handle_subprocess_execution(cmd, label, timeout_seconds)
            finally:
                self.rate_limiter.release()

        # Fall back to semaphore-only concurrency control
        if self.semaphore is None:
            self.error_logger.error("AppleScriptExecutor semaphore not initialized.")
            return None

        async with self.semaphore:
            return await self.handle_subprocess_execution(cmd, label, timeout_seconds)
