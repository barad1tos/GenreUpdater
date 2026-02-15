"""AppleScript file validation module.

This module provides security validation for AppleScript file paths
and ensures secure file access.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import logging


class AppleScriptFileValidator:
    """Validates AppleScript file paths and ensures secure file access.

    This class handles security validation for AppleScript execution:
    - Path traversal prevention
    - Symlink rejection
    - File existence and access checks
    """

    def __init__(
        self,
        apple_scripts_directory: str | None,
        error_logger: logging.Logger,
        console_logger: logging.Logger,
    ) -> None:
        """Initialize the file validator.

        Args:
            apple_scripts_directory: Base directory containing AppleScript files
            error_logger: Logger for error messages
            console_logger: Logger for debug/info messages
        """
        self.apple_scripts_directory = apple_scripts_directory
        self.error_logger = error_logger
        self.console_logger = console_logger

    def validate_script_path(self, script_path: str) -> bool:
        """Validate that the script path is safe to execute.

        Ensures the path is within the allowed scripts directory and
        doesn't contain suspicious patterns like directory traversal.

        Args:
            script_path: Path to the script to validate

        Returns:
            True if the path is safe, False otherwise
        """
        try:
            if not script_path or not self.apple_scripts_directory:
                return False

            # Resolve the path to prevent directory traversal
            resolved_path = Path(script_path).resolve()
            scripts_directory = Path(self.apple_scripts_directory).resolve()

            # Ensure the path is within the allowed directory (safe from path traversal)
            if not resolved_path.is_relative_to(scripts_directory):
                self.error_logger.error("Script path is outside allowed directory: %s", script_path)
                return False

            # Check for suspicious patterns
            if any(part.startswith((".", "~")) or part == ".." for part in Path(script_path).parts):
                self.error_logger.error("Suspicious script path: %s", script_path)
                return False

            return True

        except (ValueError, TypeError) as e:
            self.error_logger.exception("Invalid script path %s: %s", script_path, e)
            return False

    def validate_script_file_access(self, script_path: str) -> bool:
        """Validate script file exists and is accessible.

        Checks that the file exists, is not a symlink (to prevent
        path traversal attacks), resolves to allowed directory, and is readable.

        Args:
            script_path: Path to the script file

        Returns:
            True if the file is valid and accessible
        """
        script_file = Path(script_path)

        # Reject symlinks to prevent path traversal attacks
        if script_file.is_symlink():
            self.error_logger.error("Symlinks not allowed: %s", script_path)
            return False

        # Resolve the path and check for symlinks in parent directories
        try:
            resolved_path = script_file.resolve(strict=True)
            # Verify resolved path is within allowed directory
            if self.apple_scripts_directory:
                allowed_dir = Path(self.apple_scripts_directory).resolve()
                if not resolved_path.is_relative_to(allowed_dir):
                    self.error_logger.error(
                        "Resolved path escapes allowed directory: %s -> %s",
                        script_path,
                        resolved_path,
                    )
                    return False
        except (OSError, ValueError) as e:
            self.error_logger.exception("Could not resolve script path %s: %s", script_path, e)
            return False

        # Check if file exists (without following symlinks)
        if not script_file.is_file():
            self.error_logger.error("AppleScript file does not exist: %s", script_path)

            # List directory contents for debugging
            try:
                if self.apple_scripts_directory:
                    directory_contents = [f.name for f in Path(self.apple_scripts_directory).iterdir()]
                    self.console_logger.debug("Directory contents: %s", directory_contents)
            except OSError as e:
                self.console_logger.debug("Could not list directory contents: %s", e)
            return False

        # Check if the file is readable by actually trying to open it
        # (more reliable than os.access which may not reflect ACLs correctly)
        # Note: .scpt files are compiled binary AppleScript, so open in binary mode
        try:
            with script_file.open("rb") as f:
                f.read(1)  # Read one byte to verify access
        except OSError as e:
            self.error_logger.exception("AppleScript file is not readable: %s (%s)", script_path, e)
            return False

        return True
