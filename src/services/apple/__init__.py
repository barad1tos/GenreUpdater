"""AppleScript integration module.

This module provides a high-level interface for executing AppleScript commands
to interact with Apple Music via the Music.app application.

Public API:
    - AppleScriptClient: Main client for executing AppleScript commands
    - AppleScriptSanitizer: Security validator for AppleScript code
    - AppleScriptSanitizationError: Exception for security violations
    - AppleScriptExecutionError: Exception for execution failures (transient)
    - EnhancedRateLimiter: Rate limiting for AppleScript execution
"""

from services.apple.applescript_client import AppleScriptClient
from services.apple.applescript_executor import AppleScriptExecutionError
from services.apple.rate_limiter import EnhancedRateLimiter
from services.apple.sanitizer import (
    MAX_SCRIPT_SIZE,
    AppleScriptSanitizationError,
    AppleScriptSanitizer,
)

__all__ = [
    "AppleScriptClient",
    "AppleScriptExecutionError",
    "AppleScriptSanitizer",
    "AppleScriptSanitizationError",
    "EnhancedRateLimiter",
    "MAX_SCRIPT_SIZE",
]
