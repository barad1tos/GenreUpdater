"""Core exceptions for configuration and retry handling.

This module contains shared exception classes to avoid circular imports
between config.py and retry_handler.py modules.
"""


class ConfigurationError(Exception):
    """Raised when configuration loading or parsing fails."""

    def __init__(self, message: str, config_path: str | None = None) -> None:
        """Initialize the configuration error.

        Args:
            message: Error description
            config_path: Path to the config file that caused the error

        """
        super().__init__(message)
        self.config_path = config_path


class RetryError(Exception):
    """Base exception for retry-related errors."""


class RetryExhaustionError(RetryError):
    """Raised when retry attempts are exhausted."""

    def __init__(self, message: str, attempts: int, last_error: Exception | None = None) -> None:
        """Initialize retry exhaustion error.

        Args:
            message: Error description
            attempts: Number of retry attempts made
            last_error: The last error that occurred before giving up

        """
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error
