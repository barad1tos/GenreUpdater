"""AppleScript security sanitizer module.

This module provides security validation for AppleScript code
to prevent command injection and other security vulnerabilities.
"""

from __future__ import annotations

import logging
import re
from typing import Any

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
MAX_SCRIPT_SIZE = 10000  # 10KB limit for script size


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

        # Log any escaping changes for audit purposes
        if value != sanitized:
            self.logger.debug(
                "Escaped AppleScript string: %d characters processed, %d changes made",
                len(value),
                len([c for c, s in zip(value, sanitized, strict=False) if c != s]),
            )

        return sanitized

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

        # Check for reserved words that could be dangerous (exact word match)
        for reserved_word in APPLESCRIPT_RESERVED_WORDS:
            # Use word boundaries to prevent false positives (e.g., "delete" in "undelete")
            pattern = re.compile(rf"\b{re.escape(reserved_word.lower())}\b")
            if pattern.search(normalized_code):
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
            # Shell metacharacters are safe - we use create_subprocess_exec (no shell)
            sanitized_args = [self.sanitize_string(arg) for arg in arguments]
            cmd.extend(sanitized_args)

        self.logger.debug("Created safe AppleScript command with %d components", len(cmd))
        return cmd
