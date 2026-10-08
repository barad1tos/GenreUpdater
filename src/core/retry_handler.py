"""Database retry handler with exponential backoff and transient error detection.

This module retries async operations with exponential backoff,
classifying errors so that only transient ones are retried.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import NoReturn, TypeVar, TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    import logging


# Type variable for retry operation return types
RetryResult = TypeVar("RetryResult")


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
    """Timing state of one execute_with_retry call.

    Holds the policy and the start time that the total timeout is measured from.
    """

    policy: RetryPolicy
    start_time: datetime = field(default_factory=lambda: datetime.now(UTC))

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

    Provides exponential backoff with jitter and transient error classification,
    which execute_with_retry applies to an operation.

    Args:
        logger: Logger instance for retry operation tracking
        default_policy: Default retry policy for operations

    """

    def __init__(
        self,
        logger: logging.Logger,
        default_policy: RetryPolicy | None = None,
    ) -> None:
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

        Every OSError, which includes ConnectionError and TimeoutError, counts as
        transient; any other error does when its message matches a known transient pattern.

        Args:
            error: Exception to analyze

        Returns:
            True if error appears transient and operation should be retried

        """
        error_message: str = str(error).lower()

        if isinstance(error, OSError):
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

    async def execute_with_retry(
        self,
        operation: Callable[[], Awaitable[RetryResult]],
        operation_id: str,
        policy: RetryPolicy | None = None,
    ) -> RetryResult:
        """Execute operation with retry logic.

        Retries after a ValueError, RuntimeError or OSError that is_transient_error
        classifies as transient, waiting the backoff delay between attempts; any other
        exception propagates at once. A non-transient error, or any error from the last
        allowed attempt, is re-raised unchanged; a negative max_retries allows no retry but
        still runs the operation once. When no retry can start within the total timeout,
        TimeoutError, raised from the last failure, takes its place: at once if the backoff
        would end past the deadline, or after a backoff that ran late. An attempt already
        running is not interrupted, so its result, or an error that ends the loop, comes
        through unchanged even after the deadline.

        Args:
            operation: Async callable to execute with retry
            operation_id: Unique identifier for the operation
            policy: Retry policy to use

        Returns:
            Result from successful operation execution

        Raises:
            OSError: If the last allowed attempt fails with it, or, as TimeoutError, when no retry can start within the total timeout
            RuntimeError: If the operation fails with a non-transient error or retries are exhausted
            ValueError: If the operation fails with a non-transient error or retries are exhausted

        """
        retry_policy: RetryPolicy = policy or self.database_policy
        context: RetryOperationContext = RetryOperationContext(policy=retry_policy)

        self.logger.debug(
            "Starting retry operation '%s' with policy: max_retries=%d, base_delay=%.2fs",
            operation_id,
            retry_policy.max_retries,
            retry_policy.base_delay_seconds,
        )

        last_error: Exception | None = None
        attempt = 0
        # A negative max_retries still allows the one attempt the loop always makes
        attempt_limit = max(retry_policy.max_retries, 0) + 1

        while True:
            # Check for total operation timeout
            if context.has_exceeded_timeout:
                self._raise_timeout_error(operation_id, context, retry_policy, last_error)

            try:
                result = await operation()
                self.logger.debug(
                    "Operation '%s' succeeded on attempt %d/%d (%.2fs elapsed)",
                    operation_id,
                    attempt + 1,
                    attempt_limit,
                    context.total_elapsed_seconds,
                )
                return result

            except (ValueError, RuntimeError, OSError) as error:
                last_error = error

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

                # A retry that could only start past the deadline would be replaced by TimeoutError, so do not wait for it
                if context.total_elapsed_seconds + delay_seconds > retry_policy.operation_timeout_seconds:
                    self._raise_timeout_error(operation_id, context, retry_policy, error)

                self.logger.warning(
                    "Operation '%s' failed on attempt %d/%d: %s. Retrying in %.2fs...",
                    operation_id,
                    attempt + 1,
                    attempt_limit,
                    error,
                    delay_seconds,
                )

                # Wait before retry
                await asyncio.sleep(delay_seconds)
                attempt += 1

    def _raise_timeout_error(
        self,
        operation_id: str,
        context: RetryOperationContext,
        retry_policy: RetryPolicy,
        cause: Exception | None,
    ) -> NoReturn:
        """Log that no attempt fits in the total timeout and raise TimeoutError.

        Args:
            operation_id: Unique identifier for the operation
            context: Current retry operation context
            retry_policy: Active retry policy configuration
            cause: The last failure, chained as the cause and named in the log, since a caller may drop the TimeoutError

        Raises:
            TimeoutError: Always: no attempt can start within the total timeout

        """
        self.logger.error(
            "Operation '%s' stopped after %.2fs: no attempt can start within its %.2fs total timeout (last failure: %r)",
            operation_id,
            context.total_elapsed_seconds,
            retry_policy.operation_timeout_seconds,
            cause,
        )
        msg = f"Operation '{operation_id}' has no time left for an attempt within its total timeout of {retry_policy.operation_timeout_seconds}s"
        raise TimeoutError(msg) from cause
