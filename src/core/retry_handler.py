"""Database retry handler with exponential backoff and transient error detection.

This module provides sophisticated retry mechanisms for database operations
with intelligent error classification and adaptive delay strategies.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypedDict, TypeVar, cast

from src.core.exceptions import ConfigurationError

# Type variable for retry operation return types
RetryResult = TypeVar("RetryResult")


class RetryMetadata(TypedDict, total=False):
    """Type definition for retry operation metadata.

    Defines the structure of metadata that can be stored
    in RetryOperationContext for tracking and debugging.
    """

    # Database operation metadata
    table: str  # Database table name

    # Timing metadata
    created_at: str  # ISO format timestamp
    timestamp: str  # ISO format timestamp

    # Music metadata fields
    expected_year: str  # Release year as string
    track_count: str  # Number of tracks as string

    # Generic fields for extensibility
    operation_type: str  # Type of operation being retried
    source: str  # Data source identifier
    reason: str  # Reason for retry or operation


def _create_empty_metadata() -> RetryMetadata:
    """Create an empty RetryMetadata dict for default_factory."""
    # Two-step cast: dict -> object -> RetryMetadata (TypedDict)
    # Empty dict is valid for RetryMetadata since all fields are optional (total=False)
    return cast(RetryMetadata, cast(object, {}))


@dataclass
class RetryPolicy:
    """Configuration for retry behavior with exponential backoff.

    Defines retry parameters including maximum attempts, delay settings,
    and jitter for avoiding thundering herd problems.
    """

    max_retries: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0
    exponential_base: float = 2.0
    jitter_range: float = 0.1  # +/-10% randomization
    operation_timeout_seconds: float = 300.0  # 5 minutes total timeout


@dataclass
class RetryOperationContext:
    """Context information for retry operations.

    Tracks operation progress, attempt history, and metadata
    for comprehensive retry operation management.
    """

    operation_id: str
    policy: RetryPolicy
    start_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    attempt_count: int = 0
    last_error: Exception | None = None
    metadata: RetryMetadata = field(default_factory=_create_empty_metadata)

    @property
    def total_elapsed_seconds(self) -> float:
        """Calculate the total elapsed time since operation start."""
        return (datetime.now(UTC) - self.start_time).total_seconds()

    @property
    def has_exceeded_timeout(self) -> bool:
        """Check if the operation has exceeded total timeout."""
        return self.total_elapsed_seconds > self.policy.operation_timeout_seconds


class DatabaseRetryHandler:
    """Advanced retry handler for database operations with intelligent error detection.

    Provides exponential backoff with jitter, transient error classification,
    and comprehensive retry context management for reliable database operations.
    """

    def __init__(
        self,
        logger: logging.Logger,
        default_policy: RetryPolicy | None = None,
    ) -> None:
        """Initialize retry handler with logging and default policy.

        Args:
            logger: Logger instance for retry operation tracking
            default_policy: Default retry policy for operations

        """
        self.logger: logging.Logger = logger
        self.database_policy: RetryPolicy = default_policy or RetryPolicy(
            max_retries=5,
            max_delay_seconds=30.0,
            jitter_range=0.2,
        )

        # Error classification patterns
        self._transient_error_patterns: set[str] = {
            "connection refused",
            "connection reset",
            "timeout",
            "temporary failure",
            "resource temporarily unavailable",
            "too many connections",
            "deadlock",
            "lock wait timeout",
        }

    def is_transient_error(self, error: Exception) -> bool:
        """Determine if an error is transient and worth retrying.

        Analyzes error type, message content, and errno codes
        to classify errors as transient (temporary) or permanent.

        Args:
            error: Exception to analyze

        Returns:
            True if error appears transient and operation should be retried

        """
        error_message: str = str(error).lower()

        # Connection-related errors are typically transient
        if isinstance(error, ConnectionError | TimeoutError | OSError):
            # Check for specific OSError errno codes that indicate transient issues
            if hasattr(error, "errno"):
                # Common transient errno codes
                transient_errnos: set[int] = {
                    111,  # Connection refused
                    110,  # Connection timed out
                    104,  # Connection reset by peer
                    32,  # Broken pipe
                    61,  # Connection refused (macOS)
                }
                if error.errno in transient_errnos:
                    return True
            return True

        # Check error message for transient patterns
        for pattern in self._transient_error_patterns:
            if pattern in error_message:
                return True

        # Specific database-related transient errors
        database_error_patterns: list[str] = [
            "database is locked",
            "sqlite3.OperationalError",
            "cursor closed",
            "connection closed",
        ]

        return any(db_error.lower() in error_message for db_error in database_error_patterns)

    @staticmethod
    def calculate_delay_seconds(
        attempt_number: int,
        policy: RetryPolicy,
    ) -> float:
        """Calculate delay for retry attempt with exponential backoff and jitter.

        Implements exponential backoff with configurable jitter to prevent
        thundering herd problems and distribute retry attempts over time.

        Args:
            attempt_number: Current attempt number (0-based)
            policy: Retry policy configuration

        Returns:
            Delay in seconds before next retry attempt

        """
        # Calculate exponential delay
        exponential_delay: float = policy.base_delay_seconds * (policy.exponential_base**attempt_number)

        # Apply maximum delay cap
        capped_delay: float = min(exponential_delay, policy.max_delay_seconds)

        # Add deterministic jitter to prevent thundering herd
        # Using hash-based jitter for reproducible but distributed delays
        jitter_amount: float = capped_delay * policy.jitter_range

        # Create deterministic jitter based on attempt number
        # This provides good distribution without cryptographic randomness
        jitter_seed: float = (attempt_number * 31 + 17) % 100 / 100.0  # 0.0-1.0
        jitter_offset: float = (jitter_seed - 0.5) * 2 * jitter_amount  # -jitter_amount to +jitter_amount

        final_delay: float = max(0.0, capped_delay + jitter_offset)

        return final_delay

    @asynccontextmanager
    async def async_retry_operation(
        self,
        operation_id: str,
        policy: RetryPolicy | None = None,
    ) -> AsyncGenerator[RetryOperationContext]:
        """Async context manager for retry operations with comprehensive tracking.

        Provides retry context with operation tracking, error handling,
        and automatic retry logic for database operations.

        Args:
            operation_id: Unique identifier for the operation
            policy: Retry policy to use (defaults to database_policy)

        Yields:
            RetryOperationContext: Context for tracking retry progress

        Example:
            async with retry_handler.async_retry_operation("db_read") as ctx:
                ctx.metadata["table"] = "tracks"
                result = await database_operation()

        """
        retry_policy: RetryPolicy = policy or self.database_policy
        context: RetryOperationContext = RetryOperationContext(
            operation_id=operation_id,
            policy=retry_policy,
        )

        self.logger.debug(
            "Starting retry operation '%s' with policy: max_retries=%d, base_delay=%.2fs",
            operation_id,
            retry_policy.max_retries,
            retry_policy.base_delay_seconds,
        )

        for attempt in range(retry_policy.max_retries + 1):
            context.attempt_count = attempt + 1

            try:
                # Check for total operation timeout
                if context.has_exceeded_timeout:
                    self._raise_timeout_error(operation_id, context, retry_policy)

                # Yield context for operation execution
                yield context

            except (ValueError, RuntimeError, OSError) as error:
                context.last_error = error

                # Check if this is the last attempt
                if attempt >= retry_policy.max_retries:
                    self.logger.exception(
                        "Operation '%s' failed permanently after %d attempts (%.2fs elapsed)",
                        operation_id,
                        attempt + 1,
                        context.total_elapsed_seconds,
                    )
                    raise

                # Check if error is worth retrying
                if not self.is_transient_error(error):
                    self.logger.warning(
                        "Operation '%s' failed with non-transient error: %s",
                        operation_id,
                        error,
                    )
                    raise

                # Calculate delay for next attempt
                delay_seconds: float = DatabaseRetryHandler.calculate_delay_seconds(attempt, retry_policy)

                self.logger.warning(
                    "Operation '%s' failed on attempt %d/%d: %s. Retrying in %.2fs...",
                    operation_id,
                    attempt + 1,
                    retry_policy.max_retries + 1,
                    error,
                    delay_seconds,
                )

                # Wait before retry
                await asyncio.sleep(delay_seconds)

            else:
                # If we reach here, operation succeeded
                self.logger.debug(
                    "Operation '%s' succeeded on attempt %d/%d (%.2fs elapsed)",
                    operation_id,
                    attempt + 1,
                    retry_policy.max_retries + 1,
                    context.total_elapsed_seconds,
                )
                return

    async def execute_with_retry(
        self,
        operation: Callable[[], Awaitable[RetryResult]],
        operation_id: str,
        policy: RetryPolicy | None = None,
    ) -> RetryResult:
        """Execute operation with retry logic.

        Convenience method for simple retry operations that don't need
        access to the retry context during execution.

        Args:
            operation: Async callable to execute with retry
            operation_id: Unique identifier for the operation
            policy: Retry policy to use

        Returns:
            Result from successful operation execution

        """
        async with self.async_retry_operation(operation_id, policy) as _:
            return await operation()

    def _raise_timeout_error(
        self,
        operation_id: str,
        context: RetryOperationContext,
        retry_policy: RetryPolicy,
    ) -> None:
        """Raise timeout error with proper logging and context.

        Args:
            operation_id: Unique identifier for the operation
            context: Current retry operation context
            retry_policy: Active retry policy configuration

        Raises:
            TimeoutError: Operation exceeded total timeout

        """
        timeout_error = TimeoutError(f"Operation '{operation_id}' exceeded total timeout of {retry_policy.operation_timeout_seconds}s")
        context.last_error = timeout_error
        self.logger.error(
            "Operation '%s' timed out after %.2fs (max: %.2fs)",
            operation_id,
            context.total_elapsed_seconds,
            retry_policy.operation_timeout_seconds,
        )
        raise timeout_error


class ConfigurationRetryHandler:
    """Configuration-specific retry handler with fallback mechanisms.

    Provides retry logic specifically for configuration loading operations
    with support for fallback configuration files and transient error handling.
    """

    def __init__(self, logger: logging.Logger) -> None:
        """Initialize configuration retry handler.

        Args:
            logger: Logger instance for retry operation tracking

        """
        self.logger = logger
        self.retry_policy = RetryPolicy(base_delay_seconds=0.5, max_delay_seconds=10.0)

    def load_config_with_fallback(self, config_path: str, fallback_paths: list[str] | None = None) -> dict[str, Any]:
        """Load configuration with fallback mechanism.

        Attempts to load primary configuration file, falling back to
        alternate configurations if the primary fails.

        Args:
            config_path: Primary configuration file path
            fallback_paths: List of fallback configuration file paths

        Returns:
            Dictionary containing loaded configuration

        Raises:
            ConfigurationError: If all configuration sources fail

        """
        # Lazy import to avoid circular dependency
        from src.core.core_config import load_config  # noqa: PLC0415

        # Try primary configuration
        try:
            self.logger.info("Attempting to load primary config: %s", config_path)
            return load_config(config_path)
        except (OSError, ValueError, TypeError, ImportError, KeyError) as e:
            self.logger.warning("Primary config failed: %s", e)

            # Try fallback configurations
            if fallback_paths:
                for fallback_path in fallback_paths:
                    try:
                        self.logger.info("Attempting fallback config: %s", fallback_path)
                        return load_config(fallback_path)
                    except (OSError, ValueError, TypeError, ImportError, KeyError) as fallback_error:
                        self.logger.warning("Fallback config %s failed: %s", fallback_path, fallback_error)
                        continue

            # All configurations failed
            paths_tried = [config_path] + (fallback_paths or [])
            error_message = f"All configuration sources failed. Tried: {paths_tried}"
            self.logger.exception(error_message)
            raise ConfigurationError(error_message) from e
