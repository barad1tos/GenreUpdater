"""AppleScript subprocess execution module.

This module handles the low-level subprocess execution for AppleScript
commands, including timeout handling, process cleanup, and temp file management.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import time
import uuid
from pathlib import Path
import asyncio.subprocess
from typing import TYPE_CHECKING

from src.services.apple.file_validator import AppleScriptFileValidator

if TYPE_CHECKING:
    import logging


# Constants for script execution
RESULT_PREVIEW_LENGTH = 50  # characters shown when previewing small script results
LOG_PREVIEW_LENGTH = 200  # characters shown when previewing long outputs/stderr
COMPLEX_SCRIPT_THRESHOLD = 1024  # bytes
COMPLEX_PATTERN_COUNT = 2  # number of complex patterns before using temp file
MAX_TELL_BLOCKS = 3  # maximum nested tell blocks before using temp file
DANGEROUS_ARGUMENT_CHARACTERS = [";", "&", "|", "`", "$", ">", "<", "!"]


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
    ) -> None:
        """Initialize the executor.

        Args:
            semaphore: Semaphore for concurrency control (can be None initially)
            apple_scripts_directory: Directory for temporary script files
            console_logger: Logger for debug/info messages
            error_logger: Logger for error messages
        """
        self.semaphore = semaphore
        self.apple_scripts_directory = apple_scripts_directory
        self.console_logger = console_logger
        self.error_logger = error_logger

    def update_semaphore(self, semaphore: asyncio.Semaphore) -> None:
        """Update the semaphore after async initialization.

        Args:
            semaphore: The initialized semaphore
        """
        self.semaphore = semaphore

    def log_script_success(self, label: str, script_result: str, elapsed: float) -> None:
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
        """Handle subprocess execution with timeout and error handling.

        Args:
            cmd: Command to execute as a list of strings
            label: Label for logging
            timeout_seconds: Timeout in seconds

        Returns:
            Command output if successful, None otherwise
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

                self.error_logger.error(
                    "◁ %s failed with return code %s: %s",
                    label,
                    proc.returncode,
                    stderr.decode().strip() if stderr else "",
                )
                return None

            except TimeoutError:
                self.error_logger.exception("⊗ %s timeout: %ss exceeded", label, timeout_seconds)
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
                await self.cleanup_process(proc, label)

        except OSError as e:
            self.error_logger.exception("⊗ %s subprocess error: %s", label, e)
            return None

    async def run_osascript(
        self,
        cmd: list[str],
        label: str,
        timeout_seconds: float,
    ) -> str | None:
        """Run an osascript command and return output.

        Args:
            cmd: Command to execute as a list of strings
            label: Label for logging
            timeout_seconds: Timeout in seconds

        Returns:
            Command output if successful, None otherwise
        """
        if self.semaphore is None:
            self.error_logger.error("AppleScriptExecutor semaphore not initialized.")
            return None

        async with self.semaphore:
            return await self.handle_subprocess_execution(cmd, label, timeout_seconds)

    def should_use_temp_file(self, script_code: str) -> bool:
        """Determine if a script should be executed via a temporary file.

        Large or complex scripts benefit from temporary file execution to avoid
        shell escaping issues and command-line length limitations.

        Args:
            script_code: The AppleScript code to evaluate

        Returns:
            True if the script should use temporary file execution
        """
        # Check script size
        script_size = len(script_code.encode())
        if script_size > COMPLEX_SCRIPT_THRESHOLD:
            self.console_logger.debug(
                "Script size %d exceeds threshold %d, using temp file",
                script_size,
                COMPLEX_SCRIPT_THRESHOLD,
            )
            return True

        # Check for complex patterns that might cause escaping issues
        complex_patterns = [
            r'\\"',  # Escaped quotes (literal \")
            r"\\\\",  # Escaped backslashes (literal \\)
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

    async def run_via_temp_file(
        self,
        script_code: str,
        arguments: list[str] | None,
        timeout_seconds: float,
    ) -> str | None:
        """Execute AppleScript via a temporary file.

        This method writes the script to a temporary file and executes it,
        which is more reliable for complex scripts than using the -e flag.

        Args:
            script_code: The AppleScript code to execute
            arguments: Optional arguments to pass to the script
            timeout_seconds: Timeout in seconds for script execution

        Returns:
            The output of the script, or None if an error occurred
        """
        temp_file_path: str | None = None
        try:
            # Check if apple_scripts_dir is set
            if self.apple_scripts_directory is None:
                self.error_logger.error("❌ AppleScript directory is not set. Cannot create temporary file.")
                return None

            # Create a temporary file with .applescript extension using an async-friendly approach
            temp_filename = f"temp_script_{uuid.uuid4().hex}.applescript"
            temp_file_path = str(Path(self.apple_scripts_directory) / temp_filename)
            # Write the script content asynchronously using an executor
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                AppleScriptFileValidator.write_temp_file_sync,
                temp_file_path,
                script_code,
            )

            self.console_logger.debug("Created temporary script file: %s", Path(temp_file_path).name)

            # Build command with the temp file
            cmd = ["osascript", temp_file_path]
            if arguments:
                # Validate arguments
                for arg in arguments:
                    if any(c in arg for c in DANGEROUS_ARGUMENT_CHARACTERS):
                        self.error_logger.error("Potentially dangerous characters in argument: %s", arg)
                        return None
                cmd.extend(arguments)

            return await self.run_osascript(
                cmd,
                f"tempfile-script ({Path(temp_file_path).name})",
                timeout_seconds,
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
